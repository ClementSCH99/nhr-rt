# Guide d'implémentation de nhr-rt

Ce document explique la logique interne du projet, module par module et
fonction par fonction. Il décrit l'état du code de travail au 2026-08-17, y
compris les changements non encore commités.

Il ne remplace pas :

- [README.md](README.md), pour installer et utiliser le projet;
- [DEVELOPMENT.md](DEVELOPMENT.md), pour la méthode de travail et la revue;
- [ROADMAP.md](ROADMAP.md), pour les phases de validation à venir.

Les modules de `src/nhr9300` et les scripts opérateur sont détaillés ici. Les
tests sont ensuite reliés aux comportements qu'ils prouvent, mais ne sont pas
décrits fonction par fonction : ils constituent les spécifications exécutables
du code, pas une couche du produit.

## 1. Vue d'ensemble des interactions

```text
Client Python 64 bits
        |
        | HTTP JSON / SSE
        v
service.py -----> RoutineRunner --------+
     |                                  |
     +---------> AcquisitionCollector   |
     |                    |             |
     +--------------------+-------------+
                          v
                    NHR9300 facade
               validation + interlocks
                          |
                 file de requêtes
                          |
                 thread dédié au NHR
                          |
             +------------+------------+
             |                         |
      SimulatedBackend             IVIBackend
                                    IVI-COM
                                       |
                                   NHR9300 réel
```

La règle architecturale centrale est la suivante : **tout accès au backend
passe par `NHR9300`**. Cette façade garantit qu'un seul thread touche IVI-COM et
que les validations logicielles précèdent les commandes potentiellement
énergisantes.

Trois flux coexistent :

1. le **contrôle** envoie limites, consignes et changements d'état;
2. l'**acquisition** lit périodiquement les mesures et écrit le CSV;
3. le **service** transporte contrôle et mesures entre Python 64 bits et le
   processus IVI 32 bits.

## 2. Conventions transversales

### Temps UTC et temps monotone

Le projet utilise deux horloges, volontairement :

- `timestamp_utc` sert à corréler les données avec d'autres systèmes et à les
  présenter à un humain;
- `time.monotonic()` sert aux durées, cadences, délais et expirations. Cette
  horloge ne recule pas si Windows corrige son heure.

Une valeur monotone n'est valide que dans son processus. Elle ne doit donc pas
servir à synchroniser le NHR avec un client externe.

### Objets immuables

La plupart des structures utilisent `@dataclass(frozen=True, slots=True)` :

- `frozen=True` empêche une modification accidentelle après création;
- `slots=True` rend les attributs explicites et détecte plus tôt les fautes de
  nom;
- une nouvelle observation produit un nouvel objet au lieu de modifier
  silencieusement l'ancien.

### Signe du courant

Le simulateur représente la charge avec un courant négatif et la décharge avec
un courant positif. Les consignes restent des magnitudes positives; le mode
`CHARGE` ou `DISCHARGE` porte la direction.

## 3. `types.py` — vocabulaire commun

Ce module ne commande rien. Il définit les objets échangés entre le backend, la
façade, l'acquisition, les routines et le service.

### Énumérations

- **`OperatingState`** associe les états NHR à leurs valeurs numériques IVI :
  `OFF`, `STANDBY`, `CHARGE`, `DISCHARGE` et `BATTERY_EMULATION`. L'usage d'un
  `IntEnum` évite de disperser des nombres magiques dans le code.
- **`RoutineState`** décrit le cycle logiciel d'une routine : attente,
  exécution, réussite, arrêt demandé ou échec. Il hérite de `str` pour produire
  directement un JSON lisible.

### Structures de données

- **`Identity`** contient les informations stables du module physique.
- **`Capabilities`** décrit les limites annoncées par le matériel. Elles
  bornent les profils de sécurité, mais ne remplacent pas les limites propres à
  la batterie et au banc.
- **`SafetyLimits`** représente le profil approuvé par l'opérateur. Les délais
  sont transmis au NHR avec les limites. `approved` et `profile_name` forcent
  une décision consciente, mais ne constituent pas une signature sécurisée.
- **`Setpoints`** regroupe le mode, les valeurs, l'activation de chaque canal de
  régulation et les slew rates facultatifs. Une valeur et son booléen
  `*_enabled` sont séparés parce que le pilote NHR fonctionne ainsi.
