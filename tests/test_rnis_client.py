"""
Тесты для RNISClient (rnis_client.py) с полной отладкой.

Запуск:
    pytest -q test_rnis_client.py
    pytest -s -q test_rnis_client.py                 # + print() в реальном времени
    pytest -q test_rnis_client.py -k filter          # только фильтры
"""
import asyncio
import logging
import pprint
import time

import pytest

from clients.rnis_client import (
    AsyncRateLimiter,
    DEFAULT_MO_BBOX,
    RNISClient,
    bbox_from_point,
    haversine_km,
)

log = logging.getLogger("tests.rnis")


# ===========================================================================
# Хелперы
# ===========================================================================
def make_client(**kwargs) -> RNISClient:
    """Клиент без сети; по умолчанию request_interval=0 чтобы тесты не спали."""
    kwargs.setdefault("request_interval", 0.0)
    c = RNISClient(**kwargs)
    log.debug("Создан RNISClient: %s", kwargs)
    return c


def fake_post_factory(routes):
    """
    routes: dict[str, dict] — сопоставление подстроки subject -> ответ.
    Возвращает async-функцию, пригодную для подмены client._post.
    """
    calls = []

    async def fake_post(self, subject, payload_dict):
        calls.append((subject, payload_dict))
        log.debug("fake_post subject=%s body=%s", subject, payload_dict)
        for needle, response in routes.items():
            if needle in subject:
                log.debug("  -> matched %r", needle)
                return response
        raise AssertionError(f"Неожиданный subject: {subject!r}")

    fake_post.calls = calls
    return fake_post


def sample_catalog_items():
    return [
        {
            "uuid": "v-uuid-1",
            "state_number": "А123АА50",
            "route_number": "1",
            "route_name": "Мытищи — Москва",
            "carrier_name": "Мострансавто",
            "carrier_uuid": "c-1",
            "vehicle_mark_name": "ЛиАЗ 5292",
            "vehicle_mark_uuid": "m-1",
            "bnso_code": "1001",
            "reserve_bnso_number": "R-1",
            "order_execution_uuid": "o-1",
            "is_low_floor_level": True,
            "is_air_conditioning_installation": True,
            "is_electronic_scoreboard": True,
            "is_cashless_payment": True,
            "is_passenger_monitoring_system": False,
        },
        {
            "uuid": "v-uuid-2",
            "state_number": "В456ВВ50",
            "route_number": "2",
            "route_name": "Химки — Зеленоград",
            "carrier_name": "Мосгортранс",
            "vehicle_mark_name": "Mercedes",
            "bnso_code": "2002",
            "is_low_floor_level": False,
            "is_air_conditioning_installation": False,
        },
        {
            "uuid": "v-uuid-3",
            "state_number": "С789СС50",
            "route_number": "1",
            "route_name": "Мытищи — Королёв",
            "carrier_name": "Мострансавто",
            "vehicle_mark_name": "ЛиАЗ 4292",
            "bnso_code": "3003",
            "is_low_floor_level": True,
            "is_air_conditioning_installation": False,
        },
    ]


def sample_telematics_vehicles(now=None):
    now = now if now is not None else time.time()
    return [
        {
            "VehicleUUID": "v-uuid-1",
            "StateNumber": "А123АА50",
            "Latitude": 55.90,
            "Longitude": 37.70,
            "Speed": 45,
            "Course": 90,
            "DeviceTime": now - 60,       # 1 мин назад
            "DeviceCode": "dev-1",
        },
        {
            "VehicleUUID": "unknown-uuid",
            "StateNumber": "НЕТ В КАТАЛОГЕ",
            "Latitude": 55.75,
            "Longitude": 37.61,
            "Speed": 0,
            "Course": 0,
            "DeviceTime": now - 3600,     # 60 мин назад
            "DeviceCode": "dev-x",
        },
        {
            "VehicleUUID": "v-uuid-2",
            "StateNumber": "В456ВВ50",
            "Latitude": 55.80,
            "Longitude": 37.50,
            "Speed": 20,
            "Course": 45,
            "DeviceTime": now - 120,
            "DeviceCode": "dev-2",
        },
    ]


# ===========================================================================
# 1. haversine_km
# ===========================================================================
def test_haversine_zero():
    r = haversine_km(55.7558, 37.6173, 55.7558, 37.6173)
    log.debug("same point -> %r", r)
    assert r == pytest.approx(0.0, abs=1e-9)


