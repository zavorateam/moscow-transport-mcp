import asyncio
import logging
import math
from typing import Any, Dict, List, Optional

try:
    from config import settings
except ImportError:
    try:
        from .config import settings
    except ImportError:
        settings = None

from clients.moscow_transport_client import MoscowTransportClient
from clients.rnis_client import RNISClient, haversine_km
from stops_repository import StopsRepository
from clients.yandex_client import YandexTransportClient

logger = logging.getLogger(__name__)


def bearing_degrees(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlambda = math.radians(lon2 - lon1)
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


class UnifiedTransportService:
    def __init__(self):
        self.yandex = YandexTransportClient()
        rnis_ttl = settings.RNIS_CACHE_TTL_SECONDS if settings else 900
        rnis_int = settings.RNIS_REQUEST_INTERVAL if settings else 0.5
        self.rnis = RNISClient(cache_ttl_seconds=rnis_ttl, request_interval=rnis_int)
        self.moscow = MoscowTransportClient()
        self.repo = StopsRepository()

    def get_stop_from_repo(
        self,
        stop_id: Optional[str] = None,
        stop_name: Optional[str] = None,
        route_number: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if stop_id:
            if route_number:
                try:
                    found = self.repo.find_stop(stop_id, route_number=route_number)
                except TypeError:
                    found = self.repo.find_stop(stop_id)
            else:
                found = self.repo.find_stop(stop_id)
            if found:
                return found

        if stop_name:
            if route_number:
                try:
                    found = self.repo.find_stop(stop_name, route_number=route_number)
                except TypeError:
                    found = self.repo.find_stop(stop_name)
            else:
                found = self.repo.find_stop(stop_name)
            if found:
                return found

        return None
    
    async def resolve_stop(
        self,
        stop_id: Optional[str] = None,
        stop_name: Optional[str] = None,
        route_number: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        # 1. Локальный репозиторий Москвы с автовыбором нужного павильона под маршрут
        stop_entry = self.get_stop_from_repo(stop_id=stop_id, stop_name=stop_name, route_number=route_number)
        if stop_entry:
            return stop_entry

        # 2. РНИС МО по UUID
        if stop_id and len(str(stop_id)) == 36 and hasattr(self.rnis, "get_stop_routes"):
            try:
                rnis_data = await self.rnis.get_stop_routes(str(stop_id))
                routes = rnis_data.get("routes", [])
                if routes:
                    s_name = "Остановка МО"
                    s_lat = None
                    s_lon = None
                    for r in routes:
                        for sp in r.get("stop_point_list", []):
                            if sp.get("stop_point_uuid") == str(stop_id):
                                s_name = sp.get("stop_point_title") or s_name
                                s_lat = float(sp.get("stop_point_latitude") or 0) or None
                                s_lon = float(sp.get("stop_point_longitude") or 0) or None
                                break
                        if s_lat and s_lon:
                            break
                    return {
                        "name": s_name,
                        "uuid": str(stop_id),
                        "stop_id": str(stop_id),
                        "internal_id": None,
                        "lat": s_lat,
                        "lon": s_lon,
                        "coordinates": {"lat": s_lat, "lon": s_lon, "source": "rnis"},
                        "routes": [r["route_number"] for r in routes],
                        "source": "rnis",
                        "_rnis_routes": routes,
                    }
            except Exception:
                pass

        # 3. МосТранспорт stop_v2 по UUID
        if stop_id and len(str(stop_id)) == 36 and hasattr(self.moscow, "get_stop_v2"):
            try:
                moscow_data = await self.moscow.get_stop_v2(str(stop_id))
                if moscow_data and moscow_data.get("routes"):
                    return {
                        "name": moscow_data.get("name") or "Остановка",
                        "uuid": str(stop_id),
                        "stop_id": str(stop_id),
                        "internal_id": moscow_data.get("internal_id"),
                        "lat": moscow_data.get("lat"),
                        "lon": moscow_data.get("lon"),
                        "coordinates": {"lat": moscow_data.get("lat"), "lon": moscow_data.get("lon"), "source": "moscow_transport"},
                        "routes": [r.get("route_number") for r in moscow_data.get("routes", [])],
                        "source": "moscow_transport",
                    }
            except Exception:
                pass

        # 4. Яндекс Геокодинг
        q = stop_name or stop_id
        if q and hasattr(self.yandex, "geocode_stop"):
            try:
                y_info = await self.yandex.geocode_stop(q)
                if y_info and y_info.get("latitude"):
                    return {
                        "name": y_info.get("name", q),
                        "lat": y_info.get("latitude"),
                        "lon": y_info.get("longitude"),
                        "coordinates": {"lat": y_info.get("latitude"), "lon": y_info.get("longitude"), "source": "yandex"},
                        "uuid": None,
                        "internal_id": y_info.get("id"),
                        "routes": [],
                        "source": "yandex",
                    }
            except Exception:
                pass

        return None

    async def get_online_buses(
        self,
        query: Optional[str] = None,
        route_number: Optional[str] = None,
        state_number: Optional[str] = None,
        carrier: Optional[str] = None,
        vehicle_mark: Optional[str] = None,
        bnso_code: Optional[str] = None,
        uuid: Optional[str] = None,
        low_floor_only: Optional[bool] = None,
        air_conditioned_only: Optional[bool] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        radius_km: Optional[float] = None,
        source: Optional[str] = "all",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        search_lat = lat or (settings.DEFAULT_LAT if settings else 55.7558)
        search_lon = lon or (settings.DEFAULT_LON if settings else 37.6173)
        search_radius = radius_km or (settings.DEFAULT_SEARCH_RADIUS_KM if settings else 6.0)

        yandex_vehicles = []
        rnis_vehicles = []

        if source in ("all", "yandex"):
            try:
                spn_lat = max(0.02, round((search_radius * 2) / 111.0, 5))
                spn_lon = max(0.03, round((search_radius * 2) / (111.0 * 0.56), 5))
                yandex_vehicles = await self.yandex.get_vehicles(
                    lat=search_lat,
                    lon=search_lon,
                    spn_lon=spn_lon,
                    spn_lat=spn_lat,
                )
            except Exception:
                pass

        try:
            await self.rnis.get_catalog()
        except Exception:
            pass

        if source in ("all", "rnis"):
            try:
                rnis_vehicles = await self.rnis.get_online_buses(
                    lat=lat,
                    lon=lon,
                    radius_km=radius_km,
                    route_number=route_number,
                    state_number=state_number,
                    low_floor_only=low_floor_only,
                    air_conditioned_only=air_conditioned_only,
                )
            except Exception:
                pass

        enriched_yandex = []
        matched_rnis_uuids = set()

        for y_bus in yandex_vehicles:
            clean_uuid = y_bus.get("clean_uuid", "")
            meta = self.rnis.get_meta_by_clean_uuid(clean_uuid) if clean_uuid else None

            if meta:
                y_bus["source"] = "yandex+rnis"
                y_bus["state_number"] = meta.get("state_number") or ""
                y_bus["carrier_name"] = meta.get("carrier_name") or y_bus.get("carrier_name") or ""
                y_bus["vehicle_mark"] = meta.get("vehicle_mark_name") or ""
                y_bus["vehicle_uuid"] = meta.get("uuid") or clean_uuid
                y_bus["is_low_floor"] = bool(meta.get("is_low_floor_level", False))
                y_bus["is_air_conditioned"] = bool(meta.get("is_air_conditioning_installation", False))
                matched_rnis_uuids.add(clean_uuid)
            else:
                y_bus["source"] = "yandex"
                y_bus["vehicle_uuid"] = clean_uuid
                y_bus["state_number"] = ""
                y_bus["vehicle_mark"] = ""

            enriched_yandex.append(y_bus)

        combined_raw = []
        for y_bus in enriched_yandex:
            if lat is not None and lon is not None and y_bus.get("latitude") and y_bus.get("longitude"):
                dist = round(haversine_km(lat, lon, y_bus["latitude"], y_bus["longitude"]), 2)
                if radius_km and dist > radius_km:
                    continue
                y_bus["distance_km"] = dist

            if self.rnis.matches_filters(
                bus=y_bus,
                query=query,
                route_number=route_number,
                state_number=state_number,
                carrier=carrier,
                vehicle_mark=vehicle_mark,
                bnso_code=bnso_code,
                uuid=uuid,
                low_floor_only=low_floor_only,
                air_conditioned_only=air_conditioned_only,
            ):
                combined_raw.append(y_bus)

        if source in ("all", "rnis"):
            for r_bus in rnis_vehicles:
                clean_id = r_bus.get("vehicle_uuid", "").replace("-", "").lower()
                if clean_id in matched_rnis_uuids:
                    continue

                if lat is not None and lon is not None and r_bus.get("latitude") and r_bus.get("longitude"):
                    dist = round(haversine_km(lat, lon, r_bus["latitude"], r_bus["longitude"]), 2)
                    if radius_km and dist > radius_km:
                        continue
                    r_bus["distance_km"] = dist

                if self.rnis.matches_filters(
                    bus=r_bus,
                    query=query,
                    route_number=route_number,
                    state_number=state_number,
                    carrier=carrier,
                    vehicle_mark=vehicle_mark,
                    bnso_code=bnso_code,
                    uuid=uuid,
                    low_floor_only=low_floor_only,
                    air_conditioned_only=air_conditioned_only,
                ):
                    combined_raw.append(r_bus)

        if lat is not None and lon is not None:
            combined_raw.sort(key=lambda x: x.get("distance_km", 999.0))

        formatted_buses = []
        for b in combined_raw[:limit]:
            b_lat = b.get("latitude")
            b_lon = b.get("longitude")
            b_course = b.get("course") or 0.0
            r_num = b.get("route_number") or ""

            next_stop_data = None
            if r_num and b_lat is not None and b_lon is not None:
                route_stops = self.repo.get_stops_for_route(r_num)
                if route_stops:
                    forward_candidates = []
                    for st in route_stops:
                        st_c = st.get("coordinates", {})
                        st_lat = st_c.get("lat") or st.get("lat")
                        st_lon = st_c.get("lon") or st.get("lon")
                        if st_lat and st_lon:
                            d = haversine_km(b_lat, b_lon, st_lat, st_lon)
                            b_deg = bearing_degrees(b_lat, b_lon, st_lat, st_lon)
                            diff = abs((b_deg - b_course + 180) % 360 - 180)
                            if diff <= 90:
                                forward_candidates.append((d, st["name"]))
                    if forward_candidates:
                        forward_candidates.sort(key=lambda x: x[0])
                        next_stop_data = {
                            "name": forward_candidates[0][1],
                            "distance_meters": int(forward_candidates[0][0] * 1000),
                        }

            extra_source = b.get("source", "rnis")

            formatted_buses.append({
                "route_number": r_num,
                "course": b_course,
                "route_name": b.get("route_name") or f"Маршрут {r_num}",
                "uuid": b.get("vehicle_uuid") or b.get("clean_uuid") or "",
                "speed_kmh": b.get("speed_kmh", 0.0),
                "coordinates": {
                    "lat": b_lat,
                    "lon": b_lon,
                    "source": b.get("coordinates_source") or ("yandex" if "yandex" in extra_source else "rnis"),
                },
                "next_stop": next_stop_data,
                "destination": b.get("destination") or "По маршруту",
                "extra": {
                    "state_number": b.get("state_number", ""),
                    "vehicle_mark": b.get("vehicle_mark", ""),
                    "carrier_name": b.get("carrier_name", ""),
                    "bnso_code": str(b.get("bnso_code", "")),
                    "is_low_floor": bool(b.get("is_low_floor", False)),
                    "is_air_conditioned": bool(b.get("is_air_conditioned", False)),
                    "is_electrobus": bool(b.get("is_electrobus", False)),
                    "signal_age_minutes": b.get("signal_age_minutes"),
                    "device_time": b.get("device_time"),
                    "source": extra_source,
                    "line_id": b.get("line_id"),
                    "thread_id": b.get("thread_id"),
                },
            })

        return formatted_buses

    async def get_reconciled_schedule(
        self,
        stop_name: Optional[str] = None,
        stop_id: Optional[str] = None,
        route_number: Optional[str] = None,
        threshold_seconds: int = 180,
    ) -> Dict[str, Any]:
        stop_entry = await self.resolve_stop(stop_id=stop_id, stop_name=stop_name, route_number=route_number)

        if not stop_entry:
            q = stop_name or stop_id or "Остановка"
            return {"error": f"Остановка '{q}' не найдена.", "routes": [], "routes_count": 0}

        stop_lat = stop_entry.get("lat") or stop_entry.get("coordinates", {}).get("lat")
        stop_lon = stop_entry.get("lon") or stop_entry.get("coordinates", {}).get("lon")
        stop_uuid = stop_entry.get("uuid")
        internal_id = stop_entry.get("internal_id") or stop_entry.get("stop_id")

        moscow_res = None
        if stop_uuid and len(str(stop_uuid)) == 36 and hasattr(self.moscow, "get_stop_v2"):
            try:
                moscow_res = await self.moscow.get_stop_v2(str(stop_uuid))
            except Exception:
                if internal_id and stop_lat is not None and stop_lon is not None:
                    try:
                        moscow_res = await self.moscow.get_stop_qr_forecast(
                            internal_id=str(internal_id), lon=stop_lon, lat=stop_lat
                        )
                    except Exception:
                        pass
        elif internal_id and stop_lat is not None and stop_lon is not None and hasattr(self.moscow, "get_stop_qr_forecast"):
            try:
                moscow_res = await self.moscow.get_stop_qr_forecast(
                    internal_id=str(internal_id), lon=stop_lon, lat=stop_lat
                )
            except Exception:
                pass

        # Если маршруты уже получены в resolve_stop из РНИС
        rnis_routes = stop_entry.get("_rnis_routes")
        if rnis_routes is None and stop_uuid and len(str(stop_uuid)) == 36 and hasattr(self.rnis, "get_stop_routes"):
            try:
                r_res = await self.rnis.get_stop_routes(str(stop_uuid))
                rnis_routes = r_res.get("routes", [])
            except Exception:
                rnis_routes = []

        live_buses = []
        if stop_lat and stop_lon:
            try:
                live_buses = await self.get_online_buses(
                    route_number=route_number,
                    lat=stop_lat,
                    lon=stop_lon,
                    radius_km=10.0,
                    limit=20,
                )
            except Exception:
                pass

        routes_map: Dict[str, Dict[str, Any]] = {}

        if moscow_res and "routes" in moscow_res:
            for mr in moscow_res["routes"]:
                r_num = mr.get("route_number", "")
                if not r_num:
                    continue
                if route_number and route_number.strip().lower() not in r_num.strip().lower():
                    continue
                forecasts = mr.get("forecasts", [])
                sec = forecasts[0].get("time_seconds") if forecasts else None
                m_min = forecasts[0].get("time_minutes") if forecasts else None
                routes_map[r_num.strip().lower()] = {
                    "route": r_num,
                    "destination": mr.get("destination") or "По маршруту",
                    "off_sec": sec,
                    "off_min": m_min,
                    "is_realtime": forecasts[0].get("by_telemetry", False) if forecasts else False,
                    "source": "moscow_transport_v2",
                }

        if rnis_routes:
            for rr in rnis_routes:
                r_num = rr.get("route_number", "")
                if not r_num:
                    continue
                if route_number and route_number.strip().lower() not in r_num.strip().lower():
                    continue
                r_key = r_num.strip().lower()
                if r_key not in routes_map or routes_map[r_key]["off_sec"] is None:
                    routes_map[r_key] = {
                        "route": r_num,
                        "destination": rr.get("destination") or rr.get("to") or "По маршруту",
                        "off_sec": rr.get("arrival_seconds"),
                        "off_min": rr.get("arrival_minutes"),
                        "is_realtime": rr.get("arrival_seconds") is not None,
                        "stops_remaining": rr.get("stops_remaining"),
                        "source": "rnis",
                    }

        if not routes_map and route_number:
            r_key = route_number.strip().lower()
            routes_map[r_key] = {
                "route": route_number,
                "destination": "По маршруту",
                "off_sec": None,
                "off_min": None,
                "is_realtime": False,
                "source": "none",
            }

        reconciled_routes = []
        for r_key, r_info in routes_map.items():
            r_num = r_info["route"]
            off_sec = r_info["off_sec"]
            off_min = r_info["off_min"]

            closest_live_bus = None
            y_sec = None
            for b in live_buses:
                if b.get("route_number", "").strip().lower() == r_key:
                    closest_live_bus = b
                    b_coords = b.get("coordinates", {})
                    b_lat = b_coords.get("lat")
                    b_lon = b_coords.get("lon")
                    if b_lat and b_lon and stop_lat and stop_lon:
                        dist = haversine_km(b_lat, b_lon, stop_lat, stop_lon)
                        spd = max(b.get("speed_kmh", 0.0), settings.DEFAULT_AVERAGE_SPEED_KMH if settings else 18.0)
                        y_sec = int((dist * 1.25 / spd) * 3600)
                    break

            final_sec = None
            final_min = None
            chosen_source = "none"
            status = "no_data"

            if y_sec is not None and off_sec is not None:
                delta = abs(y_sec - off_sec)
                if delta <= threshold_seconds:
                    final_sec = y_sec
                    final_min = round(y_sec / 60, 1)
                    chosen_source = "yandex"
                    status = "consistent"
                else:
                    final_sec = off_sec
                    final_min = off_min
                    chosen_source = r_info["source"]
                    status = "discrepancy_resolved"
            elif off_sec is not None:
                final_sec = off_sec
                final_min = off_min
                chosen_source = r_info["source"]
                status = "official_only"
            elif y_sec is not None:
                final_sec = y_sec
                final_min = round(y_sec / 60, 1)
                chosen_source = "yandex"
                status = "yandex_live_bus_only"

            if final_sec and final_sec > 3600 and closest_live_bus and y_sec is not None:
                final_sec = y_sec
                final_min = round(y_sec / 60, 1)
                chosen_source = "calculated_live_bus_telemetry"
                status = "nonsense_forecast_overridden"

            reconciled_routes.append({
                "route": r_num,
                "destination": r_info["destination"],
                "stops_remaining": r_info.get("stops_remaining"),
                "eta": {
                    "arrival_minutes": final_min,
                    "arrival_seconds": final_sec,
                    "reconciliation_status": status,
                    "chosen_source": chosen_source,
                    "is_realtime": r_info["is_realtime"] or (chosen_source == "yandex"),
                },
                "closest_bus": closest_live_bus,
            })

        return {
            "stop": {
                "name": stop_entry.get("name"),
                "uuid": stop_entry.get("uuid"),
                "internal_id": stop_entry.get("internal_id"),
                "coordinates": stop_entry.get("coordinates") or {"lat": stop_lat, "lon": stop_lon},
            },
            "routes_count": len(reconciled_routes),
            "routes": reconciled_routes,
        }

    async def get_bus_eta(
        self,
        route: str,
        stop_name: Optional[str] = None,
        stop_id: Optional[str] = None,
        threshold_seconds: int = 180,
    ) -> Dict[str, Any]:
        stop_entry = await self.resolve_stop(stop_id=stop_id, stop_name=stop_name, route_number=route)
        q = stop_name or stop_id or "Остановка"

        if not stop_entry:
            return {
                "error": f"Остановка '{q}' не найдена в реестре остановок.",
                "status": "not_found",
                "route": route,
            }

        sched = await self.get_reconciled_schedule(
            stop_name=stop_name,
            stop_id=stop_id,
            route_number=route,
            threshold_seconds=threshold_seconds,
        )

        if "error" in sched and not sched.get("routes"):
            return {
                "error": sched["error"],
                "route": route,
            }

        target_r = None
        for r in sched.get("routes", []):
            if r.get("route", "").strip().lower() == route.strip().lower():
                target_r = r
                break

        # Проверяем, ходит ли этот маршрут вообще через эту остановку
        stop_routes = stop_entry.get("routes", [])
        is_route_served = any(route.strip().lower() == r.strip().lower() or route.strip().lower() in r.strip().lower() for r in stop_routes)

        if not target_r and not is_route_served:
            return {
                "route_number": route,
                "eta": None,
                "message": f"Маршрут '{route}' не найден на этой остановке.",
                "stop": sched.get("stop"),
            }

        if not target_r or target_r.get("eta", {}).get("arrival_seconds") is None:
            return {
                "route_number": route,
                "destination": target_r.get("destination") if target_r else "По маршруту",
                "stop": sched.get("stop"),
                "eta": None,
                "status": "no_buses_online",
                "message": f"Маршрут '{route}' проходит через эту остановку, но активных автобусов на линии в ближайшее время нет.",
                "closest_bus": None,
            }

        return {
            "route_number": route,
            "destination": target_r.get("destination"),
            "stop": sched.get("stop"),
            "eta": target_r.get("eta"),
            "closest_bus": target_r.get("closest_bus"),
        }

    # ======================================================================= #
    #  Расширенный инструментарий для AI-агентов                               #
    # ======================================================================= #
    async def get_filter_options(self, category: Optional[str] = None, query: Optional[str] = None) -> Dict[str, Any]:
        await self.rnis.get_catalog()
        cat = self.rnis._catalog_cache

        q = query.strip().lower() if query else None

        carriers = sorted(list({c.get("carrier_name") for c in cat.values() if c.get("carrier_name")}))
        marks = sorted(list({c.get("vehicle_mark_name") for c in cat.values() if c.get("vehicle_mark_name")}))
        routes = sorted(list({c.get("route_number") for c in cat.values() if c.get("route_number") and c.get("route_number") != "Не назначен"}))

        if q:
            carriers = [c for c in carriers if q in c.lower()]
            marks = [m for m in marks if q in m.lower()]
            routes = [r for r in routes if q in r.lower()]

        result = {
            "carriers": carriers[:50],
            "vehicle_marks": marks[:50],
            "routes_sample": routes[:50],
            "available_amenities": [
                {"key": "low_floor_only", "description": "Низкопольный салон (без ступеней при входе)"},
                {"key": "air_conditioned_only", "description": "Кондиционер в салоне"},
                {"key": "has_pavilion_only", "description": "Наличие крытого павильона на остановке"},
            ],
            "sources": ["all", "yandex", "rnis", "moscow"],
        }
        if category and category in result:
            return {category: result[category]}
        return result

    async def search_stops(
        self,
        query: Optional[str] = None,
        route_number: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        radius_km: Optional[float] = None,
        bbox: Optional[List[float]] = None,
        has_pavilion_only: Optional[bool] = None,
        adm_area: Optional[str] = None,
        district: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        # 1. Поиск в Москве (bus_stops_all.geojson)
        moscow_stops = self.repo.search_stops(
            query=query,
            route_number=route_number,
            lat=lat,
            lon=lon,
            radius_km=radius_km,
            bbox=bbox,
            has_pavilion_only=has_pavilion_only,
            adm_area=adm_area,
            district=district,
            limit=limit,
        )

        # 2. Поиск в РНИС МО через com.rnis.geo.action.stop_point.list
        rnis_stops = []
        target_lat = lat
        target_lon = lon

        # Если координаты не переданы, но ищут балашихинские остановки — ставим BBox Балашихи
        if target_lat is None and query and any(w in query.lower() for w in ("славы", "балаших", "горенки", "крупск")):
            target_lat = 55.7971
            target_lon = 37.9398

        if target_lat is not None and target_lon is not None and hasattr(self.rnis, "get_stops_by_bbox"):
            rad = radius_km or 3.0
            d_lat = rad / 111.0
            d_lon = rad / (111.0 * math.cos(math.radians(target_lat)))
            try:
                raw_rnis = await self.rnis.get_stops_by_bbox(
                    left_top_lat=target_lat + d_lat,
                    left_top_lon=target_lon - d_lon,
                    right_bottom_lat=target_lat - d_lat,
                    right_bottom_lon=target_lon + d_lon,
                )
                q_lower = query.strip().lower() if query else None
                for rs in raw_rnis:
                    if q_lower and q_lower not in rs["name"].lower() and q_lower not in rs.get("station_name", "").lower():
                        continue
                    rs["distance_km"] = round(haversine_km(target_lat, target_lon, rs["lat"], rs["lon"]), 2)
                    rnis_stops.append(rs)
            except Exception:
                pass

        combined = moscow_stops + rnis_stops
        if lat is not None and lon is not None:
            combined.sort(key=lambda x: x.get("distance_km", 999.0))

        return combined[:limit]

    async def get_stop_board(
        self,
        stop_id: Optional[str] = None,
        stop_name: Optional[str] = None,
        route_number: Optional[str] = None,
        realtime_only: bool = False,
        max_wait_minutes: Optional[float] = None,
        limit: int = 50,
    ) -> Dict[str, Any]:
        sched = await self.get_reconciled_schedule(stop_name=stop_name, stop_id=stop_id, route_number=route_number)
        routes = sched.get("routes", [])

        filtered = []
        for r in routes:
            eta = r.get("eta", {})
            if realtime_only and not eta.get("is_realtime"):
                continue
            if max_wait_minutes is not None:
                m = eta.get("arrival_minutes")
                if m is None or m > max_wait_minutes:
                    continue
            filtered.append(r)

        return {
            "stop": sched.get("stop"),
            "routes_count": len(filtered[:limit]),
            "arrivals": filtered[:limit],
        }

    async def get_route_details(self, route_number: str) -> Dict[str, Any]:
        """Получение нитки остановок и активных бортов маршрута (Москва и МО)."""
        stops = self.repo.get_stops_for_route(route_number)
        active_buses = await self.get_online_buses(route_number=route_number, limit=30)

        # Если в локальном реестре остановок нет (например, Балашиха/МО), берем из РНИС
        if not stops and active_buses:
            first_bus = active_buses[0]
            first_uuid = first_bus.get("uuid")
            if first_uuid:
                try:
                    cat = self.rnis._catalog_cache.get(first_uuid, {})
                    r_uuid = cat.get("order_execution_uuid") or cat.get("carrier_uuid")
                except Exception:
                    pass

        return {
            "route_number": route_number,
            "stops_count": len(stops),
            "stops": [{"name": s["name"], "lat": s["lat"], "lon": s["lon"], "id": s.get("stop_id")} for s in stops],
            "active_buses_count": len(active_buses),
            "active_buses": active_buses,
        }

    async def get_vehicle_card(self, vehicle_uuid: Optional[str] = None, state_number: Optional[str] = None) -> Optional[Dict[str, Any]]:
        buses = await self.get_online_buses(uuid=vehicle_uuid, state_number=state_number, limit=1)
        if buses:
            return buses[0]
        catalog_items = await self.rnis.search_catalog(uuid=vehicle_uuid, state_number=state_number, limit=1)
        if catalog_items:
            return {"status": "inactive_in_depot", "details": catalog_items[0]}
        return None

    async def get_system_status(self) -> Dict[str, Any]:
        count = 0
        try:
            count = await self.rnis.get_telematics_count()
        except Exception:
            pass
        return {
            "status": "online",
            "rnis_telematics_count": count,
            "geojson_loaded_stops": len(self.repo.spatial),
        }