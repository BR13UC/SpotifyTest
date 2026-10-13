import json
import socket
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask
from spotipy.exceptions import SpotifyException

from app import storage
from app.services import artist_matching as scoring, artist_sites, mb_client, mb_queue, spotify_catalogue


@pytest.fixture
def context(tmp_path):
    app = Flask(__name__)
    app.config['DATABASE_PATH'] = str(tmp_path / 'test.sqlite3')
    with app.app_context():
        storage.init_db()
        yield app


def fixtures():
    payload = {'id': 'spotify', 'name': 'Artist'}
    candidate = {'id': 'candidate', 'name': 'Artist'}
    tracks = [{'spotify_id': f't{i}', 'name': f'Song {i}', 'isrc': f'USAAA000000{i}',
               'duration_ms': 180000, 'album_name': f'Release {i}',
               'artists': [{'id': 'spotify', 'name': 'Artist'}]} for i in range(2)]
    recordings = {f't{i}': [{'id': f'r{i}', 'title': f'Song {i}', 'length': 180000,
                             'isrcs': [f'USAAA000000{i}'],
                             'artist-credit': [{'artist': {'id': 'candidate', 'name': 'Artist'}}]}] for i in range(2)}
    spotify = [{'id': f'a{i}', 'name': f'Release {i}', 'album_type': 'single'} for i in range(2)]
    releases = [{'id': f'g{i}', 'title': f'Release {i}', 'primary-type': 'Single',
                 'artist-credit': [{'artist': {'id': 'candidate'}}]} for i in range(2)]
    return payload, candidate, {}, tracks, recordings, spotify, releases


def test_score_requires_real_credited_recordings():
    args = fixtures()
    result = scoring.assess(*args)
    assert result['score'] == 95
    assert result['families'] == {'recordings': 60, 'discography': 25, 'name': 10, 'context': 0}
    assert scoring.select([result]) == 'candidate'
    for entries in args[4].values():
        entries[0]['artist-credit'][0]['artist']['id'] = 'homonym'
    result = scoring.assess(*args)
    assert result['score'] == 35
    assert scoring.select([result]) is None


@pytest.mark.parametrize('change', ['duplicate_track', 'same_isrc', 'same_recording', 'reissue'])
def test_duplicate_evidence_never_confirms(change):
    args = fixtures()
    if change == 'duplicate_track':
        args[3][1] = args[3][0]
    elif change == 'same_isrc':
        args[3][1]['isrc'] = args[3][0]['isrc']
        args[4]['t1'][0]['isrcs'] = args[4]['t0'][0]['isrcs']
    elif change == 'same_recording':
        args[4]['t1'][0]['id'] = 'r0'
    else:
        args[5].append({**args[5][0], 'id': 'reissue'})
        args[6][1] = {**args[6][0], 'id': 'another-edition'}
    result = scoring.assess(*args)
    if change == 'reissue':
        assert result['families']['discography'] == 12.5
    else:
        assert result['recording_count'] == 1
        assert scoring.select([result]) is None


def test_alias_and_duration_scoring():
    args = list(fixtures())
    args[1] = {'id': 'candidate', 'name': 'Legal name'}
    args[2] = {'aliases': [{'name': 'Artist'}]}
    for track in args[3]:
        track.pop('isrc')
    assert scoring.assess(*args)['score'] == 65
    args[4]['t1'][0]['length'] += 5001
    assert scoring.assess(*args)['families']['recordings'] == 15


@pytest.mark.parametrize('score,margin,complete,conflict,count,expected', [
    (85, 15, True, False, 2, 'candidate'), (84, 15, True, False, 2, None),
    (85, 14, True, False, 2, None), (95, 20, False, False, 2, None),
    (95, 20, True, True, 2, None), (95, 20, True, False, 1, None),
    (95, 0, True, False, 2, None)])
def test_thresholds(score, margin, complete, conflict, count, expected):
    best = {'id': 'candidate', 'score': score, 'complete': complete, 'conflict': conflict,
            'recording_count': count, 'direct': False}
    second = {**best, 'id': 'second', 'score': score - margin, 'complete': True, 'conflict': False}
    assert scoring.select([second, best]) == expected


def test_multiple_direct_identities_and_partial_analysis():
    result = scoring.assess(*fixtures(), direct=True)
    assert result['score'] == 100
    assert scoring.select([result]) == 'candidate'
    assert scoring.select([result, {**result, 'id': 'second'}]) is None
    assert scoring.select([{**result, 'complete': False}]) is None


