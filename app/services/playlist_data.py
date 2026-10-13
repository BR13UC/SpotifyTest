"""Spotify playlist loading, normalization and sorting."""

from app import spotify_auth, storage


SORT_FIELDS = {
    "name": ("Titre", lambda item: item["name"] if item["name"] != "Titre indisponible" else None),
    "artist": ("Artiste principal", lambda item: item["artist_names"][0] if item["artist_names"] else None),
    "album": ("Album", lambda item: item["album_name"] or None),
    "release_date": ("Date de sortie", lambda item: item["release_date"] or None),
    "duration_ms": ("Durée", lambda item: item["duration_ms"]),
    "added_at": ("Date d'ajout", lambda item: item["added_at"]),
}


def normalize_item(entry, position):
    item = entry.get("item") or entry.get("track") or {}
    if not isinstance(item, dict):
        item = {}
    album = item.get("album") or {}
    artists = item.get("artists") or []
    item_type = item.get("type") or "unknown"
    uri = item.get("uri")
    is_local = bool(entry.get("is_local") or item.get("is_local"))
    return {
        "position": position,
        "type": item_type,
        "uri": uri,
        "spotify_id": item.get("id") if item_type == "track" and not is_local else None,
        "isrc": (item.get("external_ids") or {}).get("isrc") if item_type == "track" and not is_local else None,
        "name": item.get("name") or "Titre indisponible",
        "artist_names": [artist.get("name", "") for artist in artists if isinstance(artist, dict)],
        "artists": [
            {"id": artist.get("id"), "name": artist.get("name", "")}
            for artist in artists if isinstance(artist, dict) and artist.get("id")
        ],
        "album_name": album.get("name") or "",
        "album_id": album.get("id"),
        "album_type": album.get("album_type"),
        "release_date": album.get("release_date") or "",
        "duration_ms": item.get("duration_ms"),
        "added_at": entry.get("added_at"),
        "exportable": item_type == "track" and not is_local and bool(uri and uri.startswith("spotify:track:")),
    }


def get_playlists_for_user(user_id):
    cached, updated_at = storage.get_cache(user_id, "playlists")
    if cached is not None and not storage.is_stale(updated_at):
        return cached
    spotify = spotify_auth.get_spotify()
    playlists = []
    offset = 0
    while True:
        page = spotify.current_user_playlists(limit=50, offset=offset)
        items = page.get("items", [])
        playlists.extend({
            "id": item["id"], "name": item.get("name") or "Sans nom",
            "owner_id": (item.get("owner") or {}).get("id"),
        } for item in items if item.get("id"))
        if not page.get("next") or not items:
            break
        offset += len(items)
    storage.set_cache(user_id, "playlists", playlists)
    return playlists


def get_items_for_user(user_id, playlist_id, enqueue=True):
    playlists = get_playlists_for_user(user_id)
    if playlist_id not in {playlist["id"] for playlist in playlists}:
        return None
    metadata, updated_at = storage.get_cache(user_id, "playlist_items", playlist_id)
    if metadata is not None and not storage.is_stale(updated_at):
        items = storage.get_playlist_items(user_id, playlist_id)
        from app.services.musicbrainz import enqueue_playlist
        if enqueue:
            enqueue_playlist(user_id, items)
        return items
    spotify = spotify_auth.get_spotify()
    items = []
    offset = 0
    while True:
        page = spotify.playlist_items(playlist_id, limit=50, offset=offset)
        entries = page.get("items", [])
        items.extend(normalize_item(entry, offset + index) for index, entry in enumerate(entries))
        if not page.get("next") or not entries:
            break
        offset += len(entries)
    storage.replace_playlist_items(user_id, playlist_id, items)
    from app.services.musicbrainz import enqueue_playlist
    if enqueue:
        enqueue_playlist(user_id, items)
    return items


def sort_items(items, option):
    field, separator, direction = (option or "").rpartition("_")
    if not separator or field not in SORT_FIELDS or direction not in {"asc", "desc"}:
        raise ValueError("Unknown sort option")
    value_for = SORT_FIELDS[field][1]
    present = []
    missing = []
    for item in items:
        value = value_for(item)
        (missing if value is None else present).append(item)
    present.sort(
        key=lambda item: value_for(item).casefold() if isinstance(value_for(item), str) else value_for(item),
        reverse=direction == "desc",
    )
    return present + missing