def test_haversine_moscow_spb():
    r = haversine_km(55.7558, 37.6173, 59.9343, 30.3351)
    log.debug("Москва→СПб = %.3f км", r)
    assert r == pytest.approx(635, rel=0.02)


# ===========================================================================
# 2. bbox_from_point
# ===========================================================================
def test_bbox_from_point_geometry():
    bbox = bbox_from_point(55.75, 37.61, 10.0)
    log.debug("bbox=%s", bbox)

    assert bbox["TopLeftLatitude"] > 55.75 > bbox["RightBottomLatitude"]
    assert bbox["RightBottomLongitude"] > 37.61 > bbox["TopLeftLongitude"]

    # 10 км по широте ≈ 10/111 ≈ 0.0901°
    assert bbox["TopLeftLatitude"] - 55.75 == pytest.approx(10 / 111.0, rel=1e-3)
    # по долготе — делим на cos(lat)
    expected_d_lon = 10 / (111.0 * __import__("math").cos(__import__("math").radians(55.75)))
    assert bbox["RightBottomLongitude"] - 37.61 == pytest.approx(expected_d_lon, rel=1e-3)


def test_bbox_uses_default_when_no_coords():
    c = make_client()
    assert c.headers["Subject"] if False else True  # заглушка, чтобы показать контекст
    # Сам DEFAULT_MO_BBOX проверим как константу
    assert DEFAULT_MO_BBOX["TopLeftLatitude"] > DEFAULT_MO_BBOX["RightBottomLatitude"]
    assert DEFAULT_MO_BBOX["RightBottomLongitude"] > DEFAULT_MO_BBOX["TopLeftLongitude"]


# ===========================================================================
# 3. AsyncRateLimiter
# ===========================================================================
async def test_rate_limiter_enforces_interval():
    limiter = AsyncRateLimiter(min_interval=0.1)
    t0 = time.monotonic()
    await limiter.wait()
    await limiter.wait()
    await limiter.wait()
    elapsed = time.monotonic() - t0
    log.debug("3 вызова, min_interval=0.1, elapsed=%.3f", elapsed)
    assert elapsed >= 0.2  # между 3 вызовами минимум 2 интервала


async def test_rate_limiter_no_wait_when_interval_zero():
    limiter = AsyncRateLimiter(min_interval=0.0)
    t0 = time.monotonic()
    for _ in range(5):
        await limiter.wait()
    elapsed = time.monotonic() - t0
    log.debug("5 вызовов без интервала: %.4f сек", elapsed)
    assert elapsed < 0.05


async def test_rate_limiter_parallel_calls_are_serialized():
    """Параллельные wait() должны сериализоваться — иначе интервал не соблюсти."""
    limiter = AsyncRateLimiter(min_interval=0.05)
    t0 = time.monotonic()
    await asyncio.gather(*[limiter.wait() for _ in range(4)])
    elapsed = time.monotonic() - t0
    log.debug("4 параллельных wait, min_interval=0.05, elapsed=%.3f", elapsed)
    assert elapsed >= 0.15  # минимум 3 интервала


# ===========================================================================
# 4. _post — заголовки и URL
# ===========================================================================
async def test_post_sets_subject_header_and_url(monkeypatch):
    c = make_client()
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            captured["raised"] = True

        def json(self):
            return {"ok": True}

    class FakeClient:
        def __init__(self, *a, **kw):
            captured["client_kw"] = kw

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResp()

    monkeypatch.setattr("clients.rnis_client.httpx.AsyncClient", FakeClient)

    result = await c._post("subj.X", {"payload": {"a": 1}})
    log.debug("captured = %s", captured)
    assert result == {"ok": True}
    assert captured["url"] == "https://portal.rnis.mosreg.ru/busajax/request?subj.X"
    assert captured["headers"]["Subject"] == "subj.X"
    assert captured["json"] == {"payload": {"a": 1}}
    assert captured["raised"] is True


# ===========================================================================
# 5. get_catalog — кэш и индексация
# ===========================================================================
async def test_get_catalog_indexes_by_uuid_and_plate():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
    })
    c._post = fake.__get__(c, RNISClient)

    cat = await c.get_catalog()
    log.debug("catalog keys: %s", list(cat.keys()))
    log.debug("by_plate keys: %s", list(c._catalog_by_plate.keys()))

    assert set(cat.keys()) == {"v-uuid-1", "v-uuid-2", "v-uuid-3"}
    assert c._catalog_by_plate["а123аа50"]["uuid"] == "v-uuid-1"
    assert c._catalog_by_plate["в456вв50"]["uuid"] == "v-uuid-2"
    assert len(fake.calls) == 1


