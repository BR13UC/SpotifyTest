"""Account-scoped artist chronology and Spotify catalogue releases."""

import json
from datetime import timedelta

from app import spotify_auth, storage


RELEASE_CACHE_AGE = timedelta(days=30)
EMPTY_CACHE_AGE = timedelta(days=7)
CATEGORIES = {"album", "single", "compilation"}


def artist_data(user_id):
    metadata, _ = storage.get_cache(user_id, "followed_artists")
    if metadata is None:
        return None
    with storage.connect() as db:
        rows = db.execute(
            "SELECT f.artist_id, f.payload, m.status, m.enriched FROM followed_artists AS f "
            "LEFT JOIN mb_matches AS m ON m.user_id=f.user_id AND m.kind='artist' AND m.spotify_id=f.artist_id "
            "WHERE f.user_id=?", (user_id,),
        ).fetchall()
        # Older enriched rows predate the MusicBrainz 'ended' field. Refresh those
        # through the existing durable worker rather than assuming they are active.
        for row in rows:
            enriched = json.loads(row["enriched"]) if row["enriched"] else None
            if row["status"] == "confirmed" and enriched and "ended" not in (enriched.get("dates") or {}):
                db.execute(
                    "INSERT OR IGNORE INTO mb_jobs(user_id,kind,spotify_id,available_at) VALUES(?,'artist',?,?)",
                    (user_id, row["artist_id"], storage.now()),
                )
    artists = []
    for row in rows:
        spotify = json.loads(row["payload"])
        enriched = json.loads(row["enriched"]) if row["enriched"] else None
        artists.append({
            "id": row["artist_id"], "name": spotify.get("name") or "Artiste inconnu",
            "match_status": row["status"] or "pending",
            "type": enriched.get("type") if enriched else None,
            "dates": enriched.get("dates") if enriched else None,
        })
    return artists


def release_page_for(user_id, spotify_id, offset=0):
    with storage.connect() as db:
        followed = db.execute("SELECT 1 FROM followed_artists WHERE user_id=? AND artist_id=?",
                              (user_id, spotify_id)).fetchone()
    if not followed:
        return None
    cache_key = f"{spotify_id}:{offset}"
    cached, updated_at = storage.get_cache(user_id, "timeline_releases", cache_key)
    if cached is not None and not storage.is_stale(updated_at, RELEASE_CACHE_AGE if cached["releases"] else EMPTY_CACHE_AGE):
        return cached

    spotify = spotify_auth.get_spotify()
    releases = []
    seen = set()
    page = spotify.artist_albums(spotify_id, album_type="album,single,compilation", limit=10, offset=offset)
    items = page.get("items") or []
    for item in items:
        release_id = item.get("id")
        category = item.get("album_group") or item.get("album_type")
        if not release_id or release_id in seen or category not in CATEGORIES:
            continue
        seen.add(release_id)
        releases.append({
            "id": release_id, "title": item.get("name") or "",
            "category": category, "date": item.get("release_date") or "",
            "url": (item.get("external_urls") or {}).get("spotify") or
                   f"https://open.spotify.com/album/{release_id}",
        })
    result = {"releases": releases, "next_offset": offset + len(items) if page.get("next") and items else None}
    storage.set_cache(user_id, "timeline_releases", result, cache_key)
    return result
