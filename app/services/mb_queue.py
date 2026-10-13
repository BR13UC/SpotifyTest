"""Persistent MusicBrainz matching queue and per-account match state."""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

from flask import current_app

from app import storage
from app.services import artist_matching, mb_client, recording_matching


RECORDING_MATCH_VERSION = 3
ARTIST_MATCH_VERSION = 2
DEFERRED_AFTER = 10
DEFERRED_DELAY = timedelta(hours=24)


def _eligible(item):
    return item.get("type") == "track" and item.get("exportable") and item.get("name") != "Titre indisponible"


def enqueue(user_id, kind, spotify_id, payload):
    if not spotify_id:
        return
    with storage.connect() as db:
        existing = db.execute("SELECT spotify_payload,status,manual,updated_at FROM mb_matches WHERE user_id=? AND kind=? AND spotify_id=?",
                              (user_id, kind, spotify_id)).fetchone()
        if existing:
            old = json.loads(existing["spotify_payload"])
            payload = {**old, **{key: value for key, value in payload.items() if value not in (None, [], {})}}
        db.execute(
            "INSERT INTO mb_matches(user_id,kind,spotify_id,spotify_payload,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(user_id,kind,spotify_id) DO UPDATE SET spotify_payload=excluded.spotify_payload",
            (user_id, kind, spotify_id, mb_client._json(payload), storage.now()),
        )
        should_queue = (not existing or existing["status"] == "pending" or
                        (existing["status"] == "not_found" and not existing["manual"]
                         and storage.is_stale(existing["updated_at"], mb_client.NEGATIVE_CACHE_AGE)) or
                        (kind == "recording" and existing["status"] == "ambiguous" and not existing["manual"]
                         and any(old.get(field) != payload.get(field) for field in
                                 ("album_name", "album_id", "album_type", "release_date"))))
        if should_queue:
            db.execute(
                "INSERT OR IGNORE INTO mb_jobs(user_id,kind,spotify_id,available_at) VALUES(?,?,?,?)",
                (user_id, kind, spotify_id, storage.now()),
            )


def enqueue_playlist(user_id, items):
    for item in items:
        if not _eligible(item):
            continue
        for artist in item.get("artists") or []:
            enqueue(user_id, "artist", artist.get("id"), artist)
        enqueue(user_id, "recording", item.get("spotify_id"), {
            "id": item.get("spotify_id"), "name": item.get("name"),
            "isrc": item.get("isrc"), "duration_ms": item.get("duration_ms"),
            "artists": item.get("artists") or [],
            "album_name": item.get("album_name"), "album_id": item.get("album_id"),
            "album_type": item.get("album_type"), "release_date": item.get("release_date"),
        })


def enqueue_followed(user_id, artists):
    for artist in artists:
        enqueue(user_id, "artist", artist.get("id"), {
            "id": artist.get("id"), "name": artist.get("name"),
            "genres": artist.get("genres") or [],
            "external_urls": artist.get("external_urls") or {},
        })


def claim_job():
    with storage.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        stale = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        db.execute("UPDATE mb_jobs SET state='queued' WHERE state='processing' AND claimed_at<?", (stale,))
        row = db.execute(
            "SELECT user_id,kind,spotify_id,attempts FROM mb_jobs WHERE state IN ('queued','deferred') AND available_at<=? "
            "ORDER BY available_at LIMIT 1", (storage.now(),),
        ).fetchone()
        if row:
            db.execute("UPDATE mb_jobs SET state='processing',claimed_at=? WHERE user_id=? AND kind=? AND spotify_id=?",
                       (storage.now(), row["user_id"], row["kind"], row["spotify_id"]))
            return dict(row)
    return None


def _claim_more_artists(limit=49):
    with storage.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT user_id,kind,spotify_id,attempts FROM mb_jobs "
                          "WHERE kind='artist' AND state IN ('queued','deferred') AND available_at<=? "
                          "ORDER BY available_at LIMIT ?", (storage.now(), limit)).fetchall()
        for row in rows:
            db.execute("UPDATE mb_jobs SET state='processing',claimed_at=? "
                       "WHERE user_id=? AND kind=? AND spotify_id=?",
                       (storage.now(), row["user_id"], row["kind"], row["spotify_id"]))
    return [dict(row) for row in rows]


