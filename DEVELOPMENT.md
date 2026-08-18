# Guide de développement

Ce document explique **comment faire évoluer nhr-rt et comment relire une
phase**. Il s'adresse à un développeur débutant et doit rester court. Pour
installer ou utiliser le projet, voir [README.md](README.md). Pour comprendre
la logique interne fonction par fonction, voir
[IMPLEMENTATION_GUIDE.md](IMPLEMENTATION_GUIDE.md). Pour les étapes à venir,
voir [ROADMAP.md](ROADMAP.md).

## Responsabilités

| Sujet | Responsable principal |
|---|---|
| Architecture, limites de phase et modes de fonctionnement | Propriétaire du projet |
| Critères d'acceptation et validation finale | Propriétaire du projet |
| Implémentation, tests automatisés et mise à jour de ce document | Codex |
| Revue du code et décision de créer le commit de référence suivant | Ensemble |

Codex peut implémenter une phase convenue de bout en bout. Une décision qui
change l'architecture, une protection ou le comportement du banc doit cependant
être présentée avant d'être intégrée.

Une demande de journalisation ou de test logiciel **n'autorise jamais** une
commande énergisante sur le NHR. Les validations matérielles restent des étapes
explicites, supervisées et acceptées séparément.

## Carte simple du projet

```text
Application, routine ou client 64 bits
                  |
                  v
       service.py / routines.py
                  |
                  v
 instrument.py  (règles et protections)
                  |
          +-------+-------+
          |               |
          v               v
 simulator.py           ivi.py
                      Python 32 bits
                          |
                          v
                    NHR9300 physique

 acquisition.py lit les mesures, publie le flux et écrit le CSV.
 types.py définit les objets échangés entre toutes les couches.
```

Le chemin de contrôle important est toujours
`appelant -> NHR9300 -> backend`. Le code appelant ne doit pas contourner
`NHR9300` pour envoyer directement une commande au backend réel.

| Fichier ou dossier | À consulter pour comprendre... |
|---|---|
| `src/nhr9300/instrument.py` | les validations, interlocks et transitions sûres |
| `src/nhr9300/backends/` | la différence entre simulateur et IVI-COM |
| `src/nhr9300/acquisition.py` | l'échantillonnage, le flux et le CSV |
| `src/nhr9300/service.py` | l'API locale utilisée par les clients 64 bits |
| `src/nhr9300/routines.py` | l'enchaînement des étapes de test |
| `src/nhr9300/types.py` | la forme des états, mesures et consignes |
| `tests/` | le comportement attendu et les régressions couvertes |
| `scripts/` | les validations manuelles guidées sur le banc |

## Cycle d'une phase

1. **Contrat** — Définir l'objectif, le hors-scope, les protections et les
   critères d'acceptation.
2. **Implémentation** — Codex modifie le code, ajoute les tests et garde les
   changements centrés sur ce contrat.
3. **Contrôle logiciel** — Codex exécute les tests pertinents, relit le diff et
   complète la section *Changements en cours* ci-dessous.
4. **Revue** — Le propriétaire lit cette section, parcourt les fichiers indiqués
   et demande les corrections nécessaires.
5. **Validation** — Le propriétaire effectue ou supervise les contrôles manuels
   et matériels prévus par le contrat.
6. **Référence** — Après accord explicite, un commit marque la phase validée. La
   section *Changements en cours* repart alors de ce nouveau commit.

Le dernier commit représente donc la dernière base acceptée, pas seulement une
sauvegarde technique.

## Changements en cours

**Base de comparaison :** `03e1b44` — `fix: expose absolute acquisition CSV path`

Cette section décrit le diff de travail depuis cette base. Elle doit être mise à
jour avant chaque revue.

### 1. Validation non énergisante de la Session 3A

- `src/nhr9300/safety_validation.py` exécute et observe séparément `disable`,
  l'application des limites, la mise en `STANDBY` avec les canaux désactivés,
  puis un dernier `standby`.
- `scripts/session3_safety.py` ajoute un runner matériel supervisé. Il exige un
  profil de banc approuvé et l'acquittement
  `NHR9300_SESSION3_ACK=SUPERVISED_SESSION3_WRITES_READY`.
- `examples/session3_bench.example.json` fournit un exemple volontairement non
  approuvé.
- `tests/test_safety_validation.py` vérifie la séquence simulée et le refus d'un
  état initial actif ou déjà activé.