- **`InstrumentStatus`** est une photographie de l'état. La façade y ajoute le
  bail d'armement local et la dernière erreur logicielle.
- **`InterlockSignal`** associe un verdict, un horodatage monotone et un détail.
  L'horodatage permet de refuser un signal ancien même s'il était sûr.
- **`RoutineEvent`** et **`RoutineResult`** forment le journal synthétique d'une
  routine. Le CSV contient les mesures détaillées; le résultat contient le
  déroulement et les erreurs.

### `Measurement.now(...)`

Construit une mesure en ajoutant l'heure UTC au moment de la lecture. Le
backend fournit séparément `monotonic_s`, utilisé pour évaluer la fraîcheur et
la cadence. Les mesures optionnelles deviennent `None` si le matériel ne les
fournit pas ou les déclare invalides.

### `to_jsonable(value)`

Convertit récursivement les objets publics en types acceptés par JSON :
`datetime` devient ISO 8601, une énumération devient sa valeur, une dataclass
devient un dictionnaire et les séquences deviennent des listes. Centraliser
cette conversion garantit le même format dans les rapports et dans le service.

## 4. `errors.py` — erreurs métier

Toutes les erreurs propres au package dérivent de **`NHRError`**. Cela permet
au service de distinguer une erreur attendue d'une panne interne.

- **`NHRConnectionError`** : session absente, perdue ou impossible à ouvrir.
- **`NHRDriverError`** : opération refusée ou échouée dans IVI-COM.
- **`NHRStateError`** : opération incompatible avec l'état courant.
- **`NHRValidationError`** : donnée invalide avant même d'appeler le matériel.
  Elle dérive aussi de `ValueError` pour rester naturelle dans une API Python.
- **`NHRNotArmedError`** : spécialisation d'état pour un bail absent ou expiré.
- **`NHRInterlockError`** : interlock absent, unsafe ou périmé.
- **`NHRRoutineError`** : erreur détectée par l'orchestrateur de routine.

La hiérarchie évite de rechercher des fragments de texte pour comprendre la
nature d'une erreur.

## 5. `interlocks.py` — autorisations externes

### `InterlockProvider.signals()`

`InterlockProvider` est un `Protocol` : toute classe possédant une méthode
`signals()` compatible peut être utilisée sans héritage obligatoire. Cette
frontière permettra un futur fournisseur CAN/BMS sans importer de pile CAN dans
le driver NHR.

### `StaticInterlockProvider`

- **`__init__(safe, name)`** crée un interlock simple pour le simulateur ou un
  banc sous supervision directe. Son état peut être modifié par un test.
- **`signals()`** crée un signal horodaté au moment de la demande. Il n'est donc
  jamais périmé tant que le fournisseur répond; il représente une autorisation
  locale, pas un signal matériel mémorisé.

### `validate_interlocks(providers, max_age_s, now=None)`

Collecte tous les signaux, exige qu'il y en ait au moins un, puis rejette ceux
qui sont unsafe ou trop anciens. Le paramètre `now` rend le calcul déterministe
dans les tests. La fonction retourne les signaux validés afin qu'un appelant
puisse éventuellement les journaliser.

## 6. `backends/base.py` — contrat des backends

**`NHRBackend`** est un `Protocol` qui liste les opérations minimales : cycle de
connexion, lectures, écriture des limites et consignes, enable, état et
watchdog. La façade dépend de ce contrat et non d'IVI-COM.

Ce découplage apporte deux choses :

- le simulateur et le backend réel sont interchangeables;
- les règles de sécurité restent testables sans équipement.

Le backend est volontairement mince : il traduit, mais ne décide pas si une
commande est autorisée. Cette décision appartient à `NHR9300`.

## 7. `backends/simulator.py` — équipement déterministe

Le simulateur sert à vérifier l'orchestration. Ce n'est pas un modèle physique
fidèle d'une batterie.

### Construction et vérification

- **`__init__(...)`** initialise connexion, état, capacités, consignes et une
  éventuelle exception injectée dans `failure`. Les capacités peuvent être
  remplacées pour tester différentes configurations.
- **`_check()`** reproduit les deux pannes utiles aux tests : une exception
  forcée ou un appel hors connexion.
