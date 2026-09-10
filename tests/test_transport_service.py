"""
Тесты для UnifiedTransportService — оркестратора между Яндексом,
РНИС МО и Московским транспортом.

Ключевой фокус:
  * РАЗДЕЛЕНИЕ ID: stop_uuid ≠ vehicle_uuid ≠ clean_uuid ≠ internal_id;
  * маршрутизация запроса к правильному клиенту;
  * сверка прогнозов и её статусы;
  * fallback-цепочки.
"""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from transport_service import UnifiedTransportService, bearing_degrees

log = logging.getLogger("tests.unified")


# --------------------------------------------------------------------------- #
#  Фикстура                                                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture
def service():
    """
    Сервис без __init__ — все внешние клиенты замоканы,
    файловая система и сеть не трогаются.
    """
    svc = UnifiedTransportService.__new__(UnifiedTransportService)

    svc.yandex = MagicMock()
    svc.yandex.get_vehicles = AsyncMock(return_value=[])
    svc.yandex.geocode_stop = AsyncMock(return_value=None)
    svc.yandex.get_stop_info = AsyncMock(return_value=None)
    svc.yandex.search_stops = AsyncMock(return_value=[])

    svc.rnis = MagicMock()
    svc.rnis.get_catalog = AsyncMock(return_value={})
    svc.rnis.get_online_buses = AsyncMock(return_value=[])
    svc.rnis.get_meta_by_clean_uuid = MagicMock(return_value=None)
    svc.rnis.matches_filters = MagicMock(return_value=True)
    svc.rnis.get_stop_routes = AsyncMock(return_value=None)

    svc.moscow = MagicMock()
    svc.moscow.get_stop_v2 = AsyncMock(return_value=None)
    svc.moscow.get_stop_qr_forecast = AsyncMock(return_value=None)

    svc.repo = MagicMock()
    svc.repo.find_stop = MagicMock(return_value=None)
    svc.repo.get_stops_for_route = MagicMock(return_value=[])

    return svc


# --------------------------------------------------------------------------- #
#  Фабрики данных                                                              #
# --------------------------------------------------------------------------- #
def make_yandex_bus(
    clean_uuid="abcdef123456",
    lat=55.77,
    lon=37.84,
    speed=30.0,
    route="1",
    course=90,
):
    return {
        "clean_uuid": clean_uuid,
        "latitude": lat,
        "longitude": lon,
        "speed_kmh": speed,
        "route_number": route,
        "route_name": f"Маршрут {route}",
        "course": course,
        "carrier_name": "Яндекс-перевозчик",
        "destination": "Конечная",
        "coordinates_source": "yandex",
    }


def make_rnis_bus(
    vehicle_uuid="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    lat=55.80,
    lon=37.90,
    speed=25.0,
    route="2",
    course=45,
):
    return {
        "vehicle_uuid": vehicle_uuid,
        "state_number": "А123АА50",
        "latitude": lat,
        "longitude": lon,
        "speed_kmh": speed,
        "course": course,
        "route_number": route,
        "route_name": f"Маршрут {route}",
        "carrier_name": "Мострансавто",
        "vehicle_mark": "ЛиАЗ",
        "destination": "Конечная",
        "coordinates_source": "rnis",
        "source": "rnis",
    }


def make_stop(
    uuid="cd90c51a-3034-4dc3-b95a-6d93aa91c15e",
    internal_id="59420839",
    lat=55.77,
    lon=37.84,
    name="Метро Сокол",
    routes=None,
):
    return {
        "uuid": uuid,
        "internal_id": internal_id,
        "stop_id": internal_id,
        "name": name,
        "lat": lat,
        "lon": lon,
        "coordinates": {"lat": lat, "lon": lon, "source": "bus_stops_all.geojson"},
        "routes": routes if routes is not None else ["1", "2"],
    }


def make_route_stop(name, lat, lon):
    return {
        "name": name,
        "stop_id": f"id-{name}",
        "coordinates": {"lat": lat, "lon": lon},
    }


# =========================================================================== #
#  1. bearing_degrees                                                         #
# =========================================================================== #
def test_bearing_north():
    assert bearing_degrees(0, 0, 1, 0) == pytest.approx(0, abs=0.5)


