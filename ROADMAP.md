# Plan des prochaines sessions

## Point de départ

La v1 logicielle est fonctionnelle en simulation : classe typée, sécurité,
acquisition CSV, routines Python/YAML, service local et client 64 bits.
La suite automatisée couvre la simulation, le service et le client; les tests
matériels sont désactivés par défaut. Les Sessions 3A, 3B et 3C ont validé sur
le cycler réel les écritures non énergisantes, une transition à faible consigne
et le comportement du watchdog après perte abrupte. La prochaine frontière est
l'exécution complète et répétable d'une routine CC réelle.

## Session 2 — Valider la lecture réelle

Objectif : confirmer que le backend IVI lit correctement et durablement le
NHR9300, puis écrit les mesures dans des CSV exploitables, sans modifier
l’état du cycler.

- Exécuter le wrapper matériel en lecture seule.
- Comparer identité, capacités, état et mesures avec PowerPanel.
- Faire une acquisition prolongée à 1, 5 et 10 Hz.
- Vérifier connexion, fermeture, reconnexion et erreurs de communication.

Le runner `scripts/session2_readonly.py` automatise ces étapes et génère un
rapport JSON de cadence et d’invariance d’état. L’écriture mentionnée ici
désigne uniquement l’écriture locale des fichiers CSV; les commandes envoyées
au NHR restent hors scope jusqu’à la session 3.

Terminé lorsque les valeurs sont cohérentes, que le CSV est exploitable et
qu’aucun changement de `Enabled` ou d’état n’est causé par la session.

État au 2026-07-27 : le runner, les CSV et la reconnexion sont validés sur
`DC PM 1` à 1, 5 et 10 Hz. Les mesures V/I/P sont cohérentes entre elles et
l’état est resté inchangé. Il reste à comparer visuellement les valeurs avec
PowerPanel, en particulier la température UUT observée autour de 157 °C, avant
de fermer complètement la session 2.

## Session 3 — Valider les primitives de sécurité

Objectif : vérifier chaque écriture séparément sur un banc supervisé, sans
encore exécuter une routine complète.

- Phase A : tester `disable`, les limites, `SetState(STANDBY)` avec tous les
  canaux désactivés, un `disable` final, puis `Close` et la reconnexion. Cette
  phase n’appelle jamais directement `enable`.
- Phase B : tester une faible consigne et `Enabled` avec un profil de banc
  approuvé et un opérateur présent.
- Phase C : caractériser séparément les erreurs de communication et le
  watchdog avant de l’activer par défaut.
- Ajouter des tests de régression pour chaque comportement observé.

Le runner `scripts/session3_safety.py` implémente la phase A. Il exige un profil
JSON approuvé et un acquittement opérateur distinct, journalise chaque état
avant/après et refuse de commencer si le module est déjà actif.

État au 2026-08-17 : la phase A est validée sur `DC PM 1` (module 613).
Le NHR réel passe de `OFF`/`Enabled=False` à
`STANDBY`/`Enabled=True` lors de `SetState(STANDBY)`, même avec tous les canaux
désactivés. Le `disable` final le ramène à `OFF`/`Enabled=False`; fermeture,
reconnexion et lecture indépendante confirment cet état sûr.
Les limites 1 A, 100 W et 75–100 V, ainsi que leurs délais de 0,1 s, ont été
relues par les getters IVI et correspondent au profil approuvé. La limite de
température reste hors validation tant que le profil contient `null`.

État logiciel de la phase B : le runner à faible consigne et ses protections
sont implémentés sur la branche `feat/session3-supervised-safety-validation`. Le
simulateur reproduit le fait que `SetState` peut activer l’entrée, sans attendre
un appel distinct à `enable()`. Le passage sur matériel réel est maintenant
validé avec le profil de banc approuvé.

État matériel au 2026-08-17 : la phase B est validée sur `DC PM 1`, module
613, avec une décharge de 0,5 A pendant 1 seconde. Le delta de courant observé
est de -0,572 A pour -0,500 A attendu; la tension est restée entre 89,108 et
89,113 V. Le nettoyage et une reconnexion indépendante confirment `OFF`,
`Enabled=False`, consignes à zéro et tous les canaux désactivés. L’acquisition
a terminé sans erreur à environ 10 Hz, avec 14 overruns sur 19 échantillons.

État de la phase C au 2026-08-18 : le runner sépare la préparation et la
récupération dans des processus bornés, conserve les preuves avant la coupure
et propose un prétest non énergisant. Avec PowerPanel fermé, ce prétest puis
une décharge de 0,5 A ont réussi. Après une perte abrupte de 10 secondes, la
reconnexion a observé `OFF`, `Enabled=False`, consignes à zéro et watchdog
revenu à `false`. Un arrêt propre avec PowerPanel ouvert n'avait pas déclenché
la protection après 1 seconde; ce scénario ne représentait pas une perte de
communication exclusive.

Terminé lorsque chaque transition connectée est prévisible et revient à
`standby` puis `disabled`. Une perte de liaison doit être traitée séparément :
après la coupure, le logiciel ne peut plus envoyer ces commandes et dépend du
comportement réel du watchdog.

## Session 4 — Premier palier CC réel

**Terminée et acceptée le 2026-08-18.**

Objectif : exécuter un palier CC court avec un profil approuvé, produire une
preuve cohérente entre le CSV, le motif de fin et le résultat de routine, puis
vérifier l'état sûr par une reconnexion indépendante.

- Faire approuver le contrat d'architecture et le profil réel par le
  propriétaire du projet.
- Implémenter un runner opérateur dédié, ses protections, son rapport JSON et
  ses tests simulés; conserver le test matériel `pytest` comme régression.
- Exécuter ensemble un prévol non énergisant, puis un seul palier actif.
- Comparer limites, mesures, consignes, motif de fin, chronologie, CSV et
  `RoutineResult`.
- Vérifier le nettoyage et une reconnexion indépendante avant toute répétition.
- Répéter deux fois sans modifier le profil après revue du premier résultat.

La Session 4 couvre charge et décharge. Les profils approuvés demandent 5 A et
500 W, avec des arrêts par durée, tension, capacité et énergie, dans des
limites de sécurité de 10 A et 1000 W. Le profil tension peut durer au plus
60 secondes; les trois autres profils restent limités à 12 secondes.
La température UUT non câblée est ignorée sur le matériel; sa future condition
d'arrêt est couverte uniquement en simulation. Le watchdog est obligatoire
pendant le palier matériel et vérifié désactivé après nettoyage.

Les quatre cas réels approuvés — durée, capacité, énergie et tension — ont
passé avec un état sûr vérifié par reconnexion. Le contrat, les corrections et
les résultats sont archivés dans
[archives/SESSION4.md](archives/SESSION4.md); les preuves brutes sont dans
`archives/session4-validated-20260818.zip`.

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