- **`connect()`** marque le backend connecté et remet à zéro son origine de
  temps.
- **`close()`** marque uniquement la session fermée; l'état simulé du module est
  conservé pour tester une reconnexion non destructive.

### Lectures

- **`read_identity()`** retourne une identité fixe reconnaissable comme simulée.
- **`read_capabilities()`** retourne les capacités injectées ou celles par
  défaut.
- **`read_status()`** reconstruit une photographie depuis l'état interne.
- **`read_measurement()`** applique un modèle volontairement simple : courant
  nul hors enable ou hors charge/décharge, petite variation de tension,
  `P = V × I`, puis intégration idéale des Ah et kWh depuis la connexion. La
  température varie légèrement avec la magnitude du courant. Le déterminisme
  rend les tests reproductibles.

### Écritures simulées

- **`configure_safety_limits()`** mémorise le dernier profil reçu.
- **`configure_setpoints()`** remplace l'objet `Setpoints` complet.
- **`set_enabled()`** change le booléen d'activation.
- **`set_state()`** recrée un `Setpoints` avec le nouvel état en conservant les
  autres champs. Cette reconstruction est nécessaire parce que `Setpoints` est
  immuable.
- **`set_watchdog()`** mémorise l'état du watchdog sans simuler son délai réel.

## 8. `backends/ivi.py` — traduction vers le pilote officiel

Ce module est la seule couche qui connaît les membres COM du pilote NHR. Toutes
ses méthodes doivent être appelées par le même thread, ce que garantit la
façade.

### Cycle COM

- **`__init__(instrument_id, resource_name, driver_dll)`** conserve le nom
  logique NHR et le chemin du DLL. Il ne se connecte pas encore.
- **`_wrap(action, function)`** traduit toute exception COM en
  `NHRDriverError` en ajoutant le contexte de l'opération.
- **`connect()`** vérifie le DLL, initialise COM sur le thread courant, génère
  les bindings, crée l'objet officiel puis appelle
  `Initialize(resource_name, False, False, "")`. Les deux `False` désactivent
  l'identity query et le reset : la connexion ne normalise pas l'état observé.
  En cas d'échec partiel, COM est libéré.
- **`close()`** appelle `Close()`, efface les références et exécute toujours
  `CoUninitialize()` sur le même thread.
- **`driver`** refuse tout accès si la session n'existe pas.

### Lectures IVI

- **`read_identity()`** lit `Maintenance` et le nom logique.
- **`read_capabilities()`** traduit les capacités IVI en `Capabilities` typé.
- **`_setpoints()`** lit tous les champs de `Input.Operation` et convertit la
  valeur numérique d'état en `OperatingState`.
- **`read_status()`** combine Remote, Enabled, état et consignes.
- **`read_measurement()`** lit V/I/P avec la méthode immédiate. Ces trois champs
  sont obligatoires : une erreur fait échouer la lecture. La fonction interne
  **`optional(kind)`** retourne `None` pour Ah, kWh ou température si IVI lève
  une erreur ou marque la valeur invalide. Ainsi, une mesure auxiliaire absente
  ne masque pas les grandeurs principales.

### Écritures IVI

- **`configure_safety_limits()`** appelle séparément les limites de charge et de
  décharge, puis la température UUT seulement si elle est définie. La validation
  des valeurs a déjà eu lieu dans la façade.
- **`configure_setpoints()`** écrit d'abord les slew rates explicitement fournis,
  puis appelle `Operation.SetState(...)` avec mode, activations et valeurs.
- **`set_enabled()`** modifie uniquement `Input.Enabled`.
- **`set_state()`** lit les consignes actuelles puis rappelle
  `configure_setpoints()` avec un nouveau mode. L'intention est de ne pas
  écraser implicitement les autres réglages lors d'un changement d'état.
- **`set_watchdog()`** modifie explicitement `Input.SafetyLimits.Watchdog`. Aucun
  comportement automatique n'est associé au watchdog dans la v1.

## 9. `instrument.py` — façade de sécurité et propriétaire du thread

`NHR9300` est le cœur du projet. Il sérialise l'accès au backend et place les
contrôles locaux avant les écritures.

### Construction et thread de travail

