# Session 5 — Routines avancées et outil supervisé

## Objectif et statut

La Session 5 doit livrer un outil utilisable pour CCCV, puissance constante,
repos, séquences et profils CSV, tout en conservant les protections validées
des Sessions 3 et 4.

Au 2026-08-19, l'implémentation et les tests logiciels sont terminés. Le
second essai CCCV charge réel a atteint le cutoff et a terminé avec un
nettoyage sûr. La session reste ouverte pour les autres routines.

## Contrat fonctionnel

| Routine | Canaux requis | Fins acceptées | Preuve principale |
|---|---|---|---|
| CC | courant + limite tension; puissance optionnelle | tension, durée, Ah ou Wh | CSV et `RoutineResult` |
| CCCV | courant + tension + cutoff current; puissance optionnelle | cutoff current ou timeout | CSV et `RoutineResult` |
| Puissance constante | puissance + limite tension; courant optionnel | tension, durée, Ah ou Wh | CSV et `RoutineResult` |
| Repos | `STANDBY`, puis sortie désactivée | durée | CSV inactif |
| Séquence | routines indépendantes ordonnées | premier échec ou fin | CSV global et CSV par étape |
| CSV dynamique | courant ou puissance signés | condition optionnelle ou fin CSV | CSV consigne/mesure |

`cutoff_current_a` est toujours une magnitude positive en charge et en décharge.
Pour éviter un faux arrêt pendant la montée initiale du courant, ce critère
reste verrouillé jusqu'au franchissement de la tension CV dans le bon sens.
Les arrêts Ah/Wh sont relatifs. Les compteurs bruts signés du NHR ne sont pas
réécrits; le bilan de séquence est une donnée dérivée et directionnelle.

Les limites de sécurité globales du NHR restent toujours actives. Les trois
booléens `*_limit_enabled` sélectionnent uniquement les canaux opérationnels de
l'étape. Le validateur refuse une étape sans limite et vérifie les canaux requis
par son mode de régulation.

## Invariants de sécurité

- Toutes les commandes passent par `NHR9300`; le runner ne touche jamais le
  backend directement.
- Un profil matériel exige deux approbations nommées, l'identité attendue, un
  acquittement d'environnement et le watchdog.
- Le prévol programme et relit les limites sans armement ni consigne active.
- Chaque activation exige mesure fraîche, interlocks frais et bail d'armement.
- Un arrêt, une exception, une condition expirée ou un défaut d'acquisition
  empêche l'étape suivante et déclenche le nettoyage.
- La fin exige consignes/canaux à zéro, `Enabled=False`, watchdog désactivé et
  confirmation par reconnexion indépendante.
- Le rapport identifie chaque CSV dynamique par chemin, taille et SHA-256 et
  refuse un fichier qui change pendant son chargement.

## Validation logicielle exécutée

Commande :

```powershell
.\.venv32\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --basetemp=.test-temp-final-2
```

Résultat avant la revue utilisateur : **73 tests réussis, 2 tests matériels
ignorés**. La nouvelle validation complète est consignée dans
`DEVELOPMENT.md`.

Après intégration des commentaires de revue : **74 tests réussis, 2 tests
matériels ignorés**. Les nouveaux tests vérifient notamment les canaux de limite
optionnels, les règles propres à chaque routine, le nom `cutoff_current`, le CSV
global et l'extraction exacte des CSV d'étape.

Après le premier passage CCCV réussi : **76 tests réussis, 2 tests matériels
ignorés**. Deux régressions vérifient que le cutoff est ignoré avant l'entrée
en CV, en charge comme en décharge.

Avant le dernier essai dynamique : **77 tests réussis, 2 tests matériels
ignorés**. La nouvelle régression vérifie que le rapport lie les octets exacts
d'un CSV dynamique à son SHA-256.

Après suppression du passage OFF imposé aux changements de signe : **78 tests
réussis, 2 tests matériels ignorés**. La régression dédiée vérifie qu'une
transition active charge → décharge ne commande aucun `disable()` intermédiaire.

La couverture Session 5 inclut :

