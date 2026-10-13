import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import create_app, spotify_auth, storage
from app.routes import pages
from app.services import geography, mb_client, mb_queue, recording_matching


PARIS = "11111111-1111-1111-1111-111111111111"
LONDON = "22222222-2222-2222-2222-222222222222"


def test_country_codes_do_not_use_postal_abbreviations():
    assert geography.COUNTRIES["IS"]["name"] == "Iceland"
    assert geography.COUNTRIES["IL"]["name"] == "Israel"
    assert geography.COUNTRIES["IS"]["latitude"] > 60


def test_existing_geography_table_gets_resolver_version(tmp_path):
    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE geo_areas (area_id TEXT PRIMARY KEY,status TEXT NOT NULL,latitude REAL,"
                   "longitude REAL,country_code TEXT,area_type TEXT,attempts INTEGER NOT NULL DEFAULT 0,"
                   "available_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
        db.execute("INSERT INTO geo_areas(area_id,status,latitude,longitude,area_type,available_at,updated_at) "
                   "VALUES(?,?,?,?,?,?,?)", (PARIS, "resolved", 55.75, 37.62, "City", storage.now(), storage.now()))
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "DATABASE_PATH": str(database), "SECRET_KEY": "test"})
    with app.app_context():
        with storage.connect() as db:
            assert db.execute("SELECT resolver_version FROM geo_areas WHERE area_id=?", (PARIS,)).fetchone()[0] == 0


def test_artist_current_country_does_not_become_origin_country(monkeypatch):
    monkeypatch.setattr(mb_client, "request", lambda *_: {
        "id": PARIS, "name": "Artist", "type": "Person", "country": "GB",
        "begin-area": {"id": LONDON, "name": "Cape Town"},
    })
    details = mb_client._details("artist", PARIS)
    assert details["country_code"] == "GB"
    assert details["origin"]["country_code"] is None


def test_non_city_area_can_still_resolve_its_country(monkeypatch):
    monkeypatch.setattr(geography.mb_client, "request", lambda *_: {
        "type": "Subdivision", "relations": [
            {"type": "wikidata", "url": {"resource": "https://www.wikidata.org/wiki/Q1"}},
        ],
    })
    monkeypatch.setattr(geography, "_wikidata", lambda qid: {
        "claims": {"P17": [{"mainsnak": {"datavalue": {"value": {"id": "Q2"}}}}]},
    } if qid == "Q1" else {"claims": {"P297": [{"mainsnak": {"datavalue": {"value": "ZA"}}}]}})
    assert geography._resolve(PARIS) == (None, None, "ZA", "Subdivision")


def test_historical_country_does_not_hide_current_country():
    entity = {"claims": {"P17": [
        {"rank": "normal", "qualifiers": {"P582": [{}]},
         "mainsnak": {"datavalue": {"value": {"id": "QOLD"}}}},
        {"rank": "preferred", "mainsnak": {"datavalue": {"value": {"id": "Q159"}}}},
    ]}}
    assert geography._claim(entity, "P17", current=True)["id"] == "Q159"


def test_deprecated_country_code_can_be_used_for_current_country(monkeypatch):
    monkeypatch.setattr(geography.mb_client, "request", lambda *_: {
        "type": "City", "relations": [
            {"type": "wikidata", "url": {"resource": "https://www.wikidata.org/wiki/Q727"}},
        ],
    })
    monkeypatch.setattr(geography, "_wikidata", lambda qid: {
        "claims": {
            "P17": [{"rank": "preferred", "mainsnak": {"datavalue": {"value": {"id": "Q55"}}}}],
            "P625": [{"mainsnak": {"datavalue": {"value": {"latitude": 52.37, "longitude": 4.89}}}}],
        },
    } if qid == "Q727" else {"claims": {
        "P297": [{"rank": "deprecated", "mainsnak": {"datavalue": {"value": "NL"}}}],
    }})
    assert geography._resolve(PARIS) == (52.37, 4.89, "NL", "City")


def test_area_iso_3166_2_supplies_country(monkeypatch):
    monkeypatch.setattr(geography.mb_client, "request", lambda *_: {
        "type": "City", "iso-3166-2-codes": ["RU-MOW"], "relations": [],
    })
    assert geography._resolve(PARIS) == (None, None, "RU", "City")


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = create_app({"TESTING": True, "DATA_DIR": str(tmp_path), "SECRET_KEY": "test"})
    monkeypatch.setattr(spotify_auth, "is_valid_token", lambda: True)
    monkeypatch.setattr(pages, "is_valid_token", lambda: True)
    return app


