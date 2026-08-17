# NHR9300 Python Driver

Librairie Python typée, moteur de routines et service local pour les cyclers
NH Research NHR9300. Le backend réel utilise le driver officiel IVI-COM; un
simulateur permet de développer et valider les routines sans équipement.

Pour comprendre l'architecture, la répartition des responsabilités et les
modifications depuis la dernière phase validée, commencer par
[DEVELOPMENT.md](DEVELOPMENT.md). Pour approfondir chaque module et la logique
de ses fonctions, consulter [IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md).

## État de la v1

- Sessions indépendantes, chacune confinée à son propre thread COM.
- Lecture d’identité, capacités, état et mesures V/I/P/énergie/température.
- Acquisition continue configurable de 1 à 10 Hz et journal CSV.
- Limites, consignes, armement temporisé, interlocks et arrêt d’urgence.
- Routines Python et YAML; premier scénario de palier CC borné.
- Service HTTP JSON/SSE sur localhost et client Python sans dépendance.
- Tests simulés, matériels en lecture seule et matériels énergisants séparés.

La connexion est non destructive : `Initialize(..., IdQuery=False,
Reset=False)` est utilisé et aucun état n’est modifié automatiquement.

## Comment le dépôt fonctionne

Le chemin le plus simple à retenir est :

```text
Routine ou application
        ↓
NHR9300 (validation et sécurité)
        ↓
Backend simulateur ou IVI-COM
        ↓
NHR9300 physique
```

En parallèle, `AcquisitionCollector` lit les mesures et écrit le CSV. Le
service HTTP expose les mêmes fonctions aux applications qui ne peuvent pas
charger directement le driver 32 bits.

| Module | Rôle |
|---|---|
| `instrument.py` | Classe principale. Sérialise les appels, valide les limites, l’armement et les interlocks. |
| `backends/ivi.py` | Traduit les appels Python vers le driver officiel IVI-COM. |
| `backends/simulator.py` | Remplace le vrai cycler pendant le développement et les tests. |
| `types.py` | Définit les états, mesures, limites, consignes et résultats partagés. |
| `interlocks.py` | Fournit les autorisations de sécurité; accueillera plus tard les signaux BMS/CAN. |
| `acquisition.py` | Lit les mesures à 1–10 Hz, publie le dernier échantillon et écrit le CSV. |
| `routines.py` | Enchaîne les étapes d’un test et assure un arrêt sûr en cas d’erreur. |
| `service.py` / `client.py` | Relie le processus IVI 32 bits aux outils clients, notamment en 64 bits. |

Exemple : une routine demande un palier de décharge. Le moteur transmet les
limites et les consignes à `NHR9300`. La classe vérifie le profil, les
capacités, l’interlock, l’état initial, la mesure récente et le bail
d’armement. Elle autorise ensuite le backend à écrire sur le cycler. Pendant
le palier, l’acquisition surveille les mesures. Une erreur ou un interlock
unsafe entraîne une tentative `standby`, puis `disable`.

Pour débuter :

1. utiliser le simulateur et lancer les tests;
2. lire l’état du vrai appareil sans écrire;
3. valider chaque commande sûre séparément;
4. seulement ensuite lancer une routine énergisante supervisée.

## Prérequis matériels

- Windows et Python 3.12 **32 bits** pour le processus qui charge IVI-COM.
- Driver NH Research 32 bits, normalement installé sous
  `C:\Program Files (x86)\IVI Foundation\IVI\Bin\NHRDCPowerModule.dll`.
- Module déclaré dans **NHR Configurator**.
- Le `ResourceName` est le nom logique NHR, par exemple `DC PM 1`, et non
  l’adresse IP ou une chaîne VISA.

Installation dans l’environnement 32 bits existant :

```powershell
.\.venv32\Scripts\python.exe -m pip install -r requirements.txt
.\.venv32\Scripts\python.exe -m pip install -e . --no-build-isolation
```

## Utilisation directe

```python
from nhr9300 import NHR9300, StaticInterlockProvider
from nhr9300.backends.ivi import IVIBackend

instrument = NHR9300(
    "cycler-1",
    IVIBackend("cycler-1", "DC PM 1"),
    # Remplacer par un vrai interlock de banc avant toute routine.
    interlocks=[StaticInterlockProvider(safe=False)],
)

with instrument:
    print(instrument.read_identity())
    print(instrument.read_status())
    print(instrument.read_measurement())
```

Une activation exige, dans cet ordre :

1. un profil `SafetyLimits` portant `approved=True` et un nom;
2. des interlocks sûrs et récents;
3. `arm()` avec une durée limitée;
4. une mesure fraîche;
5. des consignes comprises dans les capacités et le profil approuvé;
6. `enable()`.

