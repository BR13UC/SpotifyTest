"""Local AI contracts: export identity, isolation, leakage and worker lifecycle."""
import io
import json
import zipfile
from pathlib import Path

import pytest
from app import create_app, spotify_auth, storage
from app.services import ia_data as data, ia_learning as learning, ia_store as store, ia_worker as worker


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = create_app({'TESTING': True, 'DATA_DIR': str(tmp_path), 'SECRET_KEY': 'test'})
    monkeypatch.setattr(spotify_auth, 'is_valid_token', lambda: False)
    monkeypatch.setattr(spotify_auth, 'get_spotify', lambda: pytest.fail('Implicit Spotify request'))
    return app


def client_for(app, user='alice'):
    client = app.test_client()
    with client.session_transaction() as session:
        session['spotify_user_id'] = user
        session['csrf_token'] = 'csrf'
    return client


def post(client, route, payload=None):
    return client.post('/ia/api/' + route, json=payload or {}, headers={'X-CSRF-Token': 'csrf'})


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        for name, rows in files.items():
            z.writestr(name, json.dumps(rows))
    buffer.seek(0)
    return buffer


def event(uri='spotify:track:one', **fields):
    return {'spotify_track_uri': uri, 'ts': '2024-01-01T12:00:00Z', 'ms_played': 1200,
            'master_metadata_track_name': 'Title', 'master_metadata_album_artist_name': 'Artist',
            'platform': 'desktop', 'ip_addr': 'do-not-store', 'skipped': False, **fields}


def upload(client, files, filename='history.zip'):
    return client.post('/ia/api/imports', data={'archive': (archive(files), filename)}, headers={'X-CSRF-Token': 'csrf'})


def seed(app, user='alice', development=8, reserved=2):
    with app.app_context(), storage.connect() as db:
        for label in store.LABELS:
            for i in range(development + reserved):
                uri = f'spotify:track:{label}{i}'
                row = {'uri': uri, 'spotify_id': uri.split(':')[-1], 'name': f'Track {label} {i}', 'artist_names': [f'Artist {label} {i}'],
                       'artists': [{'id': f'artist-{label}-{i}', 'name': f'Artist {i}'}], 'duration_ms': 100000 + i*5000, 'release_date': '2024-01-01'}
                store.upsert_track(db, user, row)
                db.execute('INSERT INTO ia_annotations VALUES(?,?,?,?,?,?)', (user, uri, label, int(i >= development), json.dumps({'mode': 'evaluation' if i >= development else 'varied'}), storage.now()))


def small_grid(**extra):
    return {'models': ['logistic', 'forest'], 'periods': ['all'], 'musicbrainz': [False],
            'logistic': {'C': [1], 'class_weight': [None]},
            'forest': {'n_estimators': [10], 'max_depth': [None], 'min_samples_leaf': [1], 'class_weight': [None]},
            'max_configurations': 2, **extra}


def test_local_session_csrf_and_navigation(app):
    anonymous = app.test_client()
    assert anonymous.get('/ia').status_code == 302
    assert anonymous.get('/ia/api/summary').status_code == 401
    client = client_for(app)
    assert client.get('/ia').status_code == 200
    assert client.get('/settings').status_code == 200
    assert client.get('/sorts').status_code == 200
    assert client.get('/home').status_code == 200
    assert client.get('/sorts/get_sort_options').status_code == 401
    assert client.post('/ia/api/exploratory', json={}).status_code == 403
    assert client.get('/ia/api/profile').json['id'] == 'alice'
    html = client.get('/ia').get_data(as_text=True)
    nav = html.split('id="site-nav"')[1].split('</nav>')[0]
    assert 'Accueil</a>' not in nav and 'Avancement</a>' not in nav
    assert 'IA</a>' in nav
    assert 'Ouvrir Avancement' in client.get('/settings').get_data(as_text=True)