def _claim_more_recordings(limit=39):
    with storage.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        rows = db.execute("SELECT user_id,kind,spotify_id,attempts FROM mb_jobs "
                          "WHERE kind='recording' AND state='queued' AND available_at<=? "
                          "ORDER BY available_at LIMIT ?", (storage.now(), limit)).fetchall()
        for row in rows:
            db.execute("UPDATE mb_jobs SET state='processing',claimed_at=? "
                       "WHERE user_id=? AND kind=? AND spotify_id=?",
                       (storage.now(), row["user_id"], row["kind"], row["spotify_id"]))
    return [dict(row) for row in rows]


def enqueue_rechecks():
    """Upgrade old ambiguous decisions once, without changing manual choices."""
    with storage.connect() as db:
        db.execute("UPDATE mb_jobs SET state='queued',claimed_at=NULL WHERE state='processing'")
        rows = db.execute("SELECT user_id,spotify_id,spotify_payload FROM mb_matches "
                          "WHERE kind='recording' AND status='ambiguous' AND manual=0 AND match_version<?",
                          (RECORDING_MATCH_VERSION,)).fetchall()
        missing = {(row["user_id"], row["spotify_id"]): row for row in rows}
        if missing:
            for item_row in db.execute("SELECT user_id,payload FROM playlist_items").fetchall():
                item = json.loads(item_row["payload"])
                key = (item_row["user_id"], item.get("spotify_id"))
                match = missing.get(key)
                if not match:
                    continue
                payload = json.loads(match["spotify_payload"])
                for field in ("album_name", "album_id", "album_type", "release_date"):
                    if not payload.get(field) and item.get(field):
                        payload[field] = item[field]
                db.execute("UPDATE mb_matches SET spotify_payload=? WHERE user_id=? AND kind='recording' AND spotify_id=?",
                           (mb_client._json(payload), *key))
        db.execute("INSERT OR IGNORE INTO mb_jobs(user_id,kind,spotify_id,available_at) "
                   "SELECT user_id,kind,spotify_id,? FROM mb_matches "
                   "WHERE status='ambiguous' AND manual=0 AND "
                   "((kind='artist' AND match_version<?) OR (kind='recording' AND match_version<?))",
                   (storage.now(), ARTIST_MATCH_VERSION, RECORDING_MATCH_VERSION))


def _process_job(job, direct_links=None):
    key = (job["user_id"], job["kind"], job["spotify_id"])
    with storage.connect() as db:
        row = db.execute("SELECT * FROM mb_matches WHERE user_id=? AND kind=? AND spotify_id=?", key).fetchone()
    if not row:
        with storage.connect() as db:
            db.execute("DELETE FROM mb_jobs WHERE user_id=? AND kind=? AND spotify_id=?", key)
        return
    try:
        payload = json.loads(row["spotify_payload"])
        payload.setdefault("id", job["spotify_id"])
        method, evidence = row["match_method"], json.loads(row["match_evidence"]) if row["match_evidence"] else None
        if row["manual"] and row["status"] == "not_found":
            status, mbid, candidates, details = "not_found", None, [], None
        elif row["mbid"] and row["status"] == "confirmed":
            status, mbid, candidates = "confirmed", row["mbid"], json.loads(row["candidates"])
            details = mb_client._details(job["kind"], mbid)
        else:
            artist_linked = (direct_links or {}).get(job["spotify_id"], set()) if job["kind"] == "artist" else set()
            # A unique direct Spotify link makes the name search for homonyms useless.
            searched = [] if len(artist_linked) == 1 else recording_matching._artist_candidates(payload) if job["kind"] == "artist" else None
            candidates = list({item["id"]: item for item in [*json.loads(row["candidates"]), *searched]}.values()) if job["kind"] == "artist" else recording_matching._recording_candidates(payload)
            mbid = None
            method, evidence = None, None
            if job["kind"] == "artist":
                mbid, method, evidence, candidates = artist_matching.analyze(job["user_id"], payload, candidates, artist_linked)
            else:
                linked = (direct_links or {}).get((job["user_id"], job["spotify_id"]),
                                                  {"recordings": set(), "releases": set(), "groups": set()})
                mbid, method, evidence, candidates = recording_matching._select_recording(payload, candidates, linked)
            status = "confirmed" if mbid else "ambiguous" if candidates else "not_found"
            details = mb_client._details(job["kind"], mbid) if mbid else None
        selected_mbid = mbid
        if mbid and details is None:
            status, mbid = "not_found", None
        with storage.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            latest = db.execute("SELECT manual,mbid,status FROM mb_matches WHERE user_id=? AND kind=? AND spotify_id=?", key).fetchone()
            if latest and not latest["manual"]:
                db.execute(
                    "UPDATE mb_matches SET status=?,mbid=?,candidates=?,enriched=?,match_method=?,match_evidence=?,match_version=?,updated_at=? "
                    "WHERE user_id=? AND kind=? AND spotify_id=?",
                    (status, mbid, mb_client._json(candidates), mb_client._json(details) if details else None,
                     method, mb_client._json(evidence) if evidence else None, RECORDING_MATCH_VERSION if job["kind"] == "recording" else ARTIST_MATCH_VERSION,
                     storage.now(), *key),
                )
            elif latest and latest["status"] == "not_found":
                pass
            elif latest and latest["mbid"] == selected_mbid:
                db.execute("UPDATE mb_matches SET status=?,mbid=?,enriched=?,updated_at=? "
                           "WHERE user_id=? AND kind=? AND spotify_id=?",
                           (status, mbid, mb_client._json(details) if details else None, storage.now(), *key))
            if latest and latest["manual"] and latest["status"] != "not_found" and latest["mbid"] != selected_mbid:
                db.execute("UPDATE mb_jobs SET state='queued',available_at=?,claimed_at=NULL "
                           "WHERE user_id=? AND kind=? AND spotify_id=?", (storage.now(), *key))
            elif job["kind"] == "artist" and evidence and not evidence.get("complete", True) and not (latest and latest["manual"]):
                attempts = job.get("attempts", 0) + 1
                # Repeatedly incomplete analyses move to a daily lane instead of hogging the rate limit.
                deferred = attempts >= DEFERRED_AFTER
                delay = DEFERRED_DELAY if deferred else timedelta(seconds=min(3600, 30 * 2 ** min(attempts, 7)))
                retry = (datetime.now(timezone.utc) + delay).isoformat()
                db.execute("UPDATE mb_jobs SET state=?,attempts=?,available_at=?,claimed_at=NULL WHERE user_id=? AND kind=? AND spotify_id=?",
                           ("deferred" if deferred else "queued", attempts, retry, *key))
            else:
                db.execute("DELETE FROM mb_jobs WHERE user_id=? AND kind=? AND spotify_id=?", key)
            _record_attempt(db, job, failed=bool(job["kind"] == "artist" and evidence and not evidence.get("complete", True)))
    except (mb_client.TemporaryFailure, HTTPError, ValueError, KeyError, sqlite3.OperationalError) as error:
        current_app.logger.warning("MusicBrainz job delayed: %s", error)
        _delay_job(job)