Une reconnexion ne réactive jamais automatiquement l’appareil. En cas de
défaillance pendant une routine, le moteur tente `standby`, puis `disable`.
Le watchdog reste un opt-in explicite tant que son comportement réel n’a pas
été validé sur le banc.

## Simulateur et service

Lancer deux instruments simulés depuis le Python 32 bits :

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv32\Scripts\python.exe -m nhr9300.service `
  --config .\examples\service.simulator.json
```

Depuis un autre environnement, y compris Python 64 bits :

```python
from nhr9300.client import NHRServiceClient

client = NHRServiceClient()
print(client.instruments())
print(client.connect("sim-1"))
print(client.measurement("sim-1"))
```

Pour installer uniquement le client dans un autre projet et consommer le flux
continu, voir [EXTERNAL_USE.md](EXTERNAL_USE.md). L’installation de base
n’impose ni IVI-COM ni `comtypes`; le processus matériel utilise l’option
d’installation `ivi`.

Le service n’accepte qu’une adresse locale. Routes principales :

- `GET /configuration` (configuration effective chargée au démarrage)
- `GET /instruments`
- `POST /instruments/{id}/connect` et `/disconnect`
- `GET /instruments/{id}`, `/measurement`, `/acquisition` et `/stream`
- `POST /instruments/{id}/limits`, `/arm`, `/command`
- `POST /instruments/{id}/routine` et `/stop`
- `GET /instruments/{id}/routine`

Au démarrage, le service affiche le chemin absolu du JSON chargé, l’URL
d’écoute et, pour chaque instrument, son backend, sa fréquence demandée et le
prochain chemin CSV. Le JSON est lu **une seule fois au démarrage** : toute
modification exige un redémarrage du service. La v1 ne fait aucun hot reload.

`GET /instruments/{id}/acquisition` retourne la fréquence demandée,
`active`, le compteur d’échantillons, l’heure du premier échantillon, la
fréquence observée, le chemin CSV effectif et la dernière erreur. Chaque
démarrage d’acquisition réserve un nouveau nom UTC horodaté; un CSV précédent
n’est donc jamais écrasé.

Une fermeture normale du flux SSE est journalisée en une ligne `INFO` et ne
stoppe pas l’acquisition. Une erreur d’acquisition reste exposée par
`last_error` et est journalisée en `ERROR`; une anomalie inattendue du
gestionnaire SSE conserve son traceback. Les abonnements sont retirés à la
fermeture du flux afin que les reconnexions n’accumulent pas de queues.

Pour fermer le service, utilisez `Ctrl+C` dans son terminal. Cette interruption
opérateur est traitée comme un arrêt normal : le service ferme les acquisitions,
les sessions instrument et le serveur, puis confirme son arrêt sans traceback.

## Session 2 : lecture continue vers CSV

Cette étape ne programme rien dans le NHR. Le flux est volontairement simple :

```text
NHR réel
   ↓  lectures IVI-COM sérialisées
NHR9300
   ↓  échantillons horodatés
AcquisitionCollector
   ├─→ dernier échantillon / abonnés
   └─→ fichier CSV + statistiques de cadence
```

- `IVIBackend` connaît le vocabulaire du driver officiel et lit les valeurs.
- `NHR9300` garantit qu’un seul thread accède à COM, ce qui évite les accès
  concurrents difficiles à diagnostiquer.
- `AcquisitionCollector` décide quand lire, écrit chaque ligne immédiatement
  et ferme proprement le fichier à l’arrêt. Les mesures V/I/P sont lues à la
  cadence demandée; l’état et les consignes, plus coûteux via COM et beaucoup
  moins variables, sont rafraîchis une fois par seconde (et à chaque changement
  d’étape) puis répétés dans les lignes intermédiaires.
- `session2_readonly.py` orchestre la validation du banc. Il ne reçoit qu’une
  interface de lecture, compare l’état avant/après, teste 1/5/10 Hz et produit
  un rapport JSON à côté des CSV.

Lancer une validation courte :

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
$env:NHR9300_RESOURCE = "DC PM 1"
.\.venv32\Scripts\python.exe .\scripts\session2_readonly.py `
  --duration 10 --output .\session2-results
```

Puis lancer la validation nominale (60 secondes par cadence et deux
reconnexions) en omettant `--duration`. Chaque CSV est vidé sur disque après
chaque ligne. Le rapport `report.json` contient le nombre d’échantillons, la
cadence effective, l’intervalle moyen et maximal, les dépassements de période,
les états avant/après et le résultat des reconnexions.

Une session est acceptée seulement si :

- les trois phases terminent sans erreur de communication;
- `enabled`, l’état d’opération et toutes les consignes restent inchangés;
- les CSV contiennent autant de lignes que le compteur d’échantillons;
- la cadence et les intervalles observés sont cohérents avec 1, 5 et 10 Hz;
- les reconnexions réussissent sans changer l’état du module.

