"""
Тесты для StopsRepository с полной отладкой.

Запуск:
    pytest -q test_stops_repository.py            # обычный прогон (с log_cli)
    pytest -s -q test_stops_repository.py         # плюс stdout в реальном времени
    pytest -q test_stops_repository.py -k route   # только тесты про маршруты
"""
import json
import logging
import pprint

import pytest

from stops_repository import StopsRepository, haversine_km


# ---------------------------------------------------------------------------
# Логгер для самого тестового файла
# ---------------------------------------------------------------------------
log = logging.getLogger("tests.stops")


# ---------------------------------------------------------------------------
# Вспомогательные функции
# ---------------------------------------------------------------------------
def _write(tmp_path, data, name="bus_stops_all.geojson"):
    """Пишет data как JSON в tmp_path/name и возвращает Path."""
    path = tmp_path / name
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    log.debug("Записан файл %s (%d байт)", path, path.stat().st_size)
    log.debug("Содержимое:\n%s", path.read_text(encoding="utf-8"))
    return path


def _dump_repo(repo, title="Repository dump"):
    """Подробно печатает состояние репозитория."""
    log.debug("=== %s ===", title)
    log.debug("spatial (всего): %d", len(repo.spatial))
    log.debug("stops_by_id: %s", list(repo.stops_by_id.keys()))
    log.debug("stops_by_name (ключи): %s", list(repo.stops_by_name.keys()))
    log.debug("stops_by_route (ключи): %s", list(repo.stops_by_route.keys()))
    for i, stop in enumerate(repo.spatial):
        log.debug("spatial[%d]: %s", i, pprint.pformat(stop, width=100))
    log.debug("=== /%s ===", title)


def _dump_stop(stop, label="stop"):
    log.debug("%s = %s", label, pprint.pformat(stop, width=100) if stop else None)


# ---------------------------------------------------------------------------
# 0. haversine
# ---------------------------------------------------------------------------
def test_haversine_km_zero():
    log.debug("Проверяем нулевое расстояние между одинаковыми точками")
    result = haversine_km(55.7558, 37.6173, 55.7558, 37.6173)
    log.debug("haversine_km(same, same) = %r", result)
    assert result == pytest.approx(0.0, abs=1e-9)


def test_haversine_km_moscow_spb():
    log.debug("Считаем Москва → Санкт-Петербург")
    distance = haversine_km(55.7558, 37.6173, 59.9343, 30.3351)
    log.debug("distance = %.3f км", distance)
    assert distance == pytest.approx(635, rel=0.02)


# ---------------------------------------------------------------------------
# 1. FeatureCollection: загрузка, индексы, координаты
# ---------------------------------------------------------------------------
def test_load_feature_collection(tmp_path):
    data = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "11111111-1111-1111-1111-111111111111",
                "properties": {
                    "Name": "Main Stop",
                    "ID": 123,
                    "global_id": 456,
                    "RouteNumbers": "1, 2;3",
                    "Direction": "A",
                    "AdmArea": "Area",
                    "District": "Dist",
                    "Pavilion": True,
                },
                "geometry": {"type": "Point", "coordinates": [37.6173, 55.7558]},
            },
            {
                "type": "Feature",
                "properties": {
                    "Name": "Second Stop",
                    "ID": "id2",
                    "RouteNumbers": "2",
                    "Latitude_WGS84": 55.76,
                    "Longitude_WGS84": 37.62,
                },
            },
        ],
    }
    path = _write(tmp_path, data)
    repo = StopsRepository(str(path))
    _dump_repo(repo, "After FeatureCollection load")

    assert len(repo.spatial) == 2

    stop1 = repo.find_stop("Main Stop")
    _dump_stop(stop1, "stop1 (by name 'Main Stop')")

    assert stop1 is not None
    assert stop1["lat"] == pytest.approx(55.7558)
    assert stop1["lon"] == pytest.approx(37.6173)
    assert stop1["coordinates"]["lat"] == pytest.approx(55.7558)
    assert stop1["coordinates"]["lon"] == pytest.approx(37.6173)
    assert stop1["routes"] == ["1", "2", "3"]
    assert stop1["stop_id"] == "123"
    assert stop1["global_id"] == 456
    assert stop1["uuid"] == "11111111-1111-1111-1111-111111111111"

    for key in ("123", "456", "11111111-1111-1111-1111-111111111111"):
        found = repo.find_stop(key)
        log.debug("find_stop(%r) -> stop_id=%r", key, found["stop_id"] if found else None)
        assert found is stop1

    stop2 = repo.find_stop("Second Stop")
    _dump_stop(stop2, "stop2 (by name 'Second Stop')")
    assert stop2 is not None
    assert stop2["lat"] == pytest.approx(55.76)
    assert stop2["lon"] == pytest.approx(37.62)
    assert stop2["routes"] == ["2"]
    assert stop2["uuid"] is None

    log.debug("Маршрут '1': %s", [s["name"] for s in repo.get_stops_for_route("1")])
    log.debug("Маршрут '2': %s", [s["name"] for s in repo.get_stops_for_route("2")])
    log.debug("Маршрут '3': %s", [s["name"] for s in repo.get_stops_for_route("3")])
    log.debug("Маршрут '999': %s", repo.get_stops_for_route("999"))

    assert repo.get_stops_for_route("1") == [stop1]
    assert repo.get_stops_for_route("2") == [stop1, stop2]
    assert repo.get_stops_for_route("3") == [stop1]
    assert repo.get_stops_for_route("999") == []


