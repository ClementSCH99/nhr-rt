# Session 4 — Contrat du premier palier CC réel

Ce document archive le contrat, l'exécution et l'acceptation de la Session 4.
La session a été validée par le propriétaire du projet le 2026-08-18. Les
preuves complètes et les profils approuvés sont conservés dans
`archives/session4-validated-20260818.zip`.

## Résultat recherché

Exécuter un palier à courant constant court et supervisé sur `DC PM 1`, puis
démontrer avec des preuves exploitables que :

- le profil approuvé est celui réellement appliqué;
- la transition active, le courant et la durée correspondent à la demande;
- la condition de fin est identifiable;
- le CSV et le résultat de routine racontent la même chronologie;
- tout chemin de sortie ramène le module à un état sûr et vérifié;
- les répétitions acceptées donnent un comportement cohérent.

La Session 4 couvre **charge et décharge** avec des profils distincts. Les
profils réels approuvés demandent 5 A et 500 W, avec des limites de sécurité à
10 A et 1000 W. Cette enveloppe a été revue par le propriétaire du projet pour
le module 613; elle reste volontairement courte et supervisée.

## Répartition des responsabilités

| Activité | Responsable |
|---|---|
| Contrat d'architecture et revue des protections | Propriétaire du projet |
| Valeurs, cohérence et approbation du profil réel | Propriétaire du projet |
| Implémentation, tests automatisés et rapport d'exécution | Codex |
| Prévol, lancement, observation et décision de répéter | Ensemble |
| Acceptation finale de la Session 4 | Propriétaire du projet |

Codex ne lance pas seul un test matériel énergisant. Pendant la validation
commune, chaque passage actif est annoncé, confirmé, exécuté une fois, puis
interprété avant le suivant.

## Périmètre convenu

La Session 4 couvre une seule routine CC bornée :

```text
état initial sûr
    → limites approuvées et relues
    → armement court
    → mesure fraîche et préconditions
    → transition CHARGE ou DISCHARGE
    → maintien jusqu'à durée ou condition de fin
    → standby et disable
    → vérification finale et reconnexion indépendante
```

L'implémentation validée fournit maintenant `scripts/supervised_cc_hold.py`, des templates
inutilisables par défaut, une simulation avec le profil exact, un CSV à 10 Hz et
un rapport JSON durable. Le test matériel générique sous `pytest` reste une
barrière de non-régression; il n'est pas l'interface opérateur principale.

Les plafonds logiciels Session 4 sont 5 A, 500 W et 60 secondes, avec un
armement d'au plus 70 secondes. Ils limitent le runner sans remplacer les
limites de sécurité du profil. Les profils durée, capacité et énergie restent
plus courts; l'enveloppe de 60 secondes est réservée au profil tension approuvé.
Tout élargissement supplémentaire sort du contrat et revient en revue
d'architecture.

## Hors scope

- CC-CV, repos enchaînés, profils multi-paliers ou cyclage prolongé;
- exécution distante par le service HTTP ou depuis un client 64 bits;
- lancement sans opérateur, planification ou reprise automatique;
- intégration CAN/BMS et interlocks automatiques;
- mode de régulation autre que CC;
- modification des limites entre répétitions sans nouvelle revue;
- validation de performance, capacité batterie ou conformité produit.

## Profil réel à approuver

Le futur profil local devra être séparé de l'exemple versionné et rester ignoré
par Git. Il contiendra au minimum :

- description du banc, DUT/module, câblage et procédure d'arrêt;
- nom logique NHR et identification attendue du module;
- mode, courant, tension, puissance, durée et durée d'armement;
- tolérance de courant, temps de stabilisation et nombre minimal
  d'échantillons actifs qualifiés;
- limites charge/décharge, délais associés et nom de profil unique;
- délai maximal de sécurité et condition de fin éventuelle;
- politique watchdog approuvée pour cette session;
- confirmation que le profil et la section CC sont tous deux approuvés.

Le capteur UUT n'est pas câblé sur le banc actuel. Tous les templates matériels
imposent donc `uut_temperature_max: null` et
`ignore_uut_temperature: true`; aucune lecture de température ne participe à
la protection ou à l'arrêt réel. Un template séparé couvre l'arrêt par
température en simulation. Le validateur refuse ce profil avec `--hardware`.

## Méthodes d'arrêt couvertes

La durée est toujours présente comme délai maximal de sécurité. Sans condition,
son expiration est le motif normal de fin. Avec une condition, atteindre ce
délai sans atteindre le seuil fait **échouer** la routine. La condition est
évaluée une dernière fois à l'échéance afin qu'un seuil atteint exactement à
la frontière soit accepté.