Le runner 3A ne doit appeler ni `arm()`, ni `enable()`, ni le watchdog. Le
runner 3B est maintenant distinct et traite `SetState` comme la frontière
énergisante observée sur le NHR réel.

### 2. Validation logicielle de la Session 3B

- `LowSetpointValidator` limite le premier essai à 1 A, 100 W et 2 secondes.
- Une approbation 3B séparée, un armement court, une mesure fraîche, les
  interlocks et la relecture des limites sont obligatoires.
- `scripts/session3b_low_setpoint.py` produit un CSV à 10 Hz et un rapport,
  puis tente toujours `standby` et `disable`.
- La phase 3B réelle a réussi sur `DC PM 1`, module 613, à 0,5 A pendant
  1 seconde. La preuve finale est archivée localement sous `archives/`.

### 3. Validation logicielle de la Session 3C

- `read_watchdog()` relit la valeur IVI au lieu de supposer que l'écriture a
  réussi.
- `WatchdogLossValidator` vérifie la faible consigne avant de fermer la
  communication, puis exige un état désactivé après reconnexion.
- Le nettoyage remet les consignes à zéro, désactive la sortie et le watchdog.
- Le simulateur couvre un watchdog qui déclenche et un watchdog trop lent qui
  laisse la sortie active.
- Le runner réel utilise deux processus avec délais maximaux : préparation,
  perte abrupte sans `IVI Close`, puis récupération indépendante. Les preuves
  sont écrites avant chaque appel IVI potentiellement bloquant.
- PowerPanel doit être fermé afin qu'une autre application ne maintienne pas
  la communication. Le prétest désactivé et l'essai réel de 10 secondes ont
  réussi le 2026-08-18.

### 4. Mesures de capacité en Ah

- `Measurement` expose `capacity_charge_ah` et `capacity_discharge_ah`.
- Les backends IVI et simulateur remplissent ces deux valeurs.
- L'acquisition les ajoute au CSV et le service les expose dans le JSON/SSE.
- Les tests vérifient leur présence dans le CSV simulé.

Un ancien CSV ne peut pas recevoir ces colonnes après coup : il faut redémarrer
le service et commencer une nouvelle acquisition.

### 5. Propriété exclusive du port du service

- Le serveur HTTP refuse maintenant de partager son port avec une autre instance.
- Un test vérifie qu'un deuxième service ne peut pas écouter la même adresse.

Cela évite qu'un ancien processus et un nouveau service semblent piloter le même
NHR simultanément.

### 6. Documentation associée

- `README.md` explique la Session 3A et les nouvelles mesures.
- `ROADMAP.md` distingue les phases A, B et C de la Session 3.
- `EXTERNAL_USE.md` montre les champs Ah disponibles pour un client externe.
- Le présent document ajoute le contrat de développement et le guide de revue.
- `IMPLEMENTATION_GUIDE.md` explique les intentions et interactions de chaque
  module, classe et fonction du projet.

### État de validation

| Contrôle | État |
|---|---|
| Tests automatisés | 44 réussis, 2 matériels ignorés — 2026-08-18 |
| Revue du diff depuis `03e1b44` | À faire par le propriétaire |
| Session 3A sur le NHR réel | Réussie sur `DC PM 1`, module 613 |
| Session 3B sur le NHR réel | Réussie à 0,5 A pendant 1 seconde |
| Session 3C sur le NHR réel | Réussie à 0,5 A, perte abrupte de 10 secondes |

Ordre de revue conseillé :

1. lire `tests/test_safety_validation.py` pour voir le comportement attendu;
2. lire `safety_validation.py`, puis le runner `session3_safety.py`;
3. vérifier les ajouts Ah de `types.py` jusqu'à `acquisition.py`;
4. lire le test d'exclusivité du port dans `tests/test_service.py`;
5. comparer le tout avec les critères de la Session 3 dans `ROADMAP.md`.

## Règles de mise à jour de ce document

- Décrire le comportement et la raison du changement, pas chaque ligne modifiée.
- Nommer les principaux fichiers afin de rendre la revue rapide.
- Séparer clairement : testé en simulation, testé en lecture seule, testé par une
  écriture non énergisante et testé en fonctionnement énergisant.
- Noter explicitement les contrôles non exécutés et le hors-scope.
- Après validation et commit, remplacer la base de comparaison et retirer les
  détails devenus historiques. Git conserve l'historique complet.