def test_same_title_under_different_edition_isrcs_counts_once():
    args = fixtures()
    args[3][1]['name'] = args[3][0]['name']
    args[4]['t1'][0]['title'] = args[4]['t0'][0]['title']
    assert scoring.assess(*args)['recording_count'] == 1


def test_deluxe_and_remaster_releases_do_not_inflate_discography():
    args = fixtures()
    args[5][1]['name'] = 'Release 0 (Deluxe Edition)'
    args[6][1]['title'] = 'Release 0 - 2020 Remaster'
    assert scoring.assess(*args)['families']['discography'] == 12.5


def test_empty_pagination_page_is_partial(monkeypatch):
    monkeypatch.setattr(mb_client, 'request', lambda *args: {'recording-count': 10, 'recordings': []})
    with pytest.raises(mb_client.TemporaryFailure, match='incomplete'):
        scoring.browse('recording', {}, 'recordings')


def test_compilation_requires_a_matching_credited_track():
    args = fixtures()
    args[6][0]['secondary-types'] = ['Compilation']
    args[4]['t0'][0]['artist-credit'] = []
    assert scoring.assess(*args)['families']['discography'] == 12.5


def test_context_does_not_recount_scored_tracks():
    args = fixtures()
    for track in args[3]:
        track.update(release_date='2020', verified_collaborators=['friend'])
    for entries in args[4].values():
        entries[0]['first-release-date'] = '2020'
        entries[0]['artist-credit'].append({'artist': {'id': 'friend'}})
    assert scoring.assess(*args)['families']['context'] == 0
    for track in args[3]:
        track.pop('isrc')
        track.pop('duration_ms')
    assert scoring.assess(*args)['families']['context'] == 5


def test_library_account_isolation_and_unavailable_tracks(context):
    tracks = fixtures()[3]
    storage.replace_playlist_items('alice', 'playlist', [tracks[0], tracks[0]])
    storage.set_cache('alice', 'liked_collection', {'tracks': tracks + [{'spotify_id': 'bad', 'name': 'Titre indisponible', 'artists': tracks[0]['artists']}]})
    storage.set_cache('bob', 'liked_collection', {'tracks': [{**tracks[0], 'spotify_id': 'private'}]})
    assert {t['spotify_id'] for t in scoring.library_tracks('alice', 'spotify')} == {'t0', 't1'}


def test_musicbrainz_and_spotify_pagination(context, monkeypatch):
    calls = []
    def request(path, params):
        calls.append(params['offset'])
        offset = params['offset']
        return {'release-group-count': 101, 'release-groups': [{'id': str(i)} for i in range(offset, min(101, offset + 100))]}
    monkeypatch.setattr(mb_client, 'request', request)
    assert len(scoring.browse('release-group', {}, 'release-groups')) == 101
    assert calls == [0, 100]
    context.config.update(SPOTIPY_CLIENT_ID='test', SPOTIPY_CLIENT_SECRET='test')
    class Client:
        def artist_albums(self, artist, **params):
            assert params['album_type'] == 'album,single,appears_on'
            return {'items': [{'id': str(params['offset']), 'name': 'EP'}], 'next': 'next' if params['offset'] == 0 else None}
    monkeypatch.setattr(spotify_catalogue, '_spotify_catalogue_client', lambda: Client())
    assert len(scoring.spotify_releases('spotify')) == 2
    monkeypatch.setattr(spotify_catalogue, '_spotify_catalogue_client', lambda: pytest.fail('Should reuse cache'))
    assert len(scoring.spotify_releases('spotify')) == 2


def test_spotify_catalogue_renews_token_once_after_401(context, monkeypatch):
    context.config.update(SPOTIPY_CLIENT_ID='test', SPOTIPY_CLIENT_SECRET='test')
    renewals = []
    class Auth:
        def get_access_token(self, as_dict=True, check_cache=True):
            renewals.append(check_cache)
    class Client:
        auth_manager = Auth()
        def __init__(self, failures):
            self.failures = failures
        def artist_albums(self, artist, **params):
            if self.failures:
                self.failures -= 1
                raise SpotifyException(401, -1, 'The access token expired')
            return {'items': [{'id': 'album', 'name': 'EP'}], 'next': None}
    monkeypatch.setattr(spotify_catalogue, '_spotify_catalogue_client', lambda: Client(1))
    assert len(scoring.spotify_releases('renewed')) == 1
    assert renewals == [False]
    monkeypatch.setattr(spotify_catalogue, '_spotify_catalogue_client', lambda: Client(2))
    with pytest.raises(SpotifyException):
        scoring.spotify_releases('still-refused')
    assert renewals == [False, False]
    monkeypatch.setattr(spotify_catalogue, '_spotify_catalogue_client', lambda: Client(1))
    assert spotify_catalogue._spotify_albums('compared') == ['EP']
    assert renewals == [False, False, False]


