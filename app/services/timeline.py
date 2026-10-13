"""Account-scoped artist chronology and Spotify catalogue releases."""

import json
from datetime import timedelta

from app import spotify_auth, storage
from app.services import artist_sources


RELEASE_CACHE_AGE = timedelta(days=30)
EMPTY_CACHE_AGE = timedelta(days=7)
CATEGORIES = {"album", "single", "compilation"}


def artist_data(user_id, source="followed", playlist_id=None):
    records = artist_sources.records(user_id, source, playlist_id)
    if records is None:
        return None
    artists = []
    with storage.connect() as db:
        for spotify, row in records:
            enriched = json.loads(row["enriched"]) if row and row["enriched"] else None
            status = row["status"] if row else "pending"
            if status == "confirmed" and enriched and "ended" not in (enriched.get("dates") or {}):
                db.execute(
                    "INSERT OR IGNORE INTO mb_jobs(user_id,kind,spotify_id,available_at) VALUES(?,'artist',?,?)",
                    (user_id, spotify["id"], storage.now()),
                )
            artists.append({
                "id": spotify["id"], "name": spotify.get("name") or "Artiste inconnu",
                "match_status": status,
                "type": enriched.get("type") if enriched else None,
                "dates": enriched.get("dates") if enriched else None,
            })
    return artists


def release_page_for(user_id, spotify_id, offset=0, source="followed", playlist_id=None):
    artists = artist_sources.collection(user_id, source, playlist_id) or []
    if spotify_id not in {artist["id"] for artist in artists}:
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
