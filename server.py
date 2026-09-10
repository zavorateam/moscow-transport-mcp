import argparse
import json
from typing import List, Optional
import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

try:
    from mcp.server import MCPServer
except ImportError:
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP as MCPServer
        except ImportError:
            MCPServer = None

from config import settings
from transport_service import UnifiedTransportService

service = UnifiedTransportService()

mcp = None
if MCPServer is not None:
    mcp = MCPServer(
        "Transit Agent Gateway",
        instructions=(
            "Интеллектуальный шлюз для AI-агентов общественного транспорта (Москва и МО). "
            "Предоставляет инструменты для интроспекции фильтров, поиска остановок, "
            "живых табло, точного расчета ETA и телеметрии бортов (GPS, скорость, курс, оснащение)."
        ),
    )


# =========================================================================== #
#  Схемы запросов REST                                                        #
# =========================================================================== #
class ETARequest(BaseModel):
    stop_name: Optional[str] = Field(None, description="Название остановки ('Метромост')")
    stop_id: Optional[str] = Field(None, description="UUID или ID остановки")
    route: str = Field(..., description="Номер маршрута ('824')")
    threshold_seconds: int = Field(180, description="Порог сверки расписаний в секундах")


class ScheduleRequest(BaseModel):
    stop_name: Optional[str] = None
    stop_id: Optional[str] = None
    route: Optional[str] = None
    realtime_only: bool = False
    max_wait_minutes: Optional[float] = None
    limit: int = 50


class BusesSearchRequest(BaseModel):
    query: Optional[str] = None
    route: Optional[str] = None
    state_number: Optional[str] = None
    carrier: Optional[str] = None
    vehicle_mark: Optional[str] = None
    low_floor_only: Optional[bool] = None
    air_conditioned_only: Optional[bool] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    radius_km: Optional[float] = None
    source: Optional[str] = "all"
    limit: int = 50


class StopsSearchRequest(BaseModel):
    query: Optional[str] = None
    route: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    radius_km: Optional[float] = None
    bbox: Optional[List[float]] = None
    has_pavilion_only: Optional[bool] = None
    adm_area: Optional[str] = None
    district: Optional[str] = None
    limit: int = 50


# =========================================================================== #
#  MCP Tools (Регистрация для AI-агентов)                                     #
# =========================================================================== #
if mcp is not None:
    @mcp.tool()
    async def get_filter_options(category: str = None, query: str = None) -> str:
        """Интроспекция доступных фильтров: списки перевозчиков, марок ТС, маршрутов, опций (кондей, низкий пол)."""
        res = await service.get_filter_options(category=category, query=query)
        return json.dumps(res, ensure_ascii=False, indent=2)

    @mcp.tool()
    async def search_stops(
        query: str = None,
        route_number: str = None,
        lat: float = None,
        lon: float = None,
        radius_km: float = None,
        adm_area: str = None,
        district: str = None,
        limit: int = 25,
    ) -> str:
        """Поиск остановок по названию, номеру маршрута, координатам, округу или району."""
        stops = await service.search_stops(
            query=query,
            route_number=route_number,
            lat=lat,
            lon=lon,
            radius_km=radius_km,
            adm_area=adm_area,
            district=district,
            limit=limit,
        )
        return json.dumps(stops, ensure_ascii=False, indent=2)

    @mcp.tool()
    async def get_stop_board(
        stop_name: str = None,
        stop_id: str = None,
        route_number: str = None,
        realtime_only: bool = False,
        max_wait_minutes: float = None,
    ) -> str:
        """Живое табло остановки: все прибывающие маршруты, расписание и телеметрия."""
        board = await service.get_stop_board(
            stop_id=stop_id,
            stop_name=stop_name,
            route_number=route_number,
            realtime_only=realtime_only,
            max_wait_minutes=max_wait_minutes,
        )
        return json.dumps(board, ensure_ascii=False, indent=2)

    @mcp.tool()
    async def get_bus_eta(route: str, stop_name: str = None, stop_id: str = None, threshold_seconds: int = 180) -> str:
        """Точечный прогноз времени прибытия конкретного маршрута к остановке со сверкой всех источников."""
        res = await service.get_bus_eta(route=route, stop_name=stop_name, stop_id=stop_id, threshold_seconds=threshold_seconds)
        return json.dumps(res, ensure_ascii=False, indent=2)

    @mcp.tool()
    async def search_online_buses(
        route_number: str = None,
        state_number: str = None,
        carrier: str = None,
        vehicle_mark: str = None,
        low_floor_only: bool = None,
        air_conditioned_only: bool = None,
        lat: float = None,
        lon: float = None,
        radius_km: float = None,
        query: str = None,
        limit: int = 25,
    ) -> str:
        """Поиск активных автобусов на карте с фильтрацией по маршруту, госномерам, кондиционеру, низкому полу."""
        buses = await service.get_online_buses(
            query=query,
            route_number=route_number,
            state_number=state_number,
            carrier=carrier,
            vehicle_mark=vehicle_mark,
            low_floor_only=low_floor_only,
            air_conditioned_only=air_conditioned_only,
            lat=lat,
            lon=lon,
            radius_km=radius_km,
            limit=limit,
        )
        return json.dumps(buses, ensure_ascii=False, indent=2)

    @mcp.tool()
    async def get_route_details(route_number: str) -> str:
        """Информация о маршруте: полная последовательность остановок и активные борта на нитке."""
        details = await service.get_route_details(route_number=route_number)
        return json.dumps(details, ensure_ascii=False, indent=2)

    @mcp.tool()
    async def get_vehicle_card(vehicle_uuid: str = None, state_number: str = None) -> str:
        """Карточка конкретного транспортного средства по госномеру или UUID (оснащение, перевозчик, положение)."""
        card = await service.get_vehicle_card(vehicle_uuid=vehicle_uuid, state_number=state_number)
        return json.dumps(card or {"error": "Транспортное средство не найдено"}, ensure_ascii=False, indent=2)


