"""Shared account-scoped collections for artist visualizations."""

import json

from flask import request
from werkzeug.exceptions import BadRequest, NotFound

from app import spotify_auth, storage
from app.services import liked_tracks, musicbrainz
from app.services.playlist_data import get_items_for_user


def selection():
    source = request.args.get("source", "followed")
    playlist_id = request.args.get("playlist_id")
    validate(source, playlist_id)
    return source, playlist_id


def validate(source, playlist_id):
    if source not in {"followed", "liked", "playlist"}:
        raise BadRequest("Source inconnue.")
    if source == "playlist" and (not isinstance(playlist_id, str) or not playlist_id.strip()):
        raise BadRequest("Identifiant de playlist requis.")


def followed_artists(user_id, load=False):
    cached, updated_at = storage.get_cache(user_id, "followed_artists")
    if load and (cached is None or storage.is_stale(updated_at)):
        spotify = spotify_auth.get_spotify()
        artists = {}
        after = None
        while True:
            page = spotify.current_user_followed_artists(limit=50, after=after)["artists"]
            artists.update((artist["id"], artist) for artist in page.get("items", []) if artist.get("id"))
            cursor = (page.get("cursors") or {}).get("after")
            if not page.get("next") or not cursor or cursor == after:
                break
            after = cursor
        storage.replace_followed_artists(user_id, list(artists.values()))
        cached = True
    return storage.get_followed_artists(user_id) if cached is not None else None


def collection(user_id, source="followed", playlist_id=None, load=False):
    validate(source, playlist_id)
    if source == "followed":
        artists = followed_artists(user_id, load=load)
        if artists is None:
            return None
    else:
        items = tracks(user_id, source, playlist_id)
        artists_by_id = {}
        for item in items:
            if item.get("type") != "track" or item.get("is_local"):
                continue
            for artist in item.get("artists") or []:
                if artist.get("id"):
                    artists_by_id.setdefault(artist["id"], artist)
        # Full followed profiles retain Spotify genres for overlapping artists.
        profiles = {artist["id"]: artist for artist in storage.get_followed_artists(user_id)}
        artists = [profiles.get(artist_id, artist) for artist_id, artist in artists_by_id.items()]
    artists = sorted(artists, key=lambda artist: artist["id"])
    if load:
        musicbrainz.enqueue_followed(user_id, artists)
    return artists


def tracks(user_id, source, playlist_id=None):
    validate(source, playlist_id)
    if source == "playlist":
        items = get_items_for_user(user_id, playlist_id, enqueue=False)
        if items is None:
            raise NotFound("Playlist introuvable dans votre bibliothèque.")
        return items
    items = liked_tracks.collection(user_id)["tracks"]
    if source == "followed":
        ids = {artist["id"] for artist in followed_artists(user_id, load=True)}
        items = [item for item in items if any(artist["id"] in ids for artist in item["artists"])]
    return items


def records(user_id, source="followed", playlist_id=None):
    artists = collection(user_id, source, playlist_id)
    if artists is None:
        return None
    with storage.connect() as db:
        matches = {row["spotify_id"]: row for row in db.execute(
            "SELECT * FROM mb_matches WHERE user_id=? AND kind='artist'", (user_id,)
        ).fetchall()}
    records = []
    for artist in artists:
        match = matches.get(artist["id"])
        if match and "genres" not in artist:
            cached_profile = json.loads(match["spotify_payload"])
            if cached_profile.get("genres"):
                artist = {**artist, "genres": cached_profile["genres"]}
        records.append((artist, match))
    return records


def revision(user_id, source, playlist_id=None):
    rows = records(user_id, source, playlist_id) or []
    return max((row["updated_at"] for _, row in rows if row), default=None)
