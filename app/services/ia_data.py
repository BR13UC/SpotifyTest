"""Spotify export import and annotation collection; no implicit API refresh."""
import hashlib
import json
import os
import sqlite3
import random
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from flask import current_app
from werkzeug.exceptions import BadRequest
from app import storage
from app.services import ia_store as store

CONTEXT = ('reason_start', 'reason_end', 'shuffle', 'skipped', 'offline')


def identifier():
    return uuid4().hex


def job(user, kind, payload):
    key = identifier()
    store.execute('INSERT INTO ia_jobs(id,user_id,kind,payload,created_at) VALUES(?,?,?,?,?)',
                  (key, user, kind, json.dumps(payload), storage.now()))
    return key


def upload(user, file):
    if not file or not file.filename or not file.filename.lower().endswith('.zip'):
        raise BadRequest('Choisissez une archive ZIP Spotify.')
    key = identifier()
    folder = Path(current_app.config['DATA_DIR']) / 'ia' / 'imports'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / f'{key}.zip'
    with path.open('xb') as destination:
        os.chmod(path, 0o600)
        file.save(destination)
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    old = store.rows('SELECT id,state,path FROM ia_imports WHERE user_id=? AND digest=?', (user, digest.hexdigest()))
    if old:
        path.unlink()
        if old[0]['state'] == 'failed' and old[0]['path']:
            store.execute("UPDATE ia_imports SET state='queued',error=NULL WHERE id=?", (old[0]['id'],))
        return old[0]['id']
    try:
        with zipfile.ZipFile(path) as archive:
            validate_archive(archive)
    except (ValueError, zipfile.BadZipFile) as exc:
        path.unlink()
        raise BadRequest(str(exc)) from exc
    try:
        store.execute('INSERT INTO ia_imports(id,user_id,filename,digest,path,created_at) VALUES(?,?,?,?,?,?)',
                      (key, user, Path(file.filename.replace('\\', '/')).name, digest.hexdigest(), str(path), storage.now()))
    except sqlite3.IntegrityError:
        path.unlink(missing_ok=True)
        return store.rows('SELECT id FROM ia_imports WHERE user_id=? AND digest=?', (user, digest.hexdigest()))[0]['id']
    return key


def validate_archive(archive):
    files = archive.infolist()
    if len(files) > 2000 or sum(i.file_size for i in files) > 1024 * 1024 * 1024:
        raise ValueError('Archive trop volumineuse (maximum décompressé : 1 Go).')
    if any(i.file_size > 64 * 1024 * 1024 for i in files):
        raise ValueError('Un fichier dépasse 64 Mo.')
    if not any(Path(i.filename).name.startswith('Streaming_History_Audio_') and i.filename.endswith('.json') for i in files):
        raise ValueError("Cet import attend l’historique étendu audio. L’export de compte sera pris en charge après inspection de son format.")


