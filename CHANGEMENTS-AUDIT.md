# Corrections de l'audit du 13/10/2026

Ce document reprend chaque constat d'[AUDIT.md](AUDIT.md) dans le même ordre : ce qui a été fait, comment, dans quel commit, et comment le résultat a été vérifié. Tous les commits sont sur `main` et poussés sur GitHub.

**Bilan :** 18 constats traités sur 19. Il reste une action manuelle : révoquer l'ancien secret Spotify. Les tests passent de 179 à 182, tous verts ; `ruff check` est propre et la CI GitHub Actions est verte.

| Commit | Objet |
| --- | --- |
| `49c4150` | Point de départ : travail en cours commité tel quel, `work/` ignoré |
| `587dbf7` | Scripts réutilisables déplacés de `work/` vers `scripts/` |
| `6d5eeea` | `browse` : garde de pagination initialisée |
| `a88336c` | Dépendances mises à jour, ruff ajouté |
| `4bfda8d` | Configuration ruff et instructions multiples séparées |
| `00787b8` | Tests dépendant de scikit-learn ignorés avec un message |
| `5c7e213` | Intégration continue GitHub Actions |
| `2689156` | `script.js` découpé par page |
| `2a930f3` | `musicbrainz.py` découpé en quatre modules |
| `8f2bf30` | Purge quotidienne du cache HTTP MusicBrainz |
| `fb9a08c` | Une requête MusicBrainz de moins par titre (dans la plupart des cas) |
| `094bf78` | `status_for` : origines comptées en SQL |
| `c0389c6` | `library_tracks` : préfiltre SQL |
| `08590fc` | Test IA instable corrigé (hors audit, validé avec toi) |
| `f380a19` | README refondu, feuille de route dans `docs/ROADMAP.md` |
| `d22bfd9` | `CLAUDE.md` de projet |

---

## Sécurité

### Critique : ancien secret Spotify dans un dépôt public

**Fait.**
- J'ai supprimé la branche distante `not_working` (`git push origin --delete not_working`). Elle contenait le commit `5fa6ca9` avec le `.env`.
- Ce commit n'est plus atteignable depuis aucune branche.
- J'ai aussi contrôlé l'historique de `main` : le `.env` qui y a été versionné (`ca44e30` → `415a696`) avait `SPOTIPY_CLIENT_ID` et `SPOTIPY_CLIENT_SECRET` vides. Rien à révoquer de ce côté.

**Reste à faire, manuellement.** Supprimer une branche ne suffit pas :
- GitHub garde les commits orphelins accessibles par leur SHA pendant un certain temps ;
- des clones ou forks peuvent les avoir copiés.

Il faut donc révoquer le secret :
1. Ouvrir le [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) et sélectionner l'ancienne application. Son Client ID est celui du `.env` de `5fa6ca9`.
2. *Settings* → *View client secret* → *Rotate*, ou supprimer l'application si elle ne sert plus.
3. Optionnel : demander au support GitHub la purge des vues en cache du commit.

**Vérification.**
- `git branch -a --contains 5fa6ca9` ne renvoie plus rien.
- `git log --all -- .env` ne montre que les trois commits de `main` aux valeurs vides.

---

## Architecture et structure

### Important : `musicbrainz.py` (928 lignes) a trop de responsabilités

**Fait.** Commit `2a930f3` (réalisé par un agent, revérifié ensuite). Le module est supprimé et remplacé par :

| Module | Lignes | Contenu |
| --- | --- | --- |
| `mb_client.py` | 157 | Client HTTP : limite d'une requête par seconde, cache `mb_http_cache`, `TemporaryFailure`, helpers texte (`_fold`, `_album_key`…), `_details`, `purge_cache` |
| `spotify_catalogue.py` | 82 | Client Spotify en identifiants client, albums Spotify et MusicBrainz, `album_comparison` |
| `recording_matching.py` | 313 | Recherche et départage des candidats titres et artistes, contexte des éditions |
| `mb_queue.py` | 425 | File de tâches, traitement, compteurs, validations manuelles, `status_for`, `progress_for` |

