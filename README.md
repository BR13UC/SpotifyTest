# Statify

Statify est une application Flask locale pour explorer ses données Spotify, trier les titres de ses playlists et visualiser les liens entre artistes et genres. Ce README sert aussi de plan de consolidation et d'évolution du projet.

## Installation et lancement

1. Créer une application dans le [Spotify Developer Dashboard](https://developer.spotify.com) et enregistrer `http://localhost:5000/callback` comme URI de redirection.
2. Si `.env` n'existe pas encore, copier `.env.example` vers `.env`. Renseigner les variables attendues :

   ```dotenv
   SPOTIPY_CLIENT_ID=<client_id>
   SPOTIPY_CLIENT_SECRET=<client_secret>
   SPOTIPY_REDIRECT_URI=http://localhost:5000/callback
   ```

3. Lancer `docker compose up --build`, puis ouvrir [http://localhost:5000](http://localhost:5000). Docker Compose démarre l'application et le processus d'enrichissement MusicBrainz ; SQLite et les sessions sont conservés dans un volume nommé. Aucun serveur MongoDB n'est nécessaire.

Pour lancer l'application directement avec Python : créer un environnement avec `python3 -m venv venv`, installer `venv/bin/python -m pip install -r requirements.txt`, puis exécuter `venv/bin/gunicorn --bind 127.0.0.1:5000 --workers 1 --threads 4 main:app`. Dans un second terminal, lancer `venv/bin/python -m app.services.musicbrainz_worker` pour traiter la file MusicBrainz. Les données sont alors enregistrées dans `instance/`. Pour les tests : `venv/bin/python -m pip install -r requirements-dev.txt` puis `venv/bin/python -m pytest -q`.

## Interface

La navigation commune mène aux playlists, au graphe, à la carte, à l'avancement et aux paramètres. Dans `/settings`, choisir le français ou l'anglais et le thème clair, sombre ou système ; ces préférences restent dans le navigateur. Les listes, les nœuds du graphe et les lieux de la carte ouvrent un panneau de détails avec les informations disponibles et leurs sources. Les liens entre vues conservent la sélection pertinente lorsque la destination peut l'afficher. L'accueil présente un aperçu des données déjà chargées localement, sans lancer de récupération Spotify supplémentaire.

Les lectures MusicBrainz ne nécessitent ni `MUSICBRAINZ_CLIENT_ID` ni `MUSICBRAINZ_CLIENT_SECRET`. Statify utilise un User-Agent contenant l'URL de son dépôt et limite ses requêtes à une par seconde, tous processus confondus. L'enrichissement commence quand les artistes suivis ou une playlist sont ouverts ; il continue tant que le processus dédié tourne et reprend après un redémarrage. Les cas ambigus se valident dans les onglets Titres et Artistes de la page Avancement, avec les données Spotify à gauche et les candidats MusicBrainz à droite. Une panne MusicBrainz laisse les données Spotify utilisables.

**Attention :** `.env` a été retiré de l'index Git sans effacer le fichier local. Il reste présent dans les anciens commits ; si ceux-ci contiennent de vrais identifiants, les révoquer et les remplacer. L'image Docker exclut `.env` de son contexte de construction.

## État du socle

L'application utilise SQLite avec des clés de cache par compte Spotify. Les éléments de playlist et les artistes suivis sont stockés individuellement. La base MongoDB antérieure n'est ni migrée ni supprimée : les réponses Spotify mises en cache sont recréées. La clé de session, les sessions et SQLite persistent après redémarrage.

Le code suit quatre responsabilités : `app/routes/` reçoit les requêtes HTTP, `app/services/` porte la logique des playlists et accueillera les futurs enrichissements, `app/storage.py` gère SQLite, et `app/spotify_auth.py` gère l'accès OAuth à Spotify. Les pages restent dans `app/templates/` et leurs ressources dans `app/static/`.

| Priorité | État | Suite |
| --- | --- | --- |
| P0 | `.env` hors index, session et cache par compte, contrôle OAuth et protection des POST. | Remplacer les identifiants s'ils ont été exposés dans l'historique Git. |
| P1 | Pagination, données manquantes, tri stable, création privée par URI et endpoint Spotify actuel pris en charge. | Vérifier ces parcours avec le compte Spotify réel. |
| P1 | Une seule vue de graphe annoncée, ID distincts et export GraphML en téléchargement. | Améliorer les interactions et la lisibilité lors de la refonte. |
| P2 | Premiers messages et affichages sécurisés ajoutés. | Refaire l'interface complète et ses états responsive. |

## Feuille de route

La cible immédiate est un usage personnel local, avec une structure qui pourra accueillir plusieurs comptes plus tard. Les phases 1 et 2 sont implémentées ; l'étape 3 a commencé par la page Graphe.

### 1. Socle fiable — implémenté

- Configuration et cache local isolés par compte ; protection des sessions et des requêtes POST.
- Pagination complète et normalisation des éléments de playlist, y compris les titres indisponibles, épisodes et fichiers locaux.
- Tri stable et création d'une playlist privée avec les URI Spotify exactes et les [endpoints actuels](https://developer.spotify.com/documentation/web-api/reference/create-playlist).
- Graphe artistes–genres cohérent, ID artistes distincts et export GraphML direct.
- Dépendances déclarées dans `requirements.txt`, tests ciblés, lancement Docker Compose et commandes Python locales documentées.

**Critères vérifiés par tests simulés :** deux comptes ne partagent aucune réponse mise en cache ; une playlist de plusieurs pages est complète ; le tri reste utilisable avec des entrées indisponibles ; la playlist créée contient les bons morceaux dans le bon ordre ; le graphe distingue les homonymes et son export s'ouvre correctement. Le parcours réel Spotify reste à vérifier.

### 2. Enrichissement MusicBrainz — implémenté

- Ajouter un service distinct pour appeler [l'API MusicBrainz](https://musicbrainz.org/doc/MusicBrainz_API), avec identification de l'application, cache, temporisation et respect de sa [limite de requêtes](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting).
- Rapprocher les enregistrements par ISRC quand il est disponible. Pour les artistes, utiliser les noms et autres métadonnées comme candidats, conserver le MBID retenu et laisser les correspondances ambiguës non confirmées.
- Conserver pour chaque donnée enrichie sa source, son identifiant externe et son statut de correspondance. Prévoir les informations sur l'artiste, l'enregistrement, les dates, les genres et les lieux lorsqu'elles existent.
- Compléter les genres Spotify par les [genres MusicBrainz](https://musicbrainz.org/doc/MusicBrainz_API), dédupliquer les libellés normalisés et garder leur provenance. Ne pas attribuer à un titre un genre déduit d'un autre artiste sans l'indiquer.

**Critères de réussite :** un ISRC connu peut être rapproché ; deux artistes homonymes ne sont pas confondus ; l'absence de résultat ou une panne MusicBrainz n'empêche pas l'affichage des données Spotify ; les genres et lieux affichés indiquent leur origine. La couverture réelle est mesurée avant de promettre une carte complète.

L'application conserve les correspondances par compte Spotify, une file SQLite et un cache de réponses MusicBrainz partagé. La confirmation automatique d'un artiste utilise d'abord la recherche groupée des URL Spotify déclarées dans MusicBrainz : un lien unique présent parmi les candidats suffit même si plusieurs artistes ont le même nom. Sans lien unique, un nom exact unique et au moins deux titres d'albums distincts concordants permettent une confirmation ; les autres candidats restent à valider. Pour les titres, un lien MusicBrainz vers le titre Spotify exact et cohérent est prioritaire. Sinon, le rapprochement suit le titre et l'artiste uniques, puis la présence de l'enregistrement sur un album, un EP ou une contribution créditée, puis un score sur 100 (confirmation à partir de 85 points et avec 15 points d'avance). Le lien d'un album Spotify soutient la vérification de la sortie sans confirmer seul un enregistrement. La méthode, les indices et les scores sont conservés et affichés ; les compteurs distinguent les titres des autres correspondances.

Les réponses HTTP MusicBrainz et les listes d'albums Spotify positives sont conservées 30 jours, les recherches vides 7 jours. Les données déjà rapprochées (genres, dates, lieux et MBID) restent en SQLite sans rafraîchissement automatique. Un résultat introuvable est recherché de nouveau après 7 jours lors d'une prochaine consultation. Au démarrage, le processus réévalue une fois les anciens artistes et titres ambigus ; il récupère les informations de sortie des playlists déjà en cache. Les choix manuels restent enregistrés et ne sont pas remplacés automatiquement. La page `/progress` sépare l'avancement réel des titres et des artistes déjà chargés : analyse terminée, tâches dans la file et correspondances à vérifier manuellement. L'onglet Artistes indique aussi la couverture des lieux d'origine.

### 3. Interface et visualisations

La page Graphe conserve le réseau complet des artistes suivis, avec recherche et filtre de genre. Les informations MusicBrainz détaillées sont chargées lors de la sélection d'un artiste ; les nouveautés sont appliquées au réseau sans recréer la vue. Le temps de l'API, celui de l'affichage et un échantillon du temps entre images pendant l'interaction sont visibles sous les filtres pour évaluer la fluidité. La refonte des autres pages reste à faire.

- Refaire la navigation et les pages avec une interface responsive, facile à parcourir et proche des usages Spotify, centrée sur les écoutes, les artistes et les titres.
- Harmoniser la langue, les composants, les états de chargement, les états vides et les messages d'erreur. Afficher les textes venant des API comme du texte, sans insertion HTML non contrôlée.
- Stabiliser le graphe 2D : libellés, filtres, légende, interactions et comportement avec beaucoup de nœuds. **Aucun graphe 3D n'est prévu pour cette étape.**

**Critères de réussite :** les parcours principaux restent compréhensibles sur ordinateur et mobile ; le graphe est utilisable avec un jeu de données réduit comme avec un jeu plus dense ; les erreurs d'API sont expliquées dans l'interface.

### 4. Carte du monde — implémentée

- La page `/map` situe les artistes suivis dont la correspondance MusicBrainz est confirmée. Les villes liées à Wikidata sont géolocalisées par le processus d'enrichissement ; si la ville ne peut pas l'être, un repère au niveau du pays est utilisé uniquement lorsque le pays du lieu d'origine est confirmé. Le pays actuel de l'artiste ne sert pas à situer sa ville d'origine.
- Les coordonnées des lieux sont conservées dans SQLite et les artistes sans lieu exploitable sont comptés séparément. Après 30 jours, les lieux sans ville précise sont réexaminés par le processus en arrière-plan ; les repères de pays confirmés restent visibles pendant ce traitement. La carte des frontières Natural Earth 1:110m et Leaflet sont servis localement, sans fond de carte externe.

**Critères de réussite :** la carte indique la précision de chaque repère et la provenance des lieux. Un point placé au niveau du pays reste explicitement approximatif.

## Vérification prévue

Les tests du socle couvrent la pagination, les comptes distincts, les titres indisponibles, le tri, la création de playlist et l'export du graphe. Ils utilisent Spotify simulé et une base temporaire ; aucun contenu de `.env` ou de MongoDB n'a été lu pendant l'audit et les tests. Ceux de l'enrichissement devront couvrir les homonymes, les correspondances ambiguës, les données absentes, les limites de requêtes et une panne de MusicBrainz. La refonte sera vérifiée visuellement sur écran large et mobile, avec des états de chargement, d'erreur et sans données.
