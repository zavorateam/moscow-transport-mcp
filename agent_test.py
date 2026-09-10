#!/usr/bin/env python3
"""
Сквозной интеграционный тест-сьют для Transit Agent Gateway.
Имитирует полный цикл работы AI-агента со всеми сценариями и крайними случаями.

Запуск:
    python agent_test.py
    python agent_test.py --verbose
    python agent_test.py --dump-sh          # Сохранит все curl в run_curls.sh
    python agent_test.py --base-url http://localhost:8000
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# Цвета для вывода в терминал
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


class AgentTestRunner:
    def __init__(self, base_url: str = "http://localhost:8000", verbose: bool = False):
        self.base_url = base_url.rstrip("/")
        self.verbose = verbose
        self.curl_log: List[str] = []
        self.passed = 0
        self.failed = 0
        self.warnings = 0
        self.total_time_ms = 0.0

    def _http_request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        timeout: float = 15.0,
    ) -> Tuple[int, Any, float, str]:
        """Выполняет HTTP запрос и формирует точный curl эквивалент."""
        query_str = ""
        if params:
            clean_params = {k: v for k, v in params.items() if v is not None}
            if clean_params:
                query_str = "?" + urllib.parse.urlencode(clean_params)

        full_url = f"{self.base_url}{path}{query_str}"

        # Формируем эквивалентный curl
        curl_cmd = f"curl -s -X {method} '{full_url}'"
        body_bytes = None
        headers = {}

        if json_data is not None:
            body_str = json.dumps(json_data, ensure_ascii=False)
            body_bytes = body_str.encode("utf-8")
            headers["Content-Type"] = "application/json"
            curl_cmd += f" -H 'Content-Type: application/json' -d '{body_str}'"

        self.curl_log.append(curl_cmd)

        req = urllib.request.Request(full_url, data=body_bytes, headers=headers, method=method)
        t0 = time.monotonic()
        status = 0
        parsed_body = None

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                elapsed_ms = (time.monotonic() - t0) * 1000
                status = resp.status
                raw = resp.read().decode("utf-8")
                try:
                    parsed_body = json.loads(raw)
                except Exception:
                    parsed_body = raw
                return status, parsed_body, elapsed_ms, curl_cmd
        except urllib.error.HTTPError as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            status = e.code
            raw = e.read().decode("utf-8")
            try:
                parsed_body = json.loads(raw)
            except Exception:
                parsed_body = raw
            return status, parsed_body, elapsed_ms, curl_cmd
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            return 0, {"error": str(e)}, elapsed_ms, curl_cmd

    def run_case(
        self,
        title: str,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        expected_status: int = 200,
        assert_fn=None,
    ) -> Optional[Any]:
        """Выполняет и оценивает тестовый случай."""
        status, body, elapsed, curl_cmd = self._http_request(method, path, params, json_data)
        self.total_time_ms += elapsed

        is_ok = (status == expected_status)
        custom_err = None

        if is_ok and assert_fn:
            try:
                assert_fn(body)
            except AssertionError as ae:
                is_ok = False
                custom_err = str(ae)
            except Exception as ex:
                is_ok = False
                custom_err = f"Ошибка ассерта: {ex}"

        # Печать результата
        prefix = f"{GREEN}[PASS]{RESET}" if is_ok else f"{RED}[FAIL]{RESET}"
        timing = f"{CYAN}{elapsed:6.1f}ms{RESET}"
        print(f"{prefix} {timing} | {BOLD}{title}{RESET}")

        if self.verbose or not is_ok:
            print(f"       {YELLOW}curl:{RESET} {curl_cmd}")
            if custom_err:
                print(f"       {RED}Assert Error:{RESET} {custom_err}")
            preview = json.dumps(body, ensure_ascii=False) if isinstance(body, (dict, list)) else str(body)
            if len(preview) > 300:
                preview = preview[:300] + "..."
            print(f"       {YELLOW}Response ({status}):{RESET} {preview}")
            print()

        if is_ok:
            self.passed += 1
        else:
            self.failed += 1

        return body


def main():
    parser = argparse.ArgumentParser(description="Transit Agent Test Suite")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Базовый URL шлюза")
    parser.add_argument("-v", "--verbose", action="store_true", help="Подробный вывод каждого запроса")
    parser.add_argument("--dump-sh", action="store_true", help="Сохранить все curl команды в run_curls.sh")
    args = parser.parse_args()

    runner = AgentTestRunner(base_url=args.base_url, verbose=args.verbose)

    print(f"\n{BOLD}{'=' * 80}{RESET}")
    print(f"{BOLD}   TRANSIT AGENT GATEWAY — ПОЛНЫЙ E2E СЬЮТ ТЕСТОВ (REAL LIVE DATA){RESET}")
    print(f"{BOLD}{'=' * 80}{RESET}\n")

    # ======================================================================= #
    # 1. СТАТУС СИСТЕМЫ И ИНТРОСПЕКЦИЯ                                         #
    # ======================================================================= #
    print(f"\n{BOLD}--- 1. СТАТУС СИСТЕМЫ И ИНТРОСПЕКЦИЯ ФИЛЬТРОВ ---{RESET}")

    runner.run_case(
        "1.1. Общий статус онлайна телеметрии (/count)",
        "GET", "/count",
        assert_fn=lambda b: (
            b.get("status") == "online" and
            b.get("rnis_telematics_count", 0) > 0 and
            b.get("geojson_loaded_stops", 0) > 10000
        )
    )

    runner.run_case(
        "1.2. Получение полного каталога доступных фильтров (/filter-options)",
        "GET", "/filter-options",
        assert_fn=lambda b: (
            len(b.get("carriers", [])) > 10 and
            len(b.get("vehicle_marks", [])) > 10 and
            len(b.get("available_amenities", [])) >= 3
        )
    )

    runner.run_case(
        "1.3. Интроспекция: автокомплит перевозчиков по слову 'авто'",
        "GET", "/filter-options",
        params={"category": "carriers", "query": "авто"},
        assert_fn=lambda b: any("МОСТРАНСАВТО" in c for c in b.get("carriers", []))
    )

    runner.run_case(
        "1.4. Интроспекция: фильтр марок по марке 'лиаз'",
        "GET", "/filter-options",
        params={"category": "vehicle_marks", "query": "лиаз"},
        assert_fn=lambda b: any("ЛиАЗ" in m for m in b.get("vehicle_marks", []))
    )

    runner.run_case(
        "1.5. Интроспекция: некорректная категория (graceful fallback)",
        "GET", "/filter-options",
        params={"category": "non_existent_category"},
        assert_fn=lambda b: isinstance(b, dict)
    )

    # ======================================================================= #
    # 2. ПОИСК ОСТАНОВОК (DISCOVERY & DISAMBIGUATION)                          #
    # ======================================================================= #
    print(f"\n{BOLD}--- 2. ПОИСК ОСТАНОВОК (МОСКВА + МО + BBOX + ГЕОКОДИНГ) ---{RESET}")

    runner.run_case(
        "2.1. Москва: Поиск остановки 'Метромост' (все 3 павильона)",
        "POST", "/stops",
        json_data={"query": "Метромост"},
        assert_fn=lambda b: len(b) >= 2 and any(s.get("internal_id") in ("3242", "3255", "1009816") for s in b)
    )

    runner.run_case(
        "2.2. Москва: Поиск павильона 'Метромост' с фильтром по маршруту 824",
        "POST", "/stops",
        json_data={"query": "Метромост", "route": "824"},
        assert_fn=lambda b: len(b) >= 1 and any("824" in s.get("routes", []) for s in b)
    )

    runner.run_case(
        "2.3. Москва: Поиск павильона 'Метромост' с фильтром по маршруту с951",
        "POST", "/stops",
        json_data={"query": "Метромост", "route": "с951"},
        assert_fn=lambda b: len(b) >= 1 and any("с951" in s.get("routes", []) for s in b)
    )

    runner.run_case(
        "2.4. МО (Балашиха): Поиск остановки 'Славы' в РНИС",
        "POST", "/stops",
        json_data={"query": "Славы"},
        assert_fn=lambda b: any(s.get("uuid") == "a6466a64-eb47-11e7-90c9-136fdbe30654" for s in b)
    )

    runner.run_case(
        "2.5. РНИС BBox: Поиск остановок в радиусе 2 км от центра Балашихи",
        "POST", "/stops",
        json_data={"lat": 55.7971, "lon": 37.9398, "radius_km": 2.0},
        assert_fn=lambda b: len(b) >= 3 and all(s.get("source") == "rnis" or s.get("coordinates") for s in b)
    )

    runner.run_case(
        "2.6. Москва: Радиусный поиск в центре Москвы (55.7558, 37.6173)",
        "POST", "/stops",
        json_data={"lat": 55.7558, "lon": 37.6173, "radius_km": 1.0},
        assert_fn=lambda b: len(b) >= 5 and b[0].get("distance_km") is not None
    )

    runner.run_case(
        "2.7. Фильтр по наличию крытого павильона (has_pavilion_only=True)",
        "POST", "/stops",
        json_data={"query": "Метромост", "has_pavilion_only": True},
        assert_fn=lambda b: all(s.get("extra", {}).get("pavilion") is True for s in b)
    )

    runner.run_case(
        "2.8. Негативный тест: Поиск заведомо несуществующей остановки",
        "POST", "/stops",
        json_data={"query": "ОстановкаВДалекомКосмосе_999999"},
        assert_fn=lambda b: b == []
    )

    runner.run_case(
        "2.9. GET-метод поиска остановок (/stops?query=Сокол)",
        "GET", "/stops",
        params={"query": "Сокол", "limit": 5},
        assert_fn=lambda b: len(b) >= 1
    )

    # ======================================================================= #
    # 3. ТАБЛО ОСТАНОВОК (SCHEDULE / BOARD)                                    #
    # ======================================================================= #
    print(f"\n{BOLD}--- 3. ТАБЛО ОСТАНОВОК (РНИС МО + МОСГОРТРАНС) ---{RESET}")

    runner.run_case(
        "3.1. РНИС МО: Живое табло остановки 'пл. Славы' по UUID",
        "GET", "/schedule",
        params={"stop_id": "a6466a64-eb47-11e7-90c9-136fdbe30654"},
        assert_fn=lambda b: (
            b.get("stop", {}).get("name") == "пл. Славы" and
            b.get("routes_count", 0) >= 3 and
            any(r.get("route") == "16" for r in b.get("arrivals", []))
        )
    )

    runner.run_case(
        "3.2. РНИС МО: Табло только с активными GPS-рейсами (realtime_only=True)",
        "GET", "/schedule",
        params={"stop_id": "a6466a64-eb47-11e7-90c9-136fdbe30654", "realtime_only": True},
        assert_fn=lambda b: all(r.get("eta", {}).get("is_realtime") is True for r in b.get("arrivals", []))
    )

    runner.run_case(
        "3.3. РНИС МО: Табло по остановке с фильтром маршрута (route=16)",
        "GET", "/schedule",
        params={"stop_id": "a6466a64-eb47-11e7-90c9-136fdbe30654", "route": "16"},
        assert_fn=lambda b: len(b.get("arrivals", [])) >= 1 and all(r.get("route") == "16" for r in b.get("arrivals", []))
    )

    runner.run_case(
        "3.4. Москва: Табло павильона 'Метромост' (ID 3255)",
        "GET", "/schedule",
        params={"stop_id": "3255"},
        assert_fn=lambda b: b.get("stop") is not None and b.get("stop", {}).get("internal_id") == "3255"
    )

    runner.run_case(
        "3.5. Негативный тест: Табло по несуществующему stop_id",
        "GET", "/schedule",
        params={"stop_id": "non_existent_uuid_12345"},
        assert_fn=lambda b: b.get("routes_count") == 0 and b.get("arrivals") == []
    )

    # ======================================================================= #
    # 4. ТОЧНЫЙ РАСЧЕТ ВРЕМЕНИ ПРИБЫТИЯ (BUS ETA)                              #
    # ======================================================================= #
    print(f"\n{BOLD}--- 4. ТОЧНЫЙ РАСЧЕТ ETA (СВЕРКА ИСТОЧНИКОВ + БЛИЖАЙШИЙ БОРТ) ---{RESET}")

    runner.run_case(
        "4.1. РНИС МО: ETA маршрута 16 к остановке 'пл. Славы' по UUID",
        "POST", "/eta",
        json_data={"route": "16", "stop_id": "a6466a64-eb47-11e7-90c9-136fdbe30654"},
        assert_fn=lambda b: (
            b.get("route_number") == "16" and
            b.get("stop", {}).get("name") == "пл. Славы" and
            (b.get("eta") is not None or b.get("status") == "no_buses_online")
        )
    )

    runner.run_case(
        "4.2. Москва: ETA маршрута 824 на 'Метромост' (автовыбор павильона 3255)",
        "POST", "/eta",
        json_data={"route": "824", "stop_name": "Метромост"},
        assert_fn=lambda b: (
            b.get("route_number") == "824" and
            b.get("stop", {}).get("internal_id") in ("3255", "1009816") and
            (b.get("eta") is not None or b.get("status") == "no_buses_online")
        )
    )

    runner.run_case(
        "4.3. Москва: ETA маршрута с951 на 'Метромост' (павильон 3242/3255)",
        "POST", "/eta",
        json_data={"route": "с951", "stop_name": "Метромост"},
        assert_fn=lambda b: b.get("route_number") == "с951" and b.get("stop") is not None
    )

    runner.run_case(
        "4.4. Прямой запрос ETA по ID павильона 3255",
        "POST", "/eta",
        json_data={"route": "824", "stop_id": "3255"},
        assert_fn=lambda b: b.get("stop", {}).get("internal_id") == "3255"
    )

    runner.run_case(
        "4.5. Маршрут, который физически НЕ ходит через остановку",
        "POST", "/eta",
        json_data={"route": "99999", "stop_name": "Метромост"},
        assert_fn=lambda b: b.get("eta") is None and "не найден" in b.get("message", "")
    )

    runner.run_case(
        "4.6. Запрос ETA к несуществующей остановке",
        "POST", "/eta",
        json_data={"route": "16", "stop_name": "НесуществующаяОстановкаВТайге"},
        assert_fn=lambda b: b.get("status") == "not_found" or "не найдена" in b.get("error", "")
    )

    # ======================================================================= #
    # 5. ПОИСК АКТИВНЫХ АВТОБУСОВ НА ЛИНИИ (REALTIME FLEET)                   #
    # ======================================================================= #
    print(f"\n{BOLD}--- 5. ПОИСК АВТОБУСОВ ОНЛАЙН (GPS, КОНДЕЙ, ПОЛ, СКОРОСТЬ) ---{RESET}")

    runner.run_case(
        "5.1. Поиск автобусов конкретного маршрута (route=110 в Балашихе)",
        "GET", "/buses",
        params={"route": "110", "limit": 10},
        assert_fn=lambda b: len(b) >= 1 and all("110" in bus.get("route_number", "") for bus in b)
    )

    runner.run_case(
        "5.2. Фильтр: Автобусы строго с кондиционером (air_conditioned_only=True)",
        "GET", "/buses",
        params={"air_conditioned_only": True, "limit": 5},
        assert_fn=lambda b: len(b) >= 1 and all(bus.get("extra", {}).get("is_air_conditioned") is True for bus in b)
    )

    runner.run_case(
        "5.3. Фильтр: Автобусы строго низкопольные (low_floor_only=True)",
        "GET", "/buses",
        params={"low_floor_only": True, "limit": 5},
        assert_fn=lambda b: len(b) >= 1 and all(bus.get("extra", {}).get("is_low_floor") is True for bus in b)
    )

    runner.run_case(
        "5.4. Радиусный поиск вокруг точки с проверкой сортировки по distance_km",
        "GET", "/buses",
        params={"lat": 55.7971, "lon": 37.9398, "radius_km": 5.0, "limit": 5},
        assert_fn=lambda b: (
            len(b) >= 1 and
            all(bus.get("coordinates", {}).get("lat") is not None for bus in b) and
            all(bus.get("distance_km") is not None for bus in b) and
            [bus["distance_km"] for bus in b] == sorted([bus["distance_km"] for bus in b])
        )
    )

    runner.run_case(
        "5.5. Проверка полноты телеметрии борта (скорость, курс, госномер, перевозчик)",
        "GET", "/buses",
        params={"limit": 1},
        assert_fn=lambda b: (
            len(b) >= 1 and
            "speed_kmh" in b[0] and
            "course" in b[0] and
            "coordinates" in b[0] and
            "state_number" in b[0].get("extra", {}) and
            "carrier_name" in b[0].get("extra", {})
        )
    )

    # Получаем реальный борт для проверки карточки
    sample_buses = runner.run_case("5.6. Вспомогательный запрос для получения активного борта", "GET", "/buses", params={"limit": 3})
    sample_plate = None
    sample_uuid = None
    if sample_buses and isinstance(sample_buses, list):
        for bus in sample_buses:
            if bus.get("extra", {}).get("state_number"):
                sample_plate = bus["extra"]["state_number"]
                sample_uuid = bus.get("uuid")
                break

    # ======================================================================= #
    # 6. КАРТОЧКА ТРАНСПОРТНОГО СРЕДСТВА И МАРШРУТЫ                           #
    # ======================================================================= #
    print(f"\n{BOLD}--- 6. КАРТОЧКА БОРТА И ТРАССА МАРШРУТА ---{RESET}")

    if sample_plate:
        runner.run_case(
            f"6.1. Карточка живого борта по реальному госномеру ({sample_plate})",
            "GET", "/vehicle",
            params={"state_number": sample_plate},
            assert_fn=lambda b: b.get("extra", {}).get("state_number") == sample_plate or sample_plate in str(b)
        )
    else:
        print(f"{YELLOW}[SKIP]{RESET} Нет активных бортов для теста 6.1")

    runner.run_case(
        "6.2. Карточка заведомо несуществующего борта (должен вернуть 404)",
        "GET", "/vehicle",
        params={"state_number": "х999хх999"},
        expected_status=404
    )

    runner.run_case(
        "6.3. Трасса маршрута: активные борта на нитке маршрута 110",
        "GET", "/routes/110",
        assert_fn=lambda b: b.get("route_number") == "110" and b.get("active_buses_count", 0) >= 1
    )

    # ======================================================================= #
    # 7. ПРЯМЫЕ СЛУЖЕБНЫЕ ЭНДПОИНТЫ ПОСТАВЩИКОВ                                #
    # ======================================================================= #
    print(f"\n{BOLD}--- 7. ПРЯМЫЕ ШЛЮЗЫ К ПРОВАЙДЕРАМ (RNIS & MOSCOW APP) ---{RESET}")

    runner.run_case(
        "7.1. Прямой запрос маршрутов остановки РНИС МО (/rnis-routes)",
        "GET", "/rnis-routes",
        params={"stop_uuid": "a6466a64-eb47-11e7-90c9-136fdbe30654"},
        assert_fn=lambda b: b.get("routes_count", 0) >= 1 and "routes" in b
    )

    runner.run_case(
        "7.2. Прямой запрос Московского транспорта stop_v2 (/moscow-stop)",
        "GET", "/moscow-stop",
        params={"stop_uuid": "cd90c51a-3034-4dc3-b95a-6d93aa91c15e"},
        assert_fn=lambda b: b.get("name") is not None or "routes" in b
    )

    # ======================================================================= #
    # ИТОГОВЫЙ ОТЧЕТ                                                          #
    # ======================================================================= #
    total_cases = runner.passed + runner.failed
    print(f"\n{BOLD}{'=' * 80}{RESET}")
    print(f"{BOLD}   ИТОГИ ПРОГОНА АГЕНТ-ТЕСТОВ:{RESET}")
    print(f"   Всего тестов : {BOLD}{total_cases}{RESET}")
    print(f"   Успешно      : {GREEN}{BOLD}{runner.passed}{RESET}")
    print(f"   Провалено    : {RED}{BOLD}{runner.failed}{RESET}")
    print(f"   Сумм. время  : {CYAN}{runner.total_time_ms / 1000:.2f} сек{RESET}")
    print(f"{BOLD}{'=' * 80}{RESET}\n")

    if args.dump_sh:
        with open("run_curls.sh", "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\n# Автосгенерированный набор curl запросов агента\nset -x\n\n")
            for c in runner.curl_log:
                f.write(f"{c}\n")
        print(f"{GREEN}[OK] Все {len(runner.curl_log)} curl-запросов сохранены в run_curls.sh{RESET}\n")

    sys.exit(0 if runner.failed == 0 else 1)


if __name__ == "__main__":
    main()