# =========================================================================== #
#  FastAPI Application (REST API)                                             #
# =========================================================================== #
def create_http_app() -> FastAPI:
    app = FastAPI(
        title="Transit Agent Gateway",
        description="REST API и MCP-шлюз для AI-агентов общественного транспорта (Москва и МО).",
        version="7.0.0",
    )

    # /filter-options
    @app.get("/filter-options", summary="Доступные варианты фильтрации (интроспекция)")
    async def filter_options_endpoint(category: Optional[str] = Query(None), query: Optional[str] = Query(None)):
        return await service.get_filter_options(category=category, query=query)

    # /stops
    @app.post("/stops", summary="Поиск остановок (POST)")
    async def stops_post(req: StopsSearchRequest):
        return await service.search_stops(
            query=req.query,
            route_number=req.route,
            lat=req.lat,
            lon=req.lon,
            radius_km=req.radius_km,
            bbox=req.bbox,
            has_pavilion_only=req.has_pavilion_only,
            adm_area=req.adm_area,
            district=req.district,
            limit=req.limit,
        )

    @app.get("/stops", summary="Поиск остановок (GET)")
    async def stops_get(
        query: Optional[str] = Query(None),
        route: Optional[str] = Query(None),
        lat: Optional[float] = Query(None),
        lon: Optional[float] = Query(None),
        radius_km: Optional[float] = Query(None),
        has_pavilion_only: Optional[bool] = Query(None),
        adm_area: Optional[str] = Query(None),
        district: Optional[str] = Query(None),
        limit: int = Query(50),
    ):
        return await service.search_stops(
            query=query,
            route_number=route,
            lat=lat,
            lon=lon,
            radius_km=radius_km,
            has_pavilion_only=has_pavilion_only,
            adm_area=adm_area,
            district=district,
            limit=limit,
        )

    # /eta
    @app.post("/eta", summary="Сколько времени до автобуса (POST)")
    @app.post("/api/eta", include_in_schema=False)
    async def eta_post(req: ETARequest):
        return await service.get_bus_eta(
            route=req.route, stop_name=req.stop_name, stop_id=req.stop_id, threshold_seconds=req.threshold_seconds
        )

    @app.get("/eta", summary="Сколько времени до автобуса (GET)")
    @app.get("/api/eta", include_in_schema=False)
    async def eta_get(
        route: str = Query(..., description="Номер маршрута ('824')"),
        stop_name: Optional[str] = Query(None, description="Название остановки ('Метромост')"),
        stop_id: Optional[str] = Query(None, description="UUID или ID остановки"),
        threshold_seconds: int = Query(180),
    ):
        return await service.get_bus_eta(
            route=route, stop_name=stop_name, stop_id=stop_id, threshold_seconds=threshold_seconds
        )

    # /schedule & /board
    @app.post("/schedule", summary="Табло остановки (POST)")
    @app.post("/api/schedule", include_in_schema=False)
    async def schedule_post(req: ScheduleRequest):
        return await service.get_stop_board(
            stop_name=req.stop_name,
            stop_id=req.stop_id,
            route_number=req.route,
            realtime_only=req.realtime_only,
            max_wait_minutes=req.max_wait_minutes,
            limit=req.limit,
        )

    @app.get("/schedule", summary="Табло остановки (GET)")
    @app.get("/api/schedule", include_in_schema=False)
    async def schedule_get(
        stop_name: Optional[str] = Query(None),
        stop_id: Optional[str] = Query(None),
        route: Optional[str] = Query(None),
        realtime_only: bool = Query(False),
        max_wait_minutes: Optional[float] = Query(None),
        limit: int = Query(50),
    ):
        return await service.get_stop_board(
            stop_name=stop_name,
            stop_id=stop_id,
            route_number=route,
            realtime_only=realtime_only,
            max_wait_minutes=max_wait_minutes,
            limit=limit,
        )

    # /buses
    @app.post("/buses", summary="Реальные онлайн-автобусы (POST)")
    @app.post("/api/buses", include_in_schema=False)
    async def buses_post(req: BusesSearchRequest):
        return await service.get_online_buses(
            query=req.query,
            route_number=req.route,
            state_number=req.state_number,
            carrier=req.carrier,
            vehicle_mark=req.vehicle_mark,
            low_floor_only=req.low_floor_only,
            air_conditioned_only=req.air_conditioned_only,
            lat=req.lat,
            lon=req.lon,
            radius_km=req.radius_km,
            source=req.source,
            limit=req.limit,
        )

    @app.get("/buses", summary="Реальные онлайн-автобусы (GET)")
    @app.get("/api/buses", include_in_schema=False)
    async def buses_get(
        query: Optional[str] = Query(None),
        route: Optional[str] = Query(None),
        state_number: Optional[str] = Query(None),
        carrier: Optional[str] = Query(None),
        vehicle_mark: Optional[str] = Query(None),
        low_floor_only: Optional[bool] = Query(None),
        air_conditioned_only: Optional[bool] = Query(None),
        lat: Optional[float] = Query(None),
        lon: Optional[float] = Query(None),
        radius_km: Optional[float] = Query(None),
        source: Optional[str] = Query("all"),
        limit: int = Query(50),
    ):
        return await service.get_online_buses(
            query=query,
            route_number=route,
            state_number=state_number,
            carrier=carrier,
            vehicle_mark=vehicle_mark,
            low_floor_only=low_floor_only,
            air_conditioned_only=air_conditioned_only,
            lat=lat,
            lon=lon,
            radius_km=radius_km,
            source=source,
            limit=limit,
        )

    # /routes/{route_number}
    @app.get("/routes/{route_number}", summary="Трасса маршрута и активные борта")
    async def route_details_get(route_number: str):
        return await service.get_route_details(route_number=route_number)

    # /vehicle
    @app.get("/vehicle", summary="Карточка конкретного транспортного средства")
    async def vehicle_get(vehicle_uuid: Optional[str] = Query(None), state_number: Optional[str] = Query(None)):
        res = await service.get_vehicle_card(vehicle_uuid=vehicle_uuid, state_number=state_number)
        if not res:
            raise HTTPException(status_code=404, detail="Транспортное средство не найдено")
        return res

    # /rnis-routes
    @app.get("/rnis-routes", summary="Маршруты остановки РНИС МО (очищенные под агентов)")
    @app.get("/api/rnis-routes", include_in_schema=False)
    async def rnis_routes(stop_uuid: str = Query(...)):
        return await service.rnis.get_stop_routes(stop_uuid=stop_uuid)

    # /moscow-stop
    @app.get("/moscow-stop", summary="Прямой запрос stop_v2 Московского транспорта")
    @app.get("/api/moscow-stop", include_in_schema=False)
    async def moscow_stop(stop_uuid: str = Query(...)):
        return await service.moscow.get_stop_v2(stop_uuid=stop_uuid)

    # /count
    @app.get("/count", summary="Телеметрия онлайн")
    @app.get("/api/count", include_in_schema=False)
    async def count():
        return await service.get_system_status()

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--http", action="store_true", help="Запустить в режиме HTTP/REST сервера")
    parser.add_argument("--host", default=settings.HOST if settings else "0.0.0.0")
    parser.add_argument("--port", type=int, default=settings.PORT if settings else 8000)
    args = parser.parse_args()

    if args.http:
        print(f"[*] Сервер запущен: http://{args.host}:{args.port}")
        print(f"[*] Документация OpenAPI: http://localhost:{args.port}/docs")
        app = create_http_app()
        uvicorn.run(app, host=args.host, port=args.port, http="h11")
    else:
        if mcp is not None:
            mcp.run(transport="stdio")
        else:
            print("[!] MCP Server не найден, запустите с флагом --http")


if __name__ == "__main__":
    main()