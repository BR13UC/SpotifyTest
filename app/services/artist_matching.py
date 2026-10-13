"""Evidence-based artist matching; account libraries never enter public caches."""

import json
import re
from difflib import SequenceMatcher

from flask import current_app
from spotipy.exceptions import SpotifyException

from app import storage
from app.services import artist_sites, mb_client, spotify_catalogue


VERSION = 2
THRESHOLD = 85
MARGIN = 15


def library_tracks(user_id, spotify_id):
    """Use only collections already loaded, without Spotify account credentials."""
    with storage.connect() as db:
        items = [json.loads(row['payload']) for row in db.execute(
            "SELECT payload FROM playlist_items WHERE user_id=?", (user_id,))]
        liked = db.execute("SELECT payload FROM cache_entries WHERE user_id=? "
                           "AND resource='liked_collection' AND cache_key=''", (user_id,)).fetchone()
        if liked:
            items.extend(json.loads(liked['payload']).get('tracks', []))
        items.extend(json.loads(row['spotify_payload']) for row in db.execute(
            "SELECT spotify_payload FROM mb_matches WHERE user_id=? AND kind='recording'", (user_id,)))
    tracks = {}
    for item in items:
        if item.get('is_local') or item.get('name') == 'Titre indisponible':
            continue
        if not any(artist.get('id') == spotify_id for artist in item.get('artists', [])):
            continue
        key = item.get('spotify_id') or item.get('id')
        if key:
            previous = tracks.get(key, {})
            tracks[key] = {**previous, **{k: v for k, v in item.items() if v not in (None, '', [], {})}}
    return list(tracks.values())


def browse(path, params, key):
    items = {}
    offset = 0
    previous = set()
    while True:
        page = mb_client.request(path, {**params, 'limit': 100, 'offset': offset})
        entries = page.get(key, [])
        for item in entries:
            if item.get('id'):
                items[item['id']] = item
        total = page.get(f'{path}-count', page.get('count'))
        if not entries and total is not None and offset < total:
            raise mb_client.TemporaryFailure('MusicBrainz pagination incomplete')
        if not entries or (total is not None and offset + len(entries) >= total) or (total is None and len(entries) < 100):
            return list(items.values())
        if offset and not any(item.get('id') not in previous for item in entries):
            raise mb_client.TemporaryFailure('MusicBrainz pagination did not advance')
        previous = set(items)
        offset += len(entries)


def spotify_releases(spotify_id):
    cache_key = f'artist-score-releases:{spotify_id}'
    with storage.connect() as db:
        cached = db.execute('SELECT payload,updated_at FROM mb_http_cache WHERE cache_key=?', (cache_key,)).fetchone()
    if cached and not storage.is_stale(cached['updated_at'], mb_client.CACHE_AGE):
        return json.loads(cached['payload'])
    if not current_app.config.get('SPOTIPY_CLIENT_ID') or not current_app.config.get('SPOTIPY_CLIENT_SECRET'):
        return None
    items, offset = {}, 0
    while True:
        page = spotify_catalogue._catalogue_albums(spotify_id, album_type='album,single,appears_on', limit=50, offset=offset)
        previous = set(items)
        for item in page.get('items', []):
            if item.get('id'):
                items[item['id']] = item
        if not page.get('next'):
            break
        if not page.get('items'):
            raise mb_client.TemporaryFailure('Spotify pagination did not advance')
        if previous == set(items):
            raise mb_client.TemporaryFailure('Spotify pagination repeated a page')
        offset += len(page['items'])
    result = list(items.values())
    with storage.connect() as db:
        db.execute('INSERT INTO mb_http_cache VALUES(?,?,?) ON CONFLICT(cache_key) '
                   'DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at',
                   (cache_key, json.dumps(result), storage.now()))
    return result


def _credits(recording):
    return {part['artist']['id'] for part in recording.get('artist-credit', [])
            if isinstance(part, dict) and (part.get('artist') or {}).get('id')}


def _isrc(value):
    return re.sub(r'[^A-Z0-9]', '', (value or '').upper())


def _release_key(value):
    value = mb_client._fold(value)
    editions = r'(?:remaster|deluxe|expanded|anniversary|edition|reissue)'
    value = re.sub(r'\s*[\(\[][^(\[]*' + editions + r'[^)\]]*[\)\]]', '', value)
    value = re.sub(r'\s+-\s+.*' + editions + r'.*$', '', value)
    return mb_client._album_key(value)