def import_archive(record):
    user, key = record['user_id'], record['id']
    counts = Counter()
    occurrences = Counter()
    last_metadata = {}
    store.execute('UPDATE ia_imports SET progress=0,error=NULL WHERE id=?', (key,))
    with zipfile.ZipFile(record['path']) as archive:
        validate_archive(archive)
        names = sorted(n for n in archive.namelist() if Path(n).name.startswith('Streaming_History_Audio_') and n.endswith('.json'))
        store.execute('UPDATE ia_imports SET total=? WHERE id=?', (len(names), key))
        for number, name in enumerate(names, 1):
            entries = json.loads(archive.read(name))
            if not isinstance(entries, list):
                raise ValueError(f'Format JSON invalide : {Path(name).name}')
            for start in range(0, len(entries), 500):
                with storage.connect() as db:
                    for item in entries[start:start + 500]:
                        if not isinstance(item, dict):
                            counts['invalid'] += 1
                            continue
                        if item.get('spotify_episode_uri') or item.get('audiobook_uri'):
                            counts['non_music'] += 1
                            continue
                        uri = item.get('spotify_track_uri')
                        if not isinstance(uri, str) or not uri.startswith('spotify:track:'):
                            counts['missing_uri'] += 1
                            continue
                        try:
                            ts = datetime.fromisoformat(item['ts'].replace('Z', '+00:00'))
                            if ts.tzinfo is None:
                                raise ValueError('Date sans fuseau')
                            timestamp = ts.astimezone(timezone.utc).isoformat()
                            ms = item['ms_played']
                            if isinstance(ms, bool) or not isinstance(ms, int) or ms < 0:
                                raise ValueError('Durée invalide')
                        except (KeyError, ValueError, TypeError, AttributeError):
                            counts['invalid'] += 1
                            continue
                        context = {field: item.get(field) for field in CONTEXT}
                        # Platform is part of event identity but contains no IP/address.
                        fingerprint = hashlib.sha256(json.dumps([uri, timestamp, ms, item.get('platform'), context], sort_keys=True).encode()).hexdigest()
                        occurrences[fingerprint] += 1
                        inserted = db.execute('INSERT OR IGNORE INTO ia_events VALUES(?,?,?,?,?,?,?,?)',
                                              (user, fingerprint, occurrences[fingerprint], uri, timestamp, ms, json.dumps(context), key)).rowcount
                        counts['musical_events'] += 1
                        counts['added'] += inserted
                        counts['already_present'] += 1 - inserted
                        metadata = {
                            'uri': uri, 'spotify_id': uri.split(':')[-1], 'type': 'track', 'exportable': True, 'is_local': False,
                            'name': item.get('master_metadata_track_name') or 'Titre indisponible',
                            'artist_names': [item['master_metadata_album_artist_name']] if item.get('master_metadata_album_artist_name') else [],
                            'album_name': item.get('master_metadata_album_album_name'),
                        }
                        if last_metadata.get(uri) != metadata:
                            store.upsert_track(db, user, metadata)
                            last_metadata[uri] = metadata
            store.execute('UPDATE ia_imports SET progress=?,summary=? WHERE id=?', (number, json.dumps(counts), key))
    sync_local(user)
    store.execute("UPDATE ia_imports SET state='completed',summary=?,path=NULL WHERE id=?", (json.dumps(counts), key))
    # Only the uploaded copy is removed; personal download archives are untouched.
    Path(record['path']).unlink(missing_ok=True)


def sync_local(user):
    """Snapshot cached collections without using cache expiry to invoke Spotify."""
    collections = []
    library, date = storage.get_cache(user, 'liked_collection')
    if library is not None:
        collections.append(('spotify_cache', 'library', 'Bibliothèque', date, library.get('tracks', [])))
    playlists, _ = storage.get_cache(user, 'playlists')
    playlist_names = {p['id']: p['name'] for p in playlists or []}
    cached = store.rows("SELECT cache_key,updated_at FROM cache_entries WHERE user_id=? AND resource='playlist_items'", (user,))
    for row in cached:
        collection = row['cache_key']
        collections.append(('spotify_cache', collection, playlist_names.get(collection, collection),
                            row['updated_at'], storage.get_playlist_items(user, collection)))
    for source, collection, name, captured, items in collections:
        # Normalize outside the transaction; release the writer lock regularly
        # even for a large library or many cached playlists.
        payload = json.dumps(items, ensure_ascii=False)
        for start in range(0, len(items), 100):
            with storage.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for item in items[start:start + 100]:
                    store.upsert_track(db, user, item)
        with storage.connect() as db:
            db.execute('INSERT OR IGNORE INTO ia_snapshots VALUES(?,?,?,?,?,?,?)',
                       (identifier(), user, source, collection, name, captured, payload))
    return len(collections)


