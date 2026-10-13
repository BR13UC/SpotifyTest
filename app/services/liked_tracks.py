"""Saved tracks, artist membership and counts, scoped to one Spotify account."""

import json
from collections import Counter

from spotipy.exceptions import SpotifyException
from werkzeug.exceptions import Forbidden

from app import spotify_auth, storage
from app.services.playlist_data import normalize_item


RESOURCE = "liked_artist_counts"
COLLECTION_RESOURCE = "liked_collection"


def collection(user_id, refresh=False):
    cached, updated_at = storage.get_cache(user_id, COLLECTION_RESOURCE)
    if not refresh and cached is not None and not storage.is_stale(updated_at):
        return cached

    spotify = spotify_auth.get_spotify()
    counts = Counter()
    tracks = []
    seen_tracks = set()
    offset = 0
    while True:
        try:
            page = spotify.current_user_saved_tracks(limit=50, offset=offset)
        except SpotifyException as error:
            if error.http_status == 403:
                raise Forbidden("Accès aux titres likés refusé. Reconnectez-vous et autorisez l'accès à votre bibliothèque Spotify.") from error
            raise
        entries = page.get("items") or []
        for entry in entries:
            entry = entry if isinstance(entry, dict) else {}
            track = entry.get("track") or {}
            if not isinstance(track, dict):
                continue
            track_id = track.get("id")
            if not track_id or track_id in seen_tracks or track.get("is_local") or entry.get("is_local"):
                continue
            seen_tracks.add(track_id)
            # Saved-track responses can omit type for unavailable tracks.
            item = normalize_item({**entry, "track": {"type": "track", **track}}, len(tracks))
            if item["type"] != "track":
                continue
            tracks.append(item)
            counts.update({artist["id"] for artist in item["artists"]})
        if not page.get("next") or not entries:
            break
        offset += len(entries)

    result = {"tracks": tracks, "counts": dict(counts)}
    # Publish both resources together only after every page succeeds.
    with storage.connect() as db:
        db.executemany(
            "INSERT INTO cache_entries VALUES (?, ?, '', ?, ?) "
            "ON CONFLICT(user_id,resource,cache_key) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
            [(user_id, resource, json.dumps(payload), storage.now())
             for resource, payload in ((COLLECTION_RESOURCE, result), (RESOURCE, result["counts"]))],
        )
    return result


def artist_counts(user_id, refresh=False):
    cached, updated_at = storage.get_cache(user_id, RESOURCE)
    if not refresh and cached is not None and not storage.is_stale(updated_at):
        return cached
    return collection(user_id, refresh=True)["counts"]