- CCCV charge et décharge avec transition simulée vers CV et arrêt courant;
- puissance constante charge/décharge et arrêts durée, tension, Ah et Wh;
- repos mesuré avec sortie désactivée;
- charge → repos → décharge, un CSV global, trois CSV dérivés et bilans Ah/Wh;
- profil CSV courant signé charge → décharge → zéro;
- rejet des limites/profils invalides;
- runner complet simulé, rapport JSON, nettoyage et reconnexion.

Cette preuve valide la logique Python et le simulateur. Elle ne valide pas la
sélection réelle des canaux IVI, la précision CP/CCCV, les transitoires du banc
ou les compteurs NHR lors d'une séquence réelle.

## Revue du premier profil CCCV

Le profil local dédié reprend les valeurs proposées : charge 5 A, tension
89,20 V, cutoff current 4,5 A, timeout 6 s, limite de puissance opérationnelle
désactivée et limites de sécurité globales inchangées. Le schéma passe la
validation matérielle statique et le numéro de série `79503` correspond aux
profils Session 4 archivés.

La simulation exacte du 2026-08-19 s'est arrêtée proprement au timeout : 61
échantillons à 10 Hz, aucune erreur d'acquisition, 89,1004 V et 4,98 A à la fin.
Le cutoff 4,5 A n'a donc pas été atteint. Le CSV global, le CSV de l'étape, le
nettoyage et la reconnexion sûre ont été produits correctement. Ce résultat ne
prédit pas la dynamique électrique réelle du module, mais interdit de présenter
le profil comme validé avant de décider si le timeout de 6 s est volontaire ou
doit être augmenté.

Après approbation utilisateur, le timeout a été porté à 59 s. La simulation
exacte a atteint le cutoff à 4,443 A et a passé. Le prévol matériel
`20260819T155029Z` a ensuite confirmé série `79503`, état initial/final `OFF`,
`Enabled=False`, watchdog désactivé et aucune différence de limites.

Le passage réel `20260819T155058Z` a terminé **FAIL critère** au timeout :

- 592 échantillons actifs; 594 globaux à 10,0005 Hz;
- tension de 88,9731 à 89,2233 V, maximum 89,2243 V;
- franchissement de 89,20 V vers 34,53 s à 5,006 A;
- courant terminal 4,7676 A, supérieur au cutoff 4,50 A;
- 0,08169 Ah et 7,276 Wh chargés dans la fenêtre enregistrée;
- aucune erreur d'acquisition; 46 overruns rapportés;
- nettoyage et reconnexion : `OFF`, `Enabled=False`, watchdog désactivé.

Ce résultat démontre que les canaux courant+tension produisent bien une
transition CV et que le cutoff/timeout est appliqué correctement. Il ne valide
pas encore le profil puisque le cutoff approuvé n'a pas été atteint. Toute
répétition exige une nouvelle valeur de cutoff, tension ou durée explicitement
revue et approuvée.

Le profil approuvé a ensuite été porté à 119 s sans modifier la consigne ni le
cutoff. Le passage réel `20260819T160245Z` a terminé **PASS** : entrée en CV vers
14,14 s, cutoff direct à 4,494 A vers 38,9 s, 0,0529 Ah et 4,708 Wh chargés.
Les 393 échantillons ont été acquis à environ 10 Hz sans erreur. Le nettoyage
et la reconnexion ont confirmé `OFF`, `Enabled=False` et watchdog désactivé.

La revue de ce CSV a montré qu'un échantillon de montée pouvait être inférieur
au cutoff avant l'entrée en CV. Le verrouillage logiciel décrit plus haut a donc
été ajouté; sa prochaine validation matérielle sera combinée au premier essai
de séquence.

## Première séquence réelle

Le profil approuvé `session5_sequence_cccv_rest_cp.local.json`, SHA-256
`7e09aadac040b701675b57386240417ce96eafc9a1f99a392163e7bb5f400bfe`, a
été exécuté le 2026-08-19. La simulation initiale a exposé une comparaison
flottante trop stricte : le simulateur convergeait à 89,1999999999997 V et
n'activait jamais un verrou exact à 89,20 V. Une tolérance d'activation dédiée
de 1 µV a été ajoutée; elle est négligeable devant la résolution matérielle.
La simulation corrigée, le prévol et la séquence réelle ont ensuite passé.

