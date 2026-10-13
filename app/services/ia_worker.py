"""Single durable IA worker. Run separately from Flask and MusicBrainz."""
import fcntl
import json
import time
from pathlib import Path

from flask import current_app
from app import create_app, storage
from app.services import ia_data as data, ia_learning as learning, ia_store as store, mb_queue, spotify_catalogue


def recover():
    # Called only after obtaining the exclusive worker lock.
    store.execute("UPDATE ia_imports SET state='queued' WHERE state='running'")
    store.execute("UPDATE ia_jobs SET state='queued' WHERE state='running'")
    store.execute("UPDATE ia_trials SET state=CASE WHEN campaign_id IN (SELECT id FROM ia_campaigns WHERE state='stopped') THEN 'cancelled' ELSE 'queued' END WHERE state='running'")


def enrich(job):
    payload = json.loads(job['payload'])
    # User-session refresh happens in the authenticated request. A deliberate
    # catalogue backfill uses the existing public catalogue client instead.
    uris = payload['uris']
    items = [r for r in store.tracks(job['user_id']) if r['uri'] in uris]
    if payload.get('spotify_catalogue'):
        from app.services.playlist_data import normalize_item
        missing = [item for item in items if not (
            any(a.get('id') for a in item.get('artists') or []) and
            item.get('duration_ms') and item.get('release_date'))]
        if missing:
            client = spotify_catalogue._spotify_catalogue_client()
            for offset in range(0, len(missing), 50):
                tracks = client.tracks([item['spotify_id'] for item in missing[offset:offset + 50]])['tracks']
                with storage.connect() as db:
                    for track in tracks:
                        if track and track.get('uri') in uris:
                            store.upsert_track(db, job['user_id'], normalize_item({'track': track}, 0))
            items = [r for r in store.tracks(job['user_id']) if r['uri'] in uris]
    mb_queue.enqueue_playlist(job['user_id'], items)


def process_one():
    record, kind = None, None
    with storage.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        record = db.execute("SELECT * FROM ia_imports WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
        if record:
            kind = 'import'
            db.execute("UPDATE ia_imports SET state='running' WHERE id=?", (record['id'],))
        else:
            record = db.execute("SELECT * FROM ia_jobs WHERE state='queued' ORDER BY created_at LIMIT 1").fetchone()
            if record:
                kind = 'job'
                db.execute("UPDATE ia_jobs SET state='running' WHERE id=?", (record['id'],))
            else:
                record = db.execute("SELECT t.* FROM ia_trials t JOIN ia_campaigns c ON c.id=t.campaign_id WHERE t.state='queued' AND c.state='running' ORDER BY c.created_at,t.config_key,t.split LIMIT 1").fetchone()
                if record:
                    kind = 'trial'
                    db.execute("UPDATE ia_trials SET state='running' WHERE id=?", (record['id'],))
    if not record:
        return False
    record = dict(record)
    try:
        if kind == 'import':
            data.import_archive(record)
        elif kind == 'job':
            {'finalize': learning.train_final, 'exploratory': learning.train_exploratory, 'enrich': enrich}[record['kind']](record)
            store.execute("UPDATE ia_jobs SET state='completed',error=NULL WHERE id=?", (record['id'],))
        else:
            campaign = store.rows('SELECT * FROM ia_campaigns WHERE id=?', (record['campaign_id'],))[0]
            learning.run_trial(record, campaign)
    except Exception as exc:
        if storage.is_busy_error(exc):
            # A temporary lock is not an invalid import or failed training.
            # Restart interrupted work idempotently once the database is free.
            storage.wait_for_database(recover)
            return True
        current_app.logger.exception('IA %s failed (%s)', kind, record['id'])
        table = {'import': 'ia_imports', 'job': 'ia_jobs', 'trial': 'ia_trials'}[kind]
        store.execute(f"UPDATE {table} SET state='failed',error=? WHERE id=?", (str(exc)[:1000], record['id']))
    if kind == 'trial':
        with storage.connect() as db:
            remaining = db.execute("SELECT count(*) FROM ia_trials WHERE campaign_id=? AND state IN ('queued','running')", (record['campaign_id'],)).fetchone()[0]
            failed = db.execute("SELECT count(*) FROM ia_trials WHERE campaign_id=? AND state='failed'", (record['campaign_id'],)).fetchone()[0]
            if not remaining:
                db.execute("UPDATE ia_campaigns SET state=? WHERE id=? AND state='running'", ('incomplete' if failed else 'completed', record['campaign_id']))
    return True


def main():
    app = storage.wait_for_database(create_app)
    with app.app_context():
        folder = Path(app.config['DATA_DIR']) / 'ia'
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / 'worker.lock').open('w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SystemExit('Un worker IA utilise déjà ce répertoire de données.')
            storage.wait_for_database(recover)
            while True:
                try:
                    work = process_one()
                except Exception as exc:
                    if not storage.is_busy_error(exc):
                        raise
                    current_app.logger.warning('IA queue temporarily busy; recovering interrupted work')
                    storage.wait_for_database(recover)
                    work = False
                if not work:
                    time.sleep(2)


if __name__ == '__main__':
    main()