def test_bearing_east():
    assert bearing_degrees(0, 0, 0, 1) == pytest.approx(90, abs=0.5)


def test_bearing_south():
    assert bearing_degrees(1, 0, 0, 0) == pytest.approx(180, abs=0.5)


def test_bearing_west():
    assert bearing_degrees(0, 1, 0, 0) == pytest.approx(270, abs=0.5)


# =========================================================================== #
#  2. get_online_buses — маршрутизация источников                              #
# =========================================================================== #
@pytest.mark.asyncio
async def test_source_yandex_skips_rnis_online(service):
    await service.get_online_buses(source="yandex")
    assert service.yandex.get_vehicles.await_count == 1
    assert service.rnis.get_online_buses.await_count == 0
    # каталог РНИС всё равно запрашивается (для метаданных)
    assert service.rnis.get_catalog.await_count == 1


@pytest.mark.asyncio
async def test_source_rnis_skips_yandex(service):
    await service.get_online_buses(source="rnis")
    assert service.yandex.get_vehicles.await_count == 0
    assert service.rnis.get_online_buses.await_count == 1


@pytest.mark.asyncio
async def test_source_all_calls_both(service):
    await service.get_online_buses(source="all")
    assert service.yandex.get_vehicles.await_count == 1
    assert service.rnis.get_online_buses.await_count == 1


@pytest.mark.asyncio
async def test_yandex_failure_does_not_break_rnis(service):
    service.yandex.get_vehicles = AsyncMock(side_effect=RuntimeError("boom"))
    service.rnis.get_online_buses = AsyncMock(return_value=[make_rnis_bus()])

    buses = await service.get_online_buses(source="all")
    assert len(buses) == 1
    assert buses[0]["uuid"] == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@pytest.mark.asyncio
async def test_rnis_failure_does_not_break_yandex(service):
    service.yandex.get_vehicles = AsyncMock(return_value=[make_yandex_bus()])
    service.rnis.get_online_buses = AsyncMock(side_effect=RuntimeError("boom"))

    buses = await service.get_online_buses(source="all")
    log.debug("после падения RNIS: %s", buses)
    assert len(buses) == 1
    # Яндекс-шина без метаданных — см. известный баг ниже.
    # Проверяем, что данные не потерялись.
    assert buses[0]["coordinates"]["source"] == "yandex"


# =========================================================================== #
#  3. Разделение ID                                                            #
# =========================================================================== #
@pytest.mark.asyncio
async def test_yandex_clean_uuid_used_for_rnis_lookup(service):
    """РНИС ищет метаданные по clean_uuid — БЕЗ дефисов, в нижнем регистре."""
    clean = "abcdef1234567890abcdef1234567890"
    service.yandex.get_vehicles = AsyncMock(
        return_value=[make_yandex_bus(clean_uuid=clean)]
    )
    service.rnis.get_meta_by_clean_uuid = MagicMock(return_value={
        "uuid": "11111111-2222-3333-4444-555555555555",
        "state_number": "А123АА50",
        "carrier_name": "Мострансавто",
        "vehicle_mark_name": "ЛиАЗ",
    })

    await service.get_online_buses(source="yandex")

    called_with = service.rnis.get_meta_by_clean_uuid.call_args.args[0]
    log.debug("get_meta_by_clean_uuid(%r)", called_with)
    assert called_with == clean
    assert "-" not in called_with


@pytest.mark.asyncio
async def test_yandex_bus_uuid_becomes_rnis_vehicle_uuid(service):
    """Если РНИС знает шину — в ответе uuid = vehicle_uuid из РНИС, а не clean_uuid."""
    rnis_uuid = "11111111-2222-3333-4444-555555555555"
    service.yandex.get_vehicles = AsyncMock(
        return_value=[make_yandex_bus(clean_uuid="11111111222233334444555555555555")]
    )
    service.rnis.get_meta_by_clean_uuid = MagicMock(return_value={
        "uuid": rnis_uuid,
        "state_number": "А123АА50",
        "carrier_name": "Мострансавто",
        "vehicle_mark_name": "ЛиАЗ",
    })

    buses = await service.get_online_buses(source="yandex")
    b = buses[0]
    log.debug("uuid=%s extra=%s", b["uuid"], b["extra"])
    assert b["uuid"] == rnis_uuid
    assert b["extra"]["source"] == "yandex+rnis"
    assert b["extra"]["state_number"] == "А123АА50"
    assert b["extra"]["vehicle_mark"] == "ЛиАЗ"


