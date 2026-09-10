"""
Тесты для MoscowTransportClient.

Запуск:
    pytest -q test_moscow_transport_client.py
    pytest -s -q test_moscow_transport_client.py --log-cli-level=DEBUG
"""
import json
import logging

import httpx
import pytest

from clients.moscow_transport_client import MoscowTransportClient

log = logging.getLogger("tests.moscow_transport")


# =========================================================================== #
#  Образцы данных                                                             #
# =========================================================================== #
STOP_V2_JSON = {
    "id": "cd90c51a-3034-4dc3-b95a-6d93aa91c15e",
    "internal_id": 12345,
    "name": "Метро Сокол",
    "lat": 55.805,
    "lon": 37.515,
    "routePath": [
        {
            "number": "т86",
            "lastStopName": "Метро Сокол",
            "electrobus": True,
            "color": "#FF0000",
            "externalForecast": [
                {
                    "time": 120,
                    "byTelemetry": 1,
                    "tmId": "tm-001",
                    "routePathId": 42,
                },
                {
                    "time": 360,
                    "byTelemetry": 0,
                    "tmId": "tm-002",
                    "routePathId": 42,
                },
            ],
        },
        {
            "number": "403",
            "lastStopName": "Аэропорт",
            "electrobus": False,
            "color": None,
            "externalForecast": [],
        },
    ],
}

STOP_V2_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Root>
  <StopDetail>
    <id>cd90c51a-3034-4dc3-b95a-6d93aa91c15e</id>
    <internal_id>12345</internal_id>
    <name>Метро Сокол</name>
    <lat>55.805</lat>
    <lon>37.515</lon>
    <routePath>
      <StopRouteForecast>
        <number>т86</number>
        <lastStopName>Метро Сокол</lastStopName>
        <electrobus>true</electrobus>
        <color>#FF0000</color>
        <externalForecast>
          <TimeForecast>
            <time>120</time>
            <byTelemetry>1</byTelemetry>
            <tmId>tm-001</tmId>
          </TimeForecast>
          <TimeForecast>
            <time>360</time>
            <byTelemetry>0</byTelemetry>
            <tmId>tm-002</tmId>
          </TimeForecast>
        </externalForecast>
      </StopRouteForecast>
      <StopRouteForecast>
        <number>403</number>
        <lastStopName>Аэропорт</lastStopName>
        <electrobus>false</electrobus>
      </StopRouteForecast>
    </routePath>
  </StopDetail>
</Root>
"""

QR_STOP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Root>
  <StopDetail>
    <id>59420839</id>
    <internal_id>59420839</internal_id>
    <name>Остановка у метро</name>
    <lat>55.68333</lat>
    <lon>37.663834</lon>
    <routePath>
      <StopRouteForecast>
        <number>144</number>
        <lastStopName>Конечная</lastStopName>
        <electrobus>false</electrobus>
        <color>#00FF00</color>
        <externalForecast>
          <TimeForecast>
            <time>90</time>
            <byTelemetry>1</byTelemetry>
            <tmId>qr-tm-1</tmId>
          </TimeForecast>
        </externalForecast>
      </StopRouteForecast>
    </routePath>
  </StopDetail>
</Root>
"""


# =========================================================================== #
#  Фикстуры                                                                   #
# =========================================================================== #
@pytest.fixture
def client() -> MoscowTransportClient:
    return MoscowTransportClient()


def _mock_httpx(monkeypatch, handler):
    """Подменяет httpx.AsyncClient так, чтобы все запросы шли через handler."""
    transport = httpx.MockTransport(handler)
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    return transport