def test_import_multiplicity_overlap_missing_and_privacy(app):
    client = client_for(app)
    files = {'Streaming_History_Audio_2024.json': [event(), event(), event(spotify_episode_uri='episode'), event(uri=None), event(ms_played=-1)],
             'Streaming_History_Audio_2024_1.json': [event()], 'Streaming_History_Video_2024.json': [event()]}
    response = upload(client, files)
    assert response.status_code == 202
    with app.app_context():
        assert worker.process_one()
        assert len(store.rows('SELECT * FROM ia_events')) == 3
        assert not list((Path(app.config['DATA_DIR'])/'ia/imports').glob('*.zip'))
        assert 'do-not-store' not in json.dumps(store.rows('SELECT * FROM ia_events'))
        summary = data.summary('alice')
        assert summary['imports'][0]['state'] == 'completed'
        counts = json.loads(summary['imports'][0]['summary'])
        assert counts['added'] == 3 and counts['non_music'] == 1 and counts['missing_uri'] == 1 and counts['invalid'] == 1
    # Exact archive reimport returns the same record and leaves no uploaded copy.
    assert upload(client, files).json['id'] == response.json['id']
    overlap = upload(client, {'Streaming_History_Audio_2024.json': [event()]*4 + [event(ts='2024-02-02T00:00:00Z')]})
    assert overlap.status_code == 202
    with app.app_context():
        assert worker.process_one()
        assert len(store.rows('SELECT * FROM ia_events')) == 5
    assert client_for(app,'bob').get('/ia/api/summary').json['history']['events'] == 0


def test_failed_import_retry_and_recovery(app):
    client=client_for(app)
    r=upload(client, {'Streaming_History_Audio_2024.json': {'wrong': 'shape'}})
    with app.app_context():
        assert worker.process_one()
        row=store.rows('SELECT * FROM ia_imports WHERE id=?',(r.json['id'],))[0]
        assert row['state']=='failed' and Path(row['path']).exists()
        store.execute("UPDATE ia_imports SET state='running' WHERE id=?",(row['id'],))
        worker.recover()
        assert store.rows('SELECT state FROM ia_imports WHERE id=?',(row['id'],))[0]['state']=='queued'
    assert client_for(app,'bob').post(f"/ia/api/imports/{r.json['id']}/retry",headers={'X-CSRF-Token':'csrf'}).status_code == 400


def test_annotations_abstention_and_server_reserved_batches(app):
    seed(app)
    client=client_for(app)
    with app.app_context(), storage.connect() as db:
        store.upsert_track(db,'alice',{'uri':'spotify:track:new','spotify_id':'new','name':'New'})
    assert post(client,'candidates',{'mode':'evaluation','limit':1}).json['tracks'][0]['uri']=='spotify:track:new'
    assert post(client,'annotations',{'uri':'spotify:track:new','label':'unknown'}).status_code == 200
    assert post(client,'annotations',{'uri':'spotify:track:new','label':'like'}).status_code == 200
    with app.app_context():
        row=store.rows('SELECT * FROM ia_annotations WHERE uri=?',('spotify:track:new',))[0]
        assert row['reserved']==1 and row['label']=='like'
        assert len(store.rows('SELECT * FROM ia_annotation_history WHERE uri=?',('spotify:track:new',)))==2
    assert post(client_for(app,'bob'),'annotations',{'uri':'spotify:track:new','label':'love'}).status_code==400
    assert post(client,'annotations',{'uri':'spotify:track:like0','label':'neutral','reserve':True}).status_code==400


def test_snapshot_does_not_refresh_and_preserves_repeated_tracks(app):
    client=client_for(app)
    with app.app_context():
        storage.set_cache('alice','playlists',[{'id':'p','name':'Playlist'}])
        row={'uri':'spotify:track:one','spotify_id':'one','name':'Title','type':'track','artists':[]}
        storage.replace_playlist_items('alice','p',[row,row,{'type':'unknown','uri':None}])
    assert post(client,'sync-local').status_code==200
    snapshots=client.get('/ia/api/collections').json['collections']
    r=post(client,'predict',{'snapshot_id':snapshots[0]['id']})
    assert r.status_code==200
    assert len(r.json['tracks'])==2 and r.json['excluded']==1
    assert post(client_for(app,'bob'),'predict',{'snapshot_id':snapshots[0]['id']}).status_code==404
    assert post(client,'sync-local').status_code==200
    assert len(client.get('/ia/api/collections').json['collections'])==1


def test_grid_counts_deduplication_and_limits(app):
    with app.app_context():
        g=learning.grid({})
        assert g['configurations']==88 and g['trainings']==264 and g['reference_runs']==12
        duplicate=learning.grid(small_grid(periods=['all','all']))
        assert duplicate['configurations']==2 and duplicate['references']==1
        assert not learning.grid(small_grid(max_configurations=1))['within_limit']
        with pytest.raises(Exception):
            learning.grid(small_grid(forest={'n_estimators':[0]}))
    seed(app)
    assert post(client_for(app),'campaigns',small_grid(max_configurations=1)).status_code==400


