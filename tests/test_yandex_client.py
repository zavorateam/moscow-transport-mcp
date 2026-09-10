"""
Тесты для YandexTransportClient.

Запуск:
    pytest -q test_yandex_client.py
    pytest -s -q test_yandex_client.py --log-cli-level=DEBUG
"""
import logging
import time

import httpx
import pytest

from clients.yandex_client import (
    YandexTransportClient,
    extract_coords_safely,
    haversine_km,
)

log = logging.getLogger("tests.yandex")


# =========================================================================== #
#  Хелперы                                                                    #
# =========================================================================== #
def _mock_httpx(monkeypatch, handler):
    """Подменяет httpx.AsyncClient так, чтобы все запросы шли через handler."""
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return transport


@pytest.fixture
def client(monkeypatch):
    """Клиент с чистыми env-переменными (никаких случайных cookie)."""
    for k in (
        "YANDEX_CSRF_TOKEN", "YANDEX_SESSION_ID",
        "YANDEX_S_VEHICLES", "YANDEX_S_STOPS",
        "YANDEX_COOKIE",
    ):
        monkeypatch.delenv(k, raising=False)
    return YandexTransportClient()


# =========================================================================== #
#  1. extract_coords_safely                                                   #
# =========================================================================== #
def test_extract_coords_empty_geom():
    lat, lon, spd, course = extract_coords_safely({})
    log.debug("empty: %s", (lat, lon, spd, course))
    assert (lat, lon, spd, course) == (None, None, 0.0, 0.0)


def test_extract_coords_flat_lon_lat():
    """Плоский [lon, lat] → (lat, lon, 0, 0)."""
    lat, lon, spd, course = extract_coords_safely({"coordinates": [37.5, 55.7]})
    log.debug("flat: %s", (lat, lon, spd, course))
    assert lat == 55.7
    assert lon == 37.5
    assert spd == 0.0
    assert course == 0.0


def test_extract_coords_single_point_nested():
    """Одна точка в nested-формате — без курса."""
    lat, lon, spd, course = extract_coords_safely({"coordinates": [[37.5, 55.7]]})
    log.debug("single nested: %s", (lat, lon, spd, course))
    assert lat == 55.7
    assert lon == 37.5
    assert course == 0.0


def test_extract_coords_two_points_computes_course():
    """Две точки — считается курс от первой ко второй."""
    geom = {"coordinates": [[37.5, 55.7], [37.6, 55.8]]}
    lat, lon, spd, course = extract_coords_safely(geom)
    log.debug("two points: %s", (lat, lon, spd, course))
    assert lat == 55.7
    assert lon == 37.5
    # Движение на северо-восток, курс в диапазоне (0, 90)
    assert 0 < course < 90


def test_extract_coords_deeply_nested():
    """Тройная вложенность [[[lon, lat]], ...] — берём первую точку из [0][0]."""
    geom = {"coordinates": [[[37.5, 55.7]], [37.6, 55.8]]}
    lat, lon, spd, course = extract_coords_safely(geom)
    log.debug("deeply nested: %s", (lat, lon, spd, course))
    assert lat == 55.7
    assert lon == 37.5
    assert 0 < course < 90


def test_extract_coords_short_point_returns_none():
    """Точка из одного числа — невалидна."""
    lat, lon, spd, course = extract_coords_safely({"coordinates": [[37.5]]})
    assert (lat, lon, spd, course) == (None, None, 0.0, 0.0)


def test_extract_coords_west_course():
    """Строго на запад — курс ≈ 270."""
    geom = {"coordinates": [[37.6, 55.7], [37.5, 55.7]]}
    lat, lon, spd, course = extract_coords_safely(geom)
    log.debug("west course=%s", course)
    assert course == pytest.approx(270, abs=1.0)


def test_extract_coords_north_course():
    """Строго на север — курс ≈ 0 (или 360)."""
    geom = {"coordinates": [[37.5, 55.7], [37.5, 55.8]]}
    lat, lon, spd, course = extract_coords_safely(geom)
    log.debug("north course=%s", course)
    assert min(course, 360 - course) == pytest.approx(0, abs=1.0)