def process_one():
    job = claim_job()
    if not job:
        return False
    jobs = [job] + (_claim_more_artists() if job["kind"] == "artist" else _claim_more_recordings())
    direct_links = {}
    if job["kind"] == "artist":
        try:
            direct_links = recording_matching._direct_links(list(dict.fromkeys(item["spotify_id"] for item in jobs)))
        except (mb_client.TemporaryFailure, HTTPError, ValueError, sqlite3.OperationalError) as error:
            current_app.logger.warning("MusicBrainz URL lookup delayed: %s", error)
            for item in jobs:
                _delay_job(item)
            return True
    else:
        try:
            direct_links = recording_matching._recording_links(jobs)
        except (mb_client.TemporaryFailure, HTTPError, ValueError, sqlite3.OperationalError) as error:
            current_app.logger.warning("MusicBrainz recording URL lookup delayed: %s", error)
            for item in jobs:
                _delay_job(item)
            return True
    for item in jobs:
        _process_job(item, direct_links)
    return True


def _delay_job(job):
    delay = min(3600, 2 ** min(job["attempts"] + 1, 10))
    available = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
    with storage.connect() as db:
        updated = db.execute("UPDATE mb_jobs SET state='queued',available_at=?,attempts=attempts+1,claimed_at=NULL "
                   "WHERE user_id=? AND kind=? AND spotify_id=?",
                   (available, job["user_id"], job["kind"], job["spotify_id"]))
        if updated.rowcount:
            _record_attempt(db, job, failed=True)


def _record_attempt(db, job, failed):
    """Persist per-account, per-kind outcomes in the job's existing transaction."""
    row = db.execute("SELECT payload FROM cache_entries WHERE user_id=? "
                     "AND resource='musicbrainz_attempt_stats' AND cache_key=?",
                     (job["user_id"], job["kind"])).fetchone()
    stats = json.loads(row["payload"]) if row else {"attempts": 0, "failed": 0, "since": storage.now()}
    stats["attempts"] += 1
    stats["failed"] += int(failed)
    db.execute("INSERT INTO cache_entries VALUES (?, 'musicbrainz_attempt_stats', ?, ?, ?) "
               "ON CONFLICT(user_id,resource,cache_key) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
               (job["user_id"], job["kind"], mb_client._json(stats), storage.now()))


