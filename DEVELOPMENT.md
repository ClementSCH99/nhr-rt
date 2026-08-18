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
| Revue de l'architecture et des protections | Propriétaire du projet |
| Valeurs et validation des profils matériels | Propriétaire du projet |
| Implémentation, tests automatisés et documentation | Codex |
| Prévol et tests matériels supervisés | Ensemble |
| Acceptation finale et décision de créer le commit suivant | Propriétaire du projet |

Codex implémente une phase convenue de bout en bout et prépare les preuves de
revue. Une décision qui change l'architecture, une protection ou le
comportement du banc doit cependant être présentée au propriétaire avant d'être
intégrée. Les valeurs d'un profil matériel ne sont jamais approuvées par Codex.

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

## Changement validé — Session 4

**Base de comparaison :** `f76185f` — `chore: archive completed session results`

Cette section décrit le diff validé depuis cette base. La Session 4 a été
acceptée par le propriétaire le 2026-08-18.

### 1. Implémentation CC issue de la Session 4

- `archives/SESSION4.md` conserve l'objectif, le hors-scope, les profils,
  l'ordre de test, les incidents corrigés et l'acceptation finale.
- `ROADMAP.md` part maintenant de la Session 3 réellement terminée et résume la
  progression prévue pour le premier palier CC.
- `src/nhr9300/cc_profiles.py` valide le contrat du profil : charge/décharge,
  plafonds 5 A / 500 W / 60 s, double approbation, watchdog matériel et
  température ignorée. La qualification du courant commence après le temps de
  stabilisation explicite du profil.
- `scripts/supervised_cc_hold.py` exécute simulation, prévol ou un seul palier
  réel, produit les preuves et confirme le nettoyage après reconnexion.
- `routines.py` distingue durée, condition atteinte et condition expirée; les
  champs capacité et énergie choisissent automatiquement le compteur du mode.
- Le simulateur intègre et réinitialise séparément capacité et énergie pour
  charge/décharge.
- Cinq templates non approuvés couvrent les quatre essais réels proposés et la
  future condition de température simulée.
- `tests/test_cc_profiles.py` couvre le schéma, les conditions, le timeout, la
  frontière `SetState` et une exécution simulée complète du runner.

### État de validation

| Contrôle | État |
|---|---|
| Session 3 intégrée sur `main` | Oui — référence `f76185f` |
| Contrat Session 4 documenté | Oui — décisions utilisateur intégrées |
| Suite logicielle | 58 réussis, 2 matériels ignorés — 2026-08-18 |
| Implémentation Session 4 | Terminée, validée et renommée pour réutilisation |
| Profils CC réels | Approuvés puis conservés dans l'archive Session 4 |
| Charge 5 A, arrêt durée | PASS — rapport `20260818T200733Z` |
| Charge 5 A, arrêt capacité | PASS — rapport `20260818T201228Z` |
| Décharge 5 A, arrêt énergie | PASS après correction du signe — `20260818T201735Z` |
| Décharge 5 A, arrêt tension | PASS à 88,79989 V — `20260818T203842Z` |

Ordre de revue conseillé pour le changement final :

1. lire `archives/SESSION4.md` et les preuves archivées;
2. relire `tests/test_cc_profiles.py`, puis `src/nhr9300/cc_profiles.py`;
3. relire `scripts/supervised_cc_hold.py` et ses chemins de nettoyage;
4. copier et compléter les templates retenus avant la simulation commune.

## Règles de mise à jour de ce document

- Décrire le comportement et la raison du changement, pas chaque ligne modifiée.
- Nommer les principaux fichiers afin de rendre la revue rapide.
- Séparer clairement : testé en simulation, testé en lecture seule, testé par une
  écriture non énergisante et testé en fonctionnement énergisant.
- Noter explicitement les contrôles non exécutés et le hors-scope.
- Après validation et commit, remplacer la base de comparaison et retirer les
  détails devenus historiques. Git conserve l'historique complet.
