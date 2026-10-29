# Le Portionneur

Une interface centrée sur une tâche : obtenir le poids d’une portion cuite à partir d’une cible calorique.

1. Indiquer les calories du plat entier, ou choisir une recette.
2. Peser le plat après cuisson. Choisir explicitement nourriture seule/balance déjà tarée, ou plat avec son contenant.
3. Indiquer les calories souhaitées. La portion, les calories après arrondi et le reste sont affichés ensemble.

Exemple : 2 000 kcal pour 1 500 g nets cuits donnent une portion de **450 g pour 600 kcal**. Avec un contenant de 1 000 g, la balance doit afficher 2 500 g pour obtenir le même résultat.

## Recettes et sauvegardes

Les calories des ingrédients peuvent être saisies pour 100 g ou pour la quantité utilisée. Les fractions sont conservées jusqu’au résultat final. Les recettes existantes restent compatibles : les calories sont recalculées depuis leurs ingrédients lorsqu’ils sont présents.

La connexion existante à `ProjetPoids_DB` est conservée via le secret Streamlit `gcp_service_account`. Seules les rubriques `recettes` et `plats` sont utilisées et modifiées. Les anciens journaux, plannings, ingrédients et poids ne sont pas supprimés ; leurs écrans sont retirés de ce parcours simplifié.

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
python -m unittest discover -s . -p test_portionneur.py -v
```

28 tests couvrent notamment les deux régressions observées sur la V18 (cible négative et portion dépassant le plat), les valeurs nulles/non finies, la tare, l’arrondi, les virgules décimales dans les fonctions de calcul, la création puis réutilisation d’une recette, les doublons, les changements de formulaire et les sauvegardes échouées. Le test de propriétés exécute 2 000 calculs déterministes supplémentaires.

Les tests Streamlit utilisent une session locale et les tests de sauvegarde simulent Google Sheets. Les écritures sur la vraie base et le déploiement Streamlit restent à valider avant mise en service. L’ancienne version en ligne a été testée dans Chrome ; la prévisualisation locale dans Chrome a été bloquée par les restrictions réseau de l’environnement de travail.
