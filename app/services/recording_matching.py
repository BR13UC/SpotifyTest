"""MusicBrainz candidate search and recording selection for Spotify entities."""

import json
from difflib import SequenceMatcher

from app import storage
from app.services import artist_matching, mb_client


def _artist_candidates(payload):
    name = payload.get("name") or ""
    if not name.strip():
        return []
    return [mb_client._candidate(item) for item in artist_matching.browse("artist", {"query": f'artist:"{mb_client._query_text(name)}" OR alias:"{mb_client._query_text(name)}"'}, "artists")]


def _recording_candidates(payload):
    isrc = (payload.get("isrc") or "").replace("-", "").upper()
    entities = []
    from_isrc = False
    if isrc:
        result = mb_client.request(f"isrc/{isrc}", {"inc": "artist-credits"})
        entities = result.get("recordings", [])
        from_isrc = bool(entities)
    if not entities or not any(_recording_coherent(payload, mb_client._candidate(item)) for item in entities):
        title = mb_client._query_text(payload.get("name"))
        artists = payload.get("artists") or []
        if not title.strip():
            return []
        query = f'recording:"{title}"'
        if artists and artists[0].get("name"):
            query += f' AND artist:"{mb_client._query_text(artists[0]["name"])}"'
        result = mb_client.request("recording", {"query": query, "limit": 25})
        entities = result.get("recordings", [])
        from_isrc = False
    candidates = [mb_client._candidate(item) for item in entities if item.get("id")]
    if from_isrc:
        for candidate in candidates:
            candidate["isrcs"] = list(set(candidate["isrcs"]) | {isrc})
    return list({candidate["id"]: candidate for candidate in candidates}.values())


def _recording_coherent(payload, candidate):
    if mb_client._album_key(payload.get("name")) != mb_client._album_key(candidate.get("name")):
        return False
    if not _artist_overlap(payload, candidate):
        return False
    duration, length = payload.get("duration_ms"), candidate.get("length")
    return not (duration and length and abs(duration - length) > 15000)


def _artist_overlap(payload, candidate):
    artists = {mb_client._album_key(item.get("name")) for item in payload.get("artists") or [] if item.get("name")}
    credited = {mb_client._album_key(name) for name in candidate.get("artist_names") or [] if name}
    return bool(artists.intersection(credited))


def _spotify_url(spotify_id):
    return f"https://open.spotify.com/artist/{spotify_id}"


def _direct_links(spotify_ids):
    """Resolve up to 50 Spotify URLs in one rate limited MusicBrainz call."""
    if not spotify_ids:
        return {}
    urls = {_spotify_url(spotify_id): spotify_id for spotify_id in spotify_ids}
    result = mb_client.request("url", [("resource", url) for url in urls] + [("inc", "artist-rels")])
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
        result = mb_client.request("url", [("resource", url) for url in chunk] +
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


def _select(kind, payload, candidates):
    name = mb_client._fold(payload.get("name"))
    exact = [candidate for candidate in candidates if mb_client._fold(candidate["name"]) == name]
    if kind == "artist":
        return None
    spotify_artists = {mb_client._fold(artist.get("name")) for artist in payload.get("artists") or []}
    suitable = []
    for candidate in exact:
        mb_artists = {mb_client._fold(artist) for artist in candidate["artist_names"]}
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
        result = mb_client.request("release", {"recording": mbid, "inc": "release-groups+artist-credits",
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
    album_key = mb_client._album_key(payload.get("album_name"))
    if release.get("id") in linked["releases"] or release.get("group_id") in linked["groups"]:
        return "spotify_album_url"
    if album_key and album_key not in mb_client.GENERIC_ALBUMS and album_key == mb_client._album_key(release.get("title")):
        return "release_title"
    return None


def _release_rank(payload, release, linked):
    album_key = mb_client._album_key(payload.get("album_name"))
    title_match = bool(album_key and album_key not in mb_client.GENERIC_ALBUMS
                       and album_key == mb_client._album_key(release.get("title")))
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
    title_ratio = SequenceMatcher(None, mb_client._album_key(payload.get("name")), mb_client._album_key(candidate.get("name"))).ratio()
    title_points = 30 if title_ratio == 1 else 20 if title_ratio >= .9 else 0
    score += title_points
    if title_points:
        signals.append("titre")
    artists = {mb_client._album_key(item.get("name")) for item in payload.get("artists") or [] if item.get("name")}
    credited = {mb_client._album_key(name) for name in candidate.get("artist_names") or [] if name}
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
            entity = mb_client.request(f"recording/{linked_id}", {"inc": "artist-credits+isrcs"})
            if entity.get("id"):
                candidates = [*candidates, mb_client._candidate(entity)]
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
        None, mb_client._album_key(payload.get("name")), mb_client._album_key(item.get("name"))).ratio() >= .85]
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
