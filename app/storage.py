"""Account-scoped SQLite cache for Spotify responses."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from flask import current_app


def now():
    return datetime.now(timezone.utc).isoformat()


def is_stale(updated_at, max_age=timedelta(days=1)):
    if not updated_at:
        return True
    try:
        updated = datetime.fromisoformat(updated_at)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - updated > max_age
    except (TypeError, ValueError):
        return True


@contextmanager
def connect():
    connection = sqlite3.connect(current_app.config["DATABASE_PATH"])
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db():
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS cache_entries (
                user_id TEXT NOT NULL,
                resource TEXT NOT NULL,
                cache_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, resource, cache_key)
            );
            CREATE TABLE IF NOT EXISTS playlist_items (
                user_id TEXT NOT NULL,
                playlist_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (user_id, playlist_id, position)
            );
            CREATE TABLE IF NOT EXISTS followed_artists (
                user_id TEXT NOT NULL,
                artist_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (user_id, artist_id)
            );
            CREATE TABLE IF NOT EXISTS mb_matches (
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                spotify_id TEXT NOT NULL,
                spotify_payload TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                mbid TEXT,
                candidates TEXT NOT NULL DEFAULT '[]',
                enriched TEXT,
                manual INTEGER NOT NULL DEFAULT 0,
                match_method TEXT,
                match_evidence TEXT,
                match_version INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, kind, spotify_id)
            );
            CREATE TABLE IF NOT EXISTS mb_jobs (
                user_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                spotify_id TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued',
                available_at TEXT NOT NULL,
                claimed_at TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, kind, spotify_id)
            );
            CREATE TABLE IF NOT EXISTS mb_http_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mb_rate_limit (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                next_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS geo_areas (
                area_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'queued',
                latitude REAL,
                longitude REAL,
                country_code TEXT,
                area_type TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                available_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                resolver_version INTEGER NOT NULL DEFAULT 0
            );
        """)
        columns = {row["name"] for row in db.execute("PRAGMA table_info(mb_matches)")}
        for name, declaration in (
            ("match_method", "TEXT"),
            ("match_evidence", "TEXT"),
            ("match_version", "INTEGER NOT NULL DEFAULT 0"),
        ):
            if name not in columns:
                db.execute(f"ALTER TABLE mb_matches ADD COLUMN {name} {declaration}")
        geo_columns = {row["name"] for row in db.execute("PRAGMA table_info(geo_areas)")}
        if "resolver_version" not in geo_columns:
            db.execute("ALTER TABLE geo_areas ADD COLUMN resolver_version INTEGER NOT NULL DEFAULT 0")


def get_cache(user_id, resource, cache_key=""):
    with connect() as db:
        row = db.execute(
            "SELECT payload, updated_at FROM cache_entries WHERE user_id=? AND resource=? AND cache_key=?",
            (user_id, resource, cache_key),
        ).fetchone()
    return (json.loads(row["payload"]), row["updated_at"]) if row else (None, None)


def set_cache(user_id, resource, payload, cache_key=""):
    with connect() as db:
        db.execute(
            "INSERT INTO cache_entries VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id, resource, cache_key) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
            (user_id, resource, cache_key, json.dumps(payload), now()),
        )


def get_playlist_items(user_id, playlist_id):
    with connect() as db:
        rows = db.execute(
            "SELECT payload FROM playlist_items WHERE user_id=? AND playlist_id=? ORDER BY position",
            (user_id, playlist_id),
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def replace_playlist_items(user_id, playlist_id, items):
    with connect() as db:
        db.execute("DELETE FROM playlist_items WHERE user_id=? AND playlist_id=?", (user_id, playlist_id))
        db.executemany(
            "INSERT INTO playlist_items VALUES (?, ?, ?, ?)",
            [(user_id, playlist_id, position, json.dumps(item)) for position, item in enumerate(items)],
        )
        db.execute(
            "INSERT INTO cache_entries VALUES (?, 'playlist_items', ?, ?, ?) "
            "ON CONFLICT(user_id, resource, cache_key) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
            (user_id, playlist_id, json.dumps({"total": len(items)}), now()),
        )


def get_followed_artists(user_id):
    with connect() as db:
        rows = db.execute(
            "SELECT payload FROM followed_artists WHERE user_id=? ORDER BY artist_id", (user_id,)
        ).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def replace_followed_artists(user_id, artists):
    with connect() as db:
        db.execute("DELETE FROM followed_artists WHERE user_id=?", (user_id,))
        db.executemany(
            "INSERT INTO followed_artists VALUES (?, ?, ?)",
            [(user_id, artist["id"], json.dumps(artist)) for artist in artists if artist.get("id")],
        )
        db.execute(
            "INSERT INTO cache_entries VALUES (?, 'followed_artists', '', ?, ?) "
            "ON CONFLICT(user_id, resource, cache_key) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
            (user_id, json.dumps({"total": len(artists)}), now()),
        )