**Comment.**
- Le code a été déplacé tel quel. Seul change le préfixe de module sur les appels entre modules.
- Un script AST vérifie que les 63 définitions de l'ancien module existent toutes dans les nouveaux.
- Il n'y a pas de module de compatibilité : chaque importeur pointe directement vers le bon module. C'est le cas des routes, des workers, de `geography.py`, `playlist_data.py`, `ia_worker.py` et des tests.
- Les modules s'importent entre eux comme modules (`mb_client.request(...)`) et non par nom. Un `monkeypatch.setattr(mb_client, "request", ...)` dans les tests atteint donc tous les appelants. Les tests ont été mis à jour pour patcher le module qui définit chaque fonction.
- Le point d'entrée `python -m app.services.musicbrainz_worker`, utilisé par Docker, est inchangé.

**Vérification.**
- Les 179 tests passent.
- `ruff check` est propre.
- Le worker démarré réellement sur une base vide tourne sans erreur.

### Important : `script.js` (1 181 lignes) sert plusieurs pages

**Fait.** Commit `2689156` (réalisé par un agent, revérifié ensuite). `script.js` est remplacé par trois fichiers :

| Fichier | Lignes | Contenu |
| --- | --- | --- |
| `common.js` | 287 | Fonctions partagées (`fetchJson`, `postJson`, `renderMatch`, `MusicBrainzPanel`, `loadProfile`…), chargé par `base.html` à la place de `script.js` |
| `sorts.js` | 189 | `SortsPage`, chargé par `sorts.html` |
| `graphs.js` | 710 | `GraphPage`, `ReleaseYearGraph`, `initGraphModes`, `familyLabel`, chargé par `graphs.html` après `vis.min.js` |

**Comment.**
- Les lignes ont été déplacées sans modification.
- L'ancien gestionnaire `DOMContentLoaded` unique devient un gestionnaire par fichier. L'ordre d'exécution est conservé : `loadProfile` passe toujours en premier, et `sorts.js` est chargé avant `ia.js`.
- Ce qui est resté dans `common.js` l'est parce que d'autres pages l'utilisent : `ia.js`, `progress.js`, `map.js`, `timeline.js`, `rating_session.js` et `artist_source.js`.
- Le test Node `test_graph_node_sizes` charge maintenant `common.js` et `graphs.js`.

**Vérification.**
- `node --check` passe sur les trois fichiers.
- Les lignes triées des nouveaux fichiers correspondent exactement à l'ancien `script.js`, hors 7 lignes d'initialisation.
- Les tests passent.
- J'ai démarré gunicorn et contrôlé les réponses : `/static/common.js`, `/static/graphs.js` et `/static/sorts.js` renvoient 200, `/static/script.js` renvoie 404.
- L'ordre des balises `<script>` de `/sorts` et `/graphs` a été contrôlé via le client de test Flask.
- **Non vérifié :** l'affichage dans un vrai navigateur, car Playwright n'est pas installé.

### Mineur : imports circulaires contournés

**Fait.** Le graphe d'imports est maintenant acyclique (commit `2a930f3`) :

```
mb_client → storage
spotify_catalogue, artist_sites → mb_client
artist_matching → mb_client, spotify_catalogue, artist_sites
recording_matching → mb_client, artist_matching
mb_queue → mb_client, recording_matching, artist_matching
```

**Comment.**
- Les huit `from app.services import musicbrainz as mb` placés dans des fonctions de `artist_matching.py` et `artist_sites.py` sont devenus des imports en tête de fichier.
- Même traitement pour les imports différés de `browse`, `analyze` et `identity_links`, ainsi que pour ceux de `playlist_data.py` et `ia_worker.py`.

**Vérification.**
- Chaque module s'importe seul dans un interpréteur neuf.
- `grep` ne trouve plus d'import différé entre ces modules.
- Deux imports différés restent, mais ils n'ont pas de rapport avec le constat :
  - `ia_data` → `ia_learning` ;
  - `ia_worker` → `playlist_data.normalize_item`.

---

## Qualité du code

### Important : `browse` est fragile (F821)

**Fait.** Commit `6d5eeea` : `previous = set()` est initialisé avant la boucle, dans `app/services/artist_matching.py`. Le garde `if offset and ...` est conservé, donc le comportement ne change pas.

**Vérification.**
- `ruff check --select F821` est propre.
- Les tests de pagination de `test_artist_matching.py` passent.

