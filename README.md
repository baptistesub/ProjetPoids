# Le Portionneur

Un outil pour prévoir ses repas, calculer ses portions et suivre les apports réellement consommés sur la journée.

1. Indiquer les calories du plat entier, ou choisir une recette.
2. Peser le plat après cuisson. Choisir explicitement nourriture seule/balance déjà tarée, ou plat avec son contenant.
3. Indiquer les calories souhaitées. La portion, les calories après arrondi et le reste sont affichés ensemble.

Exemple : 2 000 kcal pour 1 500 g nets cuits donnent une portion de **450 g pour 600 kcal**. Avec un contenant de 1 000 g, la balance doit afficher 2 500 g pour obtenir le même résultat.

## Recettes et sauvegardes

Les calories des ingrédients peuvent être saisies pour 100 g ou pour la quantité utilisée. Les fractions sont conservées jusqu’au résultat final. Les recettes existantes restent compatibles : les calories sont recalculées depuis leurs ingrédients lorsqu’ils sont présents.

La connexion existante à `ProjetPoids_DB` est conservée. Les rubriques `recettes`, `plats`, `garde_manger`, `planning` et `journal` sont utilisées. L’ancien planning hebdomadaire est préservé : les nouveaux réglages et programmes datés occupent une clé distincte du même onglet. Les anciennes pesées ne sont pas modifiées.

Sans secret configuré, le mode local fonctionne sans compte. Ses données sont temporaires : l’interface l’indique et propose un export/import JSON. Avec une connexion configurée mais défaillante, le chargement s’arrête sans écrire de données par défaut. Une sauvegarde distante échouée ne fait pas apparaître un succès.

L’import fusionne les recettes et contenants, avec validation et confirmation avant remplacement des noms existants. Chaque rubrique distante est écrite séparément : un échec au milieu de l’import peut laisser une importation partielle, signalée à l’utilisateur. La détection des modifications entre sessions réduit les écrasements, mais Google Sheets ne fournit pas ici de transaction atomique ; éviter les modifications simultanées dans plusieurs sessions.

## Exécution

```sh
python -m pip install -r requirements.txt
python -m streamlit run mon_app.py
```

L’application n’appelle aucun moteur d’IA ni base alimentaire externe. Les calories viennent des valeurs renseignées. Un plat hétérogène ou des étiquettes imprécises limitent la précision nutritionnelle ; le poids conseillé est arrondi au gramme, avec les calories correspondantes affichées.

## Vérification

```sh
python -m unittest discover -s . -p 'test_*.py' -v
```

60 tests couvrent notamment les deux régressions observées sur la V18 (cible négative et portion dépassant le plat), les valeurs nulles/non finies, la tare, l’arrondi, les virgules décimales dans les fonctions de calcul, la création puis réutilisation d’une recette, les doublons, les changements de formulaire et les sauvegardes échouées. Le test de propriétés exécute 2 000 calculs déterministes supplémentaires.

Les tests Streamlit utilisent une session locale et les tests de sauvegarde simulent Google Sheets. Les parcours de planning, consommation et annulation ont également été vérifiés dans Chrome sur le déploiement Streamlit. La prévisualisation locale dans Chrome avait été bloquée par les restrictions réseau de l’environnement de travail.


## Évolution nutrition et saisie rapide

Les cours fournis par l’utilisateur mettent en avant la complémentarité des macronutriments et l’adaptation aux pratiques sportives. Cette version affiche protéines, glucides, lipides et fibres pour le poids effectivement conseillé. Une valeur absente reste inconnue, y compris lorsqu’un seul ingrédient de la recette n’est pas renseigné. Les plages ambiguës ou divergentes des supports ne sont pas utilisées comme formules. L’estimation et les objectifs journaliers restent un choix explicite de l’utilisateur.

La bibliothèque existante `garde_manger` est réutilisée, y compris ses anciens enregistrements contenant seulement les calories. Les nouvelles fiches indiquent les valeurs pour 100 g et une source facultative (étiquette ou Ciqual). Le nom doit préciser l’état cru/cuit/égoutté correspondant au poids saisi. Les recettes gardent une copie des valeurs de création : modifier une fiche ne réécrit pas leur historique.

Le parcours rapide est : choisir un ingrédient mémorisé, saisir les grammes, ajouter. Un collage multiligne accepte `150 g Riz cru` ou `Riz cru;150`, avec virgule décimale. Les noms inconnus ou ambigus sont signalés ; aucune valeur n’est inventée et aucune ligne n’est ajoutée si la liste contient une erreur. Les quantités peuvent être corrigées avec recalcul proportionnel. La saisie libre calorique reste accessible et ne prétend pas fournir des macros inconnues.

Les portions peuvent aussi être calculées depuis une quantité choisie de protéines ou de glucides, uniquement si ce nutriment est renseigné pour toute la recette. Ce mode n’est pas une recommandation de quantité. L’export/import inclut désormais la bibliothèque.

Validation : 39 tests passent, incluant les 28 tests précédents et 11 tests des nouveaux parcours, macros partielles, collage, mémorisation et ajustement de quantité. Le test de propriétés conserve ses 2 000 calculs déterministes.