@pytest.mark.asyncio
async def test_rnis_vehicle_uuid_keeps_dashes(service):
    """RNIS-uuid с дефисами не нормализуется в ответе."""
    rnis_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    service.rnis.get_online_buses = AsyncMock(
        return_value=[make_rnis_bus(vehicle_uuid=rnis_uuid)]
    )

    buses = await service.get_online_buses(source="rnis")
    assert buses[0]["uuid"] == rnis_uuid
    assert buses[0]["uuid"].count("-") == 4


@pytest.mark.asyncio
async def test_dedup_yandex_rnis_same_vehicle(service):
    """Одна и та же шина из Яндекса (clean_uuid) и РНИС (vehicle_uuid) не задваивается."""
    v_uuid = "11111111-2222-3333-4444-555555555555"
    clean = "11111111222233334444555555555555"  # без дефисов

    service.yandex.get_vehicles = AsyncMock(
        return_value=[make_yandex_bus(clean_uuid=clean, route="1")]
    )
    service.rnis.get_meta_by_clean_uuid = MagicMock(return_value={
        "uuid": v_uuid,
        "state_number": "А123АА50",
        "carrier_name": "Мострансавто",
        "vehicle_mark_name": "ЛиАЗ",
    })
    service.rnis.get_online_buses = AsyncMock(
        return_value=[make_rnis_bus(vehicle_uuid=v_uuid, route="1")]
    )

    buses = await service.get_online_buses(source="all")
    log.debug("после дедупа: %d записей", len(buses))
    assert len(buses) == 1
    assert buses[0]["extra"]["source"] == "yandex+rnis"


@pytest.mark.asyncio
async def test_stop_uuid_vs_vehicle_uuid_never_mix(service):
    """
    Сквозная проверка: stop_uuid уходит только в moscow.get_stop_v2,
    vehicle_uuid никогда туда не попадает.
    """
    stop_uuid = "cd90c51a-3034-4dc3-b95a-6d93aa91c15e"
    vehicle_uuid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    stop = make_stop(uuid=stop_uuid)
    service.repo.find_stop = MagicMock(return_value=stop)
    service.moscow.get_stop_v2 = AsyncMock(return_value={"routes": []})
    service.rnis.get_online_buses = AsyncMock(
        return_value=[make_rnis_bus(vehicle_uuid=vehicle_uuid)]
    )

    await service.get_online_buses(source="rnis")
    await service.get_reconciled_schedule(stop_name="X")

    # stop_v2 получил именно stop_uuid
    assert service.moscow.get_stop_v2.await_args.args[0] == stop_uuid
    # ни разу не получил vehicle_uuid
    for call in service.moscow.get_stop_v2.await_args_list:
        assert call.args[0] != vehicle_uuid


# =========================================================================== #
#  4. Формат ответа и next_stop                                                #
# =========================================================================== #
@pytest.mark.asyncio
async def test_online_buses_output_shape(service):
    service.rnis.get_online_buses = AsyncMock(return_value=[make_rnis_bus()])
    buses = await service.get_online_buses(source="rnis")
    b = buses[0]

    log.debug("форма: %s", b)
    assert set(b.keys()) == {
        "route_number", "course", "route_name", "uuid",
        "speed_kmh", "coordinates", "next_stop", "destination", "extra",
    }
    assert set(b["coordinates"].keys()) == {"lat", "lon", "source"}
    assert set(b["extra"].keys()) == {
        "state_number", "vehicle_mark", "carrier_name", "bnso_code",
        "is_low_floor", "is_air_conditioned", "is_electrobus",
        "signal_age_minutes", "device_time", "source",
        "line_id", "thread_id",
    }