# ---------------------------------------------------------------------------
# 2. list-формат + отбрасывание невалидных точек
# ---------------------------------------------------------------------------
def test_load_list_format_and_skip_invalid(tmp_path):
    data = [
        {
            "uuid": "22222222-2222-2222-2222-222222222222",
            "Name": "List Stop",
            "ID": "L1",
            "global_id": "G1",
            "RouteNumbers": "5;6",
            "geoData": {"coordinates": [30.0, 60.0]},
            "Direction": "B",
            "AdmArea": "A",
            "District": "D",
            "Pavilion": False,
        },
        {
            "uuid": "not-uuid",
            "Name": "No Coords",
            "ID": "L2",
            "geoData": {"coordinates": [None, None]},
            "Latitude_WGS84": 0,
            "Longitude_WGS84": 0,
        },
    ]
    path = _write(tmp_path, data)
    repo = StopsRepository(str(path))
    _dump_repo(repo, "After list load (1 валидный, 1 отброшен)")

    assert len(repo.spatial) == 1

    stop = repo.find_stop("List Stop")
    _dump_stop(stop, "stop (by name 'List Stop')")
    assert stop is not None
    assert stop["lat"] == pytest.approx(60.0)
    assert stop["lon"] == pytest.approx(30.0)
    assert stop["routes"] == ["5", "6"]
    assert stop["stop_id"] == "L1"
    assert stop["global_id"] == "G1"
    assert stop["uuid"] == "22222222-2222-2222-2222-222222222222"

    assert repo.find_stop("L1") is stop
    assert repo.find_stop("G1") is stop
    assert repo.find_stop("22222222-2222-2222-2222-222222222222") is stop
    assert repo.get_stops_for_route("5") == [stop]
    assert repo.get_stops_for_route("6") == [stop]


# ---------------------------------------------------------------------------
# 3. Поиск по частичному имени, регистронезависимо
# ---------------------------------------------------------------------------
def test_find_stop_by_partial_name_case_insensitive(tmp_path):
    data = [
        {"Name": "Central Bus Station", "ID": "1", "RouteNumbers": "1",
         "geoData": {"coordinates": [10.0, 20.0]}},
    ]
    path = _write(tmp_path, data)
    repo = StopsRepository(str(path))
    _dump_repo(repo, "После загрузки одного стопа")

    expected = repo.find_stop("Central Bus Station")
    _dump_stop(expected, "expected")

    for query in ("central", "bus station", "CENTRAL BUS"):
        found = repo.find_stop(query)
        log.debug("find_stop(%r) -> %r", query, found["name"] if found else None)
        assert found is expected

    assert repo.find_stop("missing") is None


