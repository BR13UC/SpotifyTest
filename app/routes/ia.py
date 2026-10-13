"""Local IA pages and APIs; Spotify access only on explicit enrichment."""
import csv
import io
import json
import time

from flask import Blueprint, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import BadRequest, NotFound, RequestEntityTooLarge
from app import spotify_auth, storage
from app.services import ia_data as data, ia_learning as learning, ia_store as store

ia_bp = Blueprint('ia', __name__)


def body():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise BadRequest('Objet JSON requis.')
    return payload


@ia_bp.errorhandler(RequestEntityTooLarge)
def too_large(error):
    return jsonify(error='Archive trop volumineuse (64 Mo maximum).', error_en='Archive too large (maximum 64 MB).', error_code='ia_upload_too_large'), 413


@ia_bp.errorhandler(BadRequest)
def invalid_request(error):
    english = {
        'Artefact du modèle introuvable. Le modèle actif reste inchangé.': 'Model artifact not found. The active model is unchanged.',
        'Objet JSON requis.': 'A JSON object is required.',
        'Choisissez une archive ZIP Spotify.': 'Choose a Spotify ZIP archive.',
        'Cet import ne peut pas être relancé.': 'This import cannot be retried.',
        'Mode de sélection inconnu.': 'Unknown selection mode.',
        'Filtres invalides.': 'Invalid filters.',
        'Exclusions invalides.': 'Invalid excluded track list.',
        'Avis inconnu.': 'Unknown rating.',
        'Titre absent du catalogue local.': 'Track not found in the local catalog.',
        'Titre absent du catalogue.': 'Track not found in the catalog.',
        'Réservez les titres via un lot indépendant « Évaluation ».': 'Reserve tracks through an independent evaluation batch.',
        'Choisissez un lot de 1 à 50 titres.': 'Choose a batch of 1 to 50 tracks.',
        'Famille de modèles inconnue.': 'Unknown model family.',
        'Période inconnue.': 'Unknown period.',
        'Variantes MusicBrainz invalides.': 'Invalid MusicBrainz variants.',
        'Choisissez entre 1 et 8 threads CPU.': 'Choose between 1 and 8 CPU threads.',
        'Plafond invalide (1 à 10 000).': 'Invalid limit (1 to 10,000).',
        'Paramètres du modèle incomplets ou inconnus.': 'Model parameters are incomplete or unknown.',
        'Chaque paramètre attend une liste de 1 à 30 valeurs.': 'Each parameter requires a list of 1 to 30 values.',
        'La grille dépasse 10 000 configurations.': 'The grid exceeds 10,000 configurations.',
        'La grille dépasse le plafond. Réduisez-la ou augmentez le plafond.': 'The grid exceeds the limit. Reduce it or increase the limit.',
        'Des versions équivalentes ont des avis différents. Corrigez-les avant de figer les données.': 'Equivalent versions have conflicting ratings. Correct them before freezing the dataset.',
        'Choisissez une configuration dont les trois essais sont terminés.': 'Choose a configuration with three completed runs.',
        'Seule une campagne incomplète peut relancer ses échecs.': 'Only incomplete campaigns can retry failed runs.',
        'Action inconnue.': 'Unknown action.',
        'Action incompatible avec cet état.': 'This action is not available in the current state.',
        'Archive trop volumineuse (maximum décompressé : 1 Go).': 'Archive too large (maximum expanded size: 1 GB).',
        'Un fichier dépasse 64 Mo.': 'A file exceeds 64 MB.',
    }.get(error.description)
    if 'groupes de développement' in error.description:
        english = error.description.split(' : ')[0] + ': insufficient development or independent evaluation groups. Collect at least 4 development groups per taste and enough independent groups for the final 20% test.'
    elif error.description.startswith('Valeur invalide pour '):
        english = 'Invalid value for ' + error.description.removeprefix('Valeur invalide pour ')
    elif error.description.startswith('Cet import attend'):
        english = 'This importer expects extended audio history. The account export adapter will be added after inspecting its format.'
    return jsonify(error=error.description, error_en=english, error_code='ia_invalid_request'), 400


@ia_bp.get('')
@ia_bp.get('/')
def page():
    if not session.get('spotify_user_id'):
        return redirect(url_for('auth.home'))
    return render_template('ia.html')


@ia_bp.get('/api/profile')
def profile():
    user = session['spotify_user_id']
    cached, _ = storage.get_cache(user, 'profile')
    return jsonify(cached or {'id': user, 'display_name': user})


@ia_bp.get('/api/summary')
def summary():
    return jsonify(data.summary(session['spotify_user_id']))


@ia_bp.post('/api/imports')
def imports():
    key = data.upload(session['spotify_user_id'], request.files.get('archive'))
    return jsonify(id=key), 202