@pytest.mark.asyncio
async def test_next_stop_forward_along_course(service):
    """Автобус едет на восток, ближайшая остановка впереди — она и есть next_stop."""
    bus = make_rnis_bus(lat=55.77, lon=37.84, route="1", course=90)
    service.rnis.get_online_buses = AsyncMock(return_value=[bus])
    service.repo.get_stops_for_route = MagicMock(return_value=[
        make_route_stop("Ahead", 55.77, 37.855),    # ~1 км на восток — впереди
        make_route_stop("Behind", 55.77, 37.825),   # ~1 км на запад — позади
        make_route_stop("VeryClose", 55.77, 37.8405),  # ~50 м на восток
    ])

    buses = await service.get_online_buses(source="rnis", route_number="1")
    ns = buses[0]["next_stop"]
    log.debug("next_stop: %s", ns)
    assert ns is not None
    assert ns["name"] == "VeryClose"
    assert ns["distance_meters"] < 100


@pytest.mark.asyncio
async def test_next_stop_none_when_no_route_stops(service):
    service.rnis.get_online_buses = AsyncMock(return_value=[make_rnis_bus()])
    service.repo.get_stops_for_route = MagicMock(return_value=[])

    buses = await service.get_online_buses(source="rnis")
    assert buses[0]["next_stop"] is None


# =========================================================================== #
#  5. get_stop_from_repo                                                       #
# =========================================================================== #
def test_get_stop_from_repo_by_id(service):
    stop = make_stop()
    service.repo.find_stop = MagicMock(return_value=stop)
    assert service.get_stop_from_repo(stop_id="59420839") is stop


def test_get_stop_from_repo_by_name_after_id_miss(service):
    stop = make_stop()

    def find(q):
        return stop if q == "Метро Сокол" else None

    service.repo.find_stop = MagicMock(side_effect=find)
    assert service.get_stop_from_repo(stop_id="missing", stop_name="Метро Сокол") is stop


def test_get_stop_from_repo_returns_none(service):
    service.repo.find_stop = MagicMock(return_value=None)
    assert service.get_stop_from_repo(stop_name="zzz") is None


# =========================================================================== #
#  6. get_reconciled_schedule — маршрутизация к MoscowTransport                #
# =========================================================================== #
@pytest.mark.asyncio
async def test_reconciled_uses_stop_v2_for_full_uuid(service):
    stop = make_stop(uuid="cd90c51a-3034-4dc3-b95a-6d93aa91c15e")
    service.repo.find_stop = MagicMock(return_value=stop)
    service.moscow.get_stop_v2 = AsyncMock(return_value={"routes": []})
    service.get_online_buses = AsyncMock(return_value=[])

    await service.get_reconciled_schedule(stop_name="X")

    assert service.moscow.get_stop_v2.await_count == 1
    assert service.moscow.get_stop_qr_forecast.await_count == 0


@pytest.mark.asyncio
async def test_reconciled_uses_qr_for_short_uuid(service):
    stop = make_stop(uuid="short", internal_id="59420839")
    service.repo.find_stop = MagicMock(return_value=stop)
    service.get_online_buses = AsyncMock(return_value=[])

    await service.get_reconciled_schedule(stop_name="X")

    assert service.moscow.get_stop_v2.await_count == 0
    assert service.moscow.get_stop_qr_forecast.await_count == 1
    kwargs = service.moscow.get_stop_qr_forecast.await_args.kwargs
    log.debug("qr-stop kwargs: %s", kwargs)
    assert kwargs["internal_id"] == "59420839"
    assert kwargs["lon"] == pytest.approx(37.84)
    assert kwargs["lat"] == pytest.approx(55.77)


@pytest.mark.asyncio
async def test_reconciled_qr_fallback_only_on_exception(service):
    """Fallback на qr-stop — только если stop_v2 бросил исключение."""
    stop = make_stop()
    service.repo.find_stop = MagicMock(return_value=stop)
    service.moscow.get_stop_v2 = AsyncMock(side_effect=RuntimeError("500"))
    service.moscow.get_stop_qr_forecast = AsyncMock(return_value=None)
    service.get_online_buses = AsyncMock(return_value=[])

    await service.get_reconciled_schedule(stop_name="X")

    assert service.moscow.get_stop_v2.await_count == 1
    assert service.moscow.get_stop_qr_forecast.await_count == 1