def test_frozen_splits_and_target_features_do_not_leak(app):
    seed(app)
    with app.app_context():
        key=learning.dataset('alice')
        frozen=learning.load_dataset('alice',key)
        test=set(frozen['test'])
        for split in frozen['splits']:
            assert not test & set(split['train']+split['validation'])
            assert not set(split['train']) & set(split['validation'])
        for split in frozen['artist_holdouts']:
            a=set().union(*(learning.artist_ids(r) for r in frozen['rows'] if r['equivalence'] in split['train']))
            b=set().union(*(learning.artist_ids(r) for r in frozen['rows'] if r['equivalence'] in split['validation']))
            assert not a & b
        row=frozen['rows'][0]
        model=learning.TasteModel({'model':'artist','period':'all','musicbrainz':False,'params':{}})
        model.profile([{**row,'history_count':100000}])
        own=model.features(row,True)
        assert own['artist_known']==0 and own['history_artist']==0
        mutated={**row,'ms_played':999,'liked':True,'playlist_count':100,'label':'love'}
        assert model.features(mutated)==model.features(row)
        data.annotate('alice',{'uri':row['uri'],'label':'unknown'})
        assert learning.load_dataset('alice',key)==frozen


def test_campaign_training_finalize_activate_and_rollback(app):
    seed(app)
    client=client_for(app)
    response=post(client,'campaigns',small_grid())
    assert response.status_code==202
    with app.app_context():
        for _ in range(9):
            assert worker.process_one()
        assert not worker.process_one()
    results=client.get('/ia/api/results').json
    assert results['campaigns'][0]['state']=='completed'
    assert len(results['results'])==3 and all(r['complete'] for r in results['results'])
    chosen=results['results'][0]
    assert post(client,'finalize',{'campaign_id':chosen['campaign_id'],'config_key':chosen['config_key']}).status_code==202
    with app.app_context():
        assert worker.process_one()
    models=client.get('/ia/api/results').json['models']
    assert len(models)==1 and models[0]['metrics']['n']==8
    assert post(client,'activate',{'model_id':models[0]['id']}).status_code==200
    assert post(client_for(app,'bob'),'activate',{'model_id':models[0]['id']}).status_code==404
    assert client.get('/ia/api/results').json['datasets'][0]['test_used']
    assert post(client,'exploratory').status_code==202
    with app.app_context():
        assert worker.process_one()
        assert store.setting('alice','active_model')==models[0]['id']
        predicted=learning.predictions('alice',data.catalog('alice')[:2])
        assert all(p['score'] is not None and p['factors'] for p in predicted)
    exploratory=next(m for m in client.get('/ia/api/results').json['models'] if m['exploratory'])
    assert post(client,'activate',{'model_id':exploratory['id']}).status_code==404
    assert 'f1_macro' in client.get('/ia/api/results.csv').get_data(as_text=True)


def test_pause_stop_recovery_partial_failure(app,monkeypatch):
    seed(app)
    client=client_for(app)
    key=post(client,'campaigns',small_grid()).json['id']
    assert post(client,f'campaigns/{key}/pause').json['state']=='paused'
    with app.app_context():
        assert not worker.process_one()
    assert post(client,f'campaigns/{key}/resume').json['state']=='running'
    original=learning.run_trial
    def fail_once(trial,campaign):
        monkeypatch.setattr(learning,'run_trial',original)
        raise RuntimeError('Synthetic failure')
    monkeypatch.setattr(learning,'run_trial',fail_once)
    with app.app_context():
        for _ in range(9):
            assert worker.process_one()
    result=client.get('/ia/api/results').json
    assert result['campaigns'][0]['state']=='incomplete'
    assert any(not r['complete'] for r in result['results'])
    assert post(client,f'campaigns/{key}/retry').status_code==200
    with app.app_context():
        assert worker.process_one()
    assert client.get('/ia/api/results').json['campaigns'][0]['state']=='completed'
    other=post(client,'campaigns',small_grid()).json['id']
    assert post(client,f'campaigns/{other}/stop').json['state']=='stopped'
    assert post(client,f'campaigns/{other}/resume').status_code==400


def test_invalid_requests_and_unavailable_account_export(app):
    client=client_for(app)
    assert client.post('/ia/api/grid',json=[],headers={'X-CSRF-Token':'csrf'}).status_code==400
    response=upload(client, {'YourLibrary.json': {'tracks': []}})
    assert response.status_code==400 and 'extended audio' in response.json['error_en']
    # An oversized decompressed entry is rejected without reading its content.
    with app.app_context():
        class Archive:
            def infolist(self):
                item=zipfile.ZipInfo('Streaming_History_Audio_2024.json')
                item.file_size=65*1024*1024
                return [item]
        with pytest.raises(ValueError):
            data.validate_archive(Archive())