### Mineur : style hétérogène, aucune configuration de lint

**Fait.** Commit `4bfda8d`.

**Comment.**
- `pyproject.toml` configure ruff avec les règles classiques `E4`, `E7`, `E9` et `F`.
  - Le jeu par défaut de ruff 0.17 ajouterait une centaine de règles de style (tri des imports, `UP`, `SIM`…), hors du périmètre demandé.
  - `E402` est ignoré dans `scripts/` et `tests/rating_session_browser.py`, qui doivent modifier `sys.path` avant d'importer l'application.
- Les 84 instructions multiples (64 avec `;`, 20 avec `if x: y`) ont été séparées par un script.
  - Le script coupe chaque ligne exactement à la position signalée par ruff, avec une assertion sur le caractère attendu.
  - Le reste du code n'est pas reformaté (pas de `ruff format` global).
  - 57 de ces lignes se trouvaient dans `tests/rating_session_browser.py`.
- Une affectation de lambda (`E731`) dans `app/routes/ia.py` est devenue une fonction.

**Vérification.**
- `ruff check .` : *All checks passed*.
- Les tests passent.
- `py_compile` passe sur les scripts navigateur. Ils ne sont pas exécutés : Playwright est absent.

### Mineur : imports inutilisés dans les tests

**Fait.** Commit `4bfda8d`.
- `time` et `geography` sont retirés de `tests/test_storage_concurrency.py`.
- `import os, sys, threading, tempfile` est séparé en quatre lignes dans `tests/rating_session_browser.py`.

---

## Dépendances

### Important : le venv local ne reflète pas `requirements.txt`

**Fait.** J'ai supprimé puis recréé le venv avec `python3 -m venv venv` et `pip install -r requirements-dev.txt`. `pymongo`, `SQLAlchemy`, `sqlmodel` et `pydantic` n'y sont plus. Le venv n'est pas versionné, donc pas de commit.

### Mineur : mises à jour mineures disponibles

**Fait.** Commit `a88336c`.
- Flask 3.0.2 → 3.1.3
- python-dotenv 1.0.1 → 1.2.4
- networkx 3.4.2 → 3.7
- `ruff==0.17.0` ajouté à `requirements-dev.txt`

**Vérification.** Les tests passent.

J'ai d'abord mesuré une suite plus lente avec les nouvelles versions. Une bissection, puis des relances, ont montré que l'écart venait de la charge de la machine et non des versions : 15 à 20 s dans les deux cas.

---

## Tests et outillage

### Important : aucune intégration continue

**Fait.** Commit `5c7e213`. `.github/workflows/ci.yml` tourne à chaque push sur `main` et sur chaque pull request :
- Ubuntu, Python 3.12 avec cache pip, Node 22 pour les tests de rendu JS ;
- `pip install -r requirements-dev.txt`, puis `ruff check .`, puis `pytest -q -rfs`.

**Vérification.** Le premier passage sur GitHub est vert en 43 s.

### Mineur : dépendance implicite à scikit-learn

**Fait.** Commit `00787b8`.

**Comment.** Les imports de scikit-learn sont différés dans `ia_learning.py`. Seuls 7 tests sur 29 de `test_ia.py` en ont besoin. Plutôt que d'ignorer tout le module, ces 7 tests reçoivent une fixture `sklearn`, qui appelle `pytest.importorskip('sklearn', reason='scikit-learn is required: pip install -r requirements.txt')`.

**Vérification.** Avec scikit-learn masqué (`sys.modules['sklearn'] = None`), on obtient 22 tests réussis et 7 ignorés avec le message, au lieu de 7 `ModuleNotFoundError`.

### Hors audit : test IA instable (validé avec toi)

**Fait.** Commit `08590fc`.

**Le problème.** `test_import_multiplicity_overlap_missing_and_privacy` échouait environ une fois sur dix. Le helper `archive()` créait les ZIP avec `writestr(nom, ...)`, qui horodate chaque entrée à l'heure courante. Deux archives « identiques » créées de part et d'autre d'une frontière de 2 s avaient donc un hash différent, et le test de réimport recevait un autre ID.

**La correction.** Les entrées sont créées via `zipfile.ZipInfo` avec une date fixe.

