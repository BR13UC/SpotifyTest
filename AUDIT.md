# Audit du projet — 13/10/2026

Constats à traiter, classés par catégorie puis par gravité : **Critique** (faille ou casse), **Important** (maintenabilité, qualité, performance), **Mineur** (finition).

État général : projet sain pour un usage personnel. 179 tests verts, séparation routes / services / stockage claire, SQLite en WAL, protection CSRF, cookies HttpOnly, conteneur non-root, dépendances épinglées.

Priorités : révoquer l'ancien secret Spotify, ignorer `work/`, purger le cache HTTP MusicBrainz.

## Sécurité

- **Critique — ancien secret Spotify dans un dépôt public.** Le commit `5fa6ca9` (branche distante `not_working`, dépôt GitHub public) contient un `.env` avec un identifiant et un secret client. Ce secret diffère de l'actuel, mais reste utilisable si l'ancienne application Spotify existe encore.
  *Correctif :* régénérer le secret de cette application dans le Spotify Developer Dashboard, ou la supprimer. Réécrire l'historique Git est facultatif une fois le secret révoqué.

## Architecture et structure

- **Important — `app/services/musicbrainz.py` (928 lignes) a trop de responsabilités.** Il regroupe le client HTTP (limite de débit, cache), la file de tâches, l'appariement des titres, le catalogue Spotify, les compteurs de progression et les validations manuelles.
  *Correctif :* séparer en `mb_client.py`, `mb_queue.py` et `recording_matching.py`, sur le modèle de `artist_matching.py`.
- **Important — `app/static/script.js` (1 181 lignes) sert plusieurs pages.** `map.js`, `timeline.js` et `progress.js` sont déjà découpés par page.
  *Correctif :* un fichier par page.
- **Mineur — imports circulaires contournés.** `artist_matching` et `musicbrainz` s'importent mutuellement à l'intérieur des fonctions. Le découpage ci-dessus le résoudrait.

## Qualité du code

- **Important — `browse` est fragile** (`app/services/artist_matching.py`, l. 56-58). `previous` est lu avant d'être défini ; cela ne fonctionne que parce que `offset` vaut 0 au premier tour (ruff F821).
  *Correctif :* initialiser `previous = set()` avant la boucle.
- **Mineur — style hétérogène.** ruff relève 79 instructions multiples sur une ligne (`;`, `if x: y`) et aucune configuration de lint ou de formatage n'existe.
  *Correctif :* configurer ruff dans un `pyproject.toml`.
- **Mineur — imports inutilisés dans les tests.** Deux dans `tests/test_storage_concurrency.py`, plus des imports mal placés dans `tests/rating_session_browser.py`.

## Dépendances

- **Important — le venv local ne reflète pas `requirements.txt`.** Il contient des restes de l'époque MongoDB (`pymongo`, `SQLAlchemy`, `sqlmodel`, `pydantic`) que le code n'utilise pas.
  *Correctif :* recréer le venv depuis `requirements-dev.txt`.
- **Mineur — mises à jour mineures disponibles.** Flask 3.0.2 → 3.1.3, python-dotenv, networkx. Rien de bloquant.

## Tests et outillage

- **Important — aucune intégration continue.** Les tests ne tournent que lancés à la main.
  *Correctif :* un workflow GitHub Actions avec `pytest` et `ruff check`.
- **Mineur — dépendance implicite à scikit-learn.** Sans elle, `tests/test_ia.py` échoue par erreur d'import.
  *Correctif :* `pytest.importorskip("sklearn")` pour un message explicite.

## Hygiène du dépôt

- **Important — `work/` pèse 991 Mo et n'est pas ignoré.** On y trouve des captures, des logs, des scripts de vérification ponctuels, une copie SQLite et un venv. Un `git add .` l'ajouterait entièrement.
  *Correctif :* ajouter `work/` au `.gitignore`, déplacer les scripts utiles vers `tests/` ou `scripts/` et supprimer le reste.
- **Mineur — fichiers résiduels.** `README-modele.md` n'est pas suivi par Git. Le volume Docker garde trois sauvegardes SQLite du 25/09/2026 (~350 Mo).

## Documentation

- **Mineur — README long et incomplet.** Il mélange guide d'utilisation et feuille de route, et ne décrit ni la file différée, ni la confirmation directe par lien Spotify, ni la mise à l'écart des sites officiels injoignables.
- **Mineur — pas de `CLAUDE.md` de projet.** Il manque les commandes de test, les conventions FR/EN et les commandes Docker.

## Performance

- **Important — le cache HTTP MusicBrainz n'est jamais purgé.** `mb_http_cache` occupe 489 Mo et grossit sans limite ; un provisioning complet dépasserait 2,5 Go.
  *Correctif :* purge périodique dans le worker (entrées plus anciennes que `CACHE_AGE`), puis un `VACUUM` ponctuel.
- **Important — ~2,7 requêtes MusicBrainz par titre**, soit environ 20 h pour 26 000 titres.
  - Vérifié : l'endpoint ISRC refuse tout enrichissement (`releases`, `genres` : HTTP 400).
  - Piste à vérifier : `recording/{id}?inc=genres+releases+release-groups` pourrait remplacer à la fois `_details` et `_release_context`, soit une requête de moins par titre confirmé.
- **Mineur — `status_for` prend 145 ms et est appelé toutes les 8 s.** Il décode le JSON de tous les artistes confirmés pour compter les origines connues.
  *Correctif :* `json_extract` en SQL ou compteur tenu à jour.
- **Mineur — `library_tracks` prend 87 ms par artiste**, car il relit toute la bibliothèque (~500 ms prévus à 26 000 titres). Ne concerne plus que les ~5 % d'artistes sans lien Spotify direct.

## Non vérifié

- Vulnérabilités connues des dépendances (`pip-audit` non installé).
- Code IA (`ia_*.py`, `ia.js`), seulement parcouru.
- Rendu de l'interface et responsive.
- Validité actuelle de l'ancien secret Spotify (ni affiché ni testé).