Le rapport réel `20260819T162627Z` confirme :

- CCCV PASS : le premier échantillon actif était sous le cutoff, mais l'arrêt
  est resté verrouillé jusqu'au franchissement de 89,20 V; cutoff direct à
  4,391 A;
- repos PASS : 10 s avec uniquement l'état `OFF` dans le CSV d'étape;
- décharge CP PASS : moyenne stabilisée -400,02 W, courant maximal 4,517 A et
  arrêt tension à 88,7997 V;
- 751 échantillons globaux à 9,998 Hz, aucune erreur d'acquisition;
- un CSV global et trois CSV d'étape avec respectivement 194, 101 et 456
  lignes;
- bilans dérivés : 0,02607 Ah / 2,321 Wh en charge et 0,05629 Ah / 4,993 Wh
  en décharge;
- nettoyage et reconnexion : `OFF`, `Enabled=False`, watchdog désactivé.

## Validation CP capacité et énergie

Le profil approuvé `session5_cp_charge_capacity_discharge_energy.local.json`,
SHA-256 `5d6e818270c0a6616a1aea13c5600f820aee635fb974050345d907aacc730d2f`,
a passé la simulation, le prévol et le test réel `20260819T163723Z` :

- charge CP : moyenne stabilisée +400,041 W et arrêt relatif à 0,020016 Ah;
- repos : 5 s avec uniquement l'état `OFF` dans le CSV;
- décharge CP : moyenne stabilisée -400,043 W et arrêt relatif à
  2,01157 Wh;
- courant absolu maximal stabilisé : 4,516 A;
- 403 échantillons globaux à 9,995 Hz, aucune erreur d'acquisition;
- CSV global et trois CSV d'étape de 165, 51 et 187 lignes;
- nettoyage et reconnexion : `OFF`, `Enabled=False`, watchdog désactivé.

Avec l'essai CP tension précédent, les deux sens et les arrêts tension,
capacité et énergie sont maintenant couverts sur le matériel. La durée reste
le timeout intrinsèque de chaque étape et est couverte par les essais de repos
et les tests logiciels CP.

## Validation des profils CSV dynamiques

La copie approuvée désormais conservée dans l'archive Session 5, initialement
nommée `session5_dynamic_final_sequence.example.json`, avait le SHA-256
`bb28b16a596d4c99e21cd3171d64b0ab06972f8fc5cfbdde20b6ae819ca6fde6`,
a référencé deux CSV explicitement hashés dans le rapport :

- courant unidirectionnel :
  `e906aeef375ef8d873fb21670f406ebc538bfe89c1bdb1d29a02a9f3b1de75b7`;
- puissance bidirectionnelle :
  `1fb6ebd365ee528ea91a188543ed7f4429dd480c885ef7af4828a4a937b53b9c`.

La simulation, le prévol et le test réel initial `20260819T164823Z` ont passé :

- courant stabilisé aux paliers 1, 2, 3,5, 5, 3 et 1 A en charge;
- puissance stabilisée à environ +150, +300, +400, -150, -300 et -400 W;
- passages par zéro observés initialement en état `OFF` et repos de 10 s;
- fins des deux routines par `profile_end`;
- 659 échantillons globaux à 10,020 Hz, aucune erreur d'acquisition;
- CSV global et trois CSV d'étape de 253, 102 et 304 lignes;
- bilans dérivés : 0,02587 Ah / 2,307 Wh en charge et 0,00994 Ah /
  0,883 Wh en décharge;
- nettoyage et reconnexion : `OFF`, `Enabled=False`, consignes à zéro et
  watchdog désactivé.

Le bruit des contacteurs observé aux points zéro a conduit à modifier ce
comportement. Un zéro interne conserve maintenant le dernier mode actif et
programme uniquement la consigne primaire à 0 A ou 0 W. Un zéro avant toute
activation reste OFF; une étape `rest` et la fin de routine continuent à
désactiver la sortie.