@ia_bp.post('/api/imports/<key>/retry')
def retry_import(key):
    changed = store.execute("UPDATE ia_imports SET state='queued',error=NULL WHERE id=? AND user_id=? AND state='failed' AND path IS NOT NULL", (key, session['spotify_user_id']))
    if not changed:
        raise BadRequest('Cet import ne peut pas être relancé.')
    return jsonify(status='queued')


@ia_bp.post('/api/sync-local')
def sync_local():
    return jsonify(collections=data.sync_local(session['spotify_user_id']))


@ia_bp.post('/api/candidates')
def candidates():
    filters = body()
    if filters.get('mode', 'varied') not in {'varied', 'favorites', 'evaluation', 'uncertain', 'review'}:
        raise BadRequest('Mode de sélection inconnu.')
    try:
        return jsonify(tracks=data.candidates(session['spotify_user_id'], filters))
    except (ValueError, TypeError) as exc:
        raise BadRequest('Filtres invalides.') from exc


@ia_bp.post('/api/annotations')
def annotate():
    data.annotate(session['spotify_user_id'], body())
    return jsonify(status='saved')


@ia_bp.post('/api/enrich')
def enrich():
    user = session['spotify_user_id']
    payload = body()
    uris = payload.get('uris', [])
    if not isinstance(uris, list) or not 1 <= len(uris) <= 50 or any(not isinstance(u, str) for u in uris):
        raise BadRequest('Choisissez un lot de 1 à 50 titres.')
    items = {r['uri']: r for r in store.tracks(user)}
    if any(u not in items for u in uris):
        raise BadRequest('Titre absent du catalogue.')
    automatic = payload.get('automatic') is True
    spotify_status = 'not_requested'

    def missing(item):
        return not (any(a.get('id') for a in item.get('artists') or []) and item.get('duration_ms') and item.get('release_date'))

    uris = list(dict.fromkeys(uris))
    if payload.get('spotify'):
        if not spotify_auth.is_valid_token():
            if not automatic:
                return jsonify(error='Session Spotify expirée. Reconnectez-vous.'), 401
            spotify_status = 'expired'
        else:
            if automatic:
                # Catch up old opinions in small portions, regardless of their taste.
                attempts = store.setting(user, 'automatic_enrichment_attempts', {})
                pending = store.rows('SELECT uri FROM ia_annotations WHERE user_id=? ORDER BY updated_at,uri', (user,))
                extra = [r['uri'] for r in pending if r['uri'] not in uris and r['uri'] in items
                         and missing(items[r['uri']]) and time.time() - attempts.get(r['uri'], 0) > 86400]
                uris.extend(extra[:min(12, 50 - len(uris))])
            requested = [u for u in uris if not automatic or missing(items[u])]
            spotify_status = 'cached'
            if requested:
                try:
                    from app.services.playlist_data import normalize_item
                    fetched = spotify_auth.get_spotify().tracks([items[u]['spotify_id'] for u in requested])['tracks']
                    with storage.connect() as db:
                        for track in fetched:
                            if track:
                                store.upsert_track(db, user, normalize_item({'track': track}, 0))
                    spotify_status = 'completed'
                    if automatic:
                        attempts.update({u: time.time() for u in requested})
                        store.set_setting(user, 'automatic_enrichment_attempts', attempts)
                except Exception:
                    if not automatic:
                        raise
                    # A Spotify failure must not interrupt opinions or MB enrichment.
                    spotify_status = 'unavailable'
    existing = store.rows("SELECT id,payload FROM ia_jobs WHERE user_id=? AND kind='enrich' AND state IN ('queued','running')", (user,))
    covered = {u for row in existing for u in json.loads(row['payload']).get('uris', [])}
    remaining = [u for u in uris if u not in covered]
    key = data.job(user, 'enrich', {'uris': remaining}) if remaining else None
    return jsonify(id=key, spotify_status=spotify_status, count=len(uris)), 202


@ia_bp.get('/api/metadata')
def metadata():
    uris = request.args.getlist('uri')
    if not 1 <= len(uris) <= 50:
        raise BadRequest('Choisissez un lot de 1 à 50 titres.')
    wanted = set(uris)
    return jsonify(tracks=[item for item in data.catalog(session['spotify_user_id']) if item['uri'] in wanted])



@ia_bp.post('/api/datasets')
def dataset():
    return jsonify(id=learning.dataset(session['spotify_user_id'])), 201


@ia_bp.post('/api/grid')
def grid():
    settings = body()
    preview = learning.grid(settings)
    preview['artist_holdout_runs'] = None
    if settings.get('dataset_id'):
        frozen = learning.load_dataset(session['spotify_user_id'], settings['dataset_id'])
        preview['artist_holdout_runs'] = preview['trainings'] + preview['reference_runs'] if frozen['artist_holdouts'] else 0
    return jsonify(preview)


