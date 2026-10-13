"""Cached coordinates for MusicBrainz areas used by the origins map."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app import storage
from app.services import artist_sources, musicbrainz


COUNTRIES_PATH = Path(__file__).resolve().parents[1] / "static" / "world-countries.geojson"
QID = re.compile(r"^Q[1-9][0-9]*$")
AREA_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
RETRY_AGE = timedelta(days=30)


def countries():
    with COUNTRIES_PATH.open(encoding="utf-8") as source:
        features = json.load(source)["features"]
    result = {}
    for feature in features:
        props = feature["properties"]
        for key in ("ISO_A2", "ISO_A2_EH"):
            code = props.get(key)
            if code and code != "-99" and len(code) == 2:
                result.setdefault(code.upper(), {
                    "code": code.upper(), "name": props.get("ADMIN") or props.get("NAME") or code,
                    "continent": props.get("CONTINENT"),
                    "latitude": props["LABEL_Y"], "longitude": props["LABEL_X"],
                })
    return result


COUNTRIES = countries()


def enqueue_area(area_id):
    if not area_id or not AREA_ID.fullmatch(area_id):
        return
    with storage.connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO geo_areas(area_id,available_at,updated_at) VALUES(?,?,?)",
            (area_id, storage.now(), storage.now()),
        )
        stale = (datetime.now(timezone.utc) - RETRY_AGE).isoformat()
        db.execute(
            "UPDATE geo_areas SET status='queued',available_at=?,attempts=0 WHERE area_id=? "
            "AND status='unlocated' AND updated_at<?", (storage.now(), area_id, stale),
        )
        db.execute(
            "UPDATE geo_areas SET status='queued',available_at=?,attempts=0 WHERE area_id=? "
            "AND status='resolved' AND country_code IS NULL "
            "AND (resolver_version<2 OR updated_at<?)",
            (storage.now(), area_id, stale),
        )


def enqueue_artist_recheck(user_id, artist_id, updated_at):
    if not storage.is_stale(updated_at, RETRY_AGE):
        return
    with storage.connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO mb_jobs(user_id,kind,spotify_id,available_at) "
            "SELECT user_id,kind,spotify_id,? FROM mb_matches "
            "WHERE user_id=? AND kind='artist' AND spotify_id=? AND status='confirmed' AND mbid IS NOT NULL",
            (storage.now(), user_id, artist_id),
        )


def recover_areas():
    with storage.connect() as db:
        db.execute("UPDATE geo_areas SET status='queued',available_at=? WHERE status='processing'", (storage.now(),))
        db.execute("UPDATE geo_areas SET status='queued',available_at=? "
                   "WHERE status='resolved' AND country_code IS NULL AND resolver_version<2", (storage.now(),))


def _wikidata(qid):
    if not QID.fullmatch(qid):
        return {}
    query = urlencode({"action": "wbgetentities", "ids": qid, "props": "claims", "format": "json"})
    request = Request(f"https://www.wikidata.org/w/api.php?{query}", headers={
        "User-Agent": musicbrainz.USER_AGENT, "Accept": "application/json"})
    with urlopen(request, timeout=8) as response:
        return json.load(response).get("entities", {}).get(qid, {})


def _claim(entity, property_id, current=False, allow_deprecated=False):
    claims = entity.get("claims", {}).get(property_id, [])
    for claim in sorted(claims, key=lambda item: item.get("rank") != "preferred"):
        if claim.get("rank") == "deprecated" and not allow_deprecated:
            continue
        if current and "P582" in claim.get("qualifiers", {}):
            continue
        value = (claim.get("mainsnak") or {}).get("datavalue", {}).get("value")
        if value is not None:
            return value
    return None


def _resolve(area_id):
    area = musicbrainz.request(f"area/{area_id}", {"inc": "url-rels"})
    code = next(iter(area.get("iso-3166-1-codes") or []), None)
    if not code:
        code = next((value.split("-", 1)[0] for value in area.get("iso-3166-2-codes") or []
                     if value.split("-", 1)[0] in COUNTRIES), None)
    area_type = area.get("type")
    for relation in area.get("relations") or []:
        resource = (relation.get("url") or {}).get("resource") or ""
        match = re.fullmatch(r"https?://(?:www\.)?wikidata\.org/wiki/(Q[1-9][0-9]*)/?", resource)
        if relation.get("type") != "wikidata" or not match:
            continue
        entity = _wikidata(match.group(1))
        position = _claim(entity, "P625")
        nation = _claim(entity, "P17", current=True)
        if isinstance(nation, dict) and QID.fullmatch(nation.get("id") or ""):
            nation_entity = _wikidata(nation["id"])
            nation_code = (_claim(nation_entity, "P297") or
                           _claim(nation_entity, "P297", allow_deprecated=True))
            if isinstance(nation_code, str) and nation_code.upper() in COUNTRIES:
                code = nation_code.upper()
        if area_type in {"City", "Municipality", "District"} and isinstance(position, dict):
            lat, lon = position.get("latitude"), position.get("longitude")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon, code, area_type
    return None, None, code, area_type


def process_area_one():
    with storage.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT area_id,attempts,latitude,longitude FROM geo_areas "
                         "WHERE status='queued' AND available_at<=? "
                         "ORDER BY CASE WHEN latitude IS NOT NULL AND longitude IS NOT NULL "
                         "AND country_code IS NULL THEN 0 ELSE 1 END, available_at LIMIT 1",
                         (storage.now(),)).fetchone()
        if row:
            db.execute("UPDATE geo_areas SET status='processing' WHERE area_id=?", (row["area_id"],))
    if not row:
        return False
    try:
        lat, lon, code, area_type = _resolve(row["area_id"])
    except (musicbrainz.TemporaryFailure, HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError):
        delay = min(3600, 2 ** min(row["attempts"] + 1, 10))
        available = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        with storage.connect() as db:
            db.execute("UPDATE geo_areas SET status='queued',available_at=?,attempts=attempts+1,updated_at=? "
                       "WHERE area_id=?", (available, storage.now(), row["area_id"]))
        return True
    with storage.connect() as db:
        has_coordinates = (lat is not None and lon is not None) or (
            row["latitude"] is not None and row["longitude"] is not None)
        db.execute("UPDATE geo_areas SET status=?,latitude=COALESCE(?,latitude),longitude=COALESCE(?,longitude),"
                   "country_code=COALESCE(?,country_code),area_type=COALESCE(?,area_type),"
                   "resolver_version=2,updated_at=? WHERE area_id=?",
                   ("resolved" if has_coordinates else "unlocated", lat, lon, code,
                    area_type, storage.now(), row["area_id"]))
    return True


def map_data(user_id, source="followed", playlist_id=None):
    artists = artist_sources.collection(user_id, source, playlist_id) or []
    with storage.connect() as db:
        rows = db.execute(
            "WITH selected AS (SELECT json_extract(value,'$.id') AS artist_id,value AS payload FROM json_each(?)) "
            "SELECT f.artist_id,f.payload,m.status,m.enriched,m.updated_at AS match_updated_at,"
            "g.status AS geo_status,g.latitude,g.longitude,"
            "g.country_code AS geo_country,g.area_type FROM selected AS f "
            "LEFT JOIN mb_matches AS m ON m.user_id=? AND m.kind='artist' AND m.spotify_id=f.artist_id "
            "LEFT JOIN geo_areas AS g ON g.area_id=json_extract(m.enriched,'$.origin.external_id') "
            "ORDER BY f.artist_id", (json.dumps(artists), user_id),
        ).fetchall()
    markers = {}
    unknown = 0
    city_count = 0
    country_count = 0
    pending = 0
    for row in rows:
        if row["status"] != "confirmed" or not row["enriched"]:
            unknown += 1
            continue
        enriched = json.loads(row["enriched"])
        origin = enriched.get("origin") or {}
        area_id = origin.get("external_id")
        valid_area = bool(area_id and AREA_ID.fullmatch(area_id))
        city = (row["area_type"] in {"City", "Municipality", "District"}
                and row["latitude"] is not None and row["longitude"] is not None)
        if valid_area:
            enqueue_area(area_id)
        if not city:
            if valid_area and row["geo_status"] in (None, "queued", "processing"):
                pending += 1
            if not valid_area or "country_code" not in enriched:
                enqueue_artist_recheck(user_id, row["artist_id"], row["match_updated_at"])
        # The artist's country describes their current area, not necessarily their birth or formation area.
        code = row["geo_country"] if origin else enriched.get("country_code")
        country = COUNTRIES.get((code or "").upper())
        if city:
            lat, lon = row["latitude"], row["longitude"]
            label = origin.get("name") or "Ville inconnue"
            key = f"city:{area_id}"
            precision = "city"
            city_count += 1
        elif country:
            lat, lon = country["latitude"], country["longitude"]
            label = country["name"]
            key = f"country:{country['code']}"
            precision = "country"
            country_count += 1
        else:
            unknown += 1
            continue
        marker = markers.setdefault(key, {"id": key, "label": label, "latitude": lat, "longitude": lon,
                                          "precision": precision, "country_code": country["code"] if country else code,
                                          "country": country["name"] if country else None,
                                          "continent": country["continent"] if country else None, "artists": []})
        artist = json.loads(row["payload"])
        marker["artists"].append({"id": row["artist_id"], "name": artist.get("name") or "Artiste inconnu",
                                  "origin_type": origin.get("type"), "origin_name": origin.get("name"),
                                  "source": origin.get("source") or "musicbrainz", "precision": precision})
    for marker in markers.values():
        marker["artists"].sort(key=lambda item: item["name"].casefold())
    return {"markers": list(markers.values()), "counts": {"total": len(rows), "city": city_count,
            "country": country_count, "unknown": unknown, "pending": pending}}
