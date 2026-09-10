import asyncio
import math
import time
from typing import Any, Dict, List, Optional
import httpx

try:
    from config import settings
except ImportError:
    try:
        from ..config import settings
    except ImportError:
        settings = None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расчет расстояния в километрах между двумя координатами."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bbox_from_point(lat: float, lon: float, radius_km: float) -> Dict[str, float]:
    """Генерация BoundingBox вокруг точки с заданным радиусом."""
    d_lat = radius_km / 111.0
    d_lon = radius_km / (111.0 * math.cos(math.radians(lat)))
    return {
        "TopLeftLatitude": lat + d_lat,
        "TopLeftLongitude": lon - d_lon,
        "RightBottomLatitude": lat - d_lat,
        "RightBottomLongitude": lon + d_lon,
    }


DEFAULT_MO_BBOX = {
    "TopLeftLatitude": 56.95,
    "TopLeftLongitude": 35.10,
    "RightBottomLatitude": 54.20,
    "RightBottomLongitude": 40.30,
}


class AsyncRateLimiter:
    def __init__(self, min_interval: float = 0.5):
        self.min_interval = min_interval
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def wait(self):
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()


class RNISClient:
    def __init__(self, cache_ttl_seconds: Optional[int] = None, request_interval: Optional[float] = None):
        self.base_url = settings.RNIS_BASE_URL if settings else "https://portal.rnis.mosreg.ru/busajax/request"
        self.BASE_URL = self.base_url
        ttl = cache_ttl_seconds if cache_ttl_seconds is not None else (settings.RNIS_CACHE_TTL_SECONDS if settings else 900)
        interval = request_interval if request_interval is not None else (settings.RNIS_REQUEST_INTERVAL if settings else 0.5)

        self.cache_ttl = ttl
        self.limiter = AsyncRateLimiter(min_interval=interval)
        self._catalog_cache: Dict[str, Dict[str, Any]] = {}
        self._catalog_by_plate: Dict[str, Dict[str, Any]] = {}
        self._last_catalog_fetch = 0.0
        self._catalog_lock = asyncio.Lock()

        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://portal.rnis.mosreg.ru",
            "Referer": "https://portal.rnis.mosreg.ru/map/bus",
            "Cookie": "mdd=0",
        }

    async def _post(self, subject: str, payload_dict: Dict[str, Any]) -> Dict[str, Any]:
        await self.limiter.wait()
        url = f"{self.base_url}?{subject}"
        headers = dict(self.headers)
        headers["Subject"] = subject

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload_dict)
            resp.raise_for_status()
            return resp.json()

    async def get_catalog(self, force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
        """Загрузка и кэширование каталога транспортных средств."""
        async with self._catalog_lock:
            now = time.time()
            if not force_refresh and self._catalog_cache and (now - self._last_catalog_fetch < self.cache_ttl):
                return self._catalog_cache

            subject = "com.rnis.vehicles.action.vehicle.list.portal.kiutr"
            body = {"headers": {"meta": {}}, "payload": {}}

            try:
                data = await self._post(subject, body)
                items = data.get("payload", {}).get("items", [])

                by_uuid = {}
                by_plate = {}
                for item in items:
                    v_uuid = item.get("uuid")
                    plate = (item.get("state_number") or "").strip().lower()
                    if v_uuid:
                        by_uuid[v_uuid] = item
                    if plate:
                        by_plate[plate] = item

                self._catalog_cache = by_uuid
                self._catalog_by_plate = by_plate
                self._last_catalog_fetch = now
                return self._catalog_cache
            except Exception as e:
                if self._catalog_cache:
                    return self._catalog_cache
                raise RuntimeError(f"Ошибка загрузки каталога РНИС: {e}")

    def get_meta_by_clean_uuid(self, clean_uuid: str) -> Optional[Dict[str, Any]]:
        if not clean_uuid:
            return None
        target = clean_uuid.strip().lower().replace("-", "")
        for u, meta in self._catalog_cache.items():
            if u.replace("-", "").lower() == target:
                return meta
        return None

    async def get_telematics_count(self) -> int:
        subject = "com.rnis.grpc-proxy.action.telematics.count=null"
        body = {"headers": {"meta": {}}, "payload": {}}
        data = await self._post(subject, body)
        return data.get("payload", {}).get("count", 0)

    async def get_raw_telematics(
        self,
        bbox: Optional[Dict[str, float]] = None,
        last_signal: str = "10000",
        zoom: int = 18,
    ) -> List[Dict[str, Any]]:
        subject = "com.rnis.grpc-proxy.action.telematics.get=null"
        filters = {
            "BoundingBox": bbox or DEFAULT_MO_BBOX,
            "Subsystem": "kiutr",
            "LastSignal": last_signal,
            "WithClustered": False,
            "Zoom": zoom,
        }
        body = {"headers": {"meta": {"filters": filters}}, "payload": {}}
        data = await self._post(subject, body)
        return data.get("payload", {}).get("vehicles", [])

    def _matches_filters(
        self,
        bus: Dict[str, Any],
        query: Optional[str] = None,
        route_number: Optional[str] = None,
        state_number: Optional[str] = None,
        carrier: Optional[str] = None,
        vehicle_mark: Optional[str] = None,
        bnso_code: Optional[str] = None,
        uuid: Optional[str] = None,
        low_floor_only: Optional[bool] = None,
        air_conditioned_only: Optional[bool] = None,
        min_speed: Optional[float] = None,
        max_age_minutes: Optional[float] = None,
    ) -> bool:
        if query:
            tokens = query.strip().lower().split()
            searchable_text = " ".join([
                str(bus.get("state_number") or ""),
                str(bus.get("route_number") or ""),
                str(bus.get("route_name") or ""),
                str(bus.get("carrier_name") or ""),
                str(bus.get("vehicle_mark") or ""),
                str(bus.get("bnso_code") or ""),
                str(bus.get("vehicle_uuid") or ""),
                str(bus.get("carrier_uuid") or ""),
                str(bus.get("order_execution_uuid") or ""),
            ]).lower()

            if not all(token in searchable_text for token in tokens):
                return False

        if route_number and route_number.strip().lower() not in str(bus.get("route_number") or "").lower():
            return False

        if state_number and state_number.strip().lower() not in str(bus.get("state_number") or "").lower():
            return False

        if carrier and carrier.strip().lower() not in str(bus.get("carrier_name") or "").lower():
            return False

        if vehicle_mark and vehicle_mark.strip().lower() not in str(bus.get("vehicle_mark") or "").lower():
            return False

        if bnso_code and bnso_code.strip() not in str(bus.get("bnso_code") or ""):
            return False

        if uuid and uuid.strip().lower() not in str(bus.get("vehicle_uuid") or "").lower():
            return False

        if low_floor_only is True and not bus.get("is_low_floor"):
            return False

        if air_conditioned_only is True and not bus.get("is_air_conditioned"):
            return False

        if min_speed is not None and (bus.get("speed_kmh") or 0) < min_speed:
            return False

        if max_age_minutes is not None:
            age = bus.get("signal_age_minutes")
            if age is not None and age > max_age_minutes:
                return False

        return True

    def matches_filters(self, bus: Dict[str, Any], **kwargs) -> bool:
        return self._matches_filters(bus, **kwargs)

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
        min_speed: Optional[float] = None,
        max_age_minutes: Optional[float] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        radius_km: Optional[float] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if lat is not None and lon is not None and radius_km:
            search_bbox = bbox_from_point(lat, lon, radius_km)
        else:
            search_bbox = DEFAULT_MO_BBOX

        catalog_task = asyncio.create_task(self.get_catalog())
        telematics_task = asyncio.create_task(self.get_raw_telematics(bbox=search_bbox))
        catalog, vehicles = await asyncio.gather(catalog_task, telematics_task)

        now = time.time()
        results = []

        for v in vehicles:
            v_uuid = v.get("VehicleUUID")
            v_plate = (v.get("StateNumber") or "").strip().lower()
            meta = catalog.get(v_uuid) or self._catalog_by_plate.get(v_plate) or {}

            dev_time = v.get("DeviceTime")
            age_min = round((now - dev_time) / 60, 1) if dev_time else None

            bus_info = {
                "state_number": v.get("StateNumber") or meta.get("state_number") or "",
                "route_number": meta.get("route_number") or "Не назначен",
                "route_name": meta.get("route_name") or "",
                "carrier_name": meta.get("carrier_name") or "",
                "carrier_uuid": meta.get("carrier_uuid") or "",
                "vehicle_mark": meta.get("vehicle_mark_name") or "",
                "vehicle_mark_uuid": meta.get("vehicle_mark_uuid") or "",
                "bnso_code": meta.get("bnso_code") or str(v.get("DeviceCode") or ""),
                "reserve_bnso_number": meta.get("reserve_bnso_number") or "",
                "order_execution_uuid": meta.get("order_execution_uuid") or "",
                "latitude": v.get("Latitude"),
                "longitude": v.get("Longitude"),
                "speed_kmh": v.get("Speed", 0),
                "course": v.get("Course", 0),
                "device_time": dev_time,
                "signal_age_minutes": age_min,
                "is_low_floor": bool(meta.get("is_low_floor_level", False)),
                "is_air_conditioned": bool(meta.get("is_air_conditioning_installation", False)),
                "is_electronic_scoreboard": bool(meta.get("is_electronic_scoreboard", False)),
                "is_cashless": bool(meta.get("is_cashless_payment", False)),
                "is_passenger_monitoring": bool(meta.get("is_passenger_monitoring_system", False)),
                "vehicle_uuid": v_uuid or meta.get("uuid") or "",
            }

            if lat is not None and lon is not None and v.get("Latitude") and v.get("Longitude"):
                dist = round(haversine_km(lat, lon, v["Latitude"], v["Longitude"]), 2)
                if radius_km and dist > radius_km:
                    continue
                bus_info["distance_km"] = dist

            if not self._matches_filters(
                bus=bus_info,
                query=query,
                route_number=route_number,
                state_number=state_number,
                carrier=carrier,
                vehicle_mark=vehicle_mark,
                bnso_code=bnso_code,
                uuid=uuid,
                low_floor_only=low_floor_only,
                air_conditioned_only=air_conditioned_only,
                min_speed=min_speed,
                max_age_minutes=max_age_minutes,
            ):
                continue

            results.append(bus_info)

        if lat is not None and lon is not None:
            results.sort(key=lambda x: x.get("distance_km", 999))

        return results[:limit]

    async def get_stop_routes(self, stop_uuid: str) -> Dict[str, Any]:
        """Получение маршрутов и прогнозов прибытия для остановки РНИС МО."""
        subject = "com.rnis.geo.action.stop_point.routes"
        body = {"headers": {"meta": {}}, "payload": {"uuid": str(stop_uuid), "time": None}}

        try:
            data = await self._post(subject, body)
            items = data.get("payload", {}).get("items", [])
        except Exception as e:
            return {"stop_uuid": stop_uuid, "routes_count": 0, "routes": [], "error": str(e), "source": "rnis"}

        routes = []
        for it in items:
            raw_time = it.get("time")
            arrival_sec = int(raw_time) if (raw_time is not None and raw_time > 0) else None
            arrival_min = round(arrival_sec / 60.0, 1) if arrival_sec is not None else None
            stops_rem = it.get("count_to_end") if it.get("count_to_end") is not None else it.get("count")

            routes.append({
                "route_number": str(it.get("number") or ""),
                "route_uuid": it.get("route_uuid") or it.get("uuid") or "",
                "title": it.get("title") or "",
                "from": it.get("from") or "",
                "to": it.get("to") or "",
                "destination": it.get("to") or "",
                "arrival_seconds": arrival_sec,
                "arrival_minutes": arrival_min,
                "stops_remaining": stops_rem,
                "is_forward": bool(it.get("is_forward_direction", True)),
                "stop_point_list": it.get("stop_point_list", []),
            })

        return {
            "stop_uuid": stop_uuid,
            "routes_count": len(routes),
            "routes": routes,
            "source": "rnis",
        }

    async def search_stops_by_bbox(
        self,
        left_top_lat: float,
        left_top_lon: float,
        right_bottom_lat: float,
        right_bottom_lon: float,
        zoom: int = 17,
    ) -> List[Dict[str, Any]]:
        subject = "com.rnis.geo.action.stop_point.list"
        body = {
            "headers": {
                "meta": {
                    "filters": {
                        "withBoundingBox": {
                            "left_top": {"latitude": left_top_lat, "longitude": left_top_lon},
                            "right_bottom": {"latitude": right_bottom_lat, "longitude": right_bottom_lon},
                        },
                        "withZoom": zoom,
                        "withSource": "inventarisation_active",
                    }
                }
            },
            "payload": {},
        }
        try:
            data = await self._post(subject, body)
            return data.get("payload", {}).get("items", [])
        except Exception:
            return []

    async def search_catalog(self, **kwargs) -> List[Dict[str, Any]]:
        await self.get_catalog()
        limit = kwargs.pop("limit", 100)
        results = []
        for item in self._catalog_cache.values():
            bus = {
                "state_number": item.get("state_number") or "",
                "route_number": item.get("route_number") or "Не назначен",
                "route_name": item.get("route_name") or "",
                "carrier_name": item.get("carrier_name") or "",
                "carrier_uuid": item.get("carrier_uuid") or "",
                "vehicle_mark": item.get("vehicle_mark_name") or "",
                "vehicle_mark_uuid": item.get("vehicle_mark_uuid") or "",
                "bnso_code": item.get("bnso_code") or "",
                "reserve_bnso_number": item.get("reserve_bnso_number") or "",
                "order_execution_uuid": item.get("order_execution_uuid") or "",
                "is_low_floor": bool(item.get("is_low_floor_level", False)),
                "is_air_conditioned": bool(item.get("is_air_conditioning_installation", False)),
                "is_electronic_scoreboard": bool(item.get("is_electronic_scoreboard", False)),
                "is_cashless": bool(item.get("is_cashless_payment", False)),
                "is_passenger_monitoring": bool(item.get("is_passenger_monitoring_system", False)),
                "vehicle_uuid": item.get("uuid") or "",
            }
            if self._matches_filters(bus=bus, **kwargs):
                results.append(bus)
            if len(results) >= limit:
                break
        return results

    async def search_routes(self, query: str) -> List[Dict[str, Any]]:
        await self.get_catalog()
        q = query.strip().lower()
        seen = set()
        routes = []

        for item in self._catalog_cache.values():
            r_num = (item.get("route_number") or "").strip()
            r_name = (item.get("route_name") or "").strip()
            key = (r_num, r_name)

            if key not in seen:
                if q in r_num.lower() or q in r_name.lower():
                    seen.add(key)
                    routes.append({
                        "route_number": r_num,
                        "route_name": r_name,
                        "carrier_name": item.get("carrier_name") or "",
                    })
        return sorted(routes, key=lambda x: x["route_number"])
    
    async def get_stops_by_bbox(
        self,
        left_top_lat: float,
        left_top_lon: float,
        right_bottom_lat: float,
        right_bottom_lon: float,
        zoom: int = 17,
    ) -> List[Dict[str, Any]]:
        """Получение списка остановок в BoundingBox через com.rnis.geo.action.stop_point.list."""
        subject = "com.rnis.geo.action.stop_point.list"
        body = {
            "headers": {
                "meta": {
                    "filters": {
                        "withBoundingBox": {
                            "left_top": {"latitude": left_top_lat, "longitude": left_top_lon},
                            "right_bottom": {"latitude": right_bottom_lat, "longitude": right_bottom_lon},
                        },
                        "withZoom": zoom,
                        "withSource": "inventarisation_active",
                    }
                }
            },
            "payload": {},
        }

        try:
            data = await self._post(subject, body)
            items = data.get("payload", {}).get("items", [])
        except Exception:
            return []

        results = []
        for it in items:
            results.append({
                "name": it.get("title") or it.get("title_short") or "",
                "station_name": it.get("title_short") or it.get("title") or "",
                "lat": it.get("latitude"),
                "lon": it.get("longitude"),
                "coordinates": {
                    "lat": it.get("latitude"),
                    "lon": it.get("longitude"),
                    "source": "rnis",
                },
                "uuid": it.get("uuid"),
                "stop_id": it.get("uuid"),
                "internal_id": str(it.get("mgt_id")) if it.get("mgt_id") else None,
                "register_number": it.get("register_number"),
                "routes": [],
                "source": "rnis",
            })
        return results