def error_stats(user_id, kind):
    stats, _ = storage.get_cache(user_id, "musicbrainz_attempt_stats", kind)
    stats = stats or {"attempts": 0, "failed": 0, "since": None}
    return {**stats, "percent": round(100 * stats["failed"] / stats["attempts"], 1) if stats["attempts"] else None}


def _row_dict(row):
    return {"kind": row["kind"], "spotify_id": row["spotify_id"], "spotify": json.loads(row["spotify_payload"]),
            "status": row["status"], "mbid": row["mbid"], "candidates": json.loads(row["candidates"]),
            "enriched": json.loads(row["enriched"]) if row["enriched"] else None, "manual": bool(row["manual"]),
            "match_method": row["match_method"],
            "match_evidence": json.loads(row["match_evidence"]) if row["match_evidence"] else None}


def get_match(user_id, kind, spotify_id):
    if not spotify_id:
        return None
    with storage.connect() as db:
        row = db.execute("SELECT * FROM mb_matches WHERE user_id=? AND kind=? AND spotify_id=?",
                         (user_id, kind, spotify_id)).fetchone()
    return _row_dict(row) if row else None


def status_for(user_id):
    with storage.connect() as db:
        rows = db.execute("SELECT status,COUNT(*) AS total FROM mb_matches WHERE user_id=? GROUP BY status", (user_id,)).fetchall()
        recording_rows = db.execute("SELECT status,COUNT(*) AS total FROM mb_matches "
                                    "WHERE user_id=? AND kind='recording' GROUP BY status", (user_id,)).fetchall()
        artist_rows = db.execute("SELECT status,COUNT(*) AS total FROM mb_matches "
                                 "WHERE user_id=? AND kind='artist' GROUP BY status", (user_id,)).fetchall()
        queued = db.execute("SELECT COUNT(*) FROM mb_jobs WHERE user_id=?", (user_id,)).fetchone()[0]
        queued_rows = db.execute("SELECT kind,COUNT(*) AS total FROM mb_jobs WHERE user_id=? GROUP BY kind", (user_id,)).fetchall()
        revision = db.execute("SELECT MAX(updated_at) FROM mb_matches WHERE user_id=?", (user_id,)).fetchone()[0]
        artist_revision = db.execute("SELECT MAX(m.updated_at) FROM mb_matches AS m "
                                     "JOIN followed_artists AS f ON f.user_id=m.user_id AND f.artist_id=m.spotify_id "
                                     "WHERE m.user_id=? AND m.kind='artist'", (user_id,)).fetchone()[0]
        artist_total = db.execute("SELECT COUNT(*) FROM mb_matches WHERE user_id=? AND kind='artist'", (user_id,)).fetchone()[0]
        origin = db.execute("SELECT enriched FROM mb_matches WHERE user_id=? AND kind='artist' AND status='confirmed'", (user_id,)).fetchall()
    counts = {"pending": 0, "confirmed": 0, "ambiguous": 0, "not_found": 0}
    counts.update({row["status"]: row["total"] for row in rows})
    counts["recordings"] = {"pending": 0, "confirmed": 0, "ambiguous": 0, "not_found": 0}
    counts["recordings"].update({row["status"]: row["total"] for row in recording_rows})
    counts["artists"] = {"pending": 0, "confirmed": 0, "ambiguous": 0, "not_found": 0}
    counts["artists"].update({row["status"]: row["total"] for row in artist_rows})
    queued_by_kind = {row["kind"]: row["total"] for row in queued_rows}
    counts["recordings"]["queued"] = queued_by_kind.get("recording", 0)
    counts["artists"]["queued"] = queued_by_kind.get("artist", 0)
    counts["queued"] = queued
    counts["revision"] = revision
    counts["artist_revision"] = artist_revision
    counts["artists_total"] = artist_total
    counts["artists_with_origin"] = sum(bool(json.loads(row["enriched"]).get("origin")) for row in origin if row["enriched"])
    return counts