- **`__init__(...)`** reçoit un backend déjà construit et au moins une source
  d'interlock. Il initialise une file de requêtes, les caches, le bail
  d'armement et un `RLock` protégeant le cycle connexion/fermeture.
- **`__enter__()` / `__exit__()`** permettent `with instrument:` et garantissent
  la fermeture de session.
- **`_worker()`** consomme `(fonction, arguments, Future)` en boucle. Il exécute
  chaque appel dans l'ordre et place résultat ou exception dans le `Future`. Un
  élément dont la fonction vaut `None` arrête proprement le thread.
- **`_start_worker()`** crée un thread daemon propre à l'instrument seulement si
  aucun thread vivant n'existe.
- **`_call(function, *args)`** dépose le travail dans la file puis attend le
  `Future`. Même si acquisition, routine et HTTP utilisent des threads
  différents, IVI-COM ne voit donc qu'un seul thread.
- **`_require_connected()`** échoue tôt avant de placer une requête impossible.

### Connexion et lectures

- **`connect()`** est idempotente. Elle démarre le worker, connecte le backend,
  met en cache les capacités et observe l'état sans le modifier. Si le module
  est déjà Enabled dans un mode actif, `_may_be_energized` le mémorise afin que
  l'acquisition applique immédiatement les contrôles runtime. Un échec de
  lecture ferme la session partielle.
- **`close()`** annule toujours l'armement, ferme le backend, envoie la sentinelle
  au worker et attend sa fin. Une erreur de `backend.close()` est conservée et
  relancée après le nettoyage du thread.
- **`read_identity()`** délègue la lecture après contrôle de connexion.
- **`read_capabilities()`** retourne le cache; les capacités sont considérées
  stables pendant une session.
- **`read_status()`** prend l'état matériel puis y ajoute le bail et la dernière
  erreur détenus par la façade.
- **`read_measurement()`** lit puis mémorise la dernière mesure. Ce cache sert à
  exiger une observation fraîche avant enable.

### Limites, consignes et armement

- **`_validate_limits()`** exige un profil nommé et approuvé, interdit les
  valeurs négatives et compare chaque limite aux capacités du NHR.
- **`configure_safety_limits()`** valide, écrit le profil, le mémorise puis
  annule tout bail existant. Changer les limites oblige donc à réarmer.
- **`_validate_setpoints()`** exige d'abord des limites. Les magnitudes doivent
  être positives; seules les grandeurs activées sont comparées aux limites du
  mode charge ou décharge.
- **`configure_setpoints()`** valide les valeurs et exige un bail pour entrer en
  charge ou décharge, même si `Enabled` est encore faux.
- **`arm(duration_s)`** accepte 1 à 300 secondes, uniquement depuis OFF ou
  STANDBY avec `Enabled=False`. Il valide les interlocks, puis calcule une
  expiration monotone. Armer ne commande pas le NHR.
- **`_require_arm()`** rejette un bail absent ou expiré et revalide les
  interlocks. L'autorisation est ainsi réévaluée, pas seulement mémorisée.
- **`check_interlocks()`** expose le même contrôle aux routines.
- **`may_be_energized`** indique le dernier risque observé ou commandé; cette
  propriété permet à l'acquisition de décider si une panne impose un arrêt.
- **`check_runtime_safety()`** exige le bail et les interlocks seulement lorsque
  l'énergie peut circuler.

### Transitions de contrôle

- **`configure_setpoints()`** exige aussi le bail, les interlocks et une mesure
  récente avant un état actif. Le banc réel a montré que `SetState` peut faire
  passer `Enabled=True`; cette méthode est donc une frontière énergisante.
- **`enable()`** conserve les mêmes barrières pour les intégrations qui
  l'utilisent explicitement, mais la sécurité ne suppose plus qu'il s'agit de
  la première commande capable de faire circuler de l'énergie.
- **`standby()`** demande seulement l'état STANDBY et efface le drapeau de risque.
  Il ne promet pas de modifier `Enabled`.
- **`disable()`** tente STANDBY puis exécute `Enabled=False` dans un `finally`.
  Même si la première commande échoue, la désactivation est donc encore tentée.
  Le bail est annulé.
- **`emergency_stop(reason)`** mémorise la raison, annule l'armement, puis tente
  indépendamment STANDBY et disable. Les deux opérations sont essayées même si
  la première échoue; une erreur signale ensuite un arrêt incomplet.
