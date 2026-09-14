# Consolidation opérateur avant M6

Plan accepté le 11 septembre 2026. Implémentation limitée à NHR-RT.

## État de livraison — 14 septembre 2026

Les quatre lots sont implémentés et validés en logiciel : enregistrement de
session finalisé, runner guidé, HMI et documentation/catalogue. La roadmap
conserve M6 comme prochain objectif fonctionnel. L'acceptation opérateur sur
son parcours réel reste distincte de cette livraison logicielle.

- Suite complète : **180 passed, 2 skipped** en 85,64 s, avec Python 32 bits.
  Les deux tests désactivés concernent le matériel.
- Parcours intégré testé : lancement de deux processus simulateur/monitor,
  preflight, démarrage, arrêt guidé, finalisation et surveillance maintenue.
- HMI testé dans Edge headless avec fixtures : courbes constantes/interrompues,
  valeurs absentes, interlock déclenché puis valeur revenue à la normale,
  finalisation et perte de service. Captures inspectées à 1440 et 1100 px.
- `compileall` et `git diff --check` passent ; l'entrée du runner est accessible
  depuis l'environnement 64 bits. Le digest de l'exemple simulé est vérifié.

Un premier passage a exposé la sensibilité du test de refus de renouvellement
aux écritures Windows : sa durée de stage de 0,4 s pouvait expirer avant
l'injection attendue. Sa marge de test a été augmentée ; le renouvellement reste
forcé à 0,1 s et aucune limite de sécurité du produit n'a été modifiée.
Des refus HTTP avaient aussi subi des interruptions Windows 10053 lors de
passages antérieurs ; leurs tests isolés puis le passage complet final passent.

Les tests complémentaires sont dans `tests/test_operator_consolidation.py` et
`tests/monitor_visual_qa.cjs` (Playwright optionnel, `NHR_QA_BROWSER_CHANNEL=msedge`
pour Edge). Le résultat JUnit local est conservé sous
`.test-temp-operator-evidence/final-results.xml`, hors fichiers versionnés.

Aucun essai physique, approbation de profil matériel, changement de la
configuration matérielle locale, commit ou push n'a été effectué.

## Objectif et ordre

Réduire les manipulations nécessaires pour préparer, lancer, comprendre et
terminer un test, en conservant les contrôles existants. Livrer successivement :

1. Fin de session et preuves NHR stables.
2. Runner CLI guidé et préparation des bundles.
3. HMI lisible, toujours en lecture seule.
4. Documentation et bibliothèque non approuvée.

M6 reste le prochain objectif fonctionnel (enveloppe SoP). M7 conserve la
validation intégrée et la préparation de v0.3.0. Les validations logicielles ne
valent pas acceptation physique d'un DUT, d'un profil ou d'une limite.

## Livrables

- Un fichier `session.csv` par run, indépendant des CSV de surveillance et de
  leurs rotations. Fermeture sous verrou après nettoyage ; aucune fermeture
  d'observateur ne termine les mesures partagées.
- Un état `recording` dans l'API du run, et un manifeste
  `session-evidence.json` avec chemins, tailles et SHA-256 des fichiers stables.
  Une erreur de fermeture ou un état sûr non vérifié interdit `finalized=true`.
- `nhr9300-operator` : lancement explicite ou rattachement au service et au
  monitor, diagnostic, sélection, préparation, preflight, start, suivi, stop et
  récupération. CAN-PY reste lancé séparément : deux terminaux usuels.
- Préparation : validation du bundle, digest et aperçu des changements avant
  application. Aucune approbation automatique ni limite inventée ; redémarrage
  explicite après modification. Chaque lancement conserve son acquittement.
- Diagnostic startup : exploiter les informations exposées ; ne pas confondre
  sortie désactivée et arrêt d'urgence. Sans signal qualifié, indiquer inconnu.
- HMI : valeurs/unités/règles/marges des interlocks, valeur de déclenchement
  séparée de la valeur courante, étapes explicites et trois courbes graduées
  V/I/P alignées dans le temps. Afficher les preuves de la dernière session.
- Guide opérateur, contrat CAN-PY et catalogue de profils non approuvés ;
  docstrings ciblées sur les responsabilités et transitions.

## Validation et acceptation

Tester fin normale, stop répété, échec, fermeture impossible, observateur détaché
et deuxième run. Les fichiers finalisés ne doivent plus grossir pendant que la
surveillance continue. Tester refus de preflight, dérive de bundle, conflit de
port, rattachement au mauvais service, perte de réponse start et récupération
avec la même clé d'idempotence. Vérifier visuellement le HMI et ses données
manquantes, périmées, constantes ou interrompues.

Le parcours simulé doit pouvoir être effectué sans commandes Python à mémoriser.
L'acceptation manuelle opérateur et les essais physiques restent à réaliser
séparément après les tests automatisés. Aucun commit/push ni essai physique
n'est inclus dans cette implémentation.

## Dépendances et limites

CAN-PY doit adopter le [contrat de preuves finalisées](SESSION_EVIDENCE.md) pour
automatiser son arrêt et son merge. Cette modification n'est pas livrée ici.
La cause du merge historique reste non établie : les sources de ce merge et
les traces du test n'étaient pas présentes dans le checkout CAN-PY inspecté.

Le backend IVI actuel ne publie pas de contact emergency-stop/inhibit qualifié.
Le diagnostic explicite cette absence ; il ne prétend pas détecter le contact.

Voir [le guide du runner](OPERATOR_RUNNER.md) et
[le catalogue non approuvé](../examples/workflows/README.md).
