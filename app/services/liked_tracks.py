"""Count saved Spotify tracks by credited artist, scoped to one account."""

from collections import Counter

from app import spotify_auth, storage


RESOURCE = "liked_artist_counts"


def artist_counts(user_id, refresh=False):
    cached, updated_at = storage.get_cache(user_id, RESOURCE)
    if not refresh and cached is not None and not storage.is_stale(updated_at):
        return cached

    spotify = spotify_auth.get_spotify()
    counts = Counter()
    seen_tracks = set()
    offset = 0
    while True:
        page = spotify.current_user_saved_tracks(limit=50, offset=offset)
        entries = page.get("items") or []
        for entry in entries:
            track = (entry or {}).get("track") or {}
            track_id = track.get("id")
            if not track_id or track_id in seen_tracks:
                continue
            seen_tracks.add(track_id)
            artist_ids = {artist.get("id") for artist in track.get("artists") or []
                          if isinstance(artist, dict) and artist.get("id")}
            counts.update(artist_ids)
        if not page.get("next") or not entries:
            break
        offset += len(entries)

    result = dict(counts)
    storage.set_cache(user_id, RESOURCE, result)
    return result