| Champ | Unité | Résolution selon le mode |
|---|---|---|
| `voltage` | V | tension immédiate NHR |
| `capacity_ah` | Ah | compteur charge ou décharge NHR |
| `energy_wh` | Wh | compteur kWh charge ou décharge NHR converti en Wh |
| `temperature` | °C | simulation uniquement sur le banc actuel |

Les compteurs charge/décharge sont choisis automatiquement selon le mode CC.
Pour capacité et énergie, `relative: true` est obligatoire : le seuil compare
la magnitude positive du delta depuis la première mesure active. Les compteurs
bruts signés restent inchangés dans le CSV et la mesure terminale du rapport.
Le rapport conserve aussi la valeur de référence normalisée. Le simulateur
reproduit le signe négatif observé sur les compteurs de décharge NHR et remet le
compteur correspondant à zéro lors de l'entrée dans le mode.

## Comportements implémentés

Le runner de Session 4 complète la routine v1 comme suit :

- `SetState(CHARGE/DISCHARGE)` est la frontière énergisante et aucun appel
  `enable()` redondant n'est ajouté;
- le résultat distingue durée, seuil atteint, seuil non atteint, arrêt opérateur
  et défaut;
- les limites, l'état final, les canaux, les consignes et le watchdog sont relus
  et consignés;
- une fin normale doit être suivie d'une vérification sûre, puis d'une
  reconnexion indépendante;
- un échec de nettoyage ou une acquisition incomplète fait échouer le rapport,
  même si le palier lui-même s'est terminé.

Toutes les commandes backend continuent de passer par la façade `NHR9300` et
son thread COM dédié. Le simulateur, les interlocks, l'armement temporisé et les
acquittements matériels restent obligatoires.

## Portes avant le premier passage actif

### Porte 1 — Revue et simulation

- contrat d'architecture accepté par le propriétaire;
- profil réel nommé et approuvé par le propriétaire;
- exemple versionné toujours `approved: false`;
- tests ciblés et suite logicielle réussis;
- simulation exécutée avec une copie exacte des valeurs approuvées;
- rapport simulé et chemins d'erreur relus.

Commande de simulation :

```powershell
$env:PYTHONPATH = (Resolve-Path .\src).Path
.\.venv32\Scripts\python.exe .\scripts\supervised_cc_hold.py `
  --simulate --profile .\examples\cc_charge_duration.local.json
```

### Porte 2 — Prévol commun sur le banc

- PowerPanel et tout autre propriétaire de communication fermés;
- `DC PM 1` et l'identité attendue confirmés;
- DUT, polarité, tension, SOC et connexions vérifiés;
- opérateur présent, arrêt d'urgence accessible et procédure comprise;
- état initial `OFF` ou `STANDBY`, `Enabled=False`, consignes nulles;
- limites appliquées puis relues sans écart;
- politique de température et de watchdog confirmée;
- chemin de sortie et dossier de résultats annoncés.

Le prévol utilise le profil réel et écrit/revérifie les limites, sans appliquer
de consigne active :

```powershell
$env:NHR9300_RESOURCE = "DC PM 1"
$env:NHR9300_CC_ACK = "SUPERVISED_CC_READY"
.\.venv32\Scripts\python.exe .\scripts\supervised_cc_hold.py `
  --hardware --preflight-only `
  --profile .\examples\cc_charge_duration.local.json