def test_official_identity_links_conflicts_and_no_name_inference(monkeypatch):
    monkeypatch.setattr(artist_sites, 'cached_links', lambda url: ['spotify'])
    entity = {'relations': [{'type': 'official homepage', 'url': {'resource': 'https://example.org/'}}]}
    assert artist_sites.identity_links(entity, 'spotify') == (['https://example.org/'], [], [], True)
    assert artist_sites.identity_links(entity, 'other')[2][0]['kind'] == 'official_other_identity'
    parser = artist_sites.Links()
    parser.feed('<a href="https://open.spotify.com/intl-fr/artist/spotify?si=123">Link</a><p>spotify other</p>')
    assert parser.ids == {'spotify'}
    def fail(url): raise OSError('offline')
    monkeypatch.setattr(artist_sites, 'cached_links', fail)
    assert artist_sites.identity_links(entity, 'spotify')[3] is False


def test_dead_official_site_does_not_block_analysis_and_is_cached(context, monkeypatch):
    entity = {'relations': [{'type': 'official homepage', 'url': {'resource': 'https://dead.example/'}}]}
    calls = []
    def dead(url):
        calls.append(url)
        raise artist_sites.UnavailableSite('Official website domain not found')
    monkeypatch.setattr(artist_sites, 'fetch_links', dead)
    for _ in range(2):
        direct, warnings, conflicts, complete = artist_sites.identity_links(entity, 'spotify')
        assert (direct, conflicts, complete) == ([], [], True)
        assert warnings == [{'kind': 'site_unavailable', 'url': 'https://dead.example/'}]
    assert calls == ['https://dead.example/']
    def offline(url): raise TimeoutError('timed out')
    monkeypatch.setattr(artist_sites, 'fetch_links', offline)
    entity['relations'][0]['url']['resource'] = 'https://slow.example/'
    _, warnings, _, complete = artist_sites.identity_links(entity, 'spotify')
    assert complete is False and warnings[0]['temporary'] is True


@pytest.mark.parametrize('failure', ['nxdomain', 'forbidden'])
def test_permanent_site_failures_are_classified(monkeypatch, failure):
    def resolve(*args, **kwargs):
        if failure == 'nxdomain':
            raise socket.gaierror(socket.EAI_NONAME, 'Name or service not known')
        return [(2, 1, 6, '', ('93.184.216.34', 80))]
    class Response:
        status = 403
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    monkeypatch.setattr(artist_sites.http.client.HTTPConnection, 'request', lambda self, *args, **kwargs: None)
    monkeypatch.setattr(artist_sites.http.client.HTTPConnection, 'getresponse', lambda self: Response())
    with pytest.raises(artist_sites.UnavailableSite):
        artist_sites.fetch_links('http://example.org/')
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: (_ for _ in ()).throw(socket.gaierror(socket.EAI_AGAIN, 'Temporary failure')))
    with pytest.raises(socket.gaierror):
        artist_sites.fetch_links('http://example.org/')


def test_official_url_blocks_nonpublic_network(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(2, 1, 6, '', ('127.0.0.1', 80))])
    with pytest.raises(ValueError, match='not public'):
        artist_sites.fetch_links('http://example.org/')