# ---------------------------------------------------------------------------
# 4. Регистронезависимый поиск маршрута
# ---------------------------------------------------------------------------
def test_route_lookup_is_case_insensitive(tmp_path):
    data = [
        {"Name": "Route Stop", "ID": "1", "RouteNumbers": "A;B",
         "geoData": {"coordinates": [10.0, 20.0]}},
    ]
    path = _write(tmp_path, data)
    repo = StopsRepository(str(path))
    _dump_repo(repo, "route lookup")

    stop = repo.find_stop("Route Stop")
    for q in ("a", "A"):
        got = repo.get_stops_for_route(q)
        log.debug("get_stops_for_route(%r) -> %s", q, [s["name"] for s in got])
        assert got == [stop]


# ---------------------------------------------------------------------------
# 5. Fallback на .json если .geojson нет
# ---------------------------------------------------------------------------
def test_fallback_to_json_when_geojson_missing(tmp_path, monkeypatch):
    data = [
        {"Name": "Fallback Stop", "ID": "F1", "RouteNumbers": "7",
         "geoData": {"coordinates": [10.0, 20.0]}},
    ]
    _write(tmp_path, data, name="bus_stops_all.json")
    monkeypatch.chdir(tmp_path)
    log.debug("cwd = %s, файлы: %s", tmp_path, sorted(p.name for p in tmp_path.iterdir()))

    repo = StopsRepository("bus_stops_all.geojson")
    _dump_repo(repo, "fallback .json")

    assert len(repo.spatial) == 1
    assert repo.find_stop("Fallback Stop") is not None


