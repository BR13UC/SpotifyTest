"""Persistent MusicBrainz matching and enrichment for Spotify entities."""

import json
import sqlite3
import time
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import current_app
from spotipy import Spotify
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyClientCredentials

from app import storage


BASE_URL = "https://musicbrainz.org/ws/2"
USER_AGENT = "Statify/0.2 (https://github.com/brieucmevel1/SpotifyTest)"
CACHE_AGE = timedelta(days=30)
NEGATIVE_CACHE_AGE = timedelta(days=7)
RECORDING_MATCH_VERSION = 3
ARTIST_MATCH_VERSION = 2
DEFERRED_AFTER = 10
DEFERRED_DELAY = timedelta(hours=24)


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _fold(value):
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in value if not unicodedata.combining(char)).casefold().strip()


def _query_text(value):
    return (value or "").replace("\\", " ").replace('"', " ").strip()


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
            (user_id, kind, spotify_id, _json(payload), storage.now()),
        )
        should_queue = (not existing or existing["status"] == "pending" or
                        (existing["status"] == "not_found" and not existing["manual"]
                         and storage.is_stale(existing["updated_at"], NEGATIVE_CACHE_AGE)) or
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


def _rate_slot():
    while True:
        with storage.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT next_at FROM mb_rate_limit WHERE id=1").fetchone()
            current = time.time()
            next_at = row["next_at"] if row else 0
            if current >= next_at:
                db.execute(
                    "INSERT INTO mb_rate_limit(id,next_at) VALUES(1,?) "
                    "ON CONFLICT(id) DO UPDATE SET next_at=excluded.next_at",
                    (current + 1.1,),
                )
                return
        time.sleep(min(next_at - current, 1.1))


class TemporaryFailure(Exception):
    pass


def request(path, params=None):
    pairs = list(params.items()) if isinstance(params, dict) else list(params or [])
    pairs.append(("fmt", "json"))
    key = path + "?" + urlencode(sorted(pairs))
    with storage.connect() as db:
        row = db.execute("SELECT payload,updated_at FROM mb_http_cache WHERE cache_key=?", (key,)).fetchone()
    if row:
        cached = json.loads(row["payload"])
        negative = (not cached or (path == "url" and not cached.get("urls")) or
                    (path == "artist" and not cached.get("artists")) or
                    ((path == "recording" or path.startswith("isrc/")) and not cached.get("recordings")))
        if not storage.is_stale(row["updated_at"], NEGATIVE_CACHE_AGE if negative else CACHE_AGE):
            return cached
    _rate_slot()
    req = Request(f"{BASE_URL}/{path}?{urlencode(pairs)}", headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(req, timeout=8) as response:
            result = json.load(response)
    except HTTPError as error:
        if error.code in (400, 404):
            result = {}
        elif error.code in (429, 500, 502, 503, 504):
            raise TemporaryFailure(f"MusicBrainz HTTP {error.code}") from error
        else:
            raise
    except (URLError, TimeoutError, OSError) as error:
        raise TemporaryFailure("MusicBrainz inaccessible") from error
    with storage.connect() as db:
        db.execute(
            "INSERT INTO mb_http_cache VALUES(?,?,?) ON CONFLICT(cache_key) "
            "DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
            (key, _json(result), storage.now()),
        )
    return result


def _candidate(entity):
    return {
        "id": entity.get("id"), "name": entity.get("name") or entity.get("title") or "",
        "disambiguation": entity.get("disambiguation") or "",
        "type": entity.get("type"),
        "area": (entity.get("begin-area") or entity.get("area") or {}).get("name"),
        "artist_names": [part["artist"].get("name", "") for part in entity.get("artist-credit", [])
                         if isinstance(part, dict) and isinstance(part.get("artist"), dict)],
        "length": entity.get("length"),
        "isrcs": entity.get("isrcs") or [],
    }


def _artist_candidates(payload):
    name = payload.get("name") or ""
    if not name.strip():
        return []
    from app.services.artist_matching import browse
    return [_candidate(item) for item in browse("artist", {"query": f'artist:"{_query_text(name)}" OR alias:"{_query_text(name)}"'}, "artists")]


def _recording_candidates(payload):
    isrc = (payload.get("isrc") or "").replace("-", "").upper()
    entities = []
    from_isrc = False
    if isrc:
        result = request(f"isrc/{isrc}", {"inc": "artist-credits"})
        entities = result.get("recordings", [])
        from_isrc = bool(entities)
    if not entities or not any(_recording_coherent(payload, _candidate(item)) for item in entities):
        title = _query_text(payload.get("name"))
        artists = payload.get("artists") or []
        if not title.strip():
            return []
        query = f'recording:"{title}"'
        if artists and artists[0].get("name"):
            query += f' AND artist:"{_query_text(artists[0]["name"])}"'
        result = request("recording", {"query": query, "limit": 25})
        entities = result.get("recordings", [])
        from_isrc = False
    candidates = [_candidate(item) for item in entities if item.get("id")]
    if from_isrc:
        for candidate in candidates:
            candidate["isrcs"] = list(set(candidate["isrcs"]) | {isrc})
    return list({candidate["id"]: candidate for candidate in candidates}.values())


def _recording_coherent(payload, candidate):
    if _album_key(payload.get("name")) != _album_key(candidate.get("name")):
        return False
    if not _artist_overlap(payload, candidate):
        return False
    duration, length = payload.get("duration_ms"), candidate.get("length")
    return not (duration and length and abs(duration - length) > 15000)


def _artist_overlap(payload, candidate):
    artists = {_album_key(item.get("name")) for item in payload.get("artists") or [] if item.get("name")}
    credited = {_album_key(name) for name in candidate.get("artist_names") or [] if name}
    return bool(artists.intersection(credited))


def _spotify_url(spotify_id):
    return f"https://open.spotify.com/artist/{spotify_id}"


def _direct_links(spotify_ids):
    """Resolve up to 50 Spotify URLs in one rate limited MusicBrainz call."""
    if not spotify_ids:
        return {}
    urls = {_spotify_url(spotify_id): spotify_id for spotify_id in spotify_ids}
    result = request("url", [("resource", url) for url in urls] + [("inc", "artist-rels")])
    links = {spotify_id: set() for spotify_id in spotify_ids}
    for entry in result.get("urls", []):
        spotify_id = urls.get(entry.get("resource"))
        if not spotify_id:
            continue
        for relation in entry.get("relations", []):
            artist = relation.get("artist") or {}
            if artist.get("id"):
                links[spotify_id].add(artist["id"])
    return links


def _recording_links(jobs):
    """Resolve track and album URLs together; an album link is only supporting evidence."""
    resources = {}
    for job in jobs:
        with storage.connect() as db:
            row = db.execute("SELECT spotify_payload FROM mb_matches WHERE user_id=? AND kind='recording' AND spotify_id=?",
                             (job["user_id"], job["spotify_id"])).fetchone()
        if not row:
            continue
        payload = json.loads(row["spotify_payload"])
        resources.setdefault(f"https://open.spotify.com/track/{job['spotify_id']}", []).append(
            (job["user_id"], job["spotify_id"], "track"))
        if payload.get("album_id"):
            resources.setdefault(f"https://open.spotify.com/album/{payload['album_id']}", []).append(
                (job["user_id"], job["spotify_id"], "album"))
    links = {(job["user_id"], job["spotify_id"]): {"recordings": set(), "releases": set(), "groups": set()}
             for job in jobs}
    for chunk_start in range(0, len(resources), 100):
        chunk = dict(list(resources.items())[chunk_start:chunk_start + 100])
        result = request("url", [("resource", url) for url in chunk] +
                         [("inc", "recording-rels+release-rels+release-group-rels")])
        for entry in result.get("urls", []):
            for user_id, spotify_id, kind in chunk.get(entry.get("resource"), []):
                linked = links[(user_id, spotify_id)]
                for relation in entry.get("relations", []):
                    if kind == "track" and (relation.get("recording") or {}).get("id"):
                        linked["recordings"].add(relation["recording"]["id"])
                    elif kind == "album":
                        if (relation.get("release") or {}).get("id"):
                            linked["releases"].add(relation["release"]["id"])
                        if (relation.get("release-group") or {}).get("id"):
                            linked["groups"].add(relation["release-group"]["id"])
    return links


def _album_key(title):
    folded = _fold(title)
    return " ".join("".join(char if char.isalnum() else " " for char in folded).split())


GENERIC_ALBUMS = {"album", "the album", "greatest hits", "best of", "live", "untitled", "demo"}
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
        if not storage.is_stale(cached["updated_at"], CACHE_AGE if albums else NEGATIVE_CACHE_AGE):
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
            raise TemporaryFailure(f"Spotify catalogue HTTP {error.http_status}") from error
        return []
    albums = list(dict.fromkeys(title for title in albums if title))
    with storage.connect() as db:
        db.execute("INSERT INTO mb_http_cache VALUES(?,?,?) ON CONFLICT(cache_key) "
                   "DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                   (cache_key, _json(albums), storage.now()))
    return albums


def _mb_albums(mbid):
    result = request("release-group", {"artist": mbid, "type": "album", "limit": 100})
    return list(dict.fromkeys(item.get("title", "") for item in result.get("release-groups", []) if item.get("title")))


def album_comparison(spotify_id, mbid):
    spotify = _spotify_albums(spotify_id)
    musicbrainz = _mb_albums(mbid)
    mb_keys = {_album_key(title) for title in musicbrainz}
    overlap = list(dict.fromkeys(title for title in spotify
                                 if _album_key(title) in mb_keys and _album_key(title) not in GENERIC_ALBUMS))
    return {"spotify_albums": spotify, "musicbrainz_albums": musicbrainz, "common_albums": overlap}


def _select(kind, payload, candidates):
    name = _fold(payload.get("name"))
    exact = [candidate for candidate in candidates if _fold(candidate["name"]) == name]
    if kind == "artist":
        return None
    spotify_artists = {_fold(artist.get("name")) for artist in payload.get("artists") or []}
    suitable = []
    for candidate in exact:
        mb_artists = {_fold(artist) for artist in candidate["artist_names"]}
        if not spotify_artists.intersection(mb_artists):
            continue
        duration = payload.get("duration_ms")
        length = candidate.get("length")
        if duration and length and abs(duration - length) > 5000:
            continue
        suitable.append(candidate)
    return suitable[0]["id"] if len(suitable) == 1 and len(candidates) == 1 else None


def _credit_names(credit):
    return [part["artist"].get("name", "") for part in credit or []
            if isinstance(part, dict) and isinstance(part.get("artist"), dict)]


def _release_context(mbid):
    """Browse actual releases containing this recording, including their release groups."""
    releases = []
    offset = 0
    while True:
        result = request("release", {"recording": mbid, "inc": "release-groups+artist-credits",
                                     "limit": 100, "offset": offset})
        page = result.get("releases") or []
        for item in page:
            group = item.get("release-group") or {}
            releases.append({"id": item.get("id"), "title": item.get("title") or group.get("title") or "",
                             "date": item.get("date") or group.get("first-release-date"),
                             "group_id": group.get("id"),
                             "group_type": (group.get("primary-type") or group.get("type") or "").casefold(),
                             "artist_names": _credit_names(item.get("artist-credit") or group.get("artist-credit"))})
        offset += len(page)
        if not page or offset >= result.get("release-count", offset):
            break
    return releases


def _release_match(payload, release, linked):
    album_key = _album_key(payload.get("album_name"))
    if release.get("id") in linked["releases"] or release.get("group_id") in linked["groups"]:
        return "spotify_album_url"
    if album_key and album_key not in GENERIC_ALBUMS and album_key == _album_key(release.get("title")):
        return "release_title"
    return None


def _release_rank(payload, release, linked):
    album_key = _album_key(payload.get("album_name"))
    title_match = bool(album_key and album_key not in GENERIC_ALBUMS
                       and album_key == _album_key(release.get("title")))
    url_match = _release_match(payload, release, linked) == "spotify_album_url"
    spotify_date, release_date = payload.get("release_date") or "", release.get("date") or ""
    date_match = 0
    if url_match or title_match:
        # Compare only the precision available on both sides (year, month, day).
        for precision in (4, 7, 10):
            if min(len(spotify_date), len(release_date)) >= precision:
                if spotify_date[:precision] != release_date[:precision]:
                    break
                date_match = precision
    group_type = release.get("group_type")
    album_type = payload.get("album_type")
    type_match = int((album_type == "album" and group_type == "album") or
                     (album_type == "single" and group_type in ("single", "ep")))
    return int(url_match), int(title_match), date_match, type_match


def _best_release(payload, releases, linked):
    matching = [release for release in releases if _release_match(payload, release, linked)]
    # An explicit final key keeps the selected edition stable across API page orders.
    return max(matching, key=lambda release: (_release_rank(payload, release, linked),
                                              release.get("id") or ""), default=None)


def _has_isrc(payload, candidate):
    isrc = (payload.get("isrc") or "").replace("-", "").upper()
    return bool(isrc and isrc in {(value or "").replace("-", "").upper()
                                for value in candidate.get("isrcs") or []})


def _recording_rank(payload, candidate, release, linked):
    url_match, title_match, date_match, type_match = _release_rank(payload, release or {}, linked)
    duration, length = payload.get("duration_ms"), candidate.get("length")
    duration_match = -abs(duration - length) if duration and length else float("-inf")
    return url_match, int(_has_isrc(payload, candidate)), title_match, date_match, duration_match, type_match


def _recording_score(payload, candidate, releases, linked):
    signals = []
    score = 0
    title_ratio = SequenceMatcher(None, _album_key(payload.get("name")), _album_key(candidate.get("name"))).ratio()
    title_points = 30 if title_ratio == 1 else 20 if title_ratio >= .9 else 0
    score += title_points
    if title_points:
        signals.append("titre")
    artists = {_album_key(item.get("name")) for item in payload.get("artists") or [] if item.get("name")}
    credited = {_album_key(name) for name in candidate.get("artist_names") or [] if name}
    artist_points = 25 if artists.intersection(credited) else 0
    score += artist_points
    if artist_points:
        signals.append("artiste crédité")
    release = _best_release(payload, releases, linked)
    if release:
        score += 25
        signals.append(f"sortie : {release['title']}")
    if _has_isrc(payload, candidate):
        score += 10
        signals.append("ISRC")
    duration, length = payload.get("duration_ms"), candidate.get("length")
    duration_conflict = bool(duration and length and abs(duration - length) > 15000)
    if duration and length and abs(duration - length) <= 5000:
        score += 5
        signals.append("durée")
    spotify_year = (payload.get("release_date") or "")[:4]
    if spotify_year and release and (release.get("date") or "")[:4] == spotify_year:
        score += 5
        signals.append("année de sortie")
    conflict = title_ratio < .85 or not artist_points or duration_conflict
    if linked["recordings"] and candidate["id"] not in linked["recordings"]:
        conflict = True
    return {"id": candidate["id"], "score": score, "signals": signals, "conflict": conflict}


def _select_recording(payload, candidates, linked):
    """Prefer direct identity, then compare compatible recordings in API order."""
    unique = {}
    for item in candidates:
        unique.setdefault(item["id"], item)
    candidates = list(unique.values())
    linked_ids = linked["recordings"]
    if len(linked_ids) == 1:
        linked_id = next(iter(linked_ids))
        if not any(candidate["id"] == linked_id for candidate in candidates):
            entity = request(f"recording/{linked_id}", {"inc": "artist-credits+isrcs"})
            if entity.get("id"):
                candidates = [*candidates, _candidate(entity)]
        target = next((item for item in candidates if item["id"] == linked_id), None)
        if target and _recording_coherent(payload, target):
            return linked_id, "spotify_url", {"spotify_url": f"https://open.spotify.com/track/{payload['id']}"}, candidates
    coherent = [item for item in candidates if _recording_coherent(payload, item)
                and (not linked_ids or item["id"] in linked_ids)]
    if len(coherent) == 1 and len(linked_ids) <= 1:
        target = coherent[0]
        return target["id"], "isrc" if _has_isrc(payload, target) else "name", {"title": target["name"],
                "artist_names": target["artist_names"]}, candidates
    plausible = [item for item in candidates if not linked_ids or item["id"] in linked_ids]
    plausible = [item for item in plausible if _artist_overlap(payload, item) and SequenceMatcher(
        None, _album_key(payload.get("name")), _album_key(item.get("name"))).ratio() >= .85]
    releases_by_id = {item["id"]: _release_context(item["id"]) for item in (coherent or plausible[:25])
                      if payload.get("album_name") or linked["releases"] or linked["groups"]}
    if coherent:
        releases = {item["id"]: _best_release(payload, releases_by_id.get(item["id"], []), linked)
                    for item in coherent}
        # Stable sorting preserves MusicBrainz's first candidate for equal evidence.
        ordered = sorted(coherent, key=lambda item: _recording_rank(
            payload, item, releases[item["id"]], linked), reverse=True)
        target = ordered[0]
        best_rank = _recording_rank(payload, target, releases[target["id"]], linked)
        tied = [item for item in ordered if _recording_rank(
            payload, item, releases[item["id"]], linked) == best_rank]
        method = "first_candidate" if len(tied) > 1 else "disambiguation"
        ranked = [_recording_score(payload, item, releases_by_id.get(item["id"], []), linked)
                  for item in ordered]
        evidence = {"ranked": ranked, "selected_score": ranked[0]["score"],
                    "selection": method, "tied_candidates": [item["id"] for item in tied]}
        release = releases[target["id"]]
        evidence["criteria"] = {
            "spotify_album_url": bool(best_rank[0]), "isrc": bool(best_rank[1]),
            "album_title": bool(best_rank[2]), "release_date": best_rank[3],
            "duration_delta_ms": -best_rank[4] if best_rank[4] != float("-inf") else None,
            "album_type": bool(best_rank[5]),
        }
        if release:
            evidence.update(release=release["title"], release_id=release.get("id"),
                            release_type=release.get("group_type"), release_date=release.get("date"))
        return target["id"], method, evidence, candidates
    ranked = sorted((_recording_score(payload, item, releases_by_id.get(item["id"], []), linked)
                     for item in candidates), key=lambda item: (-item["score"], item["id"]))
    evidence = {"ranked": ranked, "threshold": 85, "required_margin": 15}
    if ranked and ranked[0]["score"] >= 85 and not ranked[0]["conflict"] and (
            len(ranked) == 1 or ranked[0]["score"] - ranked[1]["score"] >= 15):
        evidence["selected_score"] = ranked[0]["score"]
        return ranked[0]["id"], "score", evidence, candidates
    return None, None, evidence, candidates


def _genres(entity, kind, mbid):
    return [
        {"name": genre["name"], "source": "musicbrainz", "level": kind, "external_id": mbid}
        for genre in entity.get("genres", []) if genre.get("name")
    ]


def _details(kind, mbid):
    entity = request(f"{kind}/{mbid}", {"inc": "genres"})
    if not entity.get("id"):
        return None
    details = {"source": "musicbrainz", "external_id": mbid, "name": entity.get("name") or entity.get("title"),
               "genres": _genres(entity, kind, mbid)}
    if kind == "artist":
        life = entity.get("life-span") or {}
        begin_area = entity.get("begin-area") or {}
        country_code = entity.get("country") or next(iter((entity.get("area") or {}).get("iso-3166-1-codes") or []), None)
        origin_country_code = next(iter(begin_area.get("iso-3166-1-codes") or []), None)
        details.update({
            "type": entity.get("type"),
            "dates": {"begin": life.get("begin"), "end": life.get("end"), "ended": life.get("ended")},
            "origin": ({"name": begin_area.get("name"), "type": "birth" if entity.get("type") == "Person" else "formation",
                        "external_id": begin_area.get("id"), "source": "musicbrainz", "uncertainty": "reported",
                        "country_code": origin_country_code}
                       if begin_area.get("name") else None),
            "country_code": country_code,
            "country_name": (entity.get("area") or {}).get("name"),
        })
    else:
        details["first_release_date"] = entity.get("first-release-date")
    return details


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
                           (_json(payload), *key))
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
            details = _details(job["kind"], mbid)
        else:
            artist_linked = (direct_links or {}).get(job["spotify_id"], set()) if job["kind"] == "artist" else set()
            # A unique direct Spotify link makes the name search for homonyms useless.
            searched = [] if len(artist_linked) == 1 else _artist_candidates(payload) if job["kind"] == "artist" else None
            candidates = list({item["id"]: item for item in [*json.loads(row["candidates"]), *searched]}.values()) if job["kind"] == "artist" else _recording_candidates(payload)
            mbid = None
            method, evidence = None, None
            if job["kind"] == "artist":
                from app.services.artist_matching import analyze
                mbid, method, evidence, candidates = analyze(job["user_id"], payload, candidates, artist_linked)
            else:
                linked = (direct_links or {}).get((job["user_id"], job["spotify_id"]),
                                                  {"recordings": set(), "releases": set(), "groups": set()})
                mbid, method, evidence, candidates = _select_recording(payload, candidates, linked)
            status = "confirmed" if mbid else "ambiguous" if candidates else "not_found"
            details = _details(job["kind"], mbid) if mbid else None
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
                    (status, mbid, _json(candidates), _json(details) if details else None,
                     method, _json(evidence) if evidence else None, RECORDING_MATCH_VERSION if job["kind"] == "recording" else ARTIST_MATCH_VERSION,
                     storage.now(), *key),
                )
            elif latest and latest["status"] == "not_found":
                pass
            elif latest and latest["mbid"] == selected_mbid:
                db.execute("UPDATE mb_matches SET status=?,mbid=?,enriched=?,updated_at=? "
                           "WHERE user_id=? AND kind=? AND spotify_id=?",
                           (status, mbid, _json(details) if details else None, storage.now(), *key))
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
    except (TemporaryFailure, HTTPError, ValueError, KeyError, sqlite3.OperationalError) as error:
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
            direct_links = _direct_links(list(dict.fromkeys(item["spotify_id"] for item in jobs)))
        except (TemporaryFailure, HTTPError, ValueError, sqlite3.OperationalError) as error:
            current_app.logger.warning("MusicBrainz URL lookup delayed: %s", error)
            for item in jobs:
                _delay_job(item)
            return True
    else:
        try:
            direct_links = _recording_links(jobs)
        except (TemporaryFailure, HTTPError, ValueError, sqlite3.OperationalError) as error:
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
               (job["user_id"], job["kind"], _json(stats), storage.now()))


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
            key = _fold(name)
            if not key:
                continue
            entry = genres.setdefault(key, {"name": name, "origins": []})
            origin = {field: genre.get(field) for field in ("source", "level", "external_id")}
            if origin not in entry["origins"]:
                entry["origins"].append(origin)
    return list(genres.values())
