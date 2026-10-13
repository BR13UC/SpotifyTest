"""Concurrency regressions for the shared web/IA/MusicBrainz database."""
import json
import multiprocessing
import sqlite3
import threading

import pytest
from app import create_app, storage
from app.services import ia_data, ia_store, ia_worker


@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING': True, 'DATA_DIR': str(tmp_path), 'SECRET_KEY': 'concurrency-test'})


def test_reader_does_not_block_another_writer(app):
    errors = []
    finished = threading.Event()
    with app.app_context():
        storage.set_cache('alice', 'counter', 1)
        with storage.connect() as reader:
            assert reader.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
            assert reader.execute('PRAGMA busy_timeout').fetchone()[0] == 30000
            reader.execute('BEGIN')
            assert reader.execute("SELECT payload FROM cache_entries WHERE user_id='alice' AND resource='counter'").fetchone()[0] == '1'
            def write():
                try:
                    with app.app_context():
                        storage.set_cache('alice', 'counter', 2)
                except Exception as error:
                    errors.append(error)
                finally:
                    finished.set()
            thread = threading.Thread(target=write)
            thread.start()
            try:
                assert finished.wait(2), 'An open reader blocked the writer commit'
                assert not errors
                # The reader retains its consistent snapshot until its transaction ends.
                assert reader.execute("SELECT payload FROM cache_entries WHERE user_id='alice' AND resource='counter'").fetchone()[0] == '1'
            finally:
                reader.rollback()
                thread.join(2)
        assert storage.get_cache('alice', 'counter')[0] == 2


def test_writer_waits_for_short_competing_transaction(app):
    entered, finished = threading.Event(), threading.Event()
    errors = []
    def write():
        try:
            with app.app_context():
                entered.set()
                storage.set_cache('alice', 'value', 'committed')
        except Exception as error:
            errors.append(error)
        finally:
            finished.set()
    with app.app_context():
        with storage.connect() as holder:
            holder.execute('BEGIN IMMEDIATE')
            thread = threading.Thread(target=write)
            thread.start()
            assert entered.wait(1)
            assert not finished.wait(0.1)
        thread.join(2)
        assert finished.is_set() and not errors
        assert storage.get_cache('alice', 'value')[0] == 'committed'


def test_worker_retry_only_handles_lock_errors(monkeypatch):
    monkeypatch.setattr(storage.time, 'sleep', lambda seconds: None)
    attempts = []
    def operation():
        attempts.append(1)
        if len(attempts) == 1:
            raise sqlite3.OperationalError('database is locked')
        return 'done'
    assert storage.wait_for_database(operation) == 'done'
    assert len(attempts) == 2
    def broken():
        raise sqlite3.OperationalError('no such table: broken')
    with pytest.raises(sqlite3.OperationalError, match='no such table'):
        storage.wait_for_database(broken)
    extended = sqlite3.OperationalError('snapshot conflict')
    extended.sqlite_errorcode = sqlite3.SQLITE_BUSY_SNAPSHOT
    assert storage.is_busy_error(extended)


def test_ia_lock_failure_keeps_task_retryable(app, monkeypatch):
    with app.app_context():
        key = ia_data.job('alice', 'exploratory', {})
        def busy(job):
            raise sqlite3.OperationalError('database is locked')
        monkeypatch.setattr(ia_worker.learning, 'train_exploratory', busy)
        assert ia_worker.process_one()
        row = ia_store.rows('SELECT state,error FROM ia_jobs WHERE id=?', (key,))[0]
        assert row == {'state': 'queued', 'error': None}


def test_snapshot_releases_writer_between_track_batches(app, monkeypatch):
    with app.app_context():
        items = [{'uri': f'spotify:track:{i}', 'name': f'Track {i}'} for i in range(205)]
        storage.set_cache('alice', 'liked_collection', {'tracks': items + items[:2]})
        original = ia_store.upsert_track
        completed_before_batch = []
        def observe(db, user, item):
            with storage.connect() as reader:
                completed_before_batch.append(reader.execute("SELECT count(*) FROM ia_tracks WHERE user_id='alice'").fetchone()[0])
            return original(db, user, item)
        monkeypatch.setattr(ia_store, 'upsert_track', observe)
        assert ia_data.sync_local('alice') == 1
        assert completed_before_batch[0] == 0
        assert completed_before_batch[100] == 100
        assert completed_before_batch[200] == 200
        assert len(ia_store.tracks('alice')) == 205
        snapshot = ia_store.rows('SELECT items FROM ia_snapshots')[0]
        assert len(json.loads(snapshot['items'])) == 207


def _parallel_app(path, barrier, output):
    """Spawn an independent process, as separate Docker services do."""
    try:
        barrier.wait(timeout=10)
        app = create_app({'TESTING': True, 'DATA_DIR': path, 'SECRET_KEY': 'parallel-test'})
        with app.app_context():
            with storage.connect() as db:
                mode = db.execute('PRAGMA journal_mode').fetchone()[0]
            storage.set_cache(str(multiprocessing.current_process().pid), 'started', True)
            output.put(('ok', mode))
    except Exception as error:
        output.put(('error', repr(error)))


def test_three_processes_upgrade_rollback_database_together(app):
    with app.app_context(), storage.connect() as db:
        assert db.execute('PRAGMA journal_mode=DELETE').fetchone()[0] == 'delete'
    ctx = multiprocessing.get_context('spawn')
    barrier, output = ctx.Barrier(3), ctx.Queue()
    processes = [ctx.Process(target=_parallel_app, args=(app.config['DATA_DIR'], barrier, output)) for _ in range(3)]
    try:
        for process in processes:
            process.start()
        results = [output.get(timeout=20) for _ in processes]
        assert results == [('ok', 'wal')] * 3
    finally:
        for process in processes:
            process.join(5)
            if process.is_alive():
                process.terminate()
                process.join(5)
        output.close()
    assert all(p.exitcode == 0 for p in processes)