# =========================================================================== #
#  1. Прямой парсинг JSON                                                     #
# =========================================================================== #
def test_parse_json_v2_full():
    c = MoscowTransportClient()
    parsed = c._parse_json_v2(STOP_V2_JSON)
    log.debug("parsed JSON: %s", json.dumps(parsed, ensure_ascii=False, indent=2))

    assert parsed["uuid"] == "cd90c51a-3034-4dc3-b95a-6d93aa91c15e"
    assert parsed["internal_id"] == "12345"
    assert parsed["name"] == "Метро Сокол"
    assert parsed["lat"] == 55.805
    assert parsed["lon"] == 37.515
    assert parsed["raw_format"] == "json_v2"
    assert len(parsed["routes"]) == 2

    r1 = parsed["routes"][0]
    assert r1["route_number"] == "т86"
    assert r1["destination"] == "Метро Сокол"
    assert r1["is_electrobus"] is True
    assert r1["color"] == "#FF0000"
    assert len(r1["forecasts"]) == 2

    f1 = r1["forecasts"][0]
    assert f1["time_seconds"] == 120
    assert f1["time_minutes"] == 2.0
    assert f1["by_telemetry"] is True
    assert f1["vehicle_tm_id"] == "tm-001"
    assert f1["route_path_id"] == 42

    f2 = r1["forecasts"][1]
    assert f2["by_telemetry"] is False

    r2 = parsed["routes"][1]
    assert r2["route_number"] == "403"
    assert r2["is_electrobus"] is False
    assert r2["color"] is None
    assert r2["forecasts"] == []


def test_parse_json_v2_missing_fields():
    """Все поля опциональны — парсер не должен падать."""
    c = MoscowTransportClient()
    parsed = c._parse_json_v2({})
    log.debug("parsed empty JSON: %s", parsed)

    assert parsed["uuid"] is None
    assert parsed["internal_id"] == ""
    assert parsed["name"] is None
    assert parsed["routes"] == []
    assert parsed["raw_format"] == "json_v2"


def test_parse_json_v2_null_route_path():
    c = MoscowTransportClient()
    parsed = c._parse_json_v2({"id": "x", "routePath": None})
    assert parsed["routes"] == []


# =========================================================================== #
#  2. Прямой парсинг XML                                                      #
# =========================================================================== #
def test_parse_xml_stop_v2():
    c = MoscowTransportClient()
    parsed = c._parse_xml(STOP_V2_XML)
    log.debug("parsed XML: %s", json.dumps(parsed, ensure_ascii=False, indent=2))

    assert parsed["uuid"] == "cd90c51a-3034-4dc3-b95a-6d93aa91c15e"
    assert parsed["internal_id"] == "12345"
    assert parsed["name"] == "Метро Сокол"
    assert parsed["lat"] == 55.805
    assert parsed["lon"] == 37.515
    assert parsed["raw_format"] == "xml"
    assert len(parsed["routes"]) == 2

    r1 = parsed["routes"][0]
    assert r1["route_number"] == "т86"
    assert r1["is_electrobus"] is True
    assert len(r1["forecasts"]) == 2
    assert r1["forecasts"][0]["time_minutes"] == 2.0
    assert r1["forecasts"][0]["by_telemetry"] is True

    r2 = parsed["routes"][1]
    assert r2["route_number"] == "403"
    assert r2["is_electrobus"] is False
    assert r2["forecasts"] == []


def test_parse_xml_no_stop_detail():
    c = MoscowTransportClient()
    parsed = c._parse_xml("<Root></Root>")
    log.debug("parsed no StopDetail: %s", parsed)
    assert parsed == {"error": "StopDetail not found", "routes": []}


def test_parse_xml_qr_stop():
    c = MoscowTransportClient()
    parsed = c._parse_xml(QR_STOP_XML)
    assert parsed["uuid"] == "59420839"
    assert parsed["internal_id"] == "59420839"
    assert parsed["lat"] == 55.68333
    assert parsed["lon"] == 37.663834
    assert len(parsed["routes"]) == 1
    assert parsed["routes"][0]["route_number"] == "144"
    assert parsed["routes"][0]["forecasts"][0]["vehicle_tm_id"] == "qr-tm-1"