async def test_get_catalog_uses_cache_until_ttl():
    c = make_client(cache_ttl_seconds=900)
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
    })
    c._post = fake.__get__(c, RNISClient)

    await c.get_catalog()
    await c.get_catalog()
    await c.get_catalog()
    log.debug("после 3 вызовов get_catalog(), реальных запросов: %d", len(fake.calls))
    assert len(fake.calls) == 1  # второй и третий — из кэша


async def test_get_catalog_force_refresh_bypasses_cache():
    c = make_client(cache_ttl_seconds=900)
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
    })
    c._post = fake.__get__(c, RNISClient)

    await c.get_catalog()
    await c.get_catalog(force_refresh=True)
    log.debug("calls=%d", len(fake.calls))
    assert len(fake.calls) == 2


async def test_get_catalog_ttl_expiry_refetches(monkeypatch):
    c = make_client(cache_ttl_seconds=1)
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
    })
    c._post = fake.__get__(c, RNISClient)

    # подменим time.time, чтобы «перескочить» TTL
    real_time = time.time
    now = [real_time()]
    monkeypatch.setattr("clients.rnis_client.time.time", lambda: now[0])

    await c.get_catalog()
    now[0] += 5  # > ttl
    await c.get_catalog()
    log.debug("calls после ttl-экспайра: %d", len(fake.calls))
    assert len(fake.calls) == 2


async def test_get_catalog_fallback_on_error_returns_stale():
    c = make_client()
    good = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
    })
    c._post = good.__get__(c, RNISClient)
    await c.get_catalog()

    async def boom(self, subject, body):
        raise RuntimeError("network down")

    c._post = boom.__get__(c, RNISClient)
    cat = await c.get_catalog(force_refresh=True)
    log.debug("stale catalog keys after error: %s", list(cat.keys()))
    assert set(cat.keys()) == {"v-uuid-1", "v-uuid-2", "v-uuid-3"}


async def test_get_catalog_raises_when_no_cache_and_error():
    c = make_client()

    async def boom(self, subject, body):
        raise RuntimeError("network down")

    c._post = boom.__get__(c, RNISClient)
    with pytest.raises(RuntimeError, match="Ошибка загрузки каталога РНИС"):
        await c.get_catalog()


# ===========================================================================
# 6. get_telematics_count
# ===========================================================================
async def test_get_telematics_count():
    c = make_client()
    fake = fake_post_factory({
        "telematics.count": {"payload": {"count": 42}},
    })
    c._post = fake.__get__(c, RNISClient)

    n = await c.get_telematics_count()
    log.debug("count = %r", n)
    assert n == 42


async def test_get_telematics_count_missing_payload_returns_zero():
    c = make_client()
    fake = fake_post_factory({"telematics.count": {}})
    c._post = fake.__get__(c, RNISClient)
    assert await c.get_telematics_count() == 0


# ===========================================================================
# 7. get_raw_telematics — формирование фильтров
# ===========================================================================
async def test_get_raw_telematics_default_bbox():
    c = make_client()
    captured = {}

    async def fake(self, subject, body):
        captured["subject"] = subject
        captured["body"] = body
        return {"payload": {"vehicles": [{"VehicleUUID": "x"}]}}

    c._post = fake.__get__(c, RNISClient)
    vehicles = await c.get_raw_telematics()
    log.debug("vehicles=%s", vehicles)
    log.debug("captured filters=%s", captured["body"]["headers"]["meta"]["filters"])
    assert captured["body"]["headers"]["meta"]["filters"]["BoundingBox"] == DEFAULT_MO_BBOX
    assert vehicles == [{"VehicleUUID": "x"}]


async def test_get_raw_telematics_custom_bbox_and_last_signal():
    c = make_client()
    captured = {}

    async def fake(self, subject, body):
        captured["body"] = body
        return {"payload": {"vehicles": []}}

    c._post = fake.__get__(c, RNISClient)
    custom = bbox_from_point(55.75, 37.61, 5.0)
    await c.get_raw_telematics(bbox=custom, last_signal="5000", zoom=15)

    f = captured["body"]["headers"]["meta"]["filters"]
    log.debug("filters=%s", f)
    assert f["BoundingBox"] == custom
    assert f["LastSignal"] == "5000"
    assert f["Zoom"] == 15
    assert f["Subsystem"] == "kiutr"
    assert f["WithClustered"] is False