def login(client, user):
    with client.session_transaction() as session:
        session["spotify_user_id"] = user
        session["csrf_token"] = "test"


def add_artist(user, spotify_id, status="confirmed", origin=None, country="FR"):
    storage.replace_followed_artists(user, [{"id": spotify_id, "name": spotify_id}])
    enriched = {"country_code": country, "origin": origin} if status == "confirmed" else None
    with storage.connect() as db:
        db.execute("INSERT INTO mb_matches(user_id,kind,spotify_id,spotify_payload,status,mbid,enriched,updated_at) "
                   "VALUES(?,?,?,?,?,?,?,?)", (user, "artist", spotify_id, json.dumps({"name": spotify_id}),
                                            status, PARIS if status == "confirmed" else None,
                                            json.dumps(enriched) if enriched else None, storage.now()))


def test_city_resolution_and_country_fallback(app, monkeypatch):
    def musicbrainz_request(path, params):
        assert path == f"area/{PARIS}"
        assert params == {"inc": "url-rels"}
        return {"type": "City", "relations": [{"type": "wikidata", "url": {"resource": "https://www.wikidata.org/wiki/Q90"}}]}

    def wikidata(qid):
        if qid == "Q90":
            return {"claims": {"P625": [{"mainsnak": {"datavalue": {"value": {"latitude": 48.8566, "longitude": 2.3522}}}}],
                               "P17": [{"mainsnak": {"datavalue": {"value": {"id": "Q142"}}}}]}}
        return {"claims": {"P297": [{"mainsnak": {"datavalue": {"value": "FR"}}}]}}

    monkeypatch.setattr(geography.mb_client, "request", musicbrainz_request)
    monkeypatch.setattr(geography, "_wikidata", wikidata)
    with app.app_context():
        add_artist("alice", "paris", origin={"external_id": PARIS, "name": "Paris", "type": "birth", "source": "musicbrainz"}, country="NO")
        add_artist("bob", "unplaced", origin={"external_id": LONDON, "name": "Ville", "type": "formation", "source": "musicbrainz"})
    client = app.test_client()
    login(client, "alice")
    assert client.get("/map").status_code == 200
    first = client.get("/map/data").json
    assert first["counts"] == {"total": 1, "city": 0, "country": 0, "unknown": 1, "pending": 1}
    assert first["markers"] == []
    with app.app_context():
        assert geography.process_area_one()
    resolved = client.get("/map/data").json
    assert resolved["counts"]["city"] == 1
    assert resolved["markers"][0]["latitude"] == 48.8566
    assert resolved["markers"][0]["country_code"] == "FR"
    assert resolved["markers"][0]["continent"] == "Europe"
    assert resolved["markers"][0]["artists"][0]["origin_type"] == "birth"
    login(client, "bob")
    other = client.get("/map/data").json
    assert other["counts"]["city"] == 0
    assert other["counts"]["total"] == 1
    assert other["markers"] == []


def test_unknown_and_unconfirmed_are_not_placed(app):
    with app.app_context():
        add_artist("alice", "ambiguous", status="ambiguous")
        storage.replace_followed_artists("alice", [
            {"id": "ambiguous", "name": "Ambiguous"}, {"id": "missing", "name": "Missing"}])
    client = app.test_client()
    login(client, "alice")
    data = client.get("/map/data").json
    assert data["markers"] == []
    assert data["counts"]["unknown"] == 2


def test_map_api_requires_account(app, monkeypatch):
    monkeypatch.setattr(spotify_auth, "is_valid_token", lambda: False)
    client = app.test_client()
    assert client.get("/map/data").status_code == 401


def test_unlocated_area_keeps_confirmed_country_and_is_cached(app, monkeypatch):
    monkeypatch.setattr(geography.mb_client, "request", lambda *_: {"type": "City", "iso-3166-1-codes": ["FR"], "relations": []})
    with app.app_context():
        add_artist("alice", "artist", origin={"external_id": PARIS, "name": "Ville", "type": "formation"})
    client = app.test_client()
    login(client, "alice")
    client.get("/map/data")
    with app.app_context():
        assert geography.process_area_one()
        assert not geography.process_area_one()
    result = client.get("/map/data").json
    assert result["counts"]["country"] == 1
    assert result["counts"]["pending"] == 0
    assert result["markers"][0]["precision"] == "country"
    assert result["markers"][0]["continent"] == "Europe"