def track_recordings(track):
    entities = {}
    code = _isrc(track.get('isrc'))
    if code:
        for item in mb_client.request(f'isrc/{code}', {'inc': 'artist-credits'}).get('recordings', []):
            # An ISRC lookup itself proves membership even if the response omits isrcs.
            item = {**item, 'isrcs': list(set(item.get('isrcs', [])) | {code})}
            entities[item['id']] = item
    if track.get('name'):
        artists = ' OR '.join(f'artist:"{mb_client._query_text(a.get("name"))}"' for a in track.get('artists', []) if a.get('name'))
        query = f'recording:"{mb_client._query_text(track["name"])}"'
        if artists:
            query += f' AND ({artists})'
        for item in browse('recording', {'query': query}, 'recordings'):
            entities.setdefault(item['id'], item)
    return list(entities.values())


def assess(payload, candidate, entity, tracks, recordings, spotify, releases, direct=False, complete=True):
    """Pure scoring: correlated evidence is counted once within each family."""
    points = {'recordings': 0, 'discography': 0, 'name': 0, 'context': 0}
    evidence, matched, matched_tracks = [], {}, set()
    warnings = list(entity.get('_warnings', []))
    aliases = [a.get('name', '') for a in entity.get('aliases', [])]
    name = mb_client._album_key(payload.get('name'))
    names = [mb_client._album_key(value) for value in [candidate.get('name'), *aliases] if value]
    if name and name in names:
        points['name'] = 10
    elif name and names and max(SequenceMatcher(None, name, value).ratio() for value in names) >= .85:
        points['name'] = 5
    if points['name']:
        evidence.append({'kind': 'name', 'points': points['name'], 'label': candidate.get('name'),
                         'url': f'https://musicbrainz.org/artist/{candidate["id"]}'})
    for track in tracks:
        key = track.get('spotify_id') or track.get('id')
        for recording in recordings.get(key, []):
            if candidate['id'] not in _credits(recording):
                continue
            code = _isrc(track.get('isrc'))
            isrc_match = code and code in {_isrc(value) for value in recording.get('isrcs', [])}
            title_match = _release_key(track.get('name')) == _release_key(recording.get('title'))
            duration_match = (track.get('duration_ms') is not None and recording.get('length') is not None
                              and abs(track['duration_ms'] - recording['length']) <= 5000)
            value = 30 if isrc_match else 15 if title_match and duration_match else 0
            if isrc_match and not title_match:
                warnings.append({'kind': 'isrc_title', 'url': f'https://musicbrainz.org/recording/{recording["id"]}'})
            if value and recording.get('id'):
                # Different editions with the same ISRC/title-duration cannot inflate the score.
                identity = ('title', _release_key(track.get('name')))
                if code:
                    # Shared ISRCs also merge differently titled editions.
                    identity = next((key for key, proof in matched.items() if proof.get('isrc') == code), identity)
                old = matched.get(identity)
                proof = {'kind': 'isrc' if isrc_match else 'recording', 'points': value,
                         'label': track.get('name'), 'recording_id': recording['id'],
                         'isrc': code,
                         'url': f'https://musicbrainz.org/recording/{recording["id"]}'}
                if not old or old['points'] < value:
                    matched[identity] = proof
                matched_tracks.add(key)
    # One MB recording returned for several Spotify tracks is still one proof.
    unique = {}
    for proof in matched.values():
        old = unique.get(proof['recording_id'])
        if not old or old['points'] < proof['points']:
            unique[proof['recording_id']] = proof
    points['recordings'] = min(60, sum(item['points'] for item in unique.values()))
    remaining = 60
    for proof in unique.values():
        proof['points'] = min(remaining, proof['points'])
        remaining -= proof['points']
    evidence.extend(unique.values())
    spotify_by_title = {}
    for item in spotify or []:
        title = _release_key(item.get('name') or item.get('album_name'))
        if title and title not in mb_client.GENERIC_ALBUMS:
            spotify_by_title.setdefault(title, item)
    common = {}
    for release in releases:
        title = _release_key(release.get('title'))
        item = spotify_by_title.get(title)
        if not item or candidate['id'] not in _credits(release):
            continue
        compilation = 'Compilation' in release.get('secondary-types', []) or item.get('album_group') == 'appears_on' or item.get('album_type') == 'compilation'
        if compilation and not any((t.get('spotify_id') or t.get('id')) in matched_tracks
                                   and _release_key(t.get('album_name')) == title for t in tracks):
            continue
        common.setdefault(title, {'kind': 'release', 'points': 12.5, 'label': release['title'],
                                  'url': f'https://musicbrainz.org/release-group/{release["id"]}'})
    points['discography'] = min(25, 12.5 * len(common))
    remaining = 25
    for proof in common.values():
        proof['points'] = min(remaining, proof['points'])
        remaining -= proof['points']
    evidence.extend(common.values())
    # Context uses additional, unscored tracks; release dates are never identity by themselves.
    collaborators = set()
    for track in tracks:
        key = track.get('spotify_id') or track.get('id')
        if key in matched_tracks:
            continue
        for recording in recordings.get(key, []):
            if candidate['id'] not in _credits(recording) or mb_client._album_key(track.get('name')) != mb_client._album_key(recording.get('title')):
                continue
            extra = _credits(recording) - {candidate['id']}
            if extra.intersection(track.get('verified_collaborators', [])):
                collaborators.add(recording['id'])
                evidence.append({'kind': 'collaboration', 'points': 2.5, 'label': track.get('name'),
                                 'url': f'https://musicbrainz.org/recording/{recording["id"]}'})
            date, mb_date = track.get('release_date', ''), recording.get('first-release-date', '')
            if date and mb_date and date[:4] == mb_date[:4]:
                collaborators.add(f'date:{recording["id"]}')
                evidence.append({'kind': 'date', 'points': 2.5, 'label': f'{track.get("name")} ({date[:4]})',
                                 'url': f'https://musicbrainz.org/recording/{recording["id"]}'})
    points['context'] = min(5, 2.5 * len(collaborators))
    remaining = 5
    for proof in evidence:
        if proof['kind'] in {'date', 'collaboration'}:
            proof['points'] = min(remaining, proof['points'])
            remaining -= proof['points']
    direct_urls = set(entity.get('_direct_urls', []))
    direct = direct or bool(direct_urls)
    conflicts = entity.get('_conflicts', [])
    if direct:
        evidence.append({'kind': 'identity', 'points': 100, 'label': payload.get('name'),
                         'url': next(iter(sorted(direct_urls)), f'https://open.spotify.com/artist/{payload["id"]}')})
    missing = []
    if not tracks:
        missing.append('tracks')
    if spotify is None:
        missing.append('spotify_catalogue')
    if not entity.get('aliases'):
        missing.append('aliases')
    if not complete:
        missing.append('incomplete')
    return {'id': candidate['id'], 'score': 100 if direct else min(100, sum(points.values())),
            'families': points, 'evidence': evidence, 'recording_count': len(unique),
            'direct': direct, 'complete': complete, 'conflict': bool(conflicts),
            'conflicts': conflicts, 'warnings': warnings, 'missing': missing}


