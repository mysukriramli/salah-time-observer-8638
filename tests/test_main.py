from datetime import datetime
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert "Global Salah Time Observer" in data["service"]


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_tools():
    response = client.get("/tools")
    assert response.status_code == 200
    data = response.json()
    assert "tools" in data
    assert isinstance(data["tools"], list)


def test_salah_get_valid():
    response = client.get("/salah?latitude=21.4225&longitude=39.8262")
    assert response.status_code == 200
    data = response.json()
    assert "target_timezone" in data
    assert "timings" in data

    timings = data["timings"]
    for prayer in ["fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"]:
        assert prayer in timings
        assert "target_local_iso" in timings[prayer]
        assert "utc_iso" in timings[prayer]


def test_salah_get_with_parameters():
    response = client.get(
        "/salah?latitude=3.2028&longitude=73.2207&date=2026-11-03&prayer=fajr&method=MUSLIM_WORLD_LEAGUE"
    )
    assert response.status_code == 200
    data = response.json()
    assert "timings" in data
    assert "fajr" in data["timings"]


def test_salah_alias_normalization_zohor():
    # Validates that 'zohor' maps to canonical 'dhuhr' without raising an error
    response = client.get(
        "/salah?latitude=3.2028&longitude=73.2207&date=2026-11-03&prayer=zohor"
    )
    assert response.status_code == 200
    data = response.json()
    assert "timings" in data
    assert "dhuhr" in data["timings"]


def test_salah_maldives_from_singapore_projection():
    # Maldives is UTC+5, Singapore is UTC+8 (+3 hours)
    response = client.get(
        "/salah?latitude=4.1755&longitude=73.5093&date=tomorrow&prayer=zohor&requester_timezone=Asia/Singapore"
    )
    assert response.status_code == 200
    data = response.json()
    assert "dhuhr" in data["timings"]

    dhuhr_data = data["timings"]["dhuhr"]
    assert "+05:00" in dhuhr_data["target_local_iso"]
    assert "+08:00" in dhuhr_data["requester_local_iso"]
    assert "Z" in dhuhr_data["utc_iso"] or "+00:00" in dhuhr_data["utc_iso"]

    # Verify 3 hours difference between requester (UTC+8) and target (UTC+5)
    target_dt = datetime.fromisoformat(dhuhr_data["target_local_iso"])
    requester_dt = datetime.fromisoformat(dhuhr_data["requester_local_iso"])
    assert int((requester_dt.utcoffset() - target_dt.utcoffset()).total_seconds()) == 10800


def test_salah_post_valid():
    response = client.post(
        "/salah",
        json={
            "latitude": 21.4225,
            "longitude": 39.8262,
            "date": "2026-11-03",
            "prayer": "asr",
            "method": "ISNA",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "target_timezone" in data
    assert "timings" in data
    assert "asr" in data["timings"]


def test_salah_missing_coordinates_and_location():
    # Neither coordinates nor location provided
    response = client.get("/salah")
    assert response.status_code == 400

    post_resp = client.post("/salah", json={})
    assert post_resp.status_code == 400


def test_invalid_prayer_name():
    response = client.get("/salah?latitude=21.4225&longitude=39.8262&prayer=nonexistent_prayer")
    assert response.status_code == 400


def test_invalid_coordinates_get_latitude_out_of_bounds():
    response = client.get("/salah?latitude=120.0&longitude=39.8262")
    assert response.status_code in [400, 422]


def test_invalid_coordinates_get_longitude_out_of_bounds():
    response = client.get("/salah?latitude=21.4225&longitude=-200.0")
    assert response.status_code in [400, 422]


def test_invalid_coordinates_post():
    response = client.post(
        "/salah",
        json={
            "latitude": -95.0,
            "longitude": 39.8262,
        },
    )
    assert response.status_code in [400, 422]