def test_artist_country_is_not_used_for_an_unresolved_origin(app):
    with app.app_context():
        add_artist("alice", "artist", origin={"external_id": PARIS, "name": "Cape Town", "type": "birth",
                                              "country_code": "SE"}, country="SE")
    client = app.test_client()
    login(client, "alice")
    pending = client.get("/map/data").json
    assert pending["markers"] == []
    with app.app_context():
        with storage.connect() as db:
            db.execute("UPDATE geo_areas SET status='resolved',latitude=?,longitude=?,country_code=?,area_type=? "
                       "WHERE area_id=?", (-33.9249, 18.4241, "ZA", "City", PARIS))
    resolved = client.get("/map/data").json
    assert resolved["counts"]["city"] == 1
    assert resolved["markers"][0]["country_code"] == "ZA"
    assert resolved["markers"][0]["continent"] == "Africa"
    assert resolved["markers"][0]["latitude"] == -33.9249


def test_artists_at_same_place_share_one_marker(app):
    with app.app_context():
        for artist_id in ("first", "second"):
            add_artist("alice", artist_id, origin={"external_id": PARIS, "name": "Paris", "type": "birth"})
        storage.replace_followed_artists("alice", [
            {"id": "first", "name": "First"}, {"id": "second", "name": "Second"}])
        with storage.connect() as db:
            db.execute("INSERT INTO geo_areas(area_id,status,latitude,longitude,country_code,area_type,available_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?)", (PARIS, "resolved", 48.8566, 2.3522, "FR", "City",
                                               storage.now(), storage.now()))
    client = app.test_client()
    login(client, "alice")
    data = client.get("/map/data").json
    assert len(data["markers"]) == 1
    assert {artist["id"] for artist in data["markers"][0]["artists"]} == {"first", "second"}
    assert data["counts"]["city"] == 2