- **`set_watchdog(enabled)`** est un passage explicite vers le backend. Il n'est
  appelé automatiquement nulle part tant que son comportement réel n'est pas
  validé.

## 10. `acquisition.py` — mesures, diffusion et CSV

### Structures

- **`AcquisitionSample`** ajoute à une mesure le contexte routine/étape et les
  informations d'interlock ou d'erreur.
- **`AcquisitionStatistics`** contient les preuves de cadence calculées.
- **`AcquisitionState`** est la vue compacte exposée par le service.

### Construction et cycle de vie

- **`AcquisitionCollector.__init__()`** limite la cadence à 1–10 Hz, prépare le
  chemin CSV, les abonnés, les verrous et les compteurs. L'état complet est lu
  moins souvent que V/I/P grâce à `status_refresh_interval_s`.
- **`_unique_csv_path()`** ajoute un timestamp UTC avec microsecondes et, en cas
  de collision, un index. Une nouvelle acquisition n'écrase donc jamais un CSV
  précédent.
- **`running`** indique si le thread d'acquisition vit réellement.
- **`start()`** est idempotente pendant une exécution. Lors d'un redémarrage, il
  réserve un nouveau CSV, remet statistiques et erreurs à zéro, puis démarre le
  thread daemon.
- **`stop(timeout)`** positionne l'événement et attend le thread. Une exception
  explicite indique si un appel bloqué empêche l'arrêt.
- **`state()`** assemble la vue runtime et résout le chemin CSV en absolu pour
  qu'un client exécuté depuis un autre dossier trouve le fichier.
- **`statistics()`** copie les données sous verrou puis calcule cadence,
  intervalles et overruns sans relire l'instrument.

### Contexte et publication

- **`set_context(routine_id, step)`** associe les prochaines lignes à une étape.
  Il invalide aussi le cache de statut car une transition peut changer l'état
  ou les consignes.
- **`subscribe(maxsize)`** crée une queue indépendante pour un consommateur SSE.
- **`unsubscribe(queue)`** retire cette queue sans affecter l'acquisition.
- **`subscriber_count`** expose le nombre de consommateurs, surtout pour les
  tests et diagnostics.
- **`add_callback(callback)`** ajoute un consommateur synchrone local.
- **`_publish(sample)`** met à jour `latest`, puis distribue sans bloquer. Si la
  queue d'un abonné est pleine, le plus ancien élément est retiré : un écran
  lent reçoit une valeur récente au lieu de ralentir le banc. Les callbacks,
  eux, sont synchrones et doivent rester rapides.

### CSV et boucle temporelle

- **`_row(sample)`** transforme une mesure en ligne. Le statut et les consignes
  sont mis en cache environ une seconde, mais immédiatement rafraîchis après un
  changement d'étape. Ce compromis réduit les appels COM tout en gardant V/I/P
  à la cadence demandée.
- **`_run()`** ouvre le CSV, écrit l'en-tête, puis utilise une deadline cumulée
  plutôt qu'un simple `sleep(period)`. Cela évite d'ajouter la durée de lecture
  à chaque période. Chaque ligne est flushée immédiatement pour limiter les
  pertes lors d'une interruption. Après chaque mesure, les règles runtime sont
  vérifiées. Une erreur arrête l'acquisition et déclenche un emergency stop si
  le module peut être énergisé. Le fichier est toujours fermé dans `finally`.

## 11. `routines.py` — machine de test explicite

### Conditions et étapes

- **`Condition.evaluate(measurement)`** sélectionne un champ autorisé puis
  applique un opérateur dans une table explicite. Un champ optionnel absent ne
  termine pas la routine. Les noms inconnus sont rejetés.
- **`Step.execute()`** définit le contrat commun des étapes.
- **`ConfigureLimitsStep.execute()`**, **`ArmStep.execute()`**,
  **`MeasureStep.execute()`**, **`SetpointsStep.execute()`**,
  **`EnableStep.execute()`**, **`StandbyStep.execute()`** et
  **`DisableStep.execute()`** traduisent chacun une intention unique en appel de
  façade. Cette granularité rend le journal et l'ordre faciles à relire.