**Vérification.**
- Deux archives créées à 2,1 s d'écart sont identiques octet pour octet.
- Six passages consécutifs de `test_ia.py` sont verts.

---

## Hygiène du dépôt

### Important : `work/` (991 Mo) n'est pas ignoré

**Fait.**
- `work/` est ajouté au `.gitignore` (commit `49c4150`, avant tout `git add`).
- Commit `587dbf7` : deux scripts réutilisables sont déplacés vers `scripts/` :
  - `verify_sources.py` → `scripts/ui_smoke_server.py`, qui sert l'interface avec un compte Spotify simulé ;
  - `verify_ia_ui.py` → `scripts/ia_ui_smoke.py`, qui fait des captures de la page IA avec des données synthétiques. Son chemin Chromium codé en dur est remplacé par `STATIFY_TEST_CHROMIUM`, comme dans `tests/rating_session_browser.py`.
- Le reste n'est pas déplacé, comme convenu. Il reste dans `work/`, ignoré et non supprimé :
  - les passes de données ponctuelles `artist_*_pass.py`, avec ID et snapshots codés en dur, déjà jouées ;
  - `implement_map_list.py`, un patch de code déjà appliqué ;
  - les variantes `verify_ia_*` construites par `exec` de `verify_ia_ui.py` ;
  - `verify_rating_session.py`, remplacé par `tests/rating_session_browser.py`.

### Mineur : fichiers résiduels

**Fait.**
- `README-modele.md` n'est pas un résidu : c'est le protocole du module IA. Il est maintenant suivi par Git (commit `49c4150`) et référencé depuis le README.
- Les trois sauvegardes SQLite du 25/09/2026 (~350 Mo) dans le volume Docker sont conservées, comme convenu.

---

## Documentation

### Mineur : README long et incomplet

**Fait.** Commit `f380a19`.
- Le README devient un guide d'utilisation : installation, interface, enrichissement MusicBrainz, carte, laboratoire IA, stockage, développement.
- La feuille de route, l'état du socle, les critères et la vérification prévue sont déplacés dans `docs/ROADMAP.md`.
- Trois sujets sont ajoutés, vérifiés dans le code :
  - **File différée :** délai croissant plafonné à 1 h. Après 10 analyses incomplètes, l'artiste est revérifié toutes les 24 h.
  - **Confirmation directe par lien Spotify :** un artiste MusicBrainz unique qui déclare l'URL Spotify exacte suffit. Un lien exact présent sur le site officiel ou la page Bandcamp associés aussi.
  - **Sites officiels injoignables :** ils sont écartés sans bloquer l'analyse, et ce constat est gardé 7 jours. Un site en panne passagère depuis plus de 24 h est traité comme injoignable.
- Sont aussi documentés : la purge du cache, la commande `VACUUM`, les commandes ruff, pytest et CI, et les scripts navigateur.

### Mineur : pas de `CLAUDE.md` de projet

**Fait.** Commit `d22bfd9`. Il contient :
- les commandes de setup, de test, de lint, de lancement local et Docker, et de vérification navigateur ;
- la structure des modules ;
- les conventions : interface en français avec variante anglaise, code et commits en anglais, patch des modules, limite de débit MusicBrainz.

---

## Performance

### Important : le cache HTTP MusicBrainz n'est jamais purgé

**Fait.** Commit `8f2bf30`.

**Comment.**
- `mb_client.purge_cache()` supprime les entrées de `mb_http_cache` plus anciennes que `CACHE_AGE` (30 jours).
- Aucun lecteur du cache n'accepte de données plus anciennes. J'ai vérifié les quatre : `mb_client.request`, `spotify_catalogue._spotify_albums`, `artist_matching.spotify_releases` et `artist_sites.cached_links`. Supprimer ces entrées ne change donc aucun résultat.
- La suppression se fait par lots de 5 000 lignes pour garder les verrous d'écriture courts.
- Le worker MusicBrainz l'appelle au démarrage, puis toutes les 24 h.

**`VACUUM`.**
- La commande est documentée dans le README.
- Je ne l'ai pas lancée : la base live (684 Mo, dont 489 Mo de cache) n'a encore aucune entrée de plus de 30 jours. La plus ancienne date du 25/09/2026.
- La première purge effective aura lieu vers le 25/10/2026. C'est après elle qu'un `VACUUM` libérera de la place.

