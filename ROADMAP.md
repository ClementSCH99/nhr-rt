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

Objectif recentré : livrer un outil supervisé couvrant la majorité des
routines de cyclage nécessaires avant la prochaine phase d'expansion.

- Charge et décharge CCCV avec arrêt par magnitude de courant.
- Charge et décharge à puissance constante avec arrêt par tension, durée,
  capacité ou énergie.
- Repos mesuré et sortie désactivée.
- Séquences de routines indépendantes, arrêt au premier échec, acquisition CSV
  globale, CSV dérivé par étape et bilans directionnels Ah/Wh.
- Profils CSV signés de courant ou puissance, incluant charge, décharge et
  zéro, avec limites opérationnelles explicites.
- Runner unique avec simulation, prévol, acquittement matériel, rapport JSON,
  CSV par étape, nettoyage et reconnexion indépendante.

État au 2026-08-19 : implémentation et validation logicielle terminées. La
suite compte 78 tests réussis et 2 tests matériels ignorés. Le second CCCV
charge réel du 2026-08-19 a terminé PASS à un cutoff de 4,494 A, avec nettoyage
et reconnexion sûre. Le cutoff est maintenant verrouillé jusqu'à l'entrée en
CV; sa prochaine validation réelle sera combinée à une séquence CCCV, repos et
décharge CP. Le contrat, la matrice de validation et l'ordre des essais sont
dans [SESSION5.md](SESSION5.md).

Cette séquence combinée a ensuite terminé PASS : verrouillage CCCV observé,
repos OFF pendant 10 s, décharge stable à -400 W jusqu'à 88,80 V, CSV global et
trois CSV d'étape, puis état OFF confirmé par reconnexion. Les prochains essais
doivent compléter les critères CP restants et les profils CSV dynamiques.

Les critères CP capacité et énergie ont ensuite passé dans une séquence courte
à +400 W puis -400 W. Avec l'arrêt tension précédent, la validation matérielle
CP prioritaire est complète. Le prochain palier combine un CSV unidirectionnel,
un repos et un CSV bidirectionnel.

Ce dernier palier a terminé PASS avec un profil courant unidirectionnel, un
repos de 10 s et un profil puissance bidirectionnel. Les fins CSV, les passages
par zéro, les changements de sens, le CSV global, les CSV par étape et le retour
sûr ont été observés. Les routines prioritaires de Session 5 sont donc
fonctionnellement validées; il reste le nettoyage, l'archivage des preuves et la
revue finale avant fermeture formelle.

Après retour opérateur, les zéros CSV ont été modifiés pour conserver le mode
actif et programmer seulement 0 A ou 0 W. La répétition matérielle a confirmé
l'absence de transition OFF dans les CSV, puis le retour sûr final. Une étape
`rest` reste la commande explicite pour ouvrir la sortie entre deux profils.

Le passage OFF auparavant imposé à chaque changement de signe a également été
retiré. Les transitions actives charge/décharge sont maintenant directes; les
tests logiciels, le profil complet simulé et la répétition réelle passent. Le
CSV réel confirme l'absence d'état OFF/standby durant la transition et
l'opérateur confirme l'absence de claquement. Le zéro actif conserve cependant
un résidu mesuré asymétrique, jusqu'à environ +0,47 A / +42 W pendant cet essai;
une étape `rest` reste nécessaire lorsqu'un vrai zéro électrique est requis.

La Session 5 sera fermée après validation progressive des profils réels,
revue du logging final et nettoyage documentaire. L'adaptateur CAN/BMS, le
service de commande 64 bits et l'interface graphique deviennent les premières
extensions de la roadmap suivante plutôt que des dépendances de cette session.

La synchronisation de groupe, l’acquisition waveform haute fréquence et une
interface graphique restent hors scope tant qu’un besoin concret ne les
justifie pas.

## Règle de travail

Chaque nouvelle fonction doit d’abord passer avec le simulateur, puis en
lecture seule si applicable, puis sur le banc avec un test matériel
explicitement activé. Aucun profil exemple ne doit être considéré comme
approuvé pour une batterie réelle.