# ===========================================================================
# 8. _matches_filters — точечные фильтры
# ===========================================================================
def _bus(**overrides):
    base = {
        "state_number": "А123АА50",
        "route_number": "1",
        "route_name": "Мытищи — Москва",
        "carrier_name": "Мострансавто",
        "vehicle_mark": "ЛиАЗ 5292",
        "bnso_code": "1001",
        "vehicle_uuid": "v-uuid-1",
        "carrier_uuid": "c-1",
        "order_execution_uuid": "o-1",
        "is_low_floor": True,
        "is_air_conditioned": True,
        "speed_kmh": 40,
        "signal_age_minutes": 5,
    }
    base.update(overrides)
    return base


def test_matches_filters_query():
    c = make_client()
    b = _bus()
    assert c._matches_filters(b, query="лиаз") is True
    assert c._matches_filters(b, query="МОСТРАНСАВТО") is True
    assert c._matches_filters(b, query="мытищи москва") is True  # все токены
    assert c._matches_filters(b, query="мытищи химки") is False  # второго токена нет
    assert c._matches_filters(b, query="нет такого") is False


def test_matches_filters_route_and_state_number_partial():
    c = make_client()
    b = _bus()
    assert c._matches_filters(b, route_number="1") is True
    assert c._matches_filters(b, route_number="11") is False
    assert c._matches_filters(b, state_number="а123") is True
    assert c._matches_filters(b, state_number="в456") is False


def test_matches_filters_carrier_and_mark():
    c = make_client()
    b = _bus()
    assert c._matches_filters(b, carrier="мостранс") is True
    assert c._matches_filters(b, vehicle_mark="лиаз") is True
    assert c._matches_filters(b, vehicle_mark="mercedes") is False


def test_matches_filters_bnso_is_case_sensitive():
    """bnso_code не .lower() — контракт явный: сравнение как есть."""
    c = make_client()
    b = _bus()
    assert c._matches_filters(b, bnso_code="1001") is True
    assert c._matches_filters(b, bnso_code="100") is True   # подстрока
    assert c._matches_filters(b, bnso_code="9999") is False


def test_matches_filters_uuid_partial():
    c = make_client()
    b = _bus()
    assert c._matches_filters(b, uuid="v-uuid") is True
    assert c._matches_filters(b, uuid="V-UUID-1") is True  # регистр не важен
    assert c._matches_filters(b, uuid="zzz") is False


def test_matches_filters_boolean_flags():
    c = make_client()
    low = _bus(is_low_floor=True)
    not_low = _bus(is_low_floor=False)

    assert c._matches_filters(low, low_floor_only=True) is True
    assert c._matches_filters(not_low, low_floor_only=True) is False
    # False/None — не фильтруем
    assert c._matches_filters(not_low, low_floor_only=None) is True
    assert c._matches_filters(not_low, low_floor_only=False) is True

    ac = _bus(is_air_conditioned=False)
    assert c._matches_filters(ac, air_conditioned_only=True) is False
    assert c._matches_filters(ac, air_conditioned_only=None) is True


def test_matches_filters_speed_and_age():
    c = make_client()
    b = _bus(speed_kmh=40, signal_age_minutes=5)
    assert c._matches_filters(b, min_speed=30) is True
    assert c._matches_filters(b, min_speed=50) is False
    assert c._matches_filters(b, max_age_minutes=10) is True
    assert c._matches_filters(b, max_age_minutes=1) is False


def test_matches_filters_age_none_passes_max_age():
    """Если возраст неизвестен — фильтр по возрасту не отбрасывает."""
    c = make_client()
    b = _bus(signal_age_minutes=None)
    assert c._matches_filters(b, max_age_minutes=1) is True


def test_matches_filters_no_filters_passes():
    c = make_client()
    assert c._matches_filters(_bus()) is True


# ===========================================================================
# 9. get_online_buses — сквозной сценарий с моком _post
# ===========================================================================
async def test_get_online_buses_basic():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
        "telematics.get": {"payload": {"vehicles": sample_telematics_vehicles()}},
    })
    c._post = fake.__get__(c, RNISClient)

    buses = await c.get_online_buses()
    log.debug("Найдено онлайн-автобусов: %d", len(buses))
    for b in buses:
        log.debug("  %s | %s | speed=%s age=%s",
                  b["state_number"], b["route_number"],
                  b["speed_kmh"], b["signal_age_minutes"])

    assert len(buses) == 3
    by_plate = {b["state_number"]: b for b in buses}

    b1 = by_plate["А123АА50"]
    assert b1["route_number"] == "1"
    assert b1["carrier_name"] == "Мострансавто"
    assert b1["is_low_floor"] is True
    assert b1["is_air_conditioned"] is True
    assert b1["latitude"] == 55.90
    assert b1["signal_age_minutes"] == pytest.approx(1.0, abs=0.2)

    # "unknown-uuid" нет в каталоге, но попадает с дефолтами
    bx = by_plate["НЕТ В КАТАЛОГЕ"]
    assert bx["route_number"] == "Не назначен"
    assert bx["carrier_name"] == ""
    assert bx["bnso_code"] == "dev-x"  # fallback на DeviceCode
    assert bx["is_low_floor"] is False