@pytest.mark.asyncio
async def test_reconciled_no_qr_fallback_when_stop_v2_returns_none(service):
    """
    Если stop_v2 отработал без исключения (пусть и вернул None),
    fallback на qr-stop НЕ запускается — таково текущее поведение.
    """
    stop = make_stop()
    service.repo.find_stop = MagicMock(return_value=stop)
    service.moscow.get_stop_v2 = AsyncMock(return_value=None)
    service.moscow.get_stop_qr_forecast = AsyncMock(return_value=None)
    service.get_online_buses = AsyncMock(return_value=[])

    await service.get_reconciled_schedule(stop_name="X")

    assert service.moscow.get_stop_v2.await_count == 1
    assert service.moscow.get_stop_qr_forecast.await_count == 0


# =========================================================================== #
#  7. get_reconciled_schedule — статусы сверки                                 #
# =========================================================================== #
def _official_route(route="1", sec=300, dest="Конечная", telemetry=True):
    return {
        "route_number": route,
        "destination": dest,
        "forecasts": [{
            "time_seconds": sec,
            "time_minutes": round(sec / 60, 1),
            "by_telemetry": telemetry,
        }],
    }


@pytest.mark.asyncio
async def test_reconciled_official_only(service):
    stop = make_stop()
    service.repo.find_stop = MagicMock(return_value=stop)
    service.moscow.get_stop_v2 = AsyncMock(return_value={"routes": [_official_route()]})
    service.get_online_buses = AsyncMock(return_value=[])

    result = await service.get_reconciled_schedule(stop_name="X", route_number="1")
    log.debug("result: %s", result)
    route = result["routes"][0]
    assert route["eta"]["reconciliation_status"] == "official_only"
    assert route["eta"]["arrival_seconds"] == 300
    assert route["eta"]["chosen_source"] == "moscow_transport_v2"


@pytest.mark.asyncio
async def test_reconciled_yandex_live_only(service):
    """Официального прогноза нет, но есть живой автобус в 1 км."""
    stop = make_stop()
    service.repo.find_stop = MagicMock(return_value=stop)
    service.moscow.get_stop_v2 = AsyncMock(return_value={"routes": []})
    service.moscow.get_stop_qr_forecast = AsyncMock(return_value=None)
    service.get_online_buses = AsyncMock(return_value=[{
        "route_number": "1",
        "speed_kmh": 30.0,
        "coordinates": {"lat": 55.779, "lon": 37.84},
        "uuid": "live-1",
        "extra": {},
    }])

    result = await service.get_reconciled_schedule(stop_name="X", route_number="1")
    log.debug("result: %s", result)
    route = result["routes"][0]
    assert route["eta"]["reconciliation_status"] == "yandex_live_bus_only"
    assert route["eta"]["chosen_source"] == "yandex"
    assert route["eta"]["arrival_seconds"] > 0


@pytest.mark.asyncio
async def test_reconciled_discrepancy_prefers_official(service):
    """Официальный прогноз 20 мин, живой автобус даёт 1 мин → приоритет официальному."""
    stop = make_stop()
    service.repo.find_stop = MagicMock(return_value=stop)
    service.moscow.get_stop_v2 = AsyncMock(
        return_value={"routes": [_official_route(sec=1200)]}
    )
    service.get_online_buses = AsyncMock(return_value=[{
        "route_number": "1",
        "speed_kmh": 30.0,
        "coordinates": {"lat": 55.771, "lon": 37.84},  # ~0.1 км
        "uuid": "live-1",
        "extra": {},
    }])

    result = await service.get_reconciled_schedule(
        stop_name="X", route_number="1", threshold_seconds=180
    )
    route = result["routes"][0]
    log.debug("eta: %s", route["eta"])
    assert route["eta"]["reconciliation_status"] == "discrepancy_resolved"
    assert route["eta"]["arrival_seconds"] == 1200
    assert route["eta"]["chosen_source"] == "moscow_transport_v2"