# =========================================================================== #
#  3. get_stop_v2 — с моком httpx                                            #
# =========================================================================== #
@pytest.mark.asyncio
async def test_get_stop_v2_json_success(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        log.debug("handler получил %s %s", request.method, request.url)
        assert request.url.path.endswith("/stop_v2/cd90c51a-3034-4dc3-b95a-6d93aa91c15e")
        return httpx.Response(200, json=STOP_V2_JSON)

    _mock_httpx(monkeypatch, handler)
    result = await client.get_stop_v2("cd90c51a-3034-4dc3-b95a-6d93aa91c15e")
    log.debug("result: %s", result)
    assert result["raw_format"] == "json_v2"
    assert result["name"] == "Метро Сокол"


@pytest.mark.asyncio
async def test_get_stop_v2_fallback_to_xml(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        # отдаём не-JSON, чтобы сработал fallback
        return httpx.Response(200, text=STOP_V2_XML, headers={"content-type": "application/xml"})

    _mock_httpx(monkeypatch, handler)
    result = await client.get_stop_v2("cd90c51a-3034-4dc3-b95a-6d93aa91c15e")
    log.debug("fallback result: %s", result)
    assert result["raw_format"] == "xml"
    assert result["name"] == "Метро Сокол"
    assert len(result["routes"]) == 2


@pytest.mark.asyncio
async def test_get_stop_v2_raises_on_http_error(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_stop_v2("any-id")


@pytest.mark.asyncio
async def test_get_stop_v2_sends_headers(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=STOP_V2_JSON)

    _mock_httpx(monkeypatch, handler)
    await client.get_stop_v2("x")

    h = captured["headers"]
    log.debug("headers: %s", h)
    assert h["user-agent"].startswith("Mozilla/5.0")
    assert "moscowtransport.app" in h["origin"]
    assert h["sec-fetch-mode"] == "cors"


# =========================================================================== #
#  4. get_stop_qr_forecast — с моком httpx                                    #
# =========================================================================== #
@pytest.mark.asyncio
async def test_get_stop_qr_forecast_xml_success(monkeypatch, client):
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, text=QR_STOP_XML, headers={"content-type": "application/xml"})

    _mock_httpx(monkeypatch, handler)
    result = await client.get_stop_qr_forecast("59420839", 37.663834, 55.68333)
    log.debug("qr result: %s", result)

    assert captured["params"]["p"] == "37.663834,55.68333"
    assert "/qr-stop/59420839/stop" in captured["url"]
    assert result["raw_format"] == "xml"
    assert result["uuid"] == "59420839"
    assert result["routes"][0]["route_number"] == "144"


@pytest.mark.asyncio
async def test_get_stop_qr_forecast_fallback_to_json(monkeypatch, client):
    """Если qr-stop внезапно вернул JSON — парсер тоже должен справиться."""

    async def handler(request: httpx.Request) -> httpx.Response:
        # content-type json, но тело — валидный JSON
        return httpx.Response(200, json=STOP_V2_JSON)

    _mock_httpx(monkeypatch, handler)
    result = await client.get_stop_qr_forecast("1", 0.0, 0.0)
    log.debug("qr fallback result: %s", result)
    assert result["raw_format"] == "json_v2"
    assert result["name"] == "Метро Сокол"


@pytest.mark.asyncio
async def test_get_stop_qr_forecast_raises_on_http_error(monkeypatch, client):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    _mock_httpx(monkeypatch, handler)
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_stop_qr_forecast("bad", 0.0, 0.0)


# =========================================================================== #
#  5. E2E-сценарий: «расписание с сайта + fallback по qr»                     #
# =========================================================================== #
@pytest.mark.asyncio
async def test_end_to_end_stop_v2_then_qr_fallback(monkeypatch, client):
    """stop_v2 отвечает JSON, qr-stop — XML. Оба парсятся."""
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "/stop_v2/" in str(request.url):
            return httpx.Response(200, json=STOP_V2_JSON)
        if "/qr-stop/" in str(request.url):
            return httpx.Response(200, text=QR_STOP_XML)
        return httpx.Response(404)

    _mock_httpx(monkeypatch, handler)

    by_uuid = await client.get_stop_v2("cd90c51a-3034-4dc3-b95a-6d93aa91c15e")
    by_qr = await client.get_stop_qr_forecast("59420839", 37.663834, 55.68333)

    log.debug("calls: %s", calls)
    log.debug("by_uuid.raw_format = %s", by_uuid["raw_format"])
    log.debug("by_qr.raw_format   = %s", by_qr["raw_format"])

    assert by_uuid["raw_format"] == "json_v2"
    assert by_qr["raw_format"] == "xml"
    assert len(calls) == 2