**Vérification.** Un nouveau test couvre la suppression des entrées expirées, la conservation des entrées récentes et la boucle par lots.

### Important : ~2,7 requêtes MusicBrainz par titre

**Fait.** Commit `fb9a08c`, après vérification sur l'API réelle (accord donné, moins de 1 requête/s).

**Ce que l'API a montré.**
- La lookup `recording/{id}?inc=genres+releases+release-groups+artist-credits` renvoie bien, pour chaque édition, son release-group et ses crédits artistes. Le contexte calculé est identique à celui du browse `release?recording=…`.
- **Mais** elle plafonne la liste à 25 éditions. *Billie Jean* : 25 éditions dans la lookup, 292 dans le browse. Elle ne peut donc pas remplacer le browse dans tous les cas.
- L'ordre des éditions diffère entre les deux appels. C'est sans effet : `_best_release` départage déjà par ID.

**Comment.**
- `_release_context` interroge d'abord la lookup.
- S'il y a moins de 25 éditions, la liste est complète et sert directement. Sinon, l'ancien browse paginé prend le relais.
- `_details("recording", …)` utilise exactement la même lookup. Pour le titre retenu, elle est donc servie par le cache HTTP, sans requête réseau.

**Gain.**
- Sur le cache live, 1 475 des 1 662 enregistrements (89 %) ont moins de 25 éditions. Pour eux, on passe de 2 requêtes (browse + détails) à 1.
- Pour les 11 % restants, un titre confirmé coûte autant qu'avant. Un candidat non retenu coûte une requête de plus.

**Vérification.**
- Sur l'API réelle, l'ancienne et la nouvelle méthode donnent des contextes identiques : 14 éditions, 1 requête réseau au lieu de 2 ; 292 éditions, 4 requêtes dans les deux cas.
- Les tests ont été adaptés (lookup complète, lookup tronquée suivie du browse) et un test vérifie que `_details` réutilise la lookup.

### Mineur : `status_for` lent (145 ms, appelé toutes les 8 s)

**Fait.** Commit `094bf78`. Le comptage des artistes avec une origine connue se fait en SQL :

```sql
SELECT COUNT(*) FROM mb_matches
WHERE ... AND json_extract(enriched, '$.origin') NOT IN ('{}', '[]', '', 0)
```

Avant, chaque JSON était décodé en Python.

**Vérification.**
- Mesure en lecture seule sur la base live : même résultat (1 901), 18 ms → 7 ms pour cette partie (meilleur de 5 passages, cache chaud).
- Le reste de `status_for` coûte ~17 ms, réparti entre plusieurs requêtes de comptage.
- Un nouveau test couvre les cas suivants : origine présente, `origin: null`, origine absente, `enriched` NULL, artiste non confirmé.

### Mineur : `library_tracks` relit toute la bibliothèque (87 ms par artiste)

**Fait.** Commit `c0389c6`.

**Comment.**
- Les lignes sont préfiltrées en SQL avec `instr(payload, spotify_id)`. Les titres likés passent par `json_each`, ce qui évite de décoder toute la collection.
- Seules les lignes qui peuvent mentionner l'artiste sont décodées. Le contrôle exact en Python reste en place, et l'ordre de fusion des sources est conservé.

**Vérification.**
- Mesure en lecture seule sur la base live : 87 ms → 19 ms, résultat identique.
- Les tests passent.

---

## Non traité ou non vérifié

- **Révocation de l'ancien secret Spotify :** action manuelle, voir la section Sécurité.
- **Vulnérabilités des dépendances :** `pip-audit` n'est pas installé et je ne l'ai pas installé. La piste est notée dans `docs/ROADMAP.md`.
- **Rendu dans un vrai navigateur** après le découpage JS : Playwright est absent. Les contrôles faits portent sur les fichiers servis, l'ordre des scripts et les tests Node.
- **Stack Docker en cours d'exécution :** elle tourne encore sur l'ancienne image. Il faut `docker compose up --build` pour prendre en compte les changements, notamment `musicbrainz.py` supprimé et la purge du cache.
- **Code IA** (`ia_*.py`, `ia.js`) : seulement touché pour le lint et les tests, pas audité en profondeur.