@pytest.mark.asyncio
async def test_reconciled_nonsense_forecast_overridden(service):
    """Официальный прогноз > 1 часа → пересчёт по живому автобусу."""
    stop = make_stop()
    service.repo.find_stop = MagicMock(return_value=stop)
    service.moscow.get_stop_v2 = AsyncMock(
        return_value={"routes": [_official_route(sec=7200)]}  # 2 часа
    )
    service.get_online_buses = AsyncMock(return_value=[{
        "route_number": "1",
        "speed_kmh": 30.0,
        "coordinates": {"lat": 55.779, "lon": 37.84},
        "uuid": "live-1",
        "extra": {},
    }])

    result = await service.get_reconciled_schedule(stop_name="X", route_number="1")
    route = result["routes"][0]
    log.debug("eta: %s", route["eta"])
    assert route["eta"]["reconciliation_status"] == "nonsense_forecast_overridden"
    assert route["eta"]["arrival_seconds"] < 3600
    assert route["eta"]["chosen_source"] == "calculated_live_bus_telemetry"


@pytest.mark.asyncio
async def test_reconciled_consistent(service):
    """Совпадение прогнозов (дельта < 180 сек) → consistent, источник — Яндекс."""
    stop = make_stop()
    service.repo.find_stop = MagicMock(return_value=stop)
    service.moscow.get_stop_v2 = AsyncMock(
        return_value={"routes": [_official_route(sec=300)]}
    )
    # Автобус в ~1.1 км, 30 км/ч → 1.1*1.25/30*3600 ≈ 165 сек — близко к 300
    service.get_online_buses = AsyncMock(return_value=[{
        "route_number": "1",
        "speed_kmh": 30.0,
        "coordinates": {"lat": 55.780, "lon": 37.84},
        "uuid": "live-1",
        "extra": {},
    }])

    result = await service.get_reconciled_schedule(
        stop_name="X", route_number="1", threshold_seconds=200
    )
    route = result["routes"][0]
    log.debug("eta: %s", route["eta"])
    assert route["eta"]["reconciliation_status"] == "consistent"
    assert route["eta"]["chosen_source"] == "yandex"


@pytest.mark.asyncio
async def test_reconciled_no_routes(service):
    """Никаких прогнозов нет и у остановки нет маршрутов → пустой список."""
    stop = make_stop(routes=[])
    service.repo.find_stop = MagicMock(return_value=stop)
    service.moscow.get_stop_v2 = AsyncMock(return_value={"routes": []})
    service.get_online_buses = AsyncMock(return_value=[])

    result = await service.get_reconciled_schedule(stop_name="X")
    log.debug("result: %s", result)
    assert result["routes_count"] == 0
    assert result["routes"] == []


@pytest.mark.asyncio
async def test_reconciled_stop_not_found_returns_error(service):
    service.repo.find_stop = MagicMock(return_value=None)
    service.yandex.geocode_stop = AsyncMock(return_value=None)

    result = await service.get_reconciled_schedule(stop_name="zzz")
    assert "error" in result


@pytest.mark.asyncio
async def test_reconciled_geocode_fallback(service):
    """Остановка не в реестре, Яндекс.Геокодер дал координаты."""
    service.repo.find_stop = MagicMock(return_value=None)
    service.yandex.geocode_stop = AsyncMock(return_value={
        "name": "Найденная",
        "latitude": 55.70,
        "longitude": 37.60,
        "id": "geo-1",
        "address": "ул. Пример",
    })
    service.get_online_buses = AsyncMock(return_value=[])

    result = await service.get_reconciled_schedule(stop_name="Найденная")
    log.debug("result: %s", result)
    assert "error" not in result
    assert result["stop"]["name"] == "Найденная"
    # uuid нет — stop_v2 не должен вызываться
    assert service.moscow.get_stop_v2.await_count == 0


# =========================================================================== #
#  8. get_bus_eta — обёртка                                                    #
# =========================================================================== #
@pytest.mark.asyncio
async def test_eta_returns_nested_eta(service):
    stop = make_stop()
    service.repo.find_stop = MagicMock(return_value=stop)
    service.moscow.get_stop_v2 = AsyncMock(
        return_value={"routes": [_official_route(route="1", sec=240)]}
    )
    service.get_online_buses = AsyncMock(return_value=[])

    result = await service.get_bus_eta(route="1", stop_name="X")
    log.debug("get_bus_eta: %s", result)
    assert result["route_number"] == "1"
    assert result["eta"]["arrival_seconds"] == 240
    assert result["eta"]["reconciliation_status"] == "official_only"
    # status на верхнем уровне больше НЕ отдаётся
    assert "status" not in result