def test_direct_candidate_outside_name_search_and_partial_retry(context, monkeypatch):
    monkeypatch.setattr(mb_client, 'request', lambda path, params=None: {'id': path.split('/')[-1], 'name': 'Legal name'} if path.startswith('artist/') else {})
    monkeypatch.setattr(scoring, 'spotify_releases', lambda artist: [])
    mb_queue.enqueue('alice', 'artist', 'spotify', {'id': 'spotify', 'name': 'Alias'})
    with storage.connect() as db:
        job = dict(db.execute('SELECT * FROM mb_jobs').fetchone())
    mb_queue._process_job(job, {'spotify': {'candidate'}})
    match = mb_queue.get_match('alice', 'artist', 'spotify')
    assert match['mbid'] == 'candidate'
    assert match['match_evidence']['ranked'][0]['score'] == 100
    mb_queue.enqueue('alice', 'artist', 'partial', {'id': 'partial', 'name': 'Alias'})
    with storage.connect() as db:
        db.execute("UPDATE mb_matches SET candidates=? WHERE spotify_id='partial'", (json.dumps([{'id': 'candidate', 'name': 'Alias'}]),))
        job = dict(db.execute("SELECT * FROM mb_jobs WHERE spotify_id='partial'").fetchone())
    monkeypatch.setattr(artist_sites, 'identity_links', lambda entity, spotify_id: (
        [], [{'kind': 'site_unavailable', 'url': 'https://slow.example/', 'temporary': True}], [], False))
    mb_queue._process_job(job, {'partial': set()})
    match = mb_queue.get_match('alice', 'artist', 'partial')
    assert match['status'] == 'ambiguous'
    assert match['match_evidence']['ranked'][0]['complete'] is False
    with storage.connect() as db:
        assert db.execute("SELECT attempts FROM mb_jobs WHERE spotify_id='partial'").fetchone()[0] == 1


def test_unique_direct_link_skips_homonyms_tracks_and_discographies(context, monkeypatch):
    calls = []
    def request(path, params=None):
        calls.append(path)
        return {'id': path.split('/')[-1], 'name': 'Artist'} if path.startswith('artist/') else pytest.fail(path)
    monkeypatch.setattr(mb_client, 'request', request)
    monkeypatch.setattr(scoring, 'spotify_releases', lambda artist: pytest.fail('catalogue'))
    monkeypatch.setattr(scoring, 'track_recordings', lambda track: pytest.fail('recordings'))
    payload, _, _, tracks, *_ = fixtures()
    storage.set_cache('alice', 'liked_collection', {'tracks': tracks})
    mb_queue.enqueue('alice', 'artist', 'spotify', payload)
    with storage.connect() as db:
        db.execute("UPDATE mb_matches SET candidates=?", (json.dumps([{'id': 'homonym', 'name': 'Artist'}]),))
        job = dict(db.execute('SELECT * FROM mb_jobs').fetchone())
    mb_queue._process_job(job, {'spotify': {'candidate'}})
    match = mb_queue.get_match('alice', 'artist', 'spotify')
    assert (match['status'], match['mbid'], match['match_method']) == ('confirmed', 'candidate', 'spotify_url')
    assert [item['id'] for item in match['match_evidence']['ranked']] == ['candidate']
    assert set(calls) == {'artist/candidate'}


def test_unreachable_own_site_does_not_block_a_unique_direct_link(context, monkeypatch):
    monkeypatch.setattr(mb_client, 'request', lambda path, params=None: {'id': path.split('/')[-1], 'name': 'Artist'})
    monkeypatch.setattr(artist_sites, 'identity_links', lambda entity, spotify_id: (
        [], [{'kind': 'site_unavailable', 'url': 'https://own.example/', 'temporary': True}], [], False))
    mb_queue.enqueue('alice', 'artist', 'spotify', {'id': 'spotify', 'name': 'Artist'})
    with storage.connect() as db:
        job = dict(db.execute('SELECT * FROM mb_jobs').fetchone())
    mb_queue._process_job(job, {'spotify': {'candidate'}})
    match = mb_queue.get_match('alice', 'artist', 'spotify')
    assert (match['status'], match['mbid']) == ('confirmed', 'candidate')
    assert match['match_evidence']['errors'] == ['Official website unavailable: https://own.example/']
    monkeypatch.setattr(artist_sites, 'identity_links', lambda entity, spotify_id: (
        [], [], [{'kind': 'official_other_identity', 'url': 'https://own.example/'}], True))
    mb_queue.enqueue('alice', 'artist', 'conflict', {'id': 'conflict', 'name': 'Artist'})
    with storage.connect() as db:
        job = dict(db.execute("SELECT * FROM mb_jobs WHERE spotify_id='conflict'").fetchone())
    mb_queue._process_job(job, {'conflict': {'candidate'}})
    assert mb_queue.get_match('alice', 'artist', 'conflict')['status'] == 'ambiguous'


def test_site_failing_for_more_than_a_day_is_treated_as_gone(context, monkeypatch):
    calls = []
    def unavailable(url):
        calls.append(url)
        raise OSError('Official website HTTP 503')
    monkeypatch.setattr(artist_sites, 'fetch_links', unavailable)
    with pytest.raises(OSError):
        artist_sites.cached_links('https://down.example/')
    with pytest.raises(OSError):
        artist_sites.cached_links('https://down.example/')
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    with storage.connect() as db:
        db.execute("UPDATE mb_http_cache SET payload=? WHERE cache_key='artist-score-site:https://down.example/'",
                   (json.dumps({'failing_since': old}),))
    for _ in range(2):
        with pytest.raises(artist_sites.UnavailableSite):
            artist_sites.cached_links('https://down.example/')
    assert len(calls) == 3