@ia_bp.post('/api/campaigns')
def campaign():
    return jsonify(id=learning.launch(session['spotify_user_id'], body())), 202


@ia_bp.post('/api/campaigns/<key>/<action>')
def control(key, action):
    user = session['spotify_user_id']
    rows = store.rows('SELECT state FROM ia_campaigns WHERE id=? AND user_id=?', (key, user))
    if not rows:
        raise NotFound('Campagne introuvable.')
    if action not in {'pause', 'resume', 'stop', 'retry'}:
        raise BadRequest('Action inconnue.')
    state = rows[0]['state']
    if action == 'retry':
        if state != 'incomplete':
            raise BadRequest('Seule une campagne incomplète peut relancer ses échecs.')
        store.execute("UPDATE ia_trials SET state='queued',error=NULL WHERE campaign_id=? AND state='failed'", (key,))
    elif action == 'pause' and state != 'running' or action == 'resume' and state != 'paused' or action == 'stop' and state not in {'running', 'paused'}:
        raise BadRequest('Action incompatible avec cet état.')
    target = {'pause': 'paused', 'resume': 'running', 'stop': 'stopped', 'retry': 'running'}[action]
    if target == 'running':
        unfinished = store.rows("SELECT count(*) AS n FROM ia_trials WHERE campaign_id=? AND state IN ('queued','running')", (key,))[0]['n']
        if not unfinished:
            failed = store.rows("SELECT count(*) AS n FROM ia_trials WHERE campaign_id=? AND state='failed'", (key,))[0]['n']
            target = 'incomplete' if failed else 'completed'
    store.execute('UPDATE ia_campaigns SET state=? WHERE id=? AND user_id=?', (target, key, user))
    if target == 'stopped':
        store.execute("UPDATE ia_trials SET state='cancelled' WHERE campaign_id=? AND state='queued'", (key,))
    return jsonify(state=target)


@ia_bp.get('/api/results')
def results():
    return jsonify(learning.results(session['spotify_user_id']))


@ia_bp.get('/api/results.csv')
def export():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['dataset', 'campaign', 'configuration', 'model', 'period', 'musicbrainz', 'parameters', 'complete', 'f1_macro', 'dispersion', 'seconds', 'bytes'])
    for row in learning.results(session['spotify_user_id'])['results']:
        c = row['config']
        writer.writerow([row['dataset_id'], row['campaign_id'], row['config_key'], c['model'], c['period'], c['musicbrainz'], json.dumps(c['params']), row['complete'], row['f1_macro'], row['dispersion'], row['duration'], row['bytes']])
    return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=statify-ia-results.csv'})


@ia_bp.post('/api/finalize')
def finalize():
    payload = body()
    return jsonify(id=learning.finalize(session['spotify_user_id'], payload.get('campaign_id'), payload.get('config_key'))), 202


@ia_bp.post('/api/exploratory')
def exploratory():
    return jsonify(id=data.job(session['spotify_user_id'], 'exploratory', {})), 202


@ia_bp.post('/api/activate')
def activate():
    learning.activate(session['spotify_user_id'], (body()).get('model_id'))
    return jsonify(status='active')


@ia_bp.get('/api/collections')
def collections():
    return jsonify(collections=store.rows('SELECT id,name,collection_id,captured_at,source FROM ia_snapshots WHERE user_id=? ORDER BY captured_at DESC', (session['spotify_user_id'],)))


@ia_bp.get('/api/collections/<key>')
def collection_details(key):
    snapshots = store.rows('SELECT items FROM ia_snapshots WHERE user_id=? AND id=?', (session['spotify_user_id'], key))
    if not snapshots:
        raise NotFound('Collection locale introuvable.')
    uris = [item['uri'] for item in json.loads(snapshots[0]['items']) if (item.get('uri') or '').startswith('spotify:track:')]
    return jsonify(uris=list(dict.fromkeys(uris)))


@ia_bp.post('/api/predict')
def predict():
    user = session['spotify_user_id']
    payload = body()
    snapshots = store.rows('SELECT items FROM ia_snapshots WHERE user_id=? AND id=?', (user, payload.get('snapshot_id')))
    if not snapshots:
        raise NotFound('Collection locale introuvable. Enregistrez les collections chargées depuis l’espace IA.')
    catalog = {r['uri']: r for r in data.catalog(user)}
    items = [{**item, **catalog.get(item.get('uri'), {})} for item in json.loads(snapshots[0]['items'])]
    musical = [item for item in items if (item.get('uri') or '').startswith('spotify:track:')]
    predictions = learning.predictions(user, musical)
    return jsonify(tracks=[{**item, **prediction} for item, prediction in zip(musical, predictions)],
                   excluded=len(items) - len(musical), model_id=store.setting(user, 'active_model'))