def catalog(user):
    items = store.tracks(user)
    artists = {a['id']: a for a in storage.get_followed_artists(user)}
    matches = {(r['kind'], r['spotify_id']): r for r in store.rows('SELECT kind,spotify_id,status,mbid,enriched FROM mb_matches WHERE user_id=?', (user,))}
    for item in items:
        genres = []
        for artist in item.get('artists') or []:
            genres.extend({'name': g, 'source': 'spotify', 'level': 'artist'} for g in artists.get(artist['id'], {}).get('genres', []))
        match = matches.get(('recording', item.get('spotify_id')))
        if match and match['status'] == 'confirmed':
            item['equivalence'] = 'mb:' + match['mbid']
        for kind, external_id in [('recording', item.get('spotify_id'))] + [('artist', a['id']) for a in item.get('artists') or []]:
            match = matches.get((kind, external_id))
            if match and match['status'] == 'confirmed' and match['enriched']:
                enriched = json.loads(match['enriched'])
                genres.extend({'name': g['name'], 'source': 'musicbrainz', 'level': kind} for g in enriched.get('genres', []) if g.get('name'))
                if kind == 'recording' and not item.get('release_date'):
                    item['mb_release_date'] = enriched.get('first_release_date')
        item['genres'] = list({(g['name'].casefold(), g['source'], g['level']): g for g in genres}.values())
        item['equivalence'] = item.get('equivalence') or ('isrc:' + item['isrc'] if item.get('isrc') else item['uri'])
    return items


def summary(user):
    items = catalog(user)
    years = store.rows('SELECT substr(ts,1,4) AS year,count(*) AS events,count(DISTINCT uri) AS tracks FROM ia_events WHERE user_id=? GROUP BY year ORDER BY year', (user,))
    dates = store.rows('SELECT min(ts) AS first,max(ts) AS last,count(*) AS events,count(DISTINCT uri) AS tracks FROM ia_events WHERE user_id=?', (user,))[0]
    annotations = store.rows('SELECT label,count(*) AS count FROM ia_annotations WHERE user_id=? GROUP BY label', (user,))
    coverage = {
        'distinct_artists': len({a['id'] for i in items for a in i.get('artists', []) if a.get('id')}),
        'total': len(items), 'artists': sum(bool(i.get('artists')) for i in items),
        'duration': sum(bool(i.get('duration_ms')) for i in items),
        'release_date': sum(bool(i.get('release_date') or i.get('mb_release_date')) for i in items),
        'genres': sum(bool(i['genres']) for i in items),
    }
    return {'history': dates, 'years': years, 'coverage': coverage,
            'annotations': {label: next((r['count'] for r in annotations if r['label'] == label), 0) for label in store.LABELS + ['unknown']},
            'imports': store.rows('SELECT id,filename,state,progress,total,summary,error,created_at FROM ia_imports WHERE user_id=? ORDER BY created_at DESC', (user,)),
            'snapshots': store.rows('SELECT id,source,collection_id,name,captured_at FROM ia_snapshots WHERE user_id=? ORDER BY captured_at DESC', (user,)),
            'matches': store.rows('SELECT kind,status,count(*) AS count FROM mb_matches WHERE user_id=? GROUP BY kind,status', (user,)),
            'jobs': store.rows('SELECT id,kind,state,error FROM ia_jobs WHERE user_id=? ORDER BY created_at DESC LIMIT 10', (user,))}


def membership(user, selected=None):
    likes, playlists, named_favorites = set(), {}, set()
    seen = set()
    for row in store.rows('SELECT collection_id,name,items FROM ia_snapshots WHERE user_id=? ORDER BY captured_at DESC', (user,)):
        cid = row['collection_id']
        if cid in seen:
            continue
        seen.add(cid)
        uris = {i.get('uri') for i in json.loads(row['items']) if i.get('uri')}
        if cid == 'library':
            likes |= uris
        elif selected is None or cid in selected:
            if row['name'].strip().casefold() == 'que des bons sons':
                named_favorites |= uris
            for uri in uris:
                playlists.setdefault(uri, set()).add(cid)
    return likes, playlists, named_favorites