def test_equivalent_credits_join_artist_holdouts_and_test_reuse(app):
    seed(app)
    with app.app_context():
        frozen=learning.load_dataset('alice',learning.dataset('alice'))
        dev=[r for r in frozen['rows'] if r['equivalence'] in frozen['development']]
        dev[1]['equivalence']=dev[0]['equivalence']
        frozen['artist_holdouts']=learning.artist_holdouts(frozen)
        for split in frozen['artist_holdouts']:
            assert not set(split['train']) & set(split['validation'])
            train=set().union(*(learning.artist_ids(r) for r in dev if r['equivalence'] in split['train']))
            val=set().union(*(learning.artist_ids(r) for r in dev if r['equivalence'] in split['validation']))
            assert not train & val
        key=learning.dataset('alice')
        store.execute('UPDATE ia_datasets SET test_used=1 WHERE id=?',(key,))
        repeated=learning.dataset('alice')
        assert store.rows('SELECT test_used FROM ia_datasets WHERE id=?',(repeated,))[0]['test_used']==1


def test_resume_campaign_paused_after_last_trial(app):
    seed(app)
    client=client_for(app)
    key=post(client,'campaigns',small_grid()).json['id']
    with app.app_context():
        store.execute("UPDATE ia_trials SET state='completed' WHERE campaign_id=?",(key,))
        store.execute("UPDATE ia_campaigns SET state='paused' WHERE id=?",(key,))
    assert post(client,f'campaigns/{key}/resume').json['state']=='completed'


def test_model_reproducibility_and_failed_save_preserves_active(app,monkeypatch):
    seed(app)
    with app.app_context():
        frozen=learning.load_dataset('alice',learning.dataset('alice'))
        config=learning.grid(small_grid())['configs'][1]
        split=frozen['splits'][0]
        train=learning.prepared_rows(frozen,split['train'],config)
        val=learning.prepared_rows(frozen,split['validation'],config)
        first=learning.TasteModel(config,17).fit(train)
        second=learning.TasteModel(config,17).fit(train)
        assert first.predict(val)==second.predict(val)
        store.set_setting('alice','active_model','previous-model')
        data.job('alice','exploratory',{})
        monkeypatch.setattr(learning,'save_artifact',lambda *args: (_ for _ in ()).throw(OSError('Synthetic disk failure')))
        assert worker.process_one()
        assert store.setting('alice','active_model')=='previous-model'
        assert store.rows('SELECT state FROM ia_jobs')[0]['state']=='failed'
        assert not store.rows('SELECT * FROM ia_models')


def test_five_year_window_keeps_end_of_month_exact(app):
    seed(app)
    client=client_for(app)
    upload(client,{'Streaming_History_Audio_2026.json':[
        event(uri='spotify:track:anchor',ts='2026-03-31T12:00:00Z'),
        event(uri='spotify:track:like0',ts='2021-03-30T12:00:00Z'),
    ]})
    with app.app_context():
        assert worker.process_one()
        frozen=learning.load_dataset('alice',learning.dataset('alice'))
        assert frozen['history_counts']['spotify:track:like0']=={'all':1,'five_years':0}


def test_explicit_enrichment_queues_imported_recording_without_spotify(app):
    client=client_for(app)
    upload(client,{'Streaming_History_Audio_2024.json':[event()]})
    with app.app_context():
        assert worker.process_one()
    assert post(client,'enrich',{'uris':['spotify:track:one']}).status_code==202
    with app.app_context():
        assert worker.process_one()
        jobs=store.rows('SELECT kind,spotify_id FROM mb_jobs WHERE user_id=?',('alice',))
        assert jobs==[{'kind':'recording','spotify_id':'one'}]
    assert post(client,'enrich',{'uris':['spotify:track:one'],'spotify':True}).status_code==401
    with app.app_context():
        storage.set_cache('alice','playlists',[{'id':'p','name':'Que des bons sons'}])
        row=store.tracks('alice')[0]
        storage.replace_playlist_items('alice','p',[row,row])
    post(client,'sync-local')
    snapshot=client.get('/ia/api/collections').json['collections'][0]['id']
    assert client.get('/ia/api/collections/'+snapshot).json['uris']==['spotify:track:one']
    assert client_for(app,'bob').get('/ia/api/collections/'+snapshot).status_code==404
    candidates=post(client,'candidates',{'mode':'favorites'}).json['tracks']
    assert candidates[0]['playlist_count']==1 and candidates[0]['favorite_playlist']

