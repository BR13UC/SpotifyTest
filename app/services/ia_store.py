"""Durable, account-scoped storage for the local taste laboratory."""
import json
from app import storage

LABELS = ['dislike', 'neutral', 'like', 'love']


def init_db():
    with storage.connect() as db:
        db.executescript('''
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS ia_tracks (
            user_id TEXT NOT NULL, uri TEXT NOT NULL, payload TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY(user_id,uri));
        CREATE TABLE IF NOT EXISTS ia_imports (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, filename TEXT NOT NULL,
            digest TEXT NOT NULL, path TEXT, state TEXT NOT NULL DEFAULT 'queued',
            progress INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
            summary TEXT NOT NULL DEFAULT '{}', error TEXT, created_at TEXT NOT NULL,
            UNIQUE(user_id,digest));
        CREATE TABLE IF NOT EXISTS ia_events (
            user_id TEXT NOT NULL, fingerprint TEXT NOT NULL, occurrence INTEGER NOT NULL,
            uri TEXT NOT NULL, ts TEXT NOT NULL, ms_played INTEGER NOT NULL, context TEXT NOT NULL,
            import_id TEXT NOT NULL REFERENCES ia_imports(id),
            PRIMARY KEY(user_id,fingerprint,occurrence));
        CREATE INDEX IF NOT EXISTS ia_events_track ON ia_events(user_id,uri,ts);
        CREATE TABLE IF NOT EXISTS ia_snapshots (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, source TEXT NOT NULL,
            collection_id TEXT NOT NULL, name TEXT NOT NULL, captured_at TEXT NOT NULL,
            items TEXT NOT NULL, UNIQUE(user_id,source,collection_id,captured_at));
        CREATE TABLE IF NOT EXISTS ia_annotations (
            user_id TEXT NOT NULL, uri TEXT NOT NULL, label TEXT NOT NULL,
            reserved INTEGER NOT NULL DEFAULT 0, selection TEXT NOT NULL,
            updated_at TEXT NOT NULL, PRIMARY KEY(user_id,uri));
        CREATE TABLE IF NOT EXISTS ia_annotation_history (
            id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, uri TEXT NOT NULL,
            label TEXT NOT NULL, reserved INTEGER NOT NULL, selection TEXT NOT NULL,
            created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ia_datasets (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, payload TEXT NOT NULL,
            created_at TEXT NOT NULL, test_used INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS ia_campaigns (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, dataset_id TEXT NOT NULL REFERENCES ia_datasets(id),
            state TEXT NOT NULL, settings TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ia_trials (
            id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES ia_campaigns(id),
            config_key TEXT NOT NULL, config TEXT NOT NULL, split INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'queued', metrics TEXT, duration REAL,
            error TEXT, artifact TEXT, bytes INTEGER,
            UNIQUE(campaign_id,config_key,split));
        CREATE TABLE IF NOT EXISTS ia_models (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, dataset_id TEXT,
            campaign_id TEXT, config_key TEXT, config TEXT NOT NULL, artifact TEXT NOT NULL,
            metrics TEXT NOT NULL, created_at TEXT NOT NULL,
            exploratory INTEGER NOT NULL DEFAULT 0,
            UNIQUE(campaign_id,config_key));
        CREATE TABLE IF NOT EXISTS ia_settings (
            user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
            PRIMARY KEY(user_id,key));
        CREATE TABLE IF NOT EXISTS ia_jobs (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL,
            payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
            error TEXT, created_at TEXT NOT NULL);
        ''')


def rows(sql, args=()):
    with storage.connect() as db:
        return [dict(r) for r in db.execute(sql, args)]


def execute(sql, args=()):
    with storage.connect() as db:
        return db.execute(sql, args).rowcount


def setting(user, key, default=None):
    values = rows('SELECT value FROM ia_settings WHERE user_id=? AND key=?', (user, key))
    return json.loads(values[0]['value']) if values else default


def set_setting(user, key, value):
    execute('INSERT INTO ia_settings VALUES(?,?,?) ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value',
            (user, key, json.dumps(value)))


def upsert_track(db, user, item):
    uri = item.get('uri')
    if not isinstance(uri, str) or not uri.startswith('spotify:track:'):
        return False
    old = db.execute('SELECT payload FROM ia_tracks WHERE user_id=? AND uri=?', (user, uri)).fetchone()
    merged = json.loads(old['payload']) if old else {}
    # A minimal historical record must never erase confirmed metadata.
    for key, value in item.items():
        if value is not None and value != '' and value != []:
            merged[key] = value
    if old and json.loads(old['payload']) == merged:
        return True
    db.execute('INSERT INTO ia_tracks VALUES(?,?,?,?) ON CONFLICT(user_id,uri) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at',
               (user, uri, json.dumps(merged, ensure_ascii=False), storage.now()))
    return True


def tracks(user):
    return [json.loads(r['payload']) for r in rows('SELECT payload FROM ia_tracks WHERE user_id=? ORDER BY uri', (user,))]
