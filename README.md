# NHR9300 Python Driver

Librairie Python typée, moteur de routines et service local pour les cyclers
NH Research NHR9300. Le backend réel utilise le driver officiel IVI-COM; un
simulateur permet de développer et valider les routines sans équipement.

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

Le service n’accepte qu’une adresse locale. Routes principales :

- `GET /instruments`
- `POST /instruments/{id}/connect` et `/disconnect`
- `GET /instruments/{id}`, `/measurement` et `/stream`
- `POST /instruments/{id}/limits`, `/arm`, `/command`
- `POST /instruments/{id}/routine` et `/stop`
- `GET /instruments/{id}/routine`

Dans [service.hardware.example.json](examples/service.hardware.example.json),
`operator_supervised` est volontairement `false`; l’armement est donc refusé
tant qu’un intégrateur n’a pas fourni un interlock adapté.

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