```

Le `StaticInterlockProvider(safe=True)` représente uniquement cette confirmation
opérateur; ce n'est pas un interlock physique indépendant.

### Porte 3 — Exécution progressive commune

1. Exécuter un seul palier CC avec le profil annoncé.
2. Vérifier immédiatement le résultat, le CSV et l'état sûr après reconnexion.
3. Corriger ou arrêter au premier écart non expliqué.
4. Passer au profil suivant seulement après accord commun.

Ordre proposé des essais réels :

1. charge, arrêt par durée;
2. décharge, arrêt par tension;
3. charge, arrêt par capacité;
4. décharge, arrêt par énergie.

L'arrêt par température est simulé séparément. Une répétition est ajoutée pour
tout résultat bruité, proche d'une tolérance ou non expliqué.

## Critères d'acceptation

La Session 4 est acceptée lorsque les quatre profils réels approuvés répondent à
tous les critères suivants :

- profil, ressource, identité, horodatages et acquittement sont traçables;
- aucune limite relue ne diffère du profil au-delà de la tolérance définie;
- le sens du courant est correct et sa réponse est cohérente avec la consigne;
- tous les échantillons actifs sont conservés, y compris le transitoire;
- après `current_settling_time_s`, tous les échantillons qualifiés respectent
  la tolérance de courant approuvée et leur nombre atteint le minimum du profil;
- le motif de fin est explicite et compatible avec le CSV;
- l'acquisition n'a aucune `last_error` et sa cadence réelle est rapportée;
- chaque passage finit avec `Enabled=False`, état `OFF` ou `STANDBY`, canaux et
  consignes à zéro, armement expiré/annulé et watchdog dans l'état sûr approuvé;
- la reconnexion indépendante confirme ces postconditions;
- aucun arrêt d'urgence, comportement inattendu ou écart non résolu n'est
  masqué par une moyenne entre répétitions.

Pour les profils réels actuels, le propriétaire a approuvé une tolérance de
courant de 0,5 A et un temps de stabilisation de 0,5 s. Toute modification de
ces valeurs exige une nouvelle revue du profil.

## Avancement des essais réels

Le 2026-08-18, le profil charge/durée 5 A, 500 W et 10 s a passé le prévol puis
le test réel (`session4-results/20260818T200733Z/report.json`). Le CSV conserve
102 échantillons actifs, dont 6 pendant la stabilisation. Les 96 échantillons
qualifiés ont une moyenne de 4,99997 A et une erreur absolue maximale de
0,00944 A. La routine s'est arrêtée par durée et la reconnexion a confirmé
`OFF`, `Enabled=False`, canaux et consignes à zéro, watchdog désactivé.

Le profil charge/capacité a ensuite atteint +0,0100405 Ah relatif et a passé
(`session4-results/20260818T201228Z/report.json`). Le profil décharge/énergie a
d'abord atteint son timeout de 12 s parce que les compteurs de décharge NHR
sont signés négatifs. Le nettoyage de cet essai a malgré tout confirmé toutes
les postconditions sûres (`20260818T201401Z`). L'alias générique `energy_wh` a
été corrigé pour comparer une magnitude positive, puis validé par tests,
simulation et nouveau prévol. La répétition réelle a passé à +1,00960 Wh
relatif (`20260818T201735Z`), avec une erreur courant maximale de 0,00860 A.

Ces résultats valident charge/durée, charge/capacité et décharge/énergie; ils
ne valent pas encore acceptation complète de la Session 4. L'essai décharge/
tension reste volontairement en dernier afin d'ajuster proprement son seuil et
son timeout avant exécution.

Le profil tension a ensuite été approuvé à 88,50 V avec un timeout de 60 s et
un armement de 65 s. Le runner a été borné à 60/70 s et la condition est
désormais évaluée à l'échéance. Après tests, simulation et prévol réussis,
l'essai réel `20260818T203237Z` a atteint son timeout : la tension est passée
de 88,8901 V à un minimum de 88,7742 V sans atteindre 88,50 V. Le nettoyage
et la reconnexion ont confirmé `OFF`, `Enabled=False`, canaux et consignes à
zéro et watchdog désactivé. Ce cas reste donc à ajuster et à répéter; aucun
échec de sécurité n'a été observé.

Le seuil a finalement été revu et approuvé à 88,80 V. Après une nouvelle
simulation et un nouveau prévol avec le même SHA-256, la répétition réelle
`20260818T203842Z` a passé : condition atteinte à 88,79989 V après environ
16,2 s actif. Le courant qualifié moyen était de -4,99906 A avec une erreur
absolue maximale de 0,01043 A. Le nettoyage et la reconnexion ont de nouveau
confirmé toutes les postconditions sûres. Les quatre cas CC prévus disposent
maintenant d'un résultat réel PASS. Le propriétaire a ensuite accepté et clos
la Session 4.

## Templates fournis

- `examples/cc_charge_duration.example.json`
- `examples/cc_discharge_voltage.example.json`
- `examples/cc_charge_capacity.example.json`
- `examples/cc_discharge_energy.example.json`
- `examples/cc_temperature_simulation.example.json`

Tous portent `approved: false`. Copier chaque template nécessaire vers un nom
`cc_*.local.json`, compléter l'identité et les informations de banc,
ajuster les valeurs puis valider séparément `safety_limits` et `cc_hold`.

Le runner matériel exige le watchdog activé pendant le palier, puis vérifie sa
désactivation au nettoyage et après reconnexion. Il exige aussi l'acquittement
`NHR9300_CC_ACK=SUPERVISED_CC_READY`. Aucune commande matérielle
n'est exécutée pendant l'implémentation ou la simulation.

Pendant le test matériel, le runner affiche aussi V/I/P, capacité et énergie
environ une fois par seconde. `Ctrl+C` demande l'arrêt de la routine, déclenche
l'emergency stop logiciel et conserve le nettoyage final; l'arrêt d'urgence
physique reste prioritaire.