Dans [service.hardware.example.json](examples/service.hardware.example.json),
`operator_supervised` est volontairement `false`; l’armement est donc refusé
tant qu’un intégrateur n’a pas fourni un interlock adapté.

## Session 3A : commandes de sécurité isolées

Cette première partie valide uniquement des écritures qui ne doivent pas faire
circuler d’énergie :

```text
état initial sûr
    → disable
    → limites approuvées
    → SetState(STANDBY), canaux désactivés
    → disable final
    → Close
    → reconnexion et vérification
```

Le runner refuse de commencer si le module est déjà activé ou dans un mode de
charge/décharge. Sur le NHR réel, `SetState(STANDBY)` peut afficher
temporairement `Enabled=True`; cette transition est acceptée uniquement si
tous les canaux sont désactivés, puis un `disable` explicite ramène le module
à `OFF` et `Enabled=False`. Le runner n’appelle ni `arm()`, ni le watchdog.

Copier [session3_bench.example.json](examples/session3_bench.example.json) vers
un fichier local non versionné, décrire le banc et faire approuver les limites.
L’exemple fourni porte `approved: false` et ne peut donc pas être utilisé tel
quel.

Une fois le banc revu par l’opérateur :

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
$env:NHR9300_RESOURCE = "DC PM 1"
$env:NHR9300_SESSION3_ACK = "SUPERVISED_SESSION3_WRITES_READY"
.\.venv32\Scripts\python.exe .\scripts\session3_safety.py `
  --bench-profile .\chemin\profil-session3.json
```

Chaque exécution crée un dossier horodaté et un `report.json`. Les faibles
consignes, `Enabled` et le watchdog seront ajoutés dans des phases distinctes
après validation de cette première séquence.

Le rapport compare aussi les limites demandées avec `GetChargeLimits` et
`GetDischargeLimits`. Un écart fait échouer la session. La température est
exclue de cette comparaison lorsque `uut_temperature_max` vaut `null`.

## Session 3B : première transition à faible consigne

La phase 3B traite `SetState(CHARGE/DISCHARGE)` comme la frontière qui peut
commencer à faire circuler de l’énergie. Elle ne suppose donc pas qu’un appel
ultérieur à `enable()` est nécessaire.

Le runner `scripts/session3b_low_setpoint.py` impose :

- une approbation `phase_b` distincte de celle des limites;
- au maximum 1 A, 100 W et 2 secondes;
- une mesure fraîche dans la fenêtre de tension approuvée;
- un armement de 1 à 30 secondes et des interlocks valides;
- la relecture des limites avant la transition;
- une acquisition CSV à 10 Hz;
- `standby`, puis `disable`, même après une erreur.

L’exemple porte `phase_b.approved: false`. Ne pas lancer cette phase sur le
matériel avant d’avoir adapté et revu le profil pour le banc réel. Le test
matériel exige en plus l’acquittement distinct
`NHR9300_SESSION3B_ACK=SUPERVISED_SESSION3B_LOW_SETPOINT_READY`.

Sur le NHR réel, le courant mesuré est positif en charge et négatif en
décharge. Le runner compare donc le changement de courant par rapport à la
mesure initiale, avec une tolérance adaptée au premier essai à faible courant.

## Routines

[cc_hold.example.yaml](examples/cc_hold.example.yaml) montre le schéma YAML v1.
Son profil porte volontairement `approved: false` et ne peut donc pas activer
un instrument sans modification consciente.

Les conditions de terminaison v1 acceptent `voltage`, `current`, `power` ou
`temperature` et les opérateurs `<`, `<=`, `>`, `>=`, `==`. Une routine
retourne un `RoutineResult` avec état, motif, événements, erreurs et CSV.

## Tests

Tests simulés et service :

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv32\Scripts\python.exe -m pytest -q
```

Lecture seule sur le vrai NHR :

```powershell
$env:NHR9300_RESOURCE = "DC PM 1"
.\.venv32\Scripts\python.exe -m pytest -m hardware_readonly `
  --run-hardware-readonly
```

Le wrapper énergisant exige simultanément :

- `--run-hardware-energizing`;
- `--bench-profile <fichier JSON approuvé>`;
- `NHR9300_RESOURCE`;
- `NHR9300_ENERGIZING_ACK=SUPERVISED_BENCH_READY`.

Il doit uniquement être lancé avec opérateur présent, arrêt d’urgence
accessible et limites revues pour la batterie raccordée.

## Extension BMS/CAN

`InterlockProvider` sépare les signaux de sécurité du pilote NHR. Un futur
adaptateur CAN/BMS pourra publier des signaux horodatés; une valeur unsafe ou
périmée fera échouer l’armement ou arrêtera la routine sans coupler le backend
IVI à une pile CAN particulière.

Les prochaines étapes et leurs critères de sortie sont suivis dans
[ROADMAP.md](ROADMAP.md).