- **`WaitStep.execute()`** boucle jusqu'à la durée ou la condition. À chaque
  passage, elle traite une demande d'arrêt, une panne d'acquisition, les
  interlocks et une mesure fraîche. `Event.wait()` rend l'attente interrompable.
- **`Routine`** est un nom et une séquence immuable d'étapes.
- **`RoutineContext`** regroupe instrument, acquisition et événement d'arrêt
  transmis à chaque étape.

### `RoutineRunner`

- **`__init__()`** associe exactement une façade et un collecteur au runner.
- **`running`** reflète l'état du thread de routine.
- **`start(routine)`** refuse deux routines simultanées, crée un UUID et un
  résultat, puis lance l'exécution en arrière-plan.
- **`run(routine)`** utilise `start()` mais attend la fin; c'est la variante
  synchrone pratique pour un script ou un test.
- **`stop()`** positionne l'événement puis tente immédiatement un emergency
  stop. L'exception est absorbée ici, mais `_execute()` enregistrera l'état
  final; l'objectif premier de cette méthode est de ne pas retarder l'arrêt.
- **`_event()`** ajoute un événement UTC lisible au résultat.
- **`_execute()`** démarre le CSV, nomme chaque étape avec son index, met à jour
  le contexte et les événements, puis classe la sortie en PASSED, STOPPED ou
  FAILED. Toute sortie autre que PASSED déclenche un emergency stop. Enfin, le
  collecteur est arrêté et l'heure de fin est enregistrée. Une routine réussie
  dépend de ses dernières étapes pour atteindre l'état sûr.

### Construction des routines

- **`constant_current_hold(...)`** fabrique la séquence fixe : limites → armement
  → mesure fraîche → consignes → enable → attente → standby → disable. Seuls
  CHARGE et DISCHARGE sont acceptés.
- **`routine_from_mapping(data)`** impose un petit schéma v1. Il refuse les
  champs inconnus, convertit types et état, puis appelle le constructeur CC. Un
  schéma réduit limite les ambiguïtés d'un fichier externe.
- **`load_yaml(path)`** importe PyYAML seulement au besoin, charge avec
  `safe_load`, puis réutilise exactement la validation du mapping.

## 12. `safety_validation.py` — Sessions 3A et 3B isolées

- **`PrimitiveResult`** conserve état avant/après et heures d'une seule écriture.
- **`require_safe_start(status)`** refuse un module Enabled ou déjà en mode
  actif. La validation ne doit pas prétendre avoir créé un état initial sûr.
- **`require_disabled_standby(status)`** exprime l'invariant final attendu.
- **`SafetyPrimitiveValidator.__init__()`** reçoit la façade déjà connectée.
- **`_observe(name, operation, check)`** encadre une opération par deux lectures,
  l'horodate et applique immédiatement son invariant.
- **`run_phase_a(limits)`** exécute `disable`, limites, STANDBY avec canaux
  désactivés, puis un `disable` final. Elle ne contient aucun chemin vers
  `arm()`, `enable()` ou watchdog.
- **`_require_standby_channels_disabled()`** complète l'invariant en vérifiant
  chaque booléen de canal de régulation.

Ce module est séparé des routines énergisantes afin que le périmètre de la
Session 3A soit vérifiable directement dans le code.

- **`PhaseBProfile`** porte l'approbation distincte et la faible consigne.
- **`LowSetpointValidator`** impose les plafonds 1 A, 100 W et 2 secondes,
  vérifie tension initiale, limites relues, armement et interlocks, puis traite
  `configure_setpoints()` comme l'activation. Son `finally` force `standby` et
  `disable`, y compris lors d'une erreur avant l'activation.
- Le signe observé sur le NHR réel est positif en charge et négatif en
  décharge. Le simulateur suit cette convention et la phase 3B valide le delta
  de courant mesuré par rapport au point initial.

## 13. `service.py` — propriétaire local du NHR

Le service permet au processus Python 32 bits de garder IVI-COM, tandis qu'un
client 64 bits utilise HTTP. Il écoute uniquement sur la machine locale et n'a
pas de mécanisme d'authentification réseau.

### Serveur et instruments gérés

- **`LocalThreadingHTTPServer`** désactive la réutilisation d'adresse pour
  refuser une seconde instance sur le même port.
- **`ManagedInstrument`** regroupe façade, acquisition, routine et nom de
  backend pour un identifiant.
