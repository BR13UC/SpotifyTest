"""MusicBrainz HTTP client, shared cache and text helpers."""

import json
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app import storage


BASE_URL = "https://musicbrainz.org/ws/2"
USER_AGENT = "Statify/0.2 (https://github.com/brieucmevel1/SpotifyTest)"
CACHE_AGE = timedelta(days=30)
NEGATIVE_CACHE_AGE = timedelta(days=7)
# One recording lookup serves both the release context and the details of the selected recording.
RECORDING_INC = "genres+releases+release-groups+artist-credits"
LOOKUP_LIMIT = 25


def _json(value):
    return json.dumps(value, ensure_ascii=False)


def _fold(value):
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(char for char in value if not unicodedata.combining(char)).casefold().strip()


def _query_text(value):
    return (value or "").replace("\\", " ").replace('"', " ").strip()


def _album_key(title):
    folded = _fold(title)
    return " ".join("".join(char if char.isalnum() else " " for char in folded).split())


GENERIC_ALBUMS = {"album", "the album", "greatest hits", "best of", "live", "untitled", "demo"}


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


def purge_cache(batch=5000):
    """Delete cache entries older than any reader accepts; small batches keep write locks short."""
    cutoff = (datetime.now(timezone.utc) - CACHE_AGE).isoformat()
    deleted = 0
    while True:
        with storage.connect() as db:
            count = db.execute("DELETE FROM mb_http_cache WHERE rowid IN "
                               "(SELECT rowid FROM mb_http_cache WHERE updated_at<? LIMIT ?)", (cutoff, batch)).rowcount
        deleted += count
        if count < batch:
            return deleted


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


def _genres(entity, kind, mbid):
    return [
        {"name": genre["name"], "source": "musicbrainz", "level": kind, "external_id": mbid}
        for genre in entity.get("genres", []) if genre.get("name")
    ]


def _details(kind, mbid):
    entity = request(f"{kind}/{mbid}", {"inc": RECORDING_INC if kind == "recording" else "genres"})
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
