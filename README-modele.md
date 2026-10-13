# Prototype local de prédiction des goûts musicaux

Ce document décrit le protocole du module de prédiction pour Statify / SpotifyTest. Le parcours local est implémenté : import de l’historique étendu, annotations, jeux figés, campagnes, résultats et prédictions. L’adaptateur de l’export de compte reste à finaliser après réception et inspection de ses fichiers. Aucun modèle personnel ni niveau de performance musicale n’est annoncé ; les vérifications techniques utilisent des données synthétiques. Le parcours de lancement et les limites actuelles sont décrits dans README.md.

<!-- sommaire:start -->
## Sommaire

- [Objectif et choix retenus](#objectif-et-choix-retenus)
- [État actuel et architecture cible](#état-actuel-et-architecture-cible)
- [Étape 1 — Importer et explorer les données](#étape-1--importer-et-explorer-les-données)
  - [Sources](#sources)
  - [Stockage et rapprochements](#stockage-et-rapprochements)
  - [Bilan de couverture](#bilan-de-couverture)
- [Étape 2 — Construire les avis et annotations](#étape-2--construire-les-avis-et-annotations)
  - [Avis explicites](#avis-explicites)
  - [Sélection des titres et stabilité des avis](#sélection-des-titres-et-stabilité-des-avis)
  - [Historique et signaux implicites](#historique-et-signaux-implicites)
- [Étape 3 — Préparer les jeux de données et caractéristiques](#étape-3--préparer-les-jeux-de-données-et-caractéristiques)
  - [Séparations reproductibles](#séparations-reproductibles)
  - [Caractéristiques disponibles avant l'écoute](#caractéristiques-disponibles-avant-lécoute)
- [Étape 4 — Exécuter les campagnes automatiques](#étape-4--exécuter-les-campagnes-automatiques)
  - [Modèles comparés](#modèles-comparés)
  - [Réglages depuis le site](#réglages-depuis-le-site)
  - [Grille exhaustive](#grille-exhaustive)
  - [Exécution et versionnement](#exécution-et-versionnement)
- [Étape 5 — Évaluer la polyvalence et la fiabilité](#étape-5--évaluer-la-polyvalence-et-la-fiabilité)
  - [Trois situations distinctes](#trois-situations-distinctes)
  - [Évaluations à l'aveugle](#évaluations-à-laveugle)
  - [Classement et exploration des résultats](#classement-et-exploration-des-résultats)
  - [Finalistes et test indépendant](#finalistes-et-test-indépendant)
- [Étape 6 — Intégrer les prédictions et itérer](#étape-6--intégrer-les-prédictions-et-itérer)
  - [Deuxième grande étape — Raffinement et combinaisons](#deuxième-grande-étape--raffinement-et-combinaisons)
  - [Fonctionnement local et connexion Spotify](#fonctionnement-local-et-connexion-spotify)
- [Validation et critères de réussite](#validation-et-critères-de-réussite)
  - [Vérifications techniques](#vérifications-techniques)
  - [Réussite de l'expérience](#réussite-de-lexpérience)
- [Limites et points à vérifier](#limites-et-points-à-vérifier)
- [Ordre de réalisation](#ordre-de-réalisation)
<!-- sommaire:end -->

## Objectif et choix retenus

Le prototype doit apprendre les préférences musicales d'un seul utilisateur à partir de ses évaluations exactes, en utilisant son historique Spotify et les métadonnées comme contexte. Il répartira les titres d'une playlist en quatre catégories : **je n'aime pas, neutre, j'aime, j'adore**. La confiance de la prédiction restera une information distincte.

Les trois objectifs sont de reproduire ces catégories de goût, de mesurer la fiabilité des prédictions et de comprendre les facteurs utilisés par le modèle. Des campagnes automatiques permettront de comparer les modèles et leurs réglages sans intervention pendant les essais.

- Usage personnel local, sur un ordinateur modeste, avec entraînement sur CPU.
- Import des ZIP, annotations, réglages, entraînements et comparaison des résultats depuis le site.
- Conservation de Flask, SQLite, de l'interface existante et de l'enrichissement MusicBrainz.
- Même poids pour toutes les années dans l'expérience principale ; comparaison avec les cinq dernières années.
- Goûts considérés comme relativement stables sur les cinq dernières années, comme hypothèse de départ déclarée par l'utilisateur.
- Comparaison de modèles avec et sans MusicBrainz sur les mêmes jeux de données.
- Première version sans analyse audio, recherche automatique de nouveautés ni entraînement d'un grand modèle depuis zéro.
- Fonctionnement des opérations locales lorsque la connexion Spotify expire.
- Choix manuel du modèle actif après comparaison des expériences.
- Grille exhaustive de configurations, avec trois découpages communs pour chaque configuration et un plafond en nombre de configurations, sans limite globale de durée.
- Première grande étape : grille et analyse des résultats. Raffinement adaptatif et combinaisons de modèles dans une deuxième étape distincte, après cette analyse.

Prédire « j'adore » décrit l'intensité du goût, pas simplement une forte probabilité d'apprécier le titre. La couverture des données et la confiance seront affichées séparément ; aucune probabilité ne sera présentée comme fiable avant vérification de la calibration.

## État actuel et architecture cible

| Élément | État actuel | Suite |
| --- | --- | --- |
| Application web | Flask, espace IA à quatre onglets et navigation simplifiée | Adapter l’export de compte après réception |
| Stockage | SQLite par compte, écoutes durables, snapshots, annotations, versions et essais | Snapshots issus de l’export de compte |
| Likes | Collection locale et snapshots des caches déjà chargés | Import de la bibliothèque exportée |
| Métadonnées | Normalisation, MusicBrainz confirmé, provenance et couverture | Enrichissement ciblé des lots utiles |
| Tâches longues | Workers distincts MusicBrainz et IA, reprise et file SQLite | Suivre les premières campagnes personnelles |
| Tri | Tris existants et répartition locale par goût | Évaluer l’intérêt musical sur des avis indépendants |
| Accès | Profil associé et opérations IA sans token actif ; écritures protégées | Première connexion Spotify toujours requise |
| Modèles | Référence artiste, logistique, forêt, évaluation et activation manuelle | Raffinement et combinaisons dans une deuxième étape |


Le flux cible est :

```text
Exports Spotify et collections locales
    → import et normalisation dans SQLite
    → annotations exactes + contexte historique et métadonnées MusicBrainz
    → test final réservé et trois découpages entraînement / validation
    → grille exhaustive et caractéristiques calculées sans fuite de données
    → entraînements automatiques et classement des configurations
    → sélection des finalistes et évaluation explicite sur le test final
    → activation manuelle d'une version
    → quatre colonnes de goût dans les playlists et nouvelles évaluations
```

Le module métier séparera l'import, les annotations, la préparation des caractéristiques, la construction des jeux de données, la génération des campagnes, l'entraînement, l'évaluation et la prédiction. Les routes Flask orchestreront ces services et afficheront les résultats.

## Étape 1 — Importer et explorer les données

### Sources

Importer les deux archives Spotify :

- **Données de compte** : bibliothèque sauvegardée, playlists et leurs métadonnées.
- **Historique de streaming étendu** : écoutes depuis la création du compte, durées écoutées, skips et raisons de début et de fin.

L'historique standard de l'année écoulée n'est pas nécessaire lorsque l'historique étendu couvre la même période. Les données de paiement, messages, localisation et service client ne font pas partie de l'import musical.

### Stockage et rapprochements

- Identifier les titres par URI Spotify lorsqu'elle existe. Ne pas fusionner automatiquement les titres sur leur seul nom.
- Enregistrer les écoutes séparément des caches de l'API.
- Conserver pour chaque import sa source, sa date, sa période couverte et son bilan.
- Conserver des snapshots datés de la bibliothèque et des playlists, sans écraser les états précédents.
- Réimporter une archive sans doubler les événements ; gérer les chevauchements entre exports sans supprimer des écoutes répétées légitimes.
- Signaler les entrées invalides, sans identifiant ou non rapprochées. Exclure podcasts et livres audio de l'apprentissage musical.
- Réutiliser les métadonnées locales disponibles et les correspondances MusicBrainz confirmées.

La page Données affichera le nombre d'écoutes et de titres distincts, les périodes couvertes, les likes, les playlists, les doublons évités et la couverture des métadonnées.

**Point de contrôle :** vérifier la qualité et la cohérence du bilan avant de construire les exemples d'entraînement.

### Bilan de couverture

Calculer ce bilan depuis les données importées, les annotations et les correspondances locales, sans exiger d'appels externes supplémentaires. Il doit distinguer la quantité d'écoutes, la diversité des titres et la quantité d'avis réellement disponibles.

| Dimension | Informations à afficher |
| --- | --- |
| Historique | Dates de première et dernière écoute, nombre d'écoutes et de titres distincts par année |
| Diversité | Nombre d'artistes distincts identifiés, répartition des titres et annotations par artiste et genre disponible |
| Annotations | Effectifs des quatre goûts et des abstentions, nombre de titres annotés et progression vers environ trente avis par catégorie |
| Métadonnées | Titres avec durée, date de sortie, identifiants artistes et genres exploitables ; origine des genres |
| MusicBrainz | Correspondances confirmées, ambiguës, introuvables ou en attente, séparément pour titres et artistes |
| Évaluation | Effectifs par catégorie dans entraînement, validation et test, artistes connus ou inconnus et groupes insuffisamment représentés |

Présenter chaque taux avec son numérateur et son dénominateur, par exemple « 80 titres annotés sur 120 visés » ou « 900 titres avec un genre disponible sur 2 000 titres importés ». Un genre absent doit rester dans une catégorie « inconnu », sans être ignoré dans les totaux ni inventé à partir du nom d'un artiste.

Permettre de filtrer les titres à annoter pour compléter les zones peu représentées. Ne pas conclure qu'une année sans écoute enregistrée est forcément une erreur d'import. Conserver le bilan de couverture avec chaque version des données utilisée dans une campagne ; un nouvel import ne doit pas changer rétrospectivement les chiffres d'une campagne existante.

## Étape 2 — Construire les avis et annotations

### Avis explicites

La page Évaluations proposera cinq réponses :

| Réponse | Critère personnel | Utilisation initiale |
| --- | --- | --- |
| Je n'aime pas | Le titre ne plaît vraiment pas ; l'écouter ne procure pas de plaisir. | Catégorie de goût à apprendre |
| Neutre | Le titre peut être écouté comme ça, sans enthousiasme ni rejet marqué. | Catégorie de goût à apprendre |
| J'aime | L'écoute procure un bon moment ; le titre est agréable. | Catégorie de goût à apprendre |
| J'adore | Excellent titre qui fait vibrer, avec une musique et une construction remarquables, et des paroles particulièrement appréciées lorsqu'il y en a. | Catégorie de goût à apprendre |
| Je ne sais pas | L'utilisateur ne peut pas encore donner un avis exploitable. | Abstention, exclue de l'apprentissage |

Les annotations exactes seront les seules réponses d'apprentissage de la première version. Viser progressivement environ **trente titres par catégorie**, soit 120 avis répartis entre les quatre goûts : c'est un objectif de collecte, pas un prérequis pour commencer les suggestions et les premières annotations. L'abstention « je ne sais pas » ne compte pas comme un avis neutre.

Les titres likés et la présence dans **« Que des bons sons »** sont des indices de « j'aime », pas des marqueurs automatiques de « j'adore ». La présence dans plusieurs playlists renforce l'indice de préférence et permet de rechercher des candidats « j'aime beaucoup », à vérifier par annotation. Ces indices guident la sélection des titres ; ils ne remplacent pas les annotations exactes par des catégories supposées.

### Sélection des titres et stabilité des avis

Le réservoir de titres à annoter doit couvrir tout l'historique disponible et mélanger les genres, artistes, favoris et découvertes. Ne pas le limiter aux titres récemment diffusés, aux favoris ou à un petit nombre d'artistes. La cible d'environ 120 avis n'implique pas d'annoter tous les titres importés.

Diversifier les propositions selon les artistes, genres disponibles et années, tout en gardant visibles les groupes sans métadonnées. Lorsqu'une catégorie de goût est peu représentée, recueillir davantage d'avis réels ; ne pas attribuer une note pour satisfaire un quota. Conserver le mode de sélection et la composition des ensembles pour interpréter les résultats, notamment la différence entre un ensemble équilibré et une playlist de découverte ordinaire.

Pour retrouver des candidats appréciés, compter le nombre de **playlists distinctes** contenant chaque titre. Un doublon dans une même playlist compte une seule fois. Utiliser toutes les playlists disponibles par défaut, en permettant d'en exclure certaines ou d'en sélectionner un sous-ensemble ; conserver cette sélection avec les propositions.

- Titre liké ou présent dans « Que des bons sons » : indice de « j'aime ».
- Présence dans des playlists supplémentaires : indice renforcé, croissant avec le nombre de playlists distinctes.
- À partir de **trois playlists distinctes** : candidat particulièrement intéressant pour rechercher les titres très appréciés ; ce seuil initial est réglable.
- Peu ou pas de playlists : préférence encore à vérifier, sans conversion automatique en « je n'aime pas ».

Commencer par de petits lots variés proposés par ces règles simples, sans exiger un modèle de goût déjà entraîné. Avec les premières annotations exploitables, entraîner un modèle exploratoire puis mélanger suggestions incertaines, candidats appréciés ou rejetés et tirages variés. Masquer la prédiction jusqu'à l'avis de l'utilisateur et ne jamais réinjecter une prédiction comme une annotation réelle. Les catégories insuffisamment représentées restent signalées.

Conserver un ensemble d'évaluation indépendant des suggestions guidées par le modèle. Les nouvelles notes alimentent les prochaines versions de données ; elles ne modifient pas une campagne déjà figée. La boucle de collecte précède la grosse grille de comparaison et sert à enrichir progressivement ses données.

L'utilisateur considère ses goûts relativement stables sur les cinq dernières années. Retenir cette hypothèse pour la première version, sans imposer de sessions de renotation ni de pondération récente. Conserver néanmoins la date des annotations et permettre leur correction. Une correction servira aux prochaines versions des données, sans modifier les réponses figées d'une campagne déjà lancée.

### Historique et signaux implicites

L'historique et les métadonnées serviront à construire les caractéristiques et le contexte du profil, en respectant la séparation des jeux de données. Les likes, non-likes et skips ne seront pas automatiquement transformés en réponses parmi les quatre catégories dans cette première version.

La fréquence d'écoute ne sera pas assimilée directement à l'intensité de préférence ou de rejet : l'algorithme Spotify influence aussi les titres entendus. Les skips isolés peuvent refléter une interruption ou une humeur passagère.

Les anciennes variantes d'apprentissage binaire et de négatifs supposés ne font plus partie de la première version. Un entraînement sera possible lorsque les annotations permettent de représenter les quatre catégories dans les ensembles nécessaires ; les insuffisances seront signalées plutôt que compensées par des avis inventés.

## Étape 3 — Préparer les jeux de données et caractéristiques

### Séparations reproductibles

- Créer des ensembles persistants d'entraînement, de validation et de test.
- Réserver une fois 20 % des titres annotés pour le test final, avec représentation des quatre catégories.
- Sur les 80 % restants, préparer trois séparations reproductibles : 75 % pour entraîner et 25 % pour valider. Chaque séparation correspond globalement à 60 % / 20 % / 20 %.
- Permettre de réserver manuellement des titres, notamment des likes et favoris forts.
- Garder les écoutes d'un même titre dans un seul ensemble. Regrouper également les versions identifiées comme équivalentes pour éviter des tests artificiellement faciles.
- Utiliser la validation pour choisir les réglages ; consulter le test final après ces choix. Un ensemble utilisé pour guider les réglages ne reste pas un test indépendant.
- Garder les mêmes ensembles pour les expériences comparables et figer la version des données et métadonnées.
- Utiliser les trois mêmes découpages pour toutes les configurations d'une comparaison ; figer leurs graines avant le lancement.
- Les trois découpages réutilisent les avis disponibles pour mesurer la stabilité. Ils ne créent pas de nouveaux avis indépendants, et les premières mesures sur 120 annotations resteront exploratoires.

### Caractéristiques disponibles avant l'écoute

Utiliser l'identité des artistes, leurs genres et tags confirmés, ainsi que les métadonnées disponibles telles que la durée et la date de sortie. Conserver l'origine des genres et signaler ceux attribués à l'artiste plutôt qu'au morceau.

Calculer l'affinité avec les artistes et genres uniquement à partir des exemples d'entraînement. Exclure la contribution du titre cible lors de la construction de ses caractéristiques d'entraînement.

**Les propres écoutes, likes, annotations et appartenances aux playlists du titre cible ne seront pas des caractéristiques pour prédire une découverte.** Seule son annotation exacte définit la catégorie à apprendre ; les informations réservées ne doivent pas révéler la réponse au modèle.

Les transformations, imputations et encodages seront ajustés sur l'entraînement uniquement. L'absence de métadonnées restera explicite ; l'entraînement n'attendra pas un enrichissement complet de tous les titres.

## Étape 4 — Exécuter les campagnes automatiques

### Modèles comparés

- Référence simple fondée sur l'affinité avec les artistes.
- Régression logistique multiclasse régularisée.
- Forêt aléatoire.
- Versions avec et sans MusicBrainz, sur les mêmes ensembles.

Ajouter scikit-learn pour cette chaîne d'apprentissage. La cible initiale est directement la classification entre les quatre goûts annotés.

### Réglages depuis le site

La page **Campagnes** permettra de choisir les familles de modèles, les jeux de données et annotations versionnés, les périodes, les caractéristiques avec ou sans MusicBrainz, les valeurs des paramètres propres à chaque modèle et le nombre maximal de configurations.

La variante principale utilisera tout l'historique avec un poids identique pour toutes les années. Une comparaison utilisera les cinq dernières années, sans favoriser automatiquement les titres récemment diffusés.

### Grille exhaustive

Générer toutes les combinaisons compatibles entre les choix de modèles, jeux de données, périodes, caractéristiques et paramètres. Dédupliquer les configurations identiques.

**Grille initiale proposée, modifiable depuis le site :**

| Modèle | Paramètres proposés |
| --- | --- |
| Régression logistique multiclasse | Régularisation : 0,1 / 1 / 10 ; pondération des classes : aucune / équilibrée |
| Forêt aléatoire | Arbres : 100 / 300 ; profondeur : 10 / sans limite ; minimum par feuille : 1 / 5 ; pondération : aucune / équilibrée |

Évaluer aussi la référence par affinité artiste sur les mêmes ensembles. Avant lancement, afficher le nombre exact de configurations et d'entraînements : **N configurations × trois découpages**, plus les références.

Si la grille dépasse le plafond choisi, demander de réduire la grille ou d'augmenter le plafond ; ne pas exécuter arbitrairement un sous-ensemble. Le budget porte sur le nombre de configurations, sans limite globale de durée.

### Exécution et versionnement

- Enregistrer les campagnes et essais dans SQLite, reliés aux expériences et artefacts, et les exécuter dans un worker séparé de Flask et du worker MusicBrainz.
- Une tâche d'apprentissage à la fois ; afficher son état, son avancement et les erreurs.
- Prévoir une exécution longue, notamment pendant la nuit, sur un ordinateur disposant de peu de ressources. Limiter aussi le parallélisme interne des modèles et bibliothèques numériques pour qu'un essai ne monopolise pas tous les cœurs ; rendre cette limite configurable.
- Afficher le nombre d'essais terminés et restants ainsi que les durées réellement observées. Toute estimation de fin doit rester indicative ; ni la lenteur ni le dépassement d'une nuit ne constituent une limite globale de campagne.
- Effectuer les calculs longs hors transaction SQLite.
- Conserver configuration, graine, version des caractéristiques, données figées, ensembles, métriques, durée, état, erreur éventuelle et artefact de chaque essai.
- Sauvegarder les modèles dans le répertoire de données persistant, hors Git.
- Publier un résultat seulement après une sauvegarde réussie ; un échec laisse le précédent modèle utilisable.
- Continuer après l'échec d'un essai indépendant ; signaler comme incomplète une configuration dont un découpage a échoué.
- Permettre pause, reprise et arrêt. Une pause termine l'entraînement courant puis suspend la file.
- Après redémarrage, conserver les résultats terminés et relancer les essais interrompus, sans enregistrer une expérience partiellement terminée comme réussie.
- Ne pas réentraîner automatiquement après une annotation : chaque campagne utilise une photographie figée des données.

## Étape 5 — Évaluer la polyvalence et la fiabilité

### Trois situations distinctes

| Situation | Question mesurée |
| --- | --- |
| Titres masqués de l'historique | Le modèle retrouve-t-il les préférences connues sans voir les indices propres aux titres réservés ? |
| Découvertes d'artistes connus | Fait-il mieux que la simple préférence pour l'artiste ? |
| Artistes absents de l'entraînement | Peut-il généraliser avec les métadonnées disponibles ? |

Pour le troisième test, tous les artistes crédités d'un titre doivent respecter la séparation annoncée. Les ensembles d'artistes inconnus seront construits spécifiquement, et non obtenus par un simple tirage aléatoire de titres.

### Évaluations à l'aveugle

Masquer score et explication avant la réponse dans les sessions de test. Révéler ensuite les prédictions pour comprendre les erreurs. Conserver des annotations indépendantes pour le test final ; les autres peuvent servir à l'entraînement ou à la validation.

Constituer les ensembles de référence avec des annotations exactes dans les quatre catégories. Les seuls likes réservés ne suffisent pas à distinguer tous les goûts, notamment neutre et rejeté.

### Classement et exploration des résultats

- Classer les configurations principalement par **F1 macro moyen sur les trois validations**, pour donner une importance comparable aux quatre catégories.
- Afficher la dispersion entre découpages, les résultats par catégorie, les matrices de confusion et les confusions entre goûts éloignés.
- Comparer les modèles et la référence par artiste sur les mêmes ensembles.
- Présenter les résultats par situation, taille d'échantillon et couverture des métadonnées.
- Vérifier la calibration uniquement avec suffisamment d'avis indépendants ; avant cela, afficher des scores plutôt que des pourcentages garantis.

La page **Résultats** proposera un tableau filtrable par modèle, période, caractéristiques, paramètres et version des données ; un tableau croisé avec axes sélectionnables et filtres sur les autres dimensions ; des comparaisons détaillées et un export CSV. Les durées et tailles des modèles seront visibles sans servir de limite de calcul.

Comparer ensemble uniquement les configurations évaluées sur les mêmes avis et séparations. Les campagnes utilisant des versions différentes restent consultables mais séparées.

### Finalistes et test indépendant

Après analyse de la grille, sélectionner les finalistes, les réentraîner sur les données de développement, puis déclencher explicitement leur évaluation sur le test final réservé. Ce test ne participe pas à la recherche automatique des paramètres. S'il guide de nouveaux réglages, le considérer désormais comme validation et prévoir un nouveau test indépendant pour les conclusions futures.

Une absence de gain est un résultat à conserver, pas une raison d'activer automatiquement une nouvelle version.

## Étape 6 — Intégrer les prédictions et itérer

- Ajouter une vue de prédiction dans la page Playlists avec quatre colonnes : **je n'aime pas, neutre, j'aime, j'adore**.
- Afficher la confiance sur les cartes, la couverture des données, les facteurs contributifs et la version du modèle ; permettre de laisser un titre non attribué.
- Signaler les préférences déjà connues séparément des prédictions.
- Permettre de noter les titres depuis l'interface, puis de lancer un nouvel entraînement.
- Comparer les expériences et activer manuellement une version, avec retour possible à une précédente.
- Conserver les tris existants et un état explicite lorsqu'aucun modèle n'est disponible.
- Pour les comparaisons, mesurer tous les titres et présenter couverture et abstentions séparément, afin qu'un modèle ne gagne pas simplement en refusant les cas difficiles.

Les explications décriront les indices utilisés par le modèle ; elles ne prétendront pas expliquer toute la préférence musicale de l'utilisateur.

### Deuxième grande étape — Raffinement et combinaisons

Après la grille exhaustive et l'analyse des résultats, planifier une étape distincte pour approfondir les plages prometteuses, tester une recherche adaptative et combiner les modèles utiles. Cette étape ne se lancera pas automatiquement à la fin de la première campagne. Renouveler le protocole d'évaluation si le test précédent a servi à orienter les décisions.

### Fonctionnement local et connexion Spotify

Les interfaces locales couvriront l'import, les annotations, les jeux de données, les campagnes, les essais, les résultats, l'activation et la répartition des collections enregistrées par goût. Elles utiliseront un profil local rattaché au compte et conserveront la protection des écritures.

Importer, noter, entraîner et consulter les résultats ne nécessitera pas de token Spotify actif. Actualiser des collections depuis Spotify ou créer une playlist sur Spotify conservera l'authentification actuelle. Ce changement ne rendra pas publiques les données d'autres comptes.

## Validation et critères de réussite

### Vérifications techniques

- Import identique et exports qui se chevauchent : aucune duplication artificielle, aucune perte d'écoutes répétées légitimes.
- Données manquantes, fichiers invalides et titres indisponibles : bilan explicite et traitement cohérent.
- Bibliothèque actuelle distinguée des snapshots passés ; aucune reconstitution fictive de likes puis délikes.
- Annotations et exclusions respectées dans toutes les variantes.
- Titres et artistes de test exclus des calculs d'affinité et de l'ajustement des transformations.
- Même configuration et mêmes données : résultats reproductibles.
- Nombre exact de combinaisons, paramètres compatibles et déduplication de la grille.
- Trois découpages communs, calcul des métriques sur les quatre catégories et test final exclu de la recherche automatique.
- Échecs partiels, pause, reprise, arrêt, redémarrage et respect du plafond de configurations sans limite globale de durée.
- Filtres, tableaux croisés, comparaisons et exports CSV cohérents ; versions d'évaluation différentes séparées.
- Bilan de couverture : numérateurs et dénominateurs cohérents, genres inconnus inclus, annotations distinguées des écoutes et chiffres figés par version de données.
- Métadonnées absentes ou correspondances ambiguës : couverture réduite, sans faux enrichissement confirmé.
- Token expiré : opérations locales utilisables et opérations Spotify demandant une reconnexion.
- Échec ou redémarrage du worker : état cohérent et modèle précédent disponible.
- Parcours vérifiés visuellement sur ordinateur et mobile ; absence de régression des tris existants.

### Réussite de l'expérience

Le prototype est techniquement prêt lorsque l'import, les annotations, les campagnes automatiques, la comparaison, l'activation manuelle et la répartition des titres peuvent être pilotés depuis le site de manière reproductible.

Son intérêt musical sera évalué ensuite : les modèles doivent reproduire les quatre catégories mieux que les références, sur des avis indépendants. Aucun niveau de précision n'est promis avant ces mesures. Un petit échantillon ou un résultat limité aux artistes connus doit être présenté comme tel.

## Limites et points à vérifier

- Inspecter les nouveaux exports avant de finaliser leurs adaptateurs ; les formats et champs effectivement disponibles font autorité.
- La stabilité des goûts sur cinq ans est une hypothèse personnelle de départ, pas une propriété démontrée par l'historique.
- La bibliothèque est un état à une date donnée. Elle ne permet pas nécessairement de retrouver les anciens likes puis délikes ; les prochains snapshots suivront les changements futurs.
- Les durées écoutées ne sont pas les durées totales des morceaux. Un taux d'écoute complète exige une durée de titre fiable.
- Sans audio, la généralisation dépend des artistes et métadonnées. Le modèle ne reconnaît pas directement les voix, rythmes ou productions appréciés.
- MusicBrainz apporte une couverture variable ; les résultats avec et sans enrichissement doivent rester comparables.
- Les plages de paramètres proposées sont des réglages de départ modifiables. La qualité des annotations et des comparaisons compte davantage que la seule taille de la grille.
- La disponibilité des exports ne garantit pas l'autorisation de leur entraînement. Le caractère personnel et local ne constitue pas à lui seul une exemption à la [politique développeur Spotify, section III.14](https://developer.spotify.com/policy).

## Ordre de réalisation

1. Import durable et page Données : vérifier les volumes, identifiants et périodes.
2. Suggestions simples, annotations exactes puis modèle exploratoire pour guider la collecte ; préparer le test final réservé et les trois découpages communs lorsque les avis permettent une comparaison exploitable.
3. Caractéristiques, grille exhaustive et worker : exécuter les campagnes et conserver leurs essais.
4. Résultats, tableaux croisés et évaluation des finalistes : mesurer les gains et limites sans utiliser le test final pour la recherche.
5. Activation manuelle et quatre colonnes de goût dans les playlists : utiliser puis corriger le modèle.
6. Deuxième grande étape distincte : raffinement adaptatif et combinaisons de modèles après analyse de la grille.

Chaque étape aura son bilan avant d'étendre la suivante. Les modifications locales déjà présentes dans le projet seront préservées.
