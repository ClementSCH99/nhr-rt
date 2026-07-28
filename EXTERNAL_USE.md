# Utiliser nhr-rt depuis un autre projet

## Architecture recommandée

Ne chargez pas le pilote NHR directement dans `can-py`. Gardez deux processus :

```text
Python 32 bits                         Python de can-py
nhr9300-service  ── HTTP/SSE local ─→ NHRServiceClient
      │                                      │
      ├─ lit IVI-COM                         ├─ reçoit les mesures NHR
      └─ écrit le CSV NHR                    └─ lit et décode le CAN/BMS
```

Cette séparation évite d’imposer Python 32 bits, IVI-COM et `comtypes` à
`can-py`. Le service reste propriétaire de la connexion NHR; `can-py` reçoit
seulement du JSON.

## 1. Installer le service NHR en 32 bits

Dans `nhr-rt` :

```powershell
.\.venv32\Scripts\python.exe -m pip install -e ".[ivi]" --no-build-isolation
```

L’option `ivi` installe les dépendances du service matériel (`comtypes` et
`PyYAML`). Configurez ensuite
[`examples/service.hardware.example.json`](examples/service.hardware.example.json),
puis lancez :

```powershell
.\.venv32\Scripts\nhr9300-service.exe `
  --config .\examples\service.hardware.example.json
```

Le service écoute uniquement sur `127.0.0.1`. À la connexion, il démarre
l’acquisition et continue d’écrire son propre CSV.

## 2. Installer seulement le client dans can-py

Depuis l’environnement Python de `can-py`, remplacez le chemin par le chemin
local de votre clone :

```powershell
python -m pip install -e "C:\chemin\nhr-rt" --no-build-isolation
```

L’installation de base n’a aucune dépendance externe et ne charge pas IVI-COM.
L’option `-e` signifie que les modifications faites dans `nhr-rt` deviennent
disponibles sans réinstallation.

## 3. Consommer les mesures

```python
from nhr9300 import NHRServiceClient

client = NHRServiceClient("http://127.0.0.1:9300")
instrument_id = "nhr-79503"  # champ "id" du fichier de configuration

print(client.instruments())
print(client.configuration())
client.connect(instrument_id)
print(client.acquisition(instrument_id))  # état effectif et chemin CSV unique

for measurement in client.stream(instrument_id):
    timestamp = measurement["timestamp_utc"]
    voltage_v = measurement["voltage_v"]
    current_a = measurement["current_a"]
    power_w = measurement["power_w"]

    # Transmettre ici la mesure à la queue ou à l'agrégateur de can-py.
```

`stream()` est un itérateur bloquant : exécutez-le dans un thread dédié si
`can-py` doit lire le bus CAN en parallèle. Ne faites pas de traitement lent
dans cette boucle; placez plutôt chaque mesure dans une `queue.Queue`.

Pour arrêter ce thread proprement, passez un `threading.Event` facultatif :

```python
import threading

stop_nhr = threading.Event()

for sample in client.stream("nhr-79503", stop_event=stop_nhr):
    nhr_samples.put(sample)
```

À l’arrêt, positionnez d’abord `stop_nhr`, attendez la fin du thread avec
`join()`, puis appelez `disconnect()`. N’appelez pas `disconnect()` pendant
que le thread consomme encore le flux. Une coupure inattendue doit déclencher
une lecture de `client.acquisition(...)` : un `last_error` non vide indique une
erreur réelle d’acquisition, contrairement à une simple perte de transport.

```python
import queue
import threading

from nhr9300 import NHRServiceClient

nhr_samples: queue.Queue[dict] = queue.Queue(maxsize=100)


def read_nhr_forever() -> None:
    client = NHRServiceClient()
    for sample in client.stream("nhr-79503"):
        try:
            nhr_samples.put_nowait(sample)
        except queue.Full:
            nhr_samples.get_nowait()  # abandonner le plus ancien point
            nhr_samples.put_nowait(sample)


threading.Thread(target=read_nhr_forever, daemon=True).start()
```

## Horodatage et CSV

Conservez trois sorties distinctes pendant l’intégration :

- le CSV NHR produit par `AcquisitionCollector`;
- le CSV CAN/BMS brut produit par `can-py`;
- éventuellement un CSV combiné produit par l’agrégateur de `can-py`.

Pour rapprocher les sources, utilisez `timestamp_utc`. Le champ `monotonic_s`
du NHR sert à mesurer précisément les intervalles dans le processus NHR, mais
ne doit pas être comparé à l’horloge monotone d’un autre processus.

L’agrégateur devrait enregistrer l’âge de chaque source. Une donnée BMS ou NHR
périmée ne doit pas être traitée comme une mesure actuelle.

Le fichier JSON du service est chargé une seule fois. Après toute modification
de ce fichier, redémarrez `nhr9300-service`; la v1 n’implémente pas de hot
reload. Le chemin retourné par `acquisition()` est le fichier CSV horodaté de
l’acquisition courante (ou de la dernière acquisition).

## Limite de responsabilité

Dans une première intégration, utilisez le flux uniquement pour observer et
enregistrer. Les méthodes `arm()`, `command()` et `start_routine()` du client
peuvent modifier le NHR et ne doivent pas être appelées depuis `can-py` tant
que l’intégration des interlocks BMS et les règles de sécurité n’ont pas été
validées séparément.
