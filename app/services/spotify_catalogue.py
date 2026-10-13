"""Spotify catalogue albums compared with MusicBrainz release groups."""

import json

from flask import current_app
from spotipy import Spotify
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyClientCredentials

from app import storage
from app.services import mb_client


_spotify_catalogue = None


def _spotify_catalogue_client():
    global _spotify_catalogue
    if _spotify_catalogue is None:
        _spotify_catalogue = Spotify(auth_manager=SpotifyClientCredentials(
            client_id=current_app.config["SPOTIPY_CLIENT_ID"],
            client_secret=current_app.config["SPOTIPY_CLIENT_SECRET"],
            cache_handler=MemoryCacheHandler(),
        ), requests_timeout=8, retries=0)
    return _spotify_catalogue


def _catalogue_albums(spotify_id, **params):
    """Artist albums from the catalogue client, renewing its token once after a 401."""
    client = _spotify_catalogue_client()
    try:
        return client.artist_albums(spotify_id, **params)
    except SpotifyException as error:
        if error.http_status != 401:
            raise
        # A token the local clock still deems valid may already be expired for Spotify.
        client.auth_manager.get_access_token(as_dict=False, check_cache=False)
        return client.artist_albums(spotify_id, **params)


def _spotify_albums(spotify_id):
    cache_key = f"spotify-albums/{spotify_id}"
    with storage.connect() as db:
        cached = db.execute("SELECT payload,updated_at FROM mb_http_cache WHERE cache_key=?", (cache_key,)).fetchone()
    if cached:
        albums = json.loads(cached["payload"])
        if not storage.is_stale(cached["updated_at"], mb_client.CACHE_AGE if albums else mb_client.NEGATIVE_CACHE_AGE):
            return albums
    if not current_app.config.get("SPOTIPY_CLIENT_ID") or not current_app.config.get("SPOTIPY_CLIENT_SECRET"):
        return []
    albums = []
    try:
        for offset in (0, 10, 20):
            page = _catalogue_albums(spotify_id, album_type="album", limit=10, offset=offset)
            albums.extend(item.get("name", "") for item in page.get("items", []))
            if not page.get("next"):
                break
    except SpotifyException as error:
        if error.http_status in (429, 500, 502, 503, 504):
            raise mb_client.TemporaryFailure(f"Spotify catalogue HTTP {error.http_status}") from error
        return []
    albums = list(dict.fromkeys(title for title in albums if title))
    with storage.connect() as db:
        db.execute("INSERT INTO mb_http_cache VALUES(?,?,?) ON CONFLICT(cache_key) "
                   "DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                   (cache_key, mb_client._json(albums), storage.now()))
    return albums


def _mb_albums(mbid):
    result = mb_client.request("release-group", {"artist": mbid, "type": "album", "limit": 100})
    return list(dict.fromkeys(item.get("title", "") for item in result.get("release-groups", []) if item.get("title")))


def album_comparison(spotify_id, mbid):
    spotify = _spotify_albums(spotify_id)
    musicbrainz = _mb_albums(mbid)
    mb_keys = {mb_client._album_key(title) for title in musicbrainz}
    overlap = list(dict.fromkeys(title for title in spotify
                                 if mb_client._album_key(title) in mb_keys and mb_client._album_key(title) not in mb_client.GENERIC_ALBUMS))
    return {"spotify_albums": spotify, "musicbrainz_albums": musicbrainz, "common_albums": overlap}