# =========================================================================== #
#  2. Инициализация клиента                                                   #
# =========================================================================== #
def test_init_default_tokens(monkeypatch):
    for k in ("YANDEX_CSRF_TOKEN", "YANDEX_SESSION_ID",
              "YANDEX_S_VEHICLES", "YANDEX_S_STOPS", "YANDEX_COOKIE"):
        monkeypatch.delenv(k, raising=False)

    c = YandexTransportClient()
    assert c.csrf_token == YandexTransportClient.DEFAULT_CSRF
    assert c.session_id == YandexTransportClient.DEFAULT_SESSION_ID
    assert c.s_vehicles == YandexTransportClient.DEFAULT_S_VEHICLES
    assert c.s_stops == YandexTransportClient.DEFAULT_S_STOPS
    assert "Cookie" not in c.headers


def test_init_env_override(monkeypatch):
    monkeypatch.setenv("YANDEX_CSRF_TOKEN", "custom-csrf")
    monkeypatch.setenv("YANDEX_SESSION_ID", "custom-session")
    monkeypatch.setenv("YANDEX_S_VEHICLES", "111")
    monkeypatch.setenv("YANDEX_S_STOPS", "222")
    monkeypatch.setenv("YANDEX_COOKIE", "sessionid=abc")

    c = YandexTransportClient()
    assert c.csrf_token == "custom-csrf"
    assert c.session_id == "custom-session"
    assert c.s_vehicles == "111"
    assert c.s_stops == "222"
    assert c.headers["Cookie"] == "sessionid=abc"


def test_init_cookie_argument(monkeypatch):
    for k in ("YANDEX_CSRF_TOKEN", "YANDEX_SESSION_ID",
              "YANDEX_S_VEHICLES", "YANDEX_S_STOPS", "YANDEX_COOKIE"):
        monkeypatch.delenv(k, raising=False)

    c = YandexTransportClient(cookie="my=1")
    assert c.headers["Cookie"] == "my=1"


def test_init_env_cookie_wins_over_argument(monkeypatch):
    monkeypatch.setenv("YANDEX_COOKIE", "from_env=1")
    c = YandexTransportClient(cookie="from_arg=1")
    assert c.headers["Cookie"] == "from_env=1"


def test_init_without_cookie_no_header(monkeypatch):
    for k in ("YANDEX_CSRF_TOKEN", "YANDEX_SESSION_ID",
              "YANDEX_S_VEHICLES", "YANDEX_S_STOPS", "YANDEX_COOKIE"):
        monkeypatch.delenv(k, raising=False)
    c = YandexTransportClient()
    log.debug("headers=%s", c.headers)
    assert "Cookie" not in c.headers


# =========================================================================== #
#  3. search_stops                                                            #
# =========================================================================== #
SEARCH_RESPONSE = {
    "data": {
        "items": [
            {
                "uri": "ymapsbm1://transit/stop?id=stop__123",
                "title": "Метро Сокол",
                "coordinates": [37.515, 55.805],
            },
            {
                "uri": "ymapsbm1://transit/stop?id=stop__456",
                "title": "Дубровка",
                "coordinates": [37.67, 55.72],
            },
        ]
    }
}


async def test_search_stops_adds_prefix(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=SEARCH_RESPONSE)

    _mock_httpx(monkeypatch, handler)
    results = await client.search_stops("Сокол")

    log.debug("params: %s", captured["params"])
    assert captured["params"]["text"] == "остановка Сокол"
    assert captured["params"]["csrfToken"] == client.csrf_token
    assert captured["params"]["s"] == client.s_stops
    assert len(results) == 2
    assert results[0]["id"] == "stop__123"
    assert results[0]["name"] == "Метро Сокол"
    assert results[0]["latitude"] == 55.805
    assert results[0]["longitude"] == 37.515