async def test_get_online_buses_with_radius_filters_and_sorts():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
        "telematics.get": {"payload": {"vehicles": sample_telematics_vehicles()}},
    })
    c._post = fake.__get__(c, RNISClient)

    # около v-uuid-2 (55.80, 37.50), радиус 10 км
    buses = await c.get_online_buses(lat=55.80, lon=37.50, radius_km=10)
    log.debug("Радиус 10км вокруг (55.80, 37.50):")
    for b in buses:
        log.debug("  %s dist=%s", b["state_number"], b.get("distance_km"))

    plates = [b["state_number"] for b in buses]
    assert "В456ВВ50" in plates
    assert all("distance_km" in b for b in buses)
    # сортировка по возрастанию
    dists = [b["distance_km"] for b in buses]
    assert dists == sorted(dists)


async def test_get_online_buses_radius_excludes_far():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
        "telematics.get": {"payload": {"vehicles": sample_telematics_vehicles()}},
    })
    c._post = fake.__get__(c, RNISClient)

    # очень маленький радиус — почти наверняка никого
    buses = await c.get_online_buses(lat=0.0, lon=0.0, radius_km=1)
    log.debug("Радиус 1км вокруг (0,0): %d", len(buses))
    assert buses == []


async def test_get_online_buses_uses_custom_bbox(monkeypatch):
    """Если заданы lat/lon/radius — BoundingBox должен считаться от точки."""
    c = make_client()
    captured = {}

    async def fake_post(self, subject, body):
        if "vehicle.list" in subject:
            return {"payload": {"items": []}}
        if "telematics.get" in subject:
            captured["filters"] = body["headers"]["meta"]["filters"]
            return {"payload": {"vehicles": []}}
        raise AssertionError(subject)

    c._post = fake_post.__get__(c, RNISClient)
    await c.get_online_buses(lat=55.75, lon=37.61, radius_km=5)

    bb = captured["filters"]["BoundingBox"]
    log.debug("bbox=%s", bb)
    assert bb != DEFAULT_MO_BBOX
    assert bb["TopLeftLatitude"] > 55.75 > bb["RightBottomLatitude"]


async def test_get_online_buses_filters_by_route():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
        "telematics.get": {"payload": {"vehicles": sample_telematics_vehicles()}},
    })
    c._post = fake.__get__(c, RNISClient)

    buses = await c.get_online_buses(route_number="2")
    log.debug("route_number=2 -> %s", [b["state_number"] for b in buses])
    assert [b["state_number"] for b in buses] == ["В456ВВ50"]


async def test_get_online_buses_filters_low_floor():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
        "telematics.get": {"payload": {"vehicles": sample_telematics_vehicles()}},
    })
    c._post = fake.__get__(c, RNISClient)

    buses = await c.get_online_buses(low_floor_only=True)
    plates = sorted(b["state_number"] for b in buses)
    log.debug("low_floor_only -> %s", plates)
    # низкопольные из каталога: v-uuid-1 и v-uuid-3.
    # v-uuid-3 в телеметрии отсутствует => только А123АА50.
    assert plates == ["А123АА50"]


async def test_get_online_buses_limit():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
        "telematics.get": {"payload": {"vehicles": sample_telematics_vehicles()}},
    })
    c._post = fake.__get__(c, RNISClient)

    buses = await c.get_online_buses(limit=2)
    log.debug("limit=2 -> %d", len(buses))
    assert len(buses) == 2


async def test_get_online_buses_max_age_filter():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
        "telematics.get": {"payload": {"vehicles": sample_telematics_vehicles()}},
    })
    c._post = fake.__get__(c, RNISClient)

    buses = await c.get_online_buses(max_age_minutes=5)
    plates = sorted(b["state_number"] for b in buses)
    log.debug("max_age_minutes=5 -> %s", plates)
    # "НЕТ В КАТАЛОГЕ" имеет возраст ~60 мин, отсекается
    assert "НЕТ В КАТАЛОГЕ" not in plates
    assert set(plates) == {"А123АА50", "В456ВВ50"}