def candidates(user, filters):
    excluded = filters.get('exclude_uris', [])
    if not isinstance(excluded, list) or any(not isinstance(uri, str) or not uri.startswith('spotify:track:') for uri in excluded):
        raise BadRequest('Exclusions invalides.')
    excluded = set(excluded)
    annotations = {r['uri']: r for r in store.rows('SELECT * FROM ia_annotations WHERE user_id=?', (user,))}
    years = {}
    for row in store.rows('SELECT DISTINCT uri,substr(ts,1,4) AS year FROM ia_events WHERE user_id=?', (user,)):
        years.setdefault(row['uri'], set()).add(row['year'])
    selected = filters.get('playlists')
    likes, playlists, named_favorites = membership(user, selected)
    result = []
    mode = filters.get('mode', 'varied')
    for item in catalog(user):
        if item['uri'] in excluded:
            continue
        old = annotations.get(item['uri'])
        if mode != 'review' and old:
            continue
        if mode == 'review' and not old:
            continue
        if filters.get('artist') and filters['artist'].casefold() not in ' '.join(item.get('artist_names') or []).casefold():
            continue
        if filters.get('year') and filters['year'] not in years.get(item['uri'], set()):
            continue
        if filters.get('genre'):
            names = [g['name'].casefold() for g in item['genres']] or ['unknown']
            if filters['genre'].casefold() not in names:
                continue
        item.update(annotation=old, liked=item['uri'] in likes, favorite_playlist=item['uri'] in named_favorites,
                    playlist_count=len(playlists.get(item['uri'], set())), selection=mode)
        result.append(item)
    random.Random(filters.get('seed', identifier())).shuffle(result)
    if mode == 'favorites':
        result.sort(key=lambda i: (i['playlist_count'] >= int(filters.get('threshold', 3)), i['playlist_count'], i['liked'] or i['favorite_playlist']), reverse=True)
    elif mode == 'varied':
        counts = Counter()
        diversified = []
        for item in result:
            name = (item.get('artist_names') or ['unknown'])[0]
            counts[name] += 1
            diversified.append((counts[name], item))
        result = [i for _, i in sorted(diversified, key=lambda pair: pair[0])]
    elif mode == 'uncertain':
        from app.services.ia_learning import predictions
        # Bound interactive scoring to a random pool; training remains in the worker.
        result = result[:200]
        scores = {i['uri']: i for i in predictions(user, result, exploratory=True, explanations=False)}
        result.sort(key=lambda i: scores[i['uri']].get('score') if scores[i['uri']].get('score') is not None else 0)
    composition = [item['uri'] for item in result[:min(int(filters.get('limit', 12)), 40)]]
    # Register the server-selected collection mode; clients cannot disguise guided suggestions as blind test data.
    result = result[:min(int(filters.get('limit', 12)), 40)]
    for item in result:
        store.set_setting(user, 'selection:' + item['uri'], {'mode': mode, 'playlists': selected, 'threshold': int(filters.get('threshold', 3)), 'composition': composition})
    return result


def annotate(user, data):
    uri, label = data.get('uri'), data.get('label')
    if label not in store.LABELS + ['unknown']:
        raise BadRequest('Avis inconnu.')
    if not store.rows('SELECT uri FROM ia_tracks WHERE user_id=? AND uri=?', (user, uri)):
        raise BadRequest('Titre absent du catalogue local.')
    selection = store.setting(user, 'selection:' + uri, {'mode': 'playlist'})
    old = store.rows('SELECT reserved FROM ia_annotations WHERE user_id=? AND uri=?', (user, uri))
    reserved = old[0]['reserved'] if old else int(selection['mode'] == 'evaluation')
    if data.get('reserve') and not reserved:
        raise BadRequest('Réservez les titres via un lot indépendant « Évaluation ».')
    now = storage.now()
    with storage.connect() as db:
        db.execute('INSERT INTO ia_annotations VALUES(?,?,?,?,?,?) ON CONFLICT(user_id,uri) DO UPDATE SET label=excluded.label,updated_at=excluded.updated_at',
                   (user, uri, label, reserved, json.dumps(selection), now))
        db.execute('INSERT INTO ia_annotation_history(user_id,uri,label,reserved,selection,created_at) VALUES(?,?,?,?,?,?)',
                   (user, uri, label, reserved, json.dumps(selection), now))
