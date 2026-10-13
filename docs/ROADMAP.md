# Statify — feuille de route

Ce document regroupe l'état d'avancement et les étapes prévues. Le guide d'utilisation est dans le [README](../README.md) ; le protocole du module de prédiction est dans [README-modele.md](../README-modele.md).

La cible immédiate est un usage personnel local, avec une structure qui pourra accueillir plusieurs comptes plus tard. Les phases 1, 2 et 4 sont implémentées ; l'étape 3 a commencé par la page Graphe.

## État du socle

| Priorité | État | Suite |
| --- | --- | --- |
| P0 | `.env` hors index, session et cache par compte, contrôle OAuth et protection des POST. | Révoquer l'ancien secret de l'application Spotify exposé sur l'ancienne branche `not_working` (supprimée). |
| P1 | Pagination, données manquantes, tri stable, création privée par URI et endpoint Spotify actuel pris en charge. | Vérifier ces parcours avec le compte Spotify réel. |
| P1 | Une seule vue de graphe annoncée, ID distincts et export GraphML en téléchargement. | Améliorer les interactions et la lisibilité lors de la refonte. |
| P2 | Premiers messages et affichages sécurisés ajoutés. | Refaire l'interface complète et ses états responsive. |

## 1. Socle fiable — implémenté

- Configuration et cache local isolés par compte ; protection des sessions et des requêtes POST.
- Pagination complète et normalisation des éléments de playlist, y compris les titres indisponibles, épisodes et fichiers locaux.
- Tri stable et création d'une playlist privée avec les URI Spotify exactes et les [endpoints actuels](https://developer.spotify.com/documentation/web-api/reference/create-playlist).
- Graphe artistes–genres cohérent, ID artistes distincts et export GraphML direct.
- Dépendances déclarées dans `requirements.txt`, tests ciblés, lancement Docker Compose et commandes Python locales documentées.

**Critères vérifiés par tests simulés :** deux comptes ne partagent aucune réponse mise en cache ; une playlist de plusieurs pages est complète ; le tri reste utilisable avec des entrées indisponibles ; la playlist créée contient les bons morceaux dans le bon ordre ; le graphe distingue les homonymes et son export s'ouvre correctement. Le parcours réel Spotify reste à vérifier.

## 2. Enrichissement MusicBrainz — implémenté

- Service distinct pour appeler [l'API MusicBrainz](https://musicbrainz.org/doc/MusicBrainz_API), avec identification de l'application, cache, temporisation et respect de sa [limite de requêtes](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting).
- Rapprochement des enregistrements par ISRC quand il est disponible. Pour les artistes, noms et autres métadonnées comme candidats, MBID retenu conservé et correspondances ambiguës laissées non confirmées.
- Pour chaque donnée enrichie : source, identifiant externe et statut de correspondance. Informations sur l'artiste, l'enregistrement, les dates, les genres et les lieux lorsqu'elles existent.
- Genres Spotify complétés par les [genres MusicBrainz](https://musicbrainz.org/doc/MusicBrainz_API), libellés normalisés dédupliqués avec leur provenance. Aucun genre déduit d'un autre artiste n'est attribué à un titre sans l'indiquer.

**Critères de réussite :** un ISRC connu peut être rapproché ; deux artistes homonymes ne sont pas confondus ; l'absence de résultat ou une panne MusicBrainz n'empêche pas l'affichage des données Spotify ; les genres et lieux affichés indiquent leur origine. La couverture réelle est mesurée avant de promettre une carte complète.

## 3. Interface et visualisations — en cours

La page Graphe conserve le réseau complet des artistes suivis, avec recherche et filtre de genre. Les informations MusicBrainz détaillées sont chargées lors de la sélection d'un artiste ; les nouveautés sont appliquées au réseau sans recréer la vue. Le temps de l'API, celui de l'affichage et un échantillon du temps entre images pendant l'interaction sont visibles sous les filtres pour évaluer la fluidité. La refonte des autres pages reste à faire.

- Refaire la navigation et les pages avec une interface responsive, facile à parcourir et proche des usages Spotify, centrée sur les écoutes, les artistes et les titres.
- Harmoniser la langue, les composants, les états de chargement, les états vides et les messages d'erreur. Afficher les textes venant des API comme du texte, sans insertion HTML non contrôlée.
- Stabiliser le graphe 2D : libellés, filtres, légende, interactions et comportement avec beaucoup de nœuds. **Aucun graphe 3D n'est prévu pour cette étape.**

**Critères de réussite :** les parcours principaux restent compréhensibles sur ordinateur et mobile ; le graphe est utilisable avec un jeu de données réduit comme avec un jeu plus dense ; les erreurs d'API sont expliquées dans l'interface.

## 4. Carte du monde — implémentée

**Critères de réussite :** la carte indique la précision de chaque repère et la provenance des lieux. Un point placé au niveau du pays reste explicitement approximatif.

## Vérification prévue

Les tests du socle couvrent la pagination, les comptes distincts, les titres indisponibles, le tri, la création de playlist et l'export du graphe. Ils utilisent Spotify simulé et une base temporaire. Ceux de l'enrichissement couvrent les homonymes, les correspondances ambiguës, les données absentes, les limites de requêtes et une panne de MusicBrainz. La refonte sera vérifiée visuellement sur écran large et mobile, avec des états de chargement, d'erreur et sans données.

## Pistes ouvertes

- Vulnérabilités connues des dépendances : `pip-audit` à lancer.
- Adaptateur de l'export de compte Spotify (module IA) après inspection de ses fichiers.