Référence composition : Anses, table Ciqual, https://ciqual.anses.fr/. Les étiquettes des produits effectivement utilisés restent à vérifier ; les macronutriments ne servent pas à remplacer arbitrairement l’énergie déclarée (les fibres et d’autres composants contribuent aussi à l’énergie).

## Ma journée : trois repas et une collation facultative

La première page distingue strictement le programme prévu du journal réellement consommé. Prévoir un repas n’ajoute aucune consommation. L’utilisateur indique la quantité réellement mangée puis valide. Les totaux et les restes sont recalculés depuis ces consommations seulement.

Les repères proposés sont modifiables. Ils constituent un point de départ discuté, pas une mesure des besoins ni une garantie de perte de poids. Sans collation, la répartition indicative se redistribue entre les trois repas. Cela ne modifie ni les repas déjà saisis ni les objectifs quotidiens.

La journée type propose une organisation et des repères par repas. L’utilisateur choisit ses recettes et indique le poids net cuit de chaque plat entier ; les portions sont calculées d’après la répartition calorique, puis les macros réellement obtenues sont affichées. Ce n’est pas une optimisation garantissant tous les objectifs nutritionnels. Un nutriment incomplet rend son total et son reste indéterminés, pas égaux à zéro.

Les repas prévus et mangés utilisent une copie des valeurs nutritionnelles au moment de la saisie : une modification ultérieure de la recette ne change pas les anciennes journées. Un double clic sur un repas ne le compte pas deux fois. L’annulation et la restauration sont possibles. Une restauration qui créerait un double comptage est bloquée. Les consommations futures sont désactivées.

La date du jour suit Europe/Paris. Les données se sauvegardent dans les onglets existants, avec le contrôle de conflit déjà présent. Les écritures Google Sheets restent non transactionnelles : éviter les modifications simultanées de plusieurs sessions. L’export/import inclut désormais les programmes, les repères et le journal.

Validation : les 56 tests comprennent les 39 précédents et 17 tests du planning et du journal. Ils couvrent les trois/quatre repas, la conservation exacte des totaux répartis, les très petites valeurs, les quantités réellement consommées, les doubles clics, l’annulation, la restauration, les dates futures, les jours séparés, les données nutritionnelles manquantes, les sauvegardes et la conservation des anciens programmes. En ligne, un repas prévu à 360 kcal est resté hors du consommé ; une moitié consommée a compté 180 kcal et laissé 2 220 kcal sur le repère de 2 400. Cet essai a été annulé et retiré du programme.

L’émulation de l’écran de téléphone n’a pas permis de confirmer les dimensions attendues sur l’application ; le rendu mobile reste à valider sur un appareil réel. L’affichage ordinateur et les parcours décrits ont été vérifiés. La fonction photo de code-barres reste une évolution locale distincte et n’est pas incluse dans cette publication.

## Objectif calorique et macros liées

L’objectif enregistré est visible dès le début de Ma journée. Dans les réglages, l’utilisateur saisit ses calories ou demande une estimation adulte depuis son profil (Black : constantes homme 1,083 / femme 0,963, poids en kg, taille en m, âge en années, conversion MJ en kcal par 1000/4,1855). Le maintien est le repos multiplié par le niveau d’activité choisi. Une réduction de 0 à 20 % est un choix ajustable, sans rythme de perte garanti ; 0 % correspond au maintien. Aucun objectif de perte n’est proposé pour un IMC inférieur à 18,5. Les champs du profil restent en session et ne sont pas enregistrés dans la base ni dans le dépôt.

Référence : [AFDN, besoins et apports nutritionnels](https://www.afdn.org/sites/www.afdn.org/files/documentation/2021-11/Besoins%20et%20apports%20nutritionnels.pdf). L’équation fournit une estimation, pas une mesure individuelle.

En automatique, les pourcentages de départ sont 20 % protéines, 35 % lipides, le complément en glucides (45 %). Les grammes sont recalculés dès le changement des calories ou des pourcentages : énergie × part / 4 pour protéines et glucides, / 9 pour lipides. Les fibres restent un objectif séparé. Cette répartition énergétique sert aux objectifs, sans remplacer les calories des étiquettes des aliments. La somme protéines + lipides doit rester inférieure à 100 %. Le mode manuel autorise des grammes indépendants et signale une incohérence énergétique importante.

L’aperçu n’est appliqué qu’après Enregistrer mes repères. L’enregistrement actualise les objectifs et les répartitions indicatives des nouveaux repas ; les quantités et valeurs des repas déjà prévus ou mangés restent intactes. Le mode et les pourcentages sont conservés dans les sauvegardes. Atteindre ces macros ne garantit pas tous les apports en micronutriments.

Validation : 60 tests passent, dont quatre nouveaux tests sur la conservation de l’énergie, la formule de Black, le choix manuel/automatique, l’aperçu avant enregistrement et l’application d’une estimation sans conservation du profil. Les anciens tests d’interface ciblent désormais les libellés, indépendamment de l’ordre des boutons de choix.