- **`InstrumentManager.__init__()`** lit le mapping de configuration et construit
  le backend, l'interlock statique, la façade, le collecteur et le runner de
  chaque instrument. Construire ne connecte pas le matériel.
- **`get(instrument_id)`** transforme un identifiant absent en erreur métier.
- **`inventory()`** lit les statuts; un instrument inaccessible reste visible
  avec `connected=False` au lieu de faire échouer tout l'inventaire.
- **`configuration()`** expose le fichier réellement chargé, l'URL, les cadences
  et les futurs chemins CSV. Elle indique explicitement qu'un redémarrage est
  requis après modification du JSON.
- **`close()`** arrête routine, acquisition puis session de chaque instrument.

### Gestionnaire HTTP

- **`log_message()`** neutralise le log brut de `BaseHTTPRequestHandler`; le
  projet utilise son propre logging ciblé.
- **`_json_body()`** lit la taille annoncée, décode JSON et exige un objet.
- **`_send(status, payload)`** applique `to_jsonable`, fixe les headers et écrit
  une réponse JSON complète.
- **`_route()`** découpe uniquement les routes v1 attendues et retourne
  `(instrument_id, action)`.
- **`do_GET()`** sert inventaire, configuration, statut, dernière mesure,
  résultat de routine, état d'acquisition ou flux. Pour `measurement`, la
  dernière acquisition est réutilisée; sinon une lecture directe est faite.
- **`_stream(managed)`** démarre l'acquisition si nécessaire, crée un abonnement
  SSE et envoie soit une mesure, soit un keep-alive. Une déconnexion client
  retire uniquement son abonnement : l'acquisition continue. Les fermetures
  réseau attendues sont journalisées sans traceback, les erreurs inattendues
  conservent le traceback.
- **`do_POST()`** orchestre connexion, déconnexion, limites, armement, commande,
  routine et arrêt. Une déconnexion est refusée tant qu'une routine tourne.
- **`_command()`** limite les commandes aux noms explicitement connus et
  convertit l'état textuel des setpoints en `OperatingState`. Toutes les
  commandes passent ensuite par la façade.
- **`_error()`** retourne HTTP 400 pour les erreurs métier ou de données et 500
  pour une panne inattendue.

### Démarrage du processus

- **`build_server()`** refuse une adresse non locale, construit manager et
  handler, réserve le port exclusif, puis annonce la configuration effective.
- **`serve()`** exécute la boucle HTTP et traite `Ctrl+C` comme un arrêt normal.
  Le manager et le socket sont fermés dans `finally`.
- **`main()`** configure le logging, lit les arguments, charge le JSON une seule
  fois et appelle `serve()`.

## 14. `client.py` — API 64 bits sans dépendance externe

- **`NHRServiceClient.__init__()`** normalise l'URL en retirant le `/` final.
- **`_request(method, path, body)`** construit la requête avec la bibliothèque
  standard, utilise un timeout de dix secondes, décode JSON et transforme une
  erreur HTTP du service en `NHRError`.
- **`instruments()`**, **`configuration()`**, **`connect()`**,
  **`disconnect()`**, **`status()`**, **`measurement()`** et
  **`acquisition()`** sont des wrappers lisibles sur les routes GET/POST.
- **`configure_limits()`**, **`arm()`** et **`command()`** transportent les
  décisions de contrôle vers la façade du service; elles n'ajoutent pas une
  seconde logique de sécurité côté client.
- **`start_routine()`**, **`routine()`** et **`stop()`** gèrent le cycle distant
  d'une routine.
- **`stream(instrument_id, stop_event=None)`** ouvre un flux SSE sans timeout,
  ignore les keep-alives et yield chaque ligne `data:` décodée. L'événement est
  vérifié entre les lignes; grâce aux keep-alives du serveur, un flux inactif se
  réveille périodiquement. Comme l'itérateur est bloquant, le client doit le
  consommer dans un thread dédié s'il effectue aussi d'autres tâches.

Le choix de la bibliothèque standard garde ce client installable dans `can-py`
sans imposer les dépendances IVI du service.

## 15. Fichiers `__init__.py` — surface publique

- **`nhr9300/__init__.py`** réexporte uniquement les classes nécessaires à un
  utilisateur courant. Les détails IVI et les runners spécialisés restent dans
  leurs modules explicites.