async def test_search_stops_no_double_prefix(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": {"items": []}})

    _mock_httpx(monkeypatch, handler)
    await client.search_stops("Остановка Сокол")
    assert captured["params"]["text"] == "Остановка Сокол"


async def test_search_stops_with_coords_adds_ll_and_spn(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": {"items": []}})

    _mock_httpx(monkeypatch, handler)
    await client.search_stops("Сокол", lat=55.7, lon=37.6)

    assert captured["params"]["ll"] == "37.6,55.7"
    assert captured["params"]["spn"] == "0.08,0.08"


async def test_search_stops_without_coords_no_ll(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": {"items": []}})

    _mock_httpx(monkeypatch, handler)
    await client.search_stops("Сокол")
    assert "ll" not in captured["params"]
    assert "spn" not in captured["params"]


async def test_search_stops_http_error_returns_empty(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    _mock_httpx(monkeypatch, handler)
    results = await client.search_stops("Сокол")
    assert results == []


async def test_search_stops_network_error_returns_empty(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    _mock_httpx(monkeypatch, handler)
    results = await client.search_stops("Сокол")
    assert results == []


async def test_search_stops_missing_coordinates(monkeypatch, client):
    """Отсутствие coordinates — None, не падаем."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"items": [
            {"uri": "ymapsbm1://transit/stop?id=x1", "title": "A"}
        ]}})

    _mock_httpx(monkeypatch, handler)
    results = await client.search_stops("X")
    assert results[0]["latitude"] is None
    assert results[0]["longitude"] is None


async def test_search_stops_extracts_id_from_uri(monkeypatch, client):
    """ID извлекается regex'ом 'id=([a-zA-Z0-9_-]+)'."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"items": [
            {"uri": "ymapsbm1://transit/stop?id=abc_123-XYZ",
             "title": "T", "coordinates": [0.0, 0.0]}
        ]}})

    _mock_httpx(monkeypatch, handler)
    results = await client.search_stops("T")
    assert results[0]["id"] == "abc_123-XYZ"


async def test_search_stops_fallback_to_item_id(monkeypatch, client):
    """Если в uri нет id=... — берём item['id']."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"items": [
            {"uri": "no-id-here", "id": "fallback-id",
             "title": "T", "coordinates": [0.0, 0.0]}
        ]}})

    _mock_httpx(monkeypatch, handler)
    results = await client.search_stops("T")
    assert results[0]["id"] == "fallback-id"


async def test_search_stops_sends_referer(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"data": {"items": []}})

    _mock_httpx(monkeypatch, handler)
    await client.search_stops("X")
    log.debug("headers: %s", captured["headers"])
    assert "yandex.ru/maps" in captured["headers"]["referer"]


# =========================================================================== #
#  4. geocode_stop                                                            #
# =========================================================================== #
async def test_geocode_stop_returns_first(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=SEARCH_RESPONSE)

    _mock_httpx(monkeypatch, handler)
    result = await client.geocode_stop("Сокол")
    assert result is not None
    assert result["id"] == "stop__123"


async def test_geocode_stop_returns_none_when_empty(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"items": []}})

    _mock_httpx(monkeypatch, handler)
    result = await client.geocode_stop("Ничего")
    assert result is None


async def test_geocode_stop_passes_coords(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": {"items": []}})

    _mock_httpx(monkeypatch, handler)
    await client.geocode_stop("Сокол", lat=55.7, lon=37.6)
    assert captured["params"]["ll"] == "37.6,55.7"


# =========================================================================== #
#  5. get_vehicles                                                            #
# =========================================================================== #
def _vehicle_response(transport_id="moscow|abc-def-123", duration=600):
    now = time.time()
    return {
        "data": {
            "vehicles": [
                {
                    "properties": {
                        "VehicleMetaData": {
                            "Transport": {
                                "id": transport_id,
                                "name": "т86",
                                "type": "bus",
                            }
                        }
                    },
                    "features": [
                        {
                            "geometry": {
                                "coordinates": [[37.5, 55.7], [37.6, 55.8]],
                            },
                            "properties": {
                                "TrajectorySegmentMetaData": {
                                    "time": now - 60,
                                    "duration": duration,
                                }
                            },
                        }
                    ],
                }
            ]
        }
    }


async def test_get_vehicles_basic(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_vehicle_response())

    _mock_httpx(monkeypatch, handler)
    vehicles = await client.get_vehicles(55.7, 37.6)

    log.debug("params: %s", captured["params"])
    log.debug("vehicles: %s", vehicles)

    assert captured["params"]["ll"] == "37.6,55.7"
    assert captured["params"]["spn"] == "0.04,0.02"
    assert captured["params"]["s"] == client.s_vehicles

    assert len(vehicles) == 1
    v = vehicles[0]
    assert v["route_number"] == "т86"
    assert v["transport_type"] == "bus"
    assert v["latitude"] == 55.7
    assert v["longitude"] == 37.5
    assert 0 < v["course"] < 90
    assert v["source"] == "yandex"
    assert v["coordinates_source"] == "yandex"


async def test_get_vehicles_custom_spn(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": {"vehicles": []}})

    _mock_httpx(monkeypatch, handler)
    await client.get_vehicles(55.7, 37.6, spn_lon=0.1, spn_lat=0.05)
    assert captured["params"]["spn"] == "0.1,0.05"


async def test_get_vehicles_clean_uuid_with_url_encoding(monkeypatch, client):
    """id URL-кодирован: moscow%7Cabc-def → 'moscow|abc-def' → clean='abcdef'."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_vehicle_response("moscow%7Cabc-def"))

    _mock_httpx(monkeypatch, handler)
    vehicles = await client.get_vehicles(55.7, 37.6)
    log.debug("clean_uuid=%r", vehicles[0]["clean_uuid"])
    assert vehicles[0]["clean_uuid"] == "abcdef"


async def test_get_vehicles_clean_uuid_no_pipe(monkeypatch, client):
    """Без '|' в id clean_uuid пустой."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_vehicle_response("moscow-only"))

    _mock_httpx(monkeypatch, handler)
    vehicles = await client.get_vehicles(55.7, 37.6)
    assert vehicles[0]["clean_uuid"] == ""


async def test_get_vehicles_clean_uuid_strips_dashes_and_lowercases(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_vehicle_response("moscow|AB-CD-EF"))

    _mock_httpx(monkeypatch, handler)
    vehicles = await client.get_vehicles(55.7, 37.6)
    assert vehicles[0]["clean_uuid"] == "abcdef"


async def test_get_vehicles_speed_from_duration(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_vehicle_response(duration=600))

    _mock_httpx(monkeypatch, handler)
    vehicles = await client.get_vehicles(55.7, 37.6)

    expected_d_km = haversine_km(55.7, 37.5, 55.8, 37.6)
    expected_speed = round(expected_d_km / (600 / 3600.0), 1)
    log.debug("expected speed=%s, got=%s", expected_speed, vehicles[0]["speed_kmh"])
    assert vehicles[0]["speed_kmh"] == expected_speed


async def test_get_vehicles_no_features(monkeypatch, client):
    """Без features — координаты None, скорость 0."""
    response = {
        "data": {
            "vehicles": [{
                "properties": {"VehicleMetaData": {"Transport": {"id": "moscow|a", "name": "1"}}},
                "features": [],
            }]
        }
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    _mock_httpx(monkeypatch, handler)
    vehicles = await client.get_vehicles(55.7, 37.6)
    v = vehicles[0]
    assert v["latitude"] is None
    assert v["longitude"] is None
    assert v["speed_kmh"] == 0.0
    assert v["course"] == 0.0
    assert v["signal_age_minutes"] is None


async def test_get_vehicles_carrier_moscow(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_vehicle_response("moscow|abc"))

    _mock_httpx(monkeypatch, handler)
    vehicles = await client.get_vehicles(55.7, 37.6)
    assert vehicles[0]["carrier_name"] == "Мосгортранс"


async def test_get_vehicles_carrier_mo(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_vehicle_response("mostransavto|xyz"))

    _mock_httpx(monkeypatch, handler)
    vehicles = await client.get_vehicles(55.7, 37.6)
    assert vehicles[0]["carrier_name"] == "Перевозчик МО"


async def test_get_vehicles_signal_age(monkeypatch, client):
    """signal_age_minutes считается от TrajectorySegmentMetaData.time."""
    response = _vehicle_response()
    response["data"]["vehicles"][0]["features"][0]["properties"]["TrajectorySegmentMetaData"]["time"] = time.time() - 120

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response)

    _mock_httpx(monkeypatch, handler)
    vehicles = await client.get_vehicles(55.7, 37.6)
    log.debug("age=%s", vehicles[0]["signal_age_minutes"])
    assert vehicles[0]["signal_age_minutes"] == pytest.approx(2.0, abs=0.1)


async def test_get_vehicles_raises_on_http_error(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_vehicles(55.7, 37.6)


async def test_get_vehicles_empty_list(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"vehicles": []}})

    _mock_httpx(monkeypatch, handler)
    assert await client.get_vehicles(55.7, 37.6) == []


async def test_get_vehicles_sends_csrf_and_session(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"data": {"vehicles": []}})

    _mock_httpx(monkeypatch, handler)
    await client.get_vehicles(55.7, 37.6)
    assert captured["params"]["csrfToken"] == client.csrf_token
    assert captured["params"]["sessionId"] == client.session_id


# =========================================================================== #
#  6. get_stop_info                                                           #
# =========================================================================== #
STOP_INFO_RESPONSE = {
    "data": {
        "transports": [
            {"name": "т86", "threads": [{"BriefSchedule": {"Events": [
                {"time": "2026-09-10T17:30:00", "stop": "A"}
            ], "departureTime": "2026-09-10T17:30:00"}}]}
        ]
    }
}


async def test_get_stop_info_basic(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=STOP_INFO_RESPONSE)

    _mock_httpx(monkeypatch, handler)
    result = await client.get_stop_info("stop__123")

    log.debug("params: %s", captured["params"])
    assert captured["params"]["id"] == "stop__123"
    assert captured["params"]["mode"] == "prognosis"
    assert captured["params"]["uri"] == "ymapsbm1://transit/stop?id=stop__123"
    assert captured["params"]["results"] == "20"
    assert result == STOP_INFO_RESPONSE


async def test_get_stop_info_time_dependent(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={})

    _mock_httpx(monkeypatch, handler)
    await client.get_stop_info("x")
    assert captured["params"]["timeDependent[type]"] == "departure"
    assert "timeDependent[time]" in captured["params"]


async def test_get_stop_info_raises_on_http_error(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_stop_info("missing")


async def test_get_stop_info_sends_referer(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={})

    _mock_httpx(monkeypatch, handler)
    await client.get_stop_info("x")
    assert "yandex.ru/maps" in captured["headers"]["referer"]


# =========================================================================== #
#  7. E2E: геокодирование → stop_info → vehicles                              #
# =========================================================================== #
async def test_e2e_geocode_then_stop_info_then_vehicles(monkeypatch, client):
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if "search" in url:
            return httpx.Response(200, json=SEARCH_RESPONSE)
        if "getStopInfo" in url:
            return httpx.Response(200, json=STOP_INFO_RESPONSE)
        if "getVehiclesInfoWithRegion" in url:
            return httpx.Response(200, json=_vehicle_response())
        return httpx.Response(404)

    _mock_httpx(monkeypatch, handler)

    geocoded = await client.geocode_stop("Сокол")
    stop_info = await client.get_stop_info(geocoded["id"])
    vehicles = await client.get_vehicles(geocoded["latitude"], geocoded["longitude"])

    log.debug("calls: %s", calls)
    assert geocoded["id"] == "stop__123"
    assert "transports" in stop_info["data"]
    assert len(vehicles) == 1
    assert len(calls) == 3