@pytest.mark.asyncio
async def test_eta_route_not_found_message(service):
    stop = make_stop()
    service.repo.find_stop = MagicMock(return_value=stop)
    service.moscow.get_stop_v2 = AsyncMock(
        return_value={"routes": [_official_route(route="1")]}
    )
    service.get_online_buses = AsyncMock(return_value=[])

    result = await service.get_bus_eta(route="ZZZ", stop_name="X")
    log.debug("result: %s", result)
    assert result["eta"] is None
    assert "message" in result
    assert "ZZZ" in result["message"]


@pytest.mark.asyncio
async def test_eta_case_insensitive_route_match(service):
    stop = make_stop()
    service.repo.find_stop = MagicMock(return_value=stop)
    service.moscow.get_stop_v2 = AsyncMock(
        return_value={"routes": [_official_route(route="Т86", sec=300)]}
    )
    service.get_online_buses = AsyncMock(return_value=[])

    result = await service.get_bus_eta(route="т86", stop_name="X")
    log.debug("result: %s", result)
    assert result["eta"]["arrival_seconds"] == 300


@pytest.mark.asyncio
async def test_eta_propagates_error(service):
    service.repo.find_stop = MagicMock(return_value=None)
    service.yandex.geocode_stop = AsyncMock(return_value=None)

    result = await service.get_bus_eta(route="1", stop_name="нет-такой")
    assert "error" in result


# =========================================================================== #
#  9. ИЗВЕСТНЫЕ ПРОБЛЕМЫ (xfail)                                               #
# =========================================================================== #
@pytest.mark.xfail(
    reason="Яндекс-шина без метаданных РНИС маркируется как 'rnis' в extra.source",
    strict=False,
)
@pytest.mark.asyncio
async def test_yandex_only_bus_should_be_marked_yandex(service):
    """
    Ожидаемое поведение: extra.source == 'yandex'.
    Текущее: extra.source == 'rnis', потому что поле source
    не выставляется, если meta is None.
    """
    service.yandex.get_vehicles = AsyncMock(
        return_value=[make_yandex_bus(clean_uuid="abcdef")]
    )
    # get_meta_by_clean_uuid по фикстуре возвращает None

    buses = await service.get_online_buses(source="yandex")
    assert buses[0]["extra"]["source"] == "yandex"


@pytest.mark.xfail(
    reason="Нет сортировки по distance_km при указанных lat/lon",
    strict=False,
)
@pytest.mark.asyncio
async def test_online_buses_should_sort_by_distance(service):
    """
    Ожидаемое поведение: при lat/lon сортировка по distance_km возрастает.
    Текущее: порядок = порядок получения (Яндекс → РНИС).
    """
    service.rnis.get_online_buses = AsyncMock(return_value=[
        make_rnis_bus(vehicle_uuid="11111111-1111-1111-1111-111111111111",
                      lat=55.80, lon=37.87),
        make_rnis_bus(vehicle_uuid="22222222-2222-2222-2222-222222222222",
                      lat=55.772, lon=37.841),
    ])

    buses = await service.get_online_buses(
        lat=55.77, lon=37.84, radius_km=50, source="rnis"
    )
    log.debug("порядок: %s", [b["uuid"] for b in buses])
    # второй — ближе к точке запроса, должен идти первым
    assert buses[0]["uuid"] == "22222222-2222-2222-2222-222222222222"


@pytest.mark.xfail(
    reason="RNIS-автобус с фильтром route_number матчится, но фильтр по state_number не приходит в RNIS.get_online_buses",
    strict=False,
)
@pytest.mark.asyncio
async def test_online_buses_state_number_filter_isolation(service):
    """
    Ожидание: state_number фильтруется оркестратором (через matches_filters).
    Текущее: matches_filters вызывается с state_number — проверим, что аргумент дошёл.
    """
    service.rnis.get_online_buses = AsyncMock(return_value=[make_rnis_bus()])
    service.rnis.matches_filters = MagicMock(return_value=True)

    await service.get_online_buses(source="rnis", state_number="А123")

    call = service.rnis.matches_filters.call_args
    log.debug("matches_filters: %s", call.kwargs)
    assert call.kwargs.get("state_number") == "А123"