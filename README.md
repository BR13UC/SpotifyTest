# Statify

Statify est une application Flask locale pour explorer ses données Spotify, trier les titres de ses playlists, visualiser les liens entre artistes et genres, situer les artistes sur une carte et expérimenter un modèle local de prédiction des goûts. L'état d'avancement et les étapes prévues sont dans [docs/ROADMAP.md](docs/ROADMAP.md) ; le protocole du module de prédiction est dans [README-modele.md](README-modele.md).

<!-- sommaire:start -->
## Sommaire

- [Installation et lancement](#installation-et-lancement)
- [Interface](#interface)
- [Enrichissement MusicBrainz](#enrichissement-musicbrainz)
  - [Artistes](#artistes)
  - [Titres](#titres)
  - [File d'attente, cache et reprises](#file-dattente-cache-et-reprises)
- [Carte du monde](#carte-du-monde)
- [Laboratoire IA local](#laboratoire-ia-local)
  - [Premier parcours](#premier-parcours)
- [Stockage et architecture](#stockage-et-architecture)
- [Développement](#développement)
<!-- sommaire:end -->

## Installation et lancement

1. Créer une application dans le [Spotify Developer Dashboard](https://developer.spotify.com) et enregistrer `http://localhost:5000/callback` comme URI de redirection.
2. Si `.env` n'existe pas encore, copier `.env.example` vers `.env`. Renseigner les variables attendues :

   ```dotenv
   SPOTIPY_CLIENT_ID=<client_id>
   SPOTIPY_CLIENT_SECRET=<client_secret>
   SPOTIPY_REDIRECT_URI=http://localhost:5000/callback
   ```

3. Lancer `docker compose up --build`, puis ouvrir [http://localhost:5000](http://localhost:5000). Docker Compose démarre trois services : l'application, le processus d'enrichissement MusicBrainz et le worker IA. SQLite et les sessions sont conservés dans un volume nommé.

Pour lancer l'application directement avec Python :

```sh
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
venv/bin/gunicorn --bind 127.0.0.1:5000 --workers 1 --threads 4 main:app
venv/bin/python -m app.services.musicbrainz_worker   # second terminal
venv/bin/python -m app.services.ia_worker            # troisième terminal
```

Les données sont alors enregistrées dans `instance/`. `.env` n'est pas versionné et l'image Docker l'exclut de son contexte de construction.

## Interface

La navigation commune mène aux playlists, au graphe, à la carte, à l'avancement, au laboratoire IA et aux paramètres. Dans `/settings`, choisir le français ou l'anglais et le thème clair, sombre ou système ; ces préférences restent dans le navigateur. Les listes, les nœuds du graphe et les lieux de la carte ouvrent un panneau de détails avec les informations disponibles et leurs sources. Les liens entre vues conservent la sélection pertinente lorsque la destination peut l'afficher. L'accueil présente un aperçu des données déjà chargées localement, sans lancer de récupération Spotify supplémentaire.

Le graphe, la carte et la timeline proposent trois sources communes : Artistes suivis, Titres likés et Playlist. Le choix et la playlist sont conservés entre ces vues. Les collaborations incluent tous les artistes crédités. Le graphe artistes/genres conserve une taille d'artiste basée sur vos titres likés, quelle que soit la source ; la carte montre les origines connues et la timeline les discographies complètes des artistes sélectionnés. Dans le graphe par année, Artistes suivis affiche vos titres likés créditant un artiste suivi, Titres likés affiche toute votre bibliothèque likée et Playlist affiche les titres de la playlist, doublons compris. Les titres likés sont dédupliqués et les dates absentes sont comptées séparément.

La page Graphe affiche le réseau complet des artistes suivis, avec recherche et filtre de genre. Les informations MusicBrainz détaillées sont chargées lors de la sélection d'un artiste. Le temps de l'API, celui de l'affichage et un échantillon du temps entre images sont visibles sous les filtres pour évaluer la fluidité.

## Enrichissement MusicBrainz

Les lectures MusicBrainz ne nécessitent aucun identifiant. Statify utilise un User-Agent contenant l'URL de son dépôt et limite ses requêtes à une par seconde, tous processus confondus. L'enrichissement commence quand les artistes suivis ou une playlist sont ouverts ; il continue tant que le processus dédié tourne et reprend après un redémarrage. Une panne MusicBrainz laisse les données Spotify utilisables.

Les cas ambigus se valident dans les onglets Titres et Artistes de la page Avancement, avec les données Spotify à gauche et les candidats MusicBrainz à droite. Les choix manuels restent enregistrés et ne sont jamais remplacés automatiquement.

### Artistes

**Confirmation directe par lien Spotify.** Quand un seul artiste MusicBrainz déclare l'URL Spotify exacte de l'artiste (recherche groupée des URL déclarées), il est retenu sans autre comparaison : les homonymes, la bibliothèque et les discographies ne peuvent plus changer la décision. Seuls ce candidat et ses propres sites sont alors vérifiés. Un lien exact trouvé sur le site officiel ou la page Bandcamp associés dans MusicBrainz vaut aussi confirmation directe.

**Sites officiels injoignables.** Un site officiel qui ne répond plus (domaine disparu, connexion refusée, adresse invalide) est mis à l'écart : il ne peut pas servir de preuve et ne bloque pas l'analyse. Ce constat est conservé 7 jours, comme une recherche vide. Une panne passagère laisse l'analyse provisoire et elle est retentée ; si le site échoue encore après 24 h, il est traité comme injoignable. Un site officiel qui pointe vers une autre identité Spotify impose une validation manuelle.

**Score par indices.** Sans lien direct, les candidats sont classés par force des indices sur 100 : titres et crédits (60 points), albums/singles/EP (25), nom et alias (10), contexte supplémentaire (5). Les titres proviennent des collections déjà chargées du compte ; aucune collecte de toutes les playlists n'est lancée. La confirmation automatique exige 85 points, 15 points d'avance, deux enregistrements distincts et aucune contradiction d'identité. Les preuves et leurs sources sont consultables ; le score n'est pas une probabilité.

### Titres

Un lien MusicBrainz vers le titre Spotify exact et cohérent est prioritaire ; l'ISRC est utilisé quand il est disponible. Lorsque plusieurs enregistrements ont le même titre normalisé, un artiste crédité commun et une durée compatible (écart maximal de 15 secondes si les deux durées sont disponibles), ils sont départagés dans cet ordre : lien Spotify de l'album ou de son groupe, ISRC, nom d'album, date de sortie à la précision disponible, proximité de durée et type de sortie. Toutes les éditions contenant chaque enregistrement sont examinées et la meilleure édition concordante est retenue. Sans candidat exact compatible, le score existant reste disponible (confirmation à partir de 85 points et avec 15 points d'avance), sinon la correspondance reste à vérifier ou introuvable. Les indices et les scores sont conservés et affichés.

Les genres Spotify sont complétés par les genres MusicBrainz, dédupliqués et accompagnés de leur provenance.

### File d'attente, cache et reprises

Les correspondances sont conservées par compte Spotify dans une file SQLite, avec un cache de réponses MusicBrainz partagé.

- **File différée.** Une analyse d'artiste incomplète (catalogue indisponible, site temporairement en panne…) est retentée avec un délai croissant, jusqu'à une heure. Après 10 tentatives incomplètes, l'artiste passe dans une file différée revérifiée toutes les 24 h, pour ne pas monopoliser la limite de requêtes. La page Avancement affiche ces artistes « à revérifier plus tard ».
- **Cache.** Les réponses HTTP MusicBrainz et les listes d'albums Spotify sont conservées 30 jours, les recherches vides 7 jours. Le processus MusicBrainz supprime chaque jour les entrées de plus de 30 jours. Les données déjà rapprochées (genres, dates, lieux et MBID) restent en SQLite sans rafraîchissement automatique. Un résultat introuvable est recherché de nouveau après 7 jours lors d'une prochaine consultation.
- **Reprises.** Au démarrage, le processus réévalue une fois les anciens artistes et titres ambigus issus d'une version antérieure du calcul, sans changer les validations manuelles ni les correspondances confirmées.
- **Suivi.** La page `/progress` sépare l'avancement des titres et des artistes : analyse terminée, tâches dans la file, file différée et correspondances à vérifier. L'onglet Artistes indique aussi la couverture des lieux d'origine. Chaque onglet affiche le taux de tentatives échouées (erreurs temporaires comprises) ; les correspondances introuvables ne sont pas comptées comme des erreurs techniques.

Le cache ne libère pas d'espace disque tout seul après une purge. Pour récupérer cet espace ponctuellement, arrêter les services puis lancer `VACUUM` :

```sh
docker compose stop
docker compose run --rm --no-deps statify python -c "import sqlite3; sqlite3.connect('/data/statify.sqlite3').execute('VACUUM')"
docker compose up -d
```

## Carte du monde

La page `/map` situe les artistes dont la correspondance MusicBrainz est confirmée. Les villes liées à Wikidata sont géolocalisées par le processus d'enrichissement ; si la ville ne peut pas l'être, un repère au niveau du pays est utilisé uniquement lorsque le pays du lieu d'origine est confirmé. Le pays actuel de l'artiste ne sert pas à situer sa ville d'origine. Un point placé au niveau du pays reste explicitement approximatif.

Les coordonnées des lieux sont conservées dans SQLite et les artistes sans lieu exploitable sont comptés séparément. Après 30 jours, les lieux sans ville précise sont réexaminés en arrière-plan ; les repères de pays confirmés restent visibles pendant ce traitement. La carte des frontières Natural Earth 1:110m et Leaflet sont servis localement, sans fond de carte externe.

## Laboratoire IA local

Le menu **IA** ouvre `/ia` et ses onglets Données, Évaluations, Campagnes et Résultats. Le titre Statify ramène à l'accueil ; l'avancement MusicBrainz est accessible dans Paramètres. Les opérations IA et la répartition des collections enregistrées restent utilisables après expiration du token Spotify, dans la session du compte déjà associé. Une déconnexion explicite termine cet accès ; aucune sélection publique de compte n'est proposée. Le protocole détaillé est décrit dans [README-modele.md](README-modele.md).

Le worker IA (`ia-worker` sous Docker, `python -m app.services.ia_worker` en local) importe les ZIP, entraîne les modèles et évalue les finalistes. Un verrou limite son exécution à un processus par répertoire de données. Les modèles restent dans `DATA_DIR/ia/models`, hors Git. Les copies ZIP téléversées sont temporaires, privées, supprimées après succès ; une copie ayant échoué reste disponible pour une reprise. L'archive d'origine n'est jamais modifiée. Limites : 64 Mo téléversés, 1 Go décompressé, 64 Mo par fichier et 2 000 entrées.

### Premier parcours

1. Dans **Données**, importer le ZIP d'historique étendu audio. Le bilan distingue événements ajoutés, déjà présents, invalides, sans URI et hors musique. Réimporter ou importer des exports chevauchants conserve la multiplicité réelle des événements.
2. Enregistrer les collections déjà chargées pour obtenir des snapshots de bibliothèque et playlists sans rafraîchissement Spotify. L'adaptateur de l'export de compte sera ajouté après inspection de ses fichiers ; un ZIP de compte n'est pas interprété comme un historique étendu.
3. Dans **Évaluations**, donner des avis de développement et collecter aussi des **lots d'évaluation indépendants**. La session affiche une carte à la fois : ← Je n'aime pas, ↓ Neutre, ↑ J'aime, → J'adore et Espace pour lecture/pause. Après chaque avis sauvegardé, le titre suivant apparaît ; les lots de 12 se renouvellent jusqu'à épuisement. Précédent permet de corriger, Reprendre revient au premier titre restant. Les anciennes abstentions sont conservées et peuvent être corrigées ; elles restent exclues de l'apprentissage. Le premier titre reste en pause, les suivants tentent une lecture automatique avec le lecteur Spotify compact (80 px). Chaque lot proposé est enrichi automatiquement en arrière-plan, sans bloquer les avis. Avec un token Spotify actif, les métadonnées manquantes sont récupérées et jusqu'à 12 anciens titres notés sont complétés à chaque lot, sans sélection par goût. Si Spotify est expiré ou indisponible, MusicBrainz reste en file et la notation continue. Le modèle exploratoire utilise uniquement les avis de développement et guide les suggestions incertaines, sans afficher ses prédictions avant la note.
4. Figer les données. Chaque goût nécessite au minimum quatre groupes de développement et suffisamment de groupes indépendants pour réserver environ 20 % au test (arrondi par catégorie). Les groupes équivalents ne traversent pas les séparations ; les contradictions d'avis entre versions sont signalées. Trois graines fixes sont communes aux configurations.
5. Prévisualiser puis lancer la grille. Les valeurs de paramètres sont des listes JSON modifiables. La grille initiale comporte 88 configurations apprenantes et 4 références, soit 264 entraînements et 12 essais de référence ; le protocole supplémentaire d'artistes exclus, lorsqu'il est réalisable, ajoute autant d'essais.
6. Comparer les trois validations, matrices, résultats par catégorie et protocoles. Si les crédits artistes sont incomplets ou les catégories insuffisantes, le protocole d'artistes inconnus est non mesurable. Les tableaux croisés regroupent les configurations complètes d'une même version.
7. Déclencher explicitement l'évaluation d'un finaliste sur le test final, puis activer sa version. Un test consulté est marqué comme tel, y compris lors d'un nouveau gel qui réutilise ses titres. Les anciens modèles restent activables pour revenir à une version précédente.
8. Dans **Playlists**, répartir un snapshot local en quatre goûts, avec une zone non attribuée, les avis connus, les scores non calibrés et les indices du modèle. Les importations, annotations et corrections ne relancent jamais automatiquement un entraînement.

Une pause laisse finir l'essai courant. Un arrêt annule les essais encore en file ; un redémarrage du worker relance les tâches interrompues. Les échecs indépendants n'arrêtent pas les autres essais, et les configurations incomplètes ne sont pas classées comme complètes. Aucune analyse audio ni recherche adaptative n'est intégrée à cette étape.

## Stockage et architecture

L'application utilise SQLite avec des clés de cache par compte Spotify ; la clé de session, les sessions et la base persistent après redémarrage. SQLite fonctionne en mode WAL : les consultations du site continuent pendant les écritures des workers. Chaque connexion attend jusqu'à 30 secondes un verrou d'écriture ; les workers reprennent les tâches interrompues si la contention persiste.

- `app/routes/` reçoit les requêtes HTTP, `app/services/` porte la logique métier, `app/storage.py` gère SQLite et `app/spotify_auth.py` l'accès OAuth à Spotify.
- MusicBrainz : `mb_client.py` (client HTTP limité et mis en cache), `recording_matching.py` et `artist_matching.py` (rapprochement), `artist_sites.py` (sites officiels), `spotify_catalogue.py` (discographies Spotify), `mb_queue.py` (file et état par compte), `musicbrainz_worker.py` (boucle du processus).
- Les pages sont dans `app/templates/` ; chaque page charge son propre script dans `app/static/`, et `common.js` regroupe les fonctions partagées.

## Développement

```sh
venv/bin/python -m pip install -r requirements-dev.txt
venv/bin/python -m pytest -q     # tests (Node requis pour les tests de rendu JS)
venv/bin/ruff check .            # lint, configuré dans pyproject.toml
```

Les tests utilisent Spotify simulé, des archives synthétiques et une base temporaire, sans connexion aux services externes. Les tests d'entraînement IA nécessitent scikit-learn et sont ignorés avec un message explicite s'il manque. La CI GitHub Actions (`.github/workflows/ci.yml`) lance ruff et pytest à chaque push sur `main` et sur les pull requests.

Vérifications navigateur (Playwright et Chromium requis ; `STATIFY_TEST_CHROMIUM` permet de choisir un exécutable) :

- `python tests/rating_session_browser.py` vérifie la session de notation dans quatre combinaisons langue/thème/taille, avec un contrôleur Spotify simulé ;
- `python scripts/ia_ui_smoke.py` capture les onglets IA avec des données synthétiques ;
- `python scripts/ui_smoke_server.py` sert l'interface sur le port 5097 avec un compte Spotify simulé (`/__test_login`).

Le dossier `work/` sert aux essais locaux (captures, journaux, copies de base) ; il est ignoré par Git et par Docker.