@pytest.mark.parametrize('mode', ['varied', 'favorites', 'evaluation', 'uncertain', 'review'])
def test_candidate_session_exclusions_and_exhaustion(app, monkeypatch, mode):
    seed(app)
    with app.app_context(), storage.connect() as db:
        for i in range(25):
            store.upsert_track(db, 'alice', {'uri': f'spotify:track:new{i}', 'spotify_id': f'new{i}',
                                           'name': f'New {i}', 'artist_names': [f'New artist {i}']})
    # Assert exclusions happen before even the uncertainty scoring pool.
    scored = []
    def predictions(user, tracks, **kwargs):
        scored.extend(t['uri'] for t in tracks)
        return [{'uri': t['uri'], 'score': .5} for t in tracks]
    monkeypatch.setattr(learning, 'predictions', predictions)
    client = client_for(app)
    excluded = ['spotify:track:dislike0', 'spotify:track:new0']
    seen = set(excluded)
    batches = []
    while True:
        response = post(client, 'candidates', {'mode': mode, 'exclude_uris': list(seen)})
        assert response.status_code == 200
        tracks = response.json['tracks']
        if not tracks:
            break
        uris = {t['uri'] for t in tracks}
        assert len(uris) == len(tracks) <= 12
        assert not uris & seen
        batches.append(tracks)
        seen.update(uris)
    assert sum(map(len, batches)) == (39 if mode == 'review' else 24)
    assert not set(excluded) & set(scored)
    assert post(client_for(app, 'bob'), 'candidates', {'mode': mode}).json['tracks'] == []


@pytest.mark.parametrize('excluded', [None, 'spotify:track:a', [1], ['spotify:episode:a']])
def test_candidate_exclusions_validation(app, excluded):
    assert post(client_for(app), 'candidates', {'exclude_uris': excluded}).status_code == 400


def test_review_replaces_old_abstention_preserving_test_status_and_history(app):
    seed(app)
    client = client_for(app)
    uri = 'spotify:track:love9'
    assert post(client, 'annotations', {'uri': uri, 'label': 'unknown'}).status_code == 200
    tracks = post(client, 'candidates', {'mode': 'review', 'limit': 40}).json['tracks']
    old = next(t for t in tracks if t['uri'] == uri)
    assert old['annotation']['label'] == 'unknown' and old['annotation']['reserved']
    assert post(client, 'annotations', {'uri': uri, 'label': 'neutral'}).status_code == 200
    with app.app_context():
        saved = store.rows('SELECT * FROM ia_annotations WHERE user_id=? AND uri=?', ('alice', uri))[0]
        history = store.rows('SELECT label,reserved FROM ia_annotation_history WHERE user_id=? AND uri=? ORDER BY id', ('alice', uri))
    assert saved['label'] == 'neutral' and saved['reserved']
    assert [row['label'] for row in history] == ['unknown', 'neutral']
    assert all(row['reserved'] for row in history)


def test_automatic_enrichment_expired_token_does_not_block_or_duplicate_jobs(app):
    seed(app)
    client = client_for(app)
    payload = {'uris': ['spotify:track:love0'], 'spotify': True, 'automatic': True}
    first = post(client, 'enrich', payload)
    assert first.status_code == 202 and first.json['spotify_status'] == 'expired'
    second = post(client, 'enrich', payload)
    assert second.status_code == 202 and second.json['id'] is None
    assert post(client, 'enrich', {**payload, 'automatic': False}).status_code == 401
    with app.app_context():
        assert len(store.rows("SELECT * FROM ia_jobs WHERE kind='enrich'")) == 1
        assert len(store.rows('SELECT * FROM ia_annotations')) == 40
    assert client.get('/ia/api/metadata?uri=spotify:track:love0').json['tracks'][0]['name'] == 'Track love 0'
    assert client_for(app, 'bob').get('/ia/api/metadata?uri=spotify:track:love0').json['tracks'] == []
    assert client.post('/ia/api/enrich', json=payload).status_code == 403