# ---------------------------------------------------------------------------
# 6. Отсутствие файла — не падаем
# ---------------------------------------------------------------------------
def test_missing_file_does_not_raise(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    log.debug("cwd = %s (файлов нет)", tmp_path)

    repo = StopsRepository("no_such_file.geojson")
    _dump_repo(repo, "missing file")

    assert repo.spatial == []
    assert repo.stops_by_id == {}
    assert repo.find_stop("anything") is None


# ---------------------------------------------------------------------------
# 7. Дубликаты имён
# ---------------------------------------------------------------------------
def test_find_stop_returns_first_of_duplicates(tmp_path):
    data = [
        {"Name": "Same", "ID": "1", "RouteNumbers": "1",
         "geoData": {"coordinates": [10.0, 20.0]}},
        {"Name": "same", "ID": "2", "RouteNumbers": "1",
         "geoData": {"coordinates": [11.0, 21.0]}},
    ]
    path = _write(tmp_path, data)
    repo = StopsRepository(str(path))
    _dump_repo(repo, "duplicates")

    first = repo.find_stop("Same")
    second = repo.find_stop("same")
    _dump_stop(first, "find_stop('Same')")
    _dump_stop(second, "find_stop('same')")

    assert first["stop_id"] == "1"
    assert second["stop_id"] == "1"
    assert len(repo.stops_by_name["same"]) == 2


# ---------------------------------------------------------------------------
# 8. Приоритет ID над именем
# ---------------------------------------------------------------------------
def test_find_stop_id_takes_priority_over_name(tmp_path):
    data = [
        {"Name": "123", "ID": "aaa", "RouteNumbers": "1",
         "geoData": {"coordinates": [10.0, 20.0]}},
        {"Name": "Other", "ID": "123", "RouteNumbers": "1",
         "geoData": {"coordinates": [11.0, 21.0]}},
    ]
    path = _write(tmp_path, data)
    repo = StopsRepository(str(path))
    _dump_repo(repo, "ID vs name")

    stop = repo.find_stop("123")
    _dump_stop(stop, "find_stop('123')")

    assert stop["stop_id"] == "123"
    assert stop["name"] == "Other"


# ---------------------------------------------------------------------------
# 9. Смешанные разделители RouteNumbers
# ---------------------------------------------------------------------------
def test_routes_parsing_mixed_separators(tmp_path):
    data = [
        {"Name": "S", "ID": "1", "RouteNumbers": " 5 ;; 6 ,,",
         "geoData": {"coordinates": [10.0, 20.0]}},
    ]
    path = _write(tmp_path, data)
    repo = StopsRepository(str(path))
    _dump_repo(repo, "mixed separators")

    routes = repo.find_stop("S")["routes"]
    log.debug("routes = %r", routes)
    assert routes == ["5", "6"]


# ---------------------------------------------------------------------------
# 10. Битый JSON — лог + пустой репозиторий
# ---------------------------------------------------------------------------
def test_invalid_json_logs_error_and_returns_empty(tmp_path, caplog):
    path = tmp_path / "bus_stops_all.geojson"
    path.write_text("{not valid json", encoding="utf-8")
    log.debug("Записан битый JSON: %r", path.read_text(encoding="utf-8"))

    with caplog.at_level(logging.ERROR, logger="stops_repository"):
        repo = StopsRepository(str(path))

    log.debug("Записи лога: %s", [r.getMessage() for r in caplog.records])
    _dump_repo(repo, "invalid json")

    assert repo.spatial == []
    assert repo.stops_by_id == {}
    assert any("Ошибка чтения" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 11. Пустой FeatureCollection
# ---------------------------------------------------------------------------
def test_empty_feature_collection(tmp_path):
    data = {"type": "FeatureCollection", "features": []}
    path = _write(tmp_path, data)
    repo = StopsRepository(str(path))
    _dump_repo(repo, "empty FeatureCollection")

    assert repo.spatial == []
    assert repo.find_stop("anything") is None


# ---------------------------------------------------------------------------
# 12. Стоп без имени
# ---------------------------------------------------------------------------
def test_stop_without_name(tmp_path):
    data = [
        {"ID": "42", "RouteNumbers": "1",
         "geoData": {"coordinates": [10.0, 20.0]}},
    ]
    path = _write(tmp_path, data)
    repo = StopsRepository(str(path))
    _dump_repo(repo, "no name")

    assert len(repo.spatial) == 1
    assert repo.stops_by_name == {}
    assert repo.find_stop("42")["stop_id"] == "42"
    assert repo.find_stop("") is None


# ---------------------------------------------------------------------------
# 13. Короткий uuid
# ---------------------------------------------------------------------------
def test_short_uuid_is_dropped(tmp_path):
    data = [
        {"uuid": "short", "Name": "S", "ID": "I1", "global_id": "G1",
         "RouteNumbers": "1", "geoData": {"coordinates": [10.0, 20.0]}},
    ]
    path = _write(tmp_path, data)
    repo = StopsRepository(str(path))
    _dump_repo(repo, "short uuid")

    stop = repo.find_stop("I1")
    _dump_stop(stop, "stop (by I1)")
    assert stop is not None
    assert stop["uuid"] is None
    assert repo.find_stop("G1") is stop
    assert repo.find_stop("short") is None


# ---------------------------------------------------------------------------
# 14. Кириллица
# ---------------------------------------------------------------------------
def test_cyrillic_case_insensitive_search(tmp_path):
    data = [
        {"Name": "Улица Ленина", "ID": "1", "RouteNumbers": "1",
         "geoData": {"coordinates": [10.0, 20.0]}},
    ]
    path = _write(tmp_path, data)
    repo = StopsRepository(str(path))
    _dump_repo(repo, "cyrillic")

    expected = repo.find_stop("Улица Ленина")
    _dump_stop(expected, "expected")
    for q in ("ленина", "ЛЕНИНА", "улица"):
        found = repo.find_stop(q)
        log.debug("find_stop(%r) -> %r", q, found["name"] if found else None)
        assert found is expected


# ---------------------------------------------------------------------------
# 15. Явный дамп полного содержимого репозитория (полезно при отладке)
# ---------------------------------------------------------------------------
def test_full_dump_for_debugging(tmp_path):
    data = [
        {"Name": "A", "ID": "1", "global_id": "g1", "RouteNumbers": "1;2",
         "geoData": {"coordinates": [10.0, 20.0]}},
        {"Name": "B", "ID": "2", "RouteNumbers": "2",
         "geoData": {"coordinates": [11.0, 21.0]}},
    ]
    path = _write(tmp_path, data)
    repo = StopsRepository(str(path))

    print("\n========== FULL REPOSITORY DUMP ==========")
    pprint.pprint(repo.spatial, width=120)
    print("stops_by_id  :", list(repo.stops_by_id.keys()))
    print("stops_by_name:", list(repo.stops_by_name.keys()))
    print("stops_by_route:", {k: len(v) for k, v in repo.stops_by_route.items()})
    print("==========================================\n")

    assert len(repo.spatial) == 2