# Plan des prochaines sessions

## Point de départ

La v1 logicielle est fonctionnelle en simulation : classe typée, sécurité,
acquisition CSV, routines Python/YAML, service local et client 64 bits.
La suite automatisée compte 11 tests réussis; les 2 tests matériels sont
désactivés par défaut. Les écritures IVI n’ont pas encore été validées sur le
cycler réel.

## Session 2 — Valider la lecture réelle

Objectif : confirmer que le backend IVI lit correctement et durablement le
NHR9300 sans modifier son état.

- Exécuter le wrapper matériel en lecture seule.
- Comparer identité, capacités, état et mesures avec PowerPanel.
- Faire une acquisition prolongée à 1, 5 et 10 Hz.
- Vérifier connexion, fermeture, reconnexion et erreurs de communication.

Terminé lorsque les valeurs sont cohérentes, que le CSV est exploitable et
qu’aucun changement de `Enabled` ou d’état n’est causé par la session.

## Session 3 — Valider les primitives de sécurité

Objectif : vérifier chaque écriture séparément sur un banc supervisé, sans
encore exécuter une routine complète.

- Documenter la batterie, les limites approuvées et la procédure d’arrêt.
- Tester `disable`, `standby`, les limites et les faibles consignes.
- Confirmer le comportement réel de `SetState`, `Enabled` et `Close`.
- Caractériser le watchdog avant de l’activer par défaut.
- Ajouter des tests de régression pour chaque comportement observé.

Terminé lorsque chaque transition est prévisible et revient à `standby` puis
`disabled`, y compris après une erreur volontaire ou une perte de liaison.

## Session 4 — Premier palier CC réel

Objectif : exécuter le petit palier charge ou décharge déjà couvert en
simulation.

- Revoir et signer le profil JSON du banc.
- Lancer le wrapper énergisant avec opérateur et arrêt d’urgence.
- Comparer mesures, consignes et chronologie au comportement attendu.
- Vérifier la condition de terminaison, le CSV et le `RoutineResult`.
- Corriger les différences entre simulateur et équipement réel.

Terminé lorsque plusieurs répétitions donnent le même résultat et laissent
systématiquement le module dans un état sûr.

## Session 5 — Intégration et routines avancées

Objectif : rendre le pilote utile aux premiers outils métier.

- Valider le service avec un vrai NHR depuis un client 64 bits.
- Ajouter les routines prioritaires : CC-CV, repos et profils multi-paliers.
- Définir l’adaptateur d’interlocks CAN/BMS et la politique de données périmées.
- Tester plusieurs sessions NHR indépendantes.

La synchronisation de groupe, l’acquisition waveform haute fréquence et une
interface graphique restent hors scope tant qu’un besoin concret ne les
justifie pas.

## Règle de travail

Chaque nouvelle fonction doit d’abord passer avec le simulateur, puis en
lecture seule si applicable, puis sur le banc avec un test matériel
explicitement activé. Aucun profil exemple ne doit être considéré comme
approuvé pour une batterie réelle.