def test_several_direct_links_keep_the_full_analysis(context, monkeypatch):
    calls = []
    def request(path, params=None):
        calls.append(path)
        return {'id': path.split('/')[-1], 'name': 'Artist'} if path.startswith('artist/') else {}
    monkeypatch.setattr(mb_client, 'request', request)
    monkeypatch.setattr(scoring, 'spotify_releases', lambda artist: [])
    mb_queue.enqueue('alice', 'artist', 'spotify', {'id': 'spotify', 'name': 'Artist'})
    with storage.connect() as db:
        job = dict(db.execute('SELECT * FROM mb_jobs').fetchone())
    mb_queue._process_job(job, {'spotify': {'first', 'second'}})
    match = mb_queue.get_match('alice', 'artist', 'spotify')
    assert match['status'] == 'ambiguous'
    assert {'artist', 'release-group'} <= set(calls)


def test_previous_rejected_homonyms_remain_unconfirmed():
    rejections = [{'spotify_id': '0AvC7iOS4Vs2caVtvXvNux', 'name': 'DEGO', 'rejected_mbid': '15a3b89e-f5dd-42ca-8097-f12c3fdc10c3'}, {'spotify_id': '40jfhvzgRabkPQTyTP035m', 'name': 'Cryptic', 'rejected_mbid': '1186cda6-1783-4123-b10d-ff01e5ef6218'}]
    for rejection in rejections:
        args = list(fixtures())
        args[0] = {'id': rejection['spotify_id'], 'name': rejection['name']}
        args[1] = {'id': rejection['rejected_mbid'], 'name': rejection['name']}
        args[3] = args[3][:1]
        args[4] = {'t0': [{**args[4]['t0'][0], 'artist-credit': [{'artist': {'id': rejection['rejected_mbid']}}]}]}
        assert scoring.select([scoring.assess(*args)]) is None


def test_score_job_confirms_then_enriches(context, monkeypatch):
    payload, candidate, entity, tracks, recordings, spotify, releases = fixtures()
    storage.set_cache('alice', 'liked_collection', {'tracks': tracks})
    monkeypatch.setattr(scoring, 'spotify_releases', lambda artist: spotify)
    monkeypatch.setattr(scoring, 'track_recordings', lambda track: recordings[track['spotify_id']])
    def request(path, params=None):
        if path == 'artist':
            return {'artists': [candidate]}
        if path == 'release-group':
            return {'release-groups': releases}
        return {'id': candidate['id'], 'name': 'Artist', 'begin-area': {'id': 'area', 'name': 'Origin'}}
    monkeypatch.setattr(mb_client, 'request', request)
    mb_queue.enqueue('alice', 'artist', payload['id'], payload)
    with storage.connect() as db:
        job = dict(db.execute('SELECT * FROM mb_jobs').fetchone())
    mb_queue._process_job(job, {'spotify': set()})
    result = mb_queue.get_match('alice', 'artist', 'spotify')
    assert result['status'] == 'confirmed'
    assert result['match_method'] == 'score'
    assert result['match_evidence']['ranked'][0]['score'] == 95
    assert result['enriched']['origin']['name'] == 'Origin'
    assert mb_queue.progress_for('alice', 'artist')['completed'] == 1


def test_active_score_progress_is_account_scoped(context):
    for user in ['alice', 'bob']:
        mb_queue.enqueue(user, 'artist', 'spotify', {'name': user})
    with storage.connect() as db:
        db.execute("UPDATE mb_jobs SET state='processing',claimed_at=?", (storage.now(),))
    storage.set_cache('alice', 'artist_score_progress', {'name': 'Artist', 'phase': 'candidates', 'done': 3, 'total': 10}, 'spotify')
    assert mb_queue.progress_for('alice', 'artist')['analysis']['done'] == 3
    assert 'analysis' not in mb_queue.progress_for('bob', 'artist')
    with storage.connect() as db:
        db.execute("UPDATE mb_jobs SET state='queued' WHERE user_id='alice'")
    assert 'analysis' not in mb_queue.progress_for('alice', 'artist')