def progress_for(user_id, kind="recording"):
    """Progress of the current matcher over loaded entities for this account."""
    version = RECORDING_MATCH_VERSION if kind == "recording" else ARTIST_MATCH_VERSION
    with storage.connect() as db:
        row = db.execute(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN m.status!='pending' AND "
            "(m.status!='ambiguous' OR m.match_version>=? OR m.manual=1) "
            "AND j.spotify_id IS NULL THEN 1 ELSE 0 END),0) AS completed, "
            "COALESCE(SUM(CASE WHEN m.status='confirmed' THEN 1 ELSE 0 END),0) AS confirmed, "
            "COALESCE(SUM(CASE WHEN m.status='ambiguous' AND m.match_version>=? THEN 1 ELSE 0 END),0) AS reviews, "
            "COALESCE(SUM(CASE WHEN m.status='not_found' THEN 1 ELSE 0 END),0) AS not_found, "
            "COALESCE(SUM(CASE WHEN j.spotify_id IS NOT NULL AND j.state!='deferred' THEN 1 ELSE 0 END),0) AS queued, "
            "COALESCE(SUM(CASE WHEN j.state='deferred' THEN 1 ELSE 0 END),0) AS deferred "
            "FROM mb_matches AS m LEFT JOIN mb_jobs AS j "
            "ON j.user_id=m.user_id AND j.kind=m.kind AND j.spotify_id=m.spotify_id "
            "WHERE m.user_id=? AND m.kind=?", (version, version, user_id, kind)
        ).fetchone()
    result = {**dict(row), "errors": error_stats(user_id, kind)}
    if kind == 'artist':
        with storage.connect() as db:
            active = db.execute("SELECT c.payload FROM cache_entries c JOIN mb_jobs j "
                                "ON j.user_id=c.user_id AND j.spotify_id=c.cache_key AND j.kind='artist' "
                                "WHERE c.user_id=? AND c.resource='artist_score_progress' AND j.state='processing' "
                                "AND c.updated_at>=j.claimed_at ORDER BY c.updated_at DESC LIMIT 1", (user_id,)).fetchone()
        if active:
            result['analysis'] = json.loads(active['payload'])
    return result


def reviews_for(user_id, limit=None, offset=0, kind=None):
    with storage.connect() as db:
        query = "SELECT * FROM mb_matches WHERE user_id=? AND status='ambiguous'"
        params = (user_id,)
        if kind is not None:
            query += " AND kind=?"
            params += (kind,)
        query += " ORDER BY kind,spotify_id"
        if limit is None:
            rows = db.execute(query, params).fetchall()
        else:
            rows = db.execute(query + " LIMIT ? OFFSET ?", (*params, limit, offset)).fetchall()
    return [_row_dict(row) for row in rows]


def set_manual(user_id, kind, spotify_id, mbid):
    with storage.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT candidates FROM mb_matches WHERE user_id=? AND kind=? AND spotify_id=?",
                         (user_id, kind, spotify_id)).fetchone()
        if not row:
            return False
        from uuid import UUID
        try:
            if mbid is not None:
                UUID(mbid)
        except (TypeError, ValueError):
            return False
        db.execute("UPDATE mb_matches SET status=?,mbid=?,enriched=NULL,manual=1,match_method='manual',match_evidence=NULL,updated_at=? "
                   "WHERE user_id=? AND kind=? AND spotify_id=?",
                   ("confirmed" if mbid else "not_found", mbid, storage.now(), user_id, kind, spotify_id))
        if mbid:
            db.execute("INSERT INTO mb_jobs(user_id,kind,spotify_id,available_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(user_id,kind,spotify_id) DO UPDATE SET state='queued',available_at=excluded.available_at",
                       (user_id, kind, spotify_id, storage.now()))
        else:
            db.execute("DELETE FROM mb_jobs WHERE user_id=? AND kind=? AND spotify_id=?", (user_id, kind, spotify_id))
    return True


def track_enrichment(user_id, item, spotify_genres=None):
    if not _eligible(item):
        return None
    match = get_match(user_id, "recording", item.get("spotify_id"))
    groups = []
    if match and match["enriched"]:
        groups.append(match["enriched"].get("genres") or [])
    artists = []
    for artist in item.get("artists") or []:
        artist_match = get_match(user_id, "artist", artist.get("id"))
        if artist_match:
            artists.append(artist_match)
            if artist_match["enriched"]:
                groups.append(artist_match["enriched"].get("genres") or [])
        for genre in (spotify_genres or {}).get(artist.get("id"), []):
            groups.append([{"name": genre, "source": "spotify", "level": "artist", "external_id": artist.get("id")}])
    return {"recording": match, "artists": artists, "genres": merged_genres(*groups)}


def merged_genres(*groups):
    genres = {}
    for group in groups:
        for genre in group:
            name = genre.get("name") or ""
            key = mb_client._fold(name)
            if not key:
                continue
            entry = genres.setdefault(key, {"name": name, "origins": []})
            origin = {field: genre.get(field) for field in ("source", "level", "external_id")}
            if origin not in entry["origins"]:
                entry["origins"].append(origin)
    return list(genres.values())