def test_saved_city_survives_restart_without_retry(app):
    with app.app_context():
        add_artist("alice", "artist", origin={"external_id": PARIS, "name": "Paris", "type": "birth"})
        with storage.connect() as db:
            db.execute("INSERT INTO geo_areas(area_id,status,latitude,longitude,country_code,area_type,available_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?,?)", (PARIS, "resolved", 48.8566, 2.3522, "FR", "City",
                                               storage.now(), (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()))
    restarted = create_app({"TESTING": True, "DATA_DIR": app.config["DATA_DIR"], "SECRET_KEY": "test"})
    client = restarted.test_client()
    login(client, "alice")
    data = client.get("/map/data").json
    assert data["counts"]["city"] == 1
    assert data["markers"][0]["latitude"] == 48.8566
    with restarted.app_context():
        with storage.connect() as db:
            assert db.execute("SELECT status FROM geo_areas WHERE area_id=?", (PARIS,)).fetchone()[0] == "resolved"
            assert db.execute("SELECT COUNT(*) FROM mb_jobs").fetchone()[0] == 0


def test_saved_city_without_country_is_backfilled_without_losing_its_marker(app, monkeypatch):
    with app.app_context():
        add_artist("alice", "moscow", origin={"external_id": PARIS, "name": "Moscow", "type": "birth"}, country="RU")
        with storage.connect() as db:
            db.execute("INSERT INTO geo_areas(area_id,status,latitude,longitude,area_type,available_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?)", (PARIS, "resolved", 55.7506, 37.6175, "City",
                                               storage.now(), storage.now()))
    client = app.test_client()
    login(client, "alice")
    first = client.get("/map/data").json
    assert first["markers"][0]["latitude"] == 55.7506
    assert first["markers"][0]["country_code"] is None
    assert first["markers"][0]["continent"] is None
    with app.app_context():
        with storage.connect() as db:
            assert db.execute("SELECT status FROM geo_areas WHERE area_id=?", (PARIS,)).fetchone()[0] == "queued"
        monkeypatch.setattr(geography, "_resolve", lambda _: (55.7506, 37.6175, "RU", "City"))
        assert geography.process_area_one()
    second = client.get("/map/data").json
    assert second["markers"][0]["country_code"] == "RU"
    assert second["markers"][0]["latitude"] == 55.7506
    with app.app_context():
        with storage.connect() as db:
            assert db.execute("SELECT resolver_version FROM geo_areas WHERE area_id=?", (PARIS,)).fetchone()[0] == 2


def test_worker_recovers_old_cities_without_country_on_startup(app):
    with app.app_context():
        with storage.connect() as db:
            db.execute("INSERT INTO geo_areas(area_id,status,latitude,longitude,area_type,available_at,updated_at) "
                       "VALUES(?,?,?,?,?,?,?)", (PARIS, "resolved", 51.5072, -0.1276, "City",
                                               storage.now(), storage.now()))
        geography.recover_areas()
        with storage.connect() as db:
            row = db.execute("SELECT status,latitude,longitude FROM geo_areas WHERE area_id=?", (PARIS,)).fetchone()
            assert tuple(row) == ("queued", 51.5072, -0.1276)


def test_unlocated_city_retries_after_thirty_days_without_hiding_country(app, monkeypatch):
    with app.app_context():
        add_artist("alice", "artist", origin={"external_id": PARIS, "name": "Paris", "type": "birth"})
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        with storage.connect() as db:
            db.execute("INSERT INTO geo_areas(area_id,status,country_code,area_type,available_at,updated_at) "
                       "VALUES(?,?,?,?,?,?)", (PARIS, "unlocated", "FR", "City", old, old))
    client = app.test_client()
    login(client, "alice")
    data = client.get("/map/data").json
    assert data["markers"][0]["precision"] == "country"
    with app.app_context():
        with storage.connect() as db:
            assert db.execute("SELECT status FROM geo_areas WHERE area_id=?", (PARIS,)).fetchone()[0] == "queued"
        monkeypatch.setattr(geography, "_resolve", lambda area_id: (48.8566, 2.3522, "FR", "City"))
        assert geography.process_area_one()
    assert client.get("/map/data").json["counts"]["city"] == 1


def test_recent_unlocated_city_is_not_retried(app):
    with app.app_context():
        add_artist("alice", "artist", origin={"external_id": PARIS, "name": "Paris", "type": "birth"})
        with storage.connect() as db:
            db.execute("INSERT INTO geo_areas(area_id,status,country_code,area_type,available_at,updated_at) "
                       "VALUES(?,?,?,?,?,?)", (PARIS, "unlocated", "FR", "City", storage.now(), storage.now()))
    client = app.test_client()
    login(client, "alice")
    assert client.get("/map/data").json["counts"]["country"] == 1
    with app.app_context():
        with storage.connect() as db:
            assert db.execute("SELECT status FROM geo_areas WHERE area_id=?", (PARIS,)).fetchone()[0] == "unlocated"


def test_country_only_artist_rechecks_after_thirty_days_per_account(app, monkeypatch):
    with app.app_context():
        add_artist("alice", "old", origin=None)
        add_artist("bob", "recent", origin=None)
        old = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
        with storage.connect() as db:
            db.execute("UPDATE mb_matches SET updated_at=? WHERE user_id='alice'", (old,))
    client = app.test_client()
    login(client, "alice")
    assert client.get("/map/data").json["counts"]["country"] == 1
    with app.app_context():
        with storage.connect() as db:
            jobs = db.execute("SELECT user_id,spotify_id FROM mb_jobs").fetchall()
            assert [(row["user_id"], row["spotify_id"]) for row in jobs] == [("alice", "old")]
    login(client, "bob")
    assert client.get("/map/data").json["counts"]["country"] == 1
    with app.app_context():
        with storage.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM mb_jobs").fetchone()[0] == 1
        monkeypatch.setattr(recording_matching, "_direct_links", lambda artist_ids: {})
        monkeypatch.setattr(mb_client, "_details", lambda kind, mbid: {
            "name": "Old", "country_code": "FR",
            "origin": {"external_id": PARIS, "name": "Paris", "type": "birth", "source": "musicbrainz"},
        })
        assert mb_queue.process_one()
        with storage.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM mb_jobs").fetchone()[0] == 0
    login(client, "alice")
    assert client.get("/map/data").json["markers"] == []
    with app.app_context():
        with storage.connect() as db:
            assert db.execute("SELECT status FROM geo_areas WHERE area_id=?", (PARIS,)).fetchone()[0] == "queued"