def test_automatic_enrichment_fetches_missing_metadata_and_catches_up_old_opinions(app, monkeypatch):
    seed(app)
    with app.app_context(), storage.connect() as db:
        for label in store.LABELS:
            uri=f'spotify:track:{label}0'
            db.execute('UPDATE ia_tracks SET payload=? WHERE user_id=? AND uri=?',
                       (json.dumps({'uri':uri,'spotify_id':uri.split(':')[-1],'name':'Old','artist_names':['Old artist']}),'alice',uri))
        store.upsert_track(db,'alice',{'uri':'spotify:track:current','spotify_id':'current','name':'Current','artist_names':['Artist']})
    calls=[]
    class Spotify:
        def tracks(self, ids):
            calls.append(ids)
            return {'tracks':[{'uri':'spotify:track:'+key,'id':key,'type':'track','name':'Completed '+key,
                               'artists':[{'id':'artist-'+key,'name':'Artist '+key}],
                               'album':{'release_date':'2024-01-01'},'duration_ms':180000} for key in ids]}
    monkeypatch.setattr(spotify_auth,'is_valid_token',lambda:True)
    monkeypatch.setattr(spotify_auth,'get_spotify',lambda:Spotify())
    client=client_for(app)
    response=post(client,'enrich',{'uris':['spotify:track:current'],'spotify':True,'automatic':True})
    assert response.status_code==202 and response.json['spotify_status']=='completed'
    assert set(calls[0])=={'current','dislike0','neutral0','like0','love0'}
    assert len(client.get('/ia/api/metadata?uri=spotify:track:current').json['tracks'][0]['artists'])==1
    post(client,'enrich',{'uris':['spotify:track:current'],'spotify':True,'automatic':True})
    assert len(calls)==1  # Cached tracks and completed backlog are not fetched again.
    with app.app_context():
        assert len(store.rows('SELECT * FROM ia_annotations'))==40
        assert not store.rows('SELECT * FROM ia_annotation_history')


def test_automatic_spotify_failure_still_queues_musicbrainz(app, monkeypatch):
    with app.app_context(), storage.connect() as db:
        store.upsert_track(db,'alice',{'uri':'spotify:track:missing','spotify_id':'missing','name':'Missing'})
    monkeypatch.setattr(spotify_auth,'is_valid_token',lambda:True)
    def unavailable():raise RuntimeError('Synthetic API failure')
    monkeypatch.setattr(spotify_auth,'get_spotify',unavailable)
    response=post(client_for(app),'enrich',{'uris':['spotify:track:missing'],'spotify':True,'automatic':True})
    assert response.status_code==202 and response.json['spotify_status']=='unavailable'
    with app.app_context():
        assert store.rows("SELECT * FROM ia_jobs WHERE kind='enrich'")[0]['state']=='queued'


def test_catalogue_backfill_worker_enriches_without_user_token_and_preserves_opinions(app, monkeypatch):
    from app.services import musicbrainz
    seed(app)
    with app.app_context(), storage.connect() as db:
        for user in ['alice','bob']:
            db.execute('UPDATE ia_tracks SET payload=? WHERE user_id=? AND uri=?',
                       (json.dumps({'uri':'spotify:track:love0','spotify_id':'love0','name':'Old','type':'track','exportable':True}),user,'spotify:track:love0'))
        store.upsert_track(db,'bob',{'uri':'spotify:track:love0','spotify_id':'love0','name':'Other account'})
    class Catalogue:
        def tracks(self, ids):
            assert ids==['love0']
            return {'tracks':[{'uri':'spotify:track:love0','id':'love0','type':'track','name':'Completed',
                               'artists':[{'id':'artist','name':'Artist'}], 'duration_ms':180000,
                               'album':{'release_date':'2020-01-01'}}]}
    monkeypatch.setattr(musicbrainz,'_spotify_catalogue_client',lambda:Catalogue())
    queued=[]
    monkeypatch.setattr(musicbrainz,'enqueue_playlist',lambda user,items:queued.append((user,items)))
    with app.app_context():
        data.job('alice','enrich',{'uris':['spotify:track:love0'],'spotify_catalogue':True})
        assert worker.process_one()
        assert store.tracks('bob')[0]['name']=='Other account'
        saved=next(t for t in store.tracks('alice') if t['uri']=='spotify:track:love0')
        assert saved['name']=='Completed' and saved['artists'][0]['id']=='artist'
        assert len(store.rows('SELECT * FROM ia_annotations WHERE user_id=?',('alice',)))==40
        assert not store.rows('SELECT * FROM ia_annotation_history')
        assert store.rows('SELECT state FROM ia_jobs')[0]['state']=='completed'
    assert queued[0][0]=='alice' and queued[0][1][0]['name']=='Completed'