La simulation, le prévol et la répétition réelle `20260819T174723Z` ont passé.
Les trois segments zéro sont restés respectivement en `charge`, `charge` et
`discharge`, sans échantillon OFF/standby. Les consignes relues étaient bien
0 A ou 0 W. La mesure ne tombe toutefois pas instantanément à zéro lorsque les
contacteurs restent fermés : environ +0,526 A après 2 s, +0,193 A et -0,181 A
après 3 s dans cet essai. Le repos séparé est resté entièrement OFF. Le
nettoyage et la reconnexion ont confirmé `OFF`, `Enabled=False`, consignes à
zéro et watchdog désactivé.

Le claquement encore entendu au changement de signe provenait d'un `disable()`
explicitement commandé par le runner avant chaque transition charge/décharge.
Cette coupure intermédiaire a été retirée : le runner conserve le même bail
d'armement et commande directement le nouvel état actif avec ses limites
directionnelles, puis rafraîchit les références de capacité/énergie. Une
simulation exacte du profil approuvé a terminé PASS dans
`dynamic-direct-sign-simulation/20260819T175456Z`; le CSV montre les segments
zéro en mode actif puis la transition `charge` → `discharge`, sans segment
OFF/standby.

Le passage matériel `dynamic-direct-sign-hardware-final/20260819T175949Z` a
ensuite terminé PASS : 657 échantillons globaux à 9,996 Hz, aucune erreur
d'acquisition, trois étapes réussies et aucun état OFF/standby entre les
segments actifs du profil bidirectionnel. Le journal passe de `charge, 0 W` à
`discharge, 150 W` en environ 0,13 s et atteint environ -150 W en 0,6 s. La
reconnexion indépendante confirme `OFF`, `Enabled=False`, consignes à zéro et
watchdog désactivé. L'opérateur confirme qu'aucun claquement de contacteur n'a
été entendu au changement de signe.

La consigne active zéro n'est toutefois pas un zéro électrique précis. Sur la
dernière seconde des trois paliers, les moyennes étaient environ +0,449 A /
+40,03 W en courant-charge, +0,471 A / +41,93 W en puissance-charge et
-0,214 A / -19,19 W en puissance-décharge. Aucune plage minimale documentée
n'a été trouvée dans le pilote local; ces valeurs sont donc conservées comme
limitation mesurée du mode actif à consigne nulle, sans compensation empirique.
Utiliser `rest` lorsqu'un vrai zéro avec isolation est requis. Les étapes
`rest`, la fin normale et tout nettoyage d'erreur continuent à désactiver la
sortie.

## Ordre des validations réelles

Chaque passage doit suivre : profil local revu → simulation exacte → prévol
commun → un seul essai actif → revue CSV/rapport/état sûr → autorisation de
continuer.

1. CCCV charge courte, arrêt `cutoff_current`.
2. CCCV décharge courte, même critère positif.
3. CP charge puis CP décharge, d'abord par durée.
4. CP avec arrêts tension, capacité et énergie.
5. Repos seul, puis charge CCCV → repos → décharge CP.
6. CSV unidirectionnel à faible amplitude.
7. CSV bidirectionnel incluant zéro, après revue des transitions précédentes.

Pour chaque cas, accepter seulement si sens, consignes, limites, motif de fin,
chronologie, compteurs, absence d'erreur d'acquisition et postconditions sûres
sont cohérents. Tout écart non expliqué arrête la progression.

## Critères de fermeture

- tous les cas réels prioritaires ci-dessus sont PASS ou une exclusion est
  explicitement acceptée et justifiée;
- les preuves sont archivées avec SHA-256 et une synthèse des incidents;
- la documentation reflète exactement le comportement observé;
- la suite logicielle et `git diff --check` sont propres;
- le propriétaire accepte explicitement la Session 5.

Après fermeture, l'intégration CAN/BMS, le service de commande 64 bits,
l'interface graphique et le logging enrichi seront cadrés dans une nouvelle
roadmap. Ils ne sont pas requis pour démontrer les routines de cette session.