- **`backends/__init__.py`** expose le contrat et le simulateur. `IVIBackend`
  n'est pas importé automatiquement afin que l'import de base reste indépendant
  de COM.

`__all__` documente cette surface et évite que des imports internes deviennent
accidentellement une API promise.

## 16. Scripts opérateur

### `scripts/verify_connection.py`

- **`print_read_only_status(driver)`** affiche le minimum utile depuis l'objet
  COM officiel.
- **`main()`** vérifie le DLL, initialise COM, se connecte sans identity query ni
  reset, lit puis ferme dans `finally`.

Ce script est un diagnostic direct et minimal. Il contourne volontairement la
façade pour isoler la découverte IVI; il ne doit pas devenir un chemin de
contrôle de production.

### `scripts/session2_readonly.py`

- **`ReadOnlyInstrument`** réduit statiquement l'API autorisée aux lectures et au
  contrôle runtime. Cette seconde frontière rend une écriture accidentelle plus
  visible lors de la revue.
- **`state_signature()`** sélectionne Enabled, état et consignes, qui doivent
  rester identiques.
- **`wait_for_duration()`** attend avec l'horloge monotone et remonte
  immédiatement une erreur d'acquisition.
- **`acquire_phase()`** photographie avant/après, exécute une cadence, ferme le
  collecteur même en erreur et retourne preuves de cadence et invariance.
- **`parse_args()` / `validate_args()`** séparent lecture de la CLI et règles de
  validité.
- **`main()`** crée un dossier de résultat unique, exécute 1/5/10 Hz puis les
  reconnexions, et écrit toujours un rapport JSON. L'interlock est volontairement
  unsafe : même une erreur de logique ne pourrait pas armer l'instrument.

### `scripts/session3_safety.py`

- **`parse_args()`** exige un profil de banc et accepte le ResourceName via CLI
  ou variable d'environnement.
- **`load_limits()`** exige un objet `safety_limits`, un profil nommé et
  `approved=True`.
- **`main()`** ajoute une seconde confirmation via
  `NHR9300_SESSION3_ACK`, crée le rapport, exécute la Phase A, ferme/reconnecte
  et vérifie l'état persistant. Le `finally` tente disable, ferme la session et
  écrit le rapport même après une erreur.

Le profil approuvé et l'acquittement d'environnement remplissent deux rôles
différents : données de banc revues d'un côté, confirmation opérateur au moment
de l'exécution de l'autre.

## 17. Comment les tests prouvent ces intentions

| Fichier | Responsabilité couverte |
|---|---|
| `tests/test_instrument.py` | limites, armement, fraîcheur, interlocks et enable |
| `tests/test_acquisition_routines.py` | cadence, CSV, routines, arrêts et interlocks runtime |
| `tests/test_service.py` | API 32/64 bits, SSE, erreurs, port exclusif et shutdown |
| `tests/test_safety_validation.py` | Session 3A et protections simulées de la Session 3B |
| `tests/test_public_api.py` | exports publics attendus |
| `tests/hardware/test_readonly.py` | lectures réelles, opt-in explicite |
| `tests/hardware/test_energizing.py` | palier réel, avec barrières opérateur supplémentaires |

Le simulateur prouve la logique logicielle. Il ne prouve ni la correspondance
exacte des membres IVI, ni le comportement électrique, ni le watchdog réel.

## 18. Parcours de lecture conseillé

Pour comprendre le projet sans tout lire d'un coup :

1. `types.py` pour apprendre le vocabulaire;
2. `backends/base.py`, puis `simulator.py` pour voir le contrat;
3. `instrument.py` pour comprendre les protections;
4. `acquisition.py` pour suivre une mesure jusqu'au CSV/SSE;
5. `routines.py` pour suivre un palier complet;
6. `service.py`, puis `client.py` pour comprendre la séparation 32/64 bits;
7. `ivi.py` en dernier, comme traduction technique vers le matériel;
8. le test correspondant après chaque module pour vérifier l'intention décrite.

Quand l'implémentation d'un module change, ce guide doit être mis à jour dans la
même phase. La section *Changements en cours* de `DEVELOPMENT.md` indique alors
rapidement quelles parties de ce guide doivent être relues.
