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
    assert "timezone" in data
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
    assert "timezone" in data
    assert "timings" in data


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