def select(ranked):
    ordered = sorted(ranked, key=lambda item: (-item['score'], item['id']))
    if not ordered or not all(item['complete'] for item in ordered):
        return None
    direct = [item for item in ordered if item['direct']]
    if direct:
        return direct[0]['id'] if len(direct) == 1 and not direct[0]['conflict'] else None
    best = ordered[0]
    margin = best['score'] - ordered[1]['score'] if len(ordered) > 1 else 100
    return best['id'] if (best['score'] >= THRESHOLD and margin >= MARGIN and 'spotify_catalogue' not in best.get('missing', []) and
                          best['recording_count'] >= 2 and not best['conflict']) else None


def analyze(user_id, payload, candidates, linked):
    # One MusicBrainz artist declaring this exact Spotify URL is the identity: homonyms, library tracks
    # and discographies cannot change the decision, so only that candidate and its own sites are checked.
    shortcut = len(linked) == 1
    by_id = {item['id']: item for item in candidates if not shortcut or item['id'] in linked}
    for mbid in linked:
        if mbid not in by_id:
            by_id[mbid] = mb_client._candidate(mb_client.request(f'artist/{mbid}', {'inc': 'aliases+url-rels'}))
    tracks = [] if shortcut else library_tracks(user_id, payload['id'])
    with storage.connect() as db:
        verified = {row['spotify_id']: row['mbid'] for row in db.execute(
            "SELECT spotify_id,mbid FROM mb_matches WHERE user_id=? AND kind='artist' AND status='confirmed'",
            (user_id,))}
    for track in tracks:
        track['verified_collaborators'] = [verified[a['id']] for a in track.get('artists', []) if a.get('id') in verified and a['id'] != payload['id']]
    recordings, complete = {}, True
    errors = []
    def progress(phase, done, total, candidate=None):
        storage.set_cache(user_id, 'artist_score_progress',
                          {'name': payload.get('name'), 'phase': phase, 'done': done, 'total': total,
                           'candidate': candidate}, payload['id'])
    progress('tracks', 0, len(tracks))
    for index, track in enumerate(tracks):
        try:
            recordings[track.get('spotify_id') or track.get('id')] = track_recordings(track)
        except mb_client.TemporaryFailure as error:
            complete = False
            errors.append(str(error))
        progress('tracks', index + 1, len(tracks))
    if shortcut:
        spotify = []
    else:
        progress('catalogue', 0, 1)
        try:
            spotify = spotify_releases(payload['id'])
        except (mb_client.TemporaryFailure, SpotifyException) as error:
            spotify, complete = None, False
            errors.append(str(error))
    # A missing catalogue prevents score-based decisions, but not direct identity.
    library_releases = [{'name': t.get('album_name'), 'album_type': t.get('album_type'), 'release_date': t.get('release_date')} for t in tracks]
    ranked = []
    for index, candidate in enumerate(by_id.values()):
        progress('candidates', index, len(by_id), candidate.get('name'))
        candidate_complete = complete
        try:
            entity = mb_client.request(f'artist/{candidate["id"]}', {'inc': 'aliases+url-rels'})
            if entity.get('id') != candidate['id']:
                raise mb_client.TemporaryFailure('MusicBrainz candidate details unavailable')
            urls, warnings, conflicts, site_complete = artist_sites.identity_links(entity, payload['id'])
            entity = {**entity, '_direct_urls': urls, '_warnings': warnings, '_conflicts': conflicts}
            # With a unique direct link, an unreachable own site cannot add proof; only a readable conflict blocks.
            candidate_complete = candidate_complete and (site_complete or shortcut)
            errors.extend(f"Official website unavailable: {w['url']}" for w in warnings if w.get('temporary'))
            releases = [] if shortcut else browse('release-group', {'artist': candidate['id'], 'inc': 'artist-credits'}, 'release-groups')
        except mb_client.TemporaryFailure as error:
            entity, releases, candidate_complete = {}, [], False
            errors.append(str(error))
        ranked.append(assess(payload, candidate, entity, tracks, recordings,
                             (spotify or []) + library_releases if spotify is not None else library_releases,
                             releases, direct=candidate['id'] in linked, complete=candidate_complete))
        if spotify is None:
            ranked[-1]['missing'].append('spotify_catalogue')
        progress('candidates', index + 1, len(by_id), candidate.get('name'))
    analysis_complete = complete and all(item['complete'] for item in ranked)
    if not analysis_complete:
        for item in ranked:
            item['complete'] = False
            if 'incomplete' not in item['missing']:
                item['missing'].append('incomplete')
    ranked.sort(key=lambda item: (-item['score'], item['id']))
    selected = select(ranked)
    evidence = {'version': VERSION, 'ranked': ranked, 'threshold': THRESHOLD, 'required_margin': MARGIN,
                'complete': analysis_complete, 'errors': list(dict.fromkeys(errors))}
    with storage.connect() as db:
        db.execute("DELETE FROM cache_entries WHERE user_id=? AND resource='artist_score_progress' AND cache_key=?",
                   (user_id, payload['id']))
    return selected, ('spotify_url' if selected and next(r for r in ranked if r['id'] == selected)['direct'] else 'score') if selected else None, evidence, list(by_id.values())


def comparison(user_id, spotify_id, mbid, match):
    tracks = library_tracks(user_id, spotify_id)
    spotify = spotify_releases(spotify_id) or []
    releases = browse('release-group', {'artist': mbid, 'inc': 'artist-credits'}, 'release-groups')
    assessment = next((r for r in match['match_evidence']['ranked'] if r['id'] == mbid), {})
    return {'spotify_albums': list(dict.fromkeys([r['name'] for r in spotify] + [t['album_name'] for t in tracks if t.get('album_name')])),
            'musicbrainz_albums': list(dict.fromkeys(r['title'] for r in releases)),
            'common_albums': [e['label'] for e in assessment.get('evidence', []) if e['kind'] == 'release'],
            'matched_tracks': [e['label'] for e in assessment.get('evidence', []) if e['kind'] in {'isrc', 'recording'}],
            'assessment': assessment}