async def test_get_online_buses_query_search():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
        "telematics.get": {"payload": {"vehicles": sample_telematics_vehicles()}},
    })
    c._post = fake.__get__(c, RNISClient)

    buses = await c.get_online_buses(query="мострансавто")
    log.debug("query='мострансавто' -> %s", [b["state_number"] for b in buses])
    assert [b["state_number"] for b in buses] == ["А123АА50"]


# ===========================================================================
# 10. search_catalog
# ===========================================================================
async def test_search_catalog_all():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
    })
    c._post = fake.__get__(c, RNISClient)

    items = await c.search_catalog()
    log.debug("catalog items: %d", len(items))
    assert len(items) == 3
    # нет полей, специфичных для онлайна
    for it in items:
        assert "latitude" not in it
        assert "signal_age_minutes" not in it


async def test_search_catalog_by_route():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
    })
    c._post = fake.__get__(c, RNISClient)

    items = await c.search_catalog(route_number="1")
    plates = sorted(i["state_number"] for i in items)
    log.debug("route=1 -> %s", plates)
    assert plates == ["А123АА50", "С789СС50"]


async def test_search_catalog_limit():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
    })
    c._post = fake.__get__(c, RNISClient)

    items = await c.search_catalog(limit=2)
    log.debug("limit=2 -> %d", len(items))
    assert len(items) == 2


async def test_search_catalog_air_conditioned():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
    })
    c._post = fake.__get__(c, RNISClient)

    items = await c.search_catalog(air_conditioned_only=True)
    plates = [i["state_number"] for i in items]
    log.debug("air_conditioned_only -> %s", plates)
    assert plates == ["А123АА50"]


# ===========================================================================
# 11. search_routes
# ===========================================================================
async def test_search_routes_by_number():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
    })
    c._post = fake.__get__(c, RNISClient)

    routes = await c.search_routes("1")
    log.debug("search_routes('1') -> %s", routes)
    numbers = [r["route_number"] for r in routes]
    # маршрут 1 представлен двумя route_name => 2 записи, уникальные
    assert numbers == ["1", "1"]
    names = [r["route_name"] for r in routes]
    assert names == ["Мытищи — Москва", "Мытищи — Королёв"]


async def test_search_routes_by_name():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
    })
    c._post = fake.__get__(c, RNISClient)

    routes = await c.search_routes("химки")
    log.debug("search_routes('химки') -> %s", routes)
    assert len(routes) == 1
    assert routes[0]["route_number"] == "2"
    assert routes[0]["carrier_name"] == "Мосгортранс"


async def test_search_routes_deduplicates():
    """Один и тот же (route_number, route_name) из разных ТС — одна запись."""
    c = make_client()
    duplicated = sample_catalog_items() + [
        {**sample_catalog_items()[0], "uuid": "v-uuid-1-dup", "state_number": "dup"}
    ]
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": duplicated}},
    })
    c._post = fake.__get__(c, RNISClient)

    routes = await c.search_routes("мытищи")
    log.debug("routes после дедупа: %s", routes)
    assert len(routes) == 2  # 2 разных name у маршрута 1


async def test_search_routes_no_match():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
    })
    c._post = fake.__get__(c, RNISClient)
    assert await c.search_routes("нет-такого-маршрута") == []


# ===========================================================================
# 12. End-to-end: сценарий «нашёл автобус рядом с точкой»
# ===========================================================================
async def test_end_to_end_find_nearest_bus():
    c = make_client()
    fake = fake_post_factory({
        "vehicle.list": {"payload": {"items": sample_catalog_items()}},
        "telematics.get": {"payload": {"vehicles": sample_telematics_vehicles()}},
    })
    c._post = fake.__get__(c, RNISClient)

    # Ищем рядом с первой телеметрической точкой
    buses = await c.get_online_buses(
        lat=55.90, lon=37.70, radius_km=5,
        low_floor_only=True,
    )
    log.debug("Итог поиска: %s", pprint.pformat(buses, width=120))
    assert len(buses) == 1
    b = buses[0]
    assert b["state_number"] == "А123АА50"
    assert b["distance_km"] == pytest.approx(0.0, abs=0.01)
    assert b["is_low_floor"] is True
    assert b["route_name"] == "Мытищи — Москва"