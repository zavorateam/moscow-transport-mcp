import asyncio
import math
import os
import re
import time
import urllib.parse
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
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def extract_coords_safely(geom: Dict[str, Any]):
    raw = geom.get("coordinates", [])
    if not raw:
        return None, None, 0.0, 0.0

    if isinstance(raw[0], (int, float)):
        return float(raw[1]), float(raw[0]), 0.0, 0.0

    if isinstance(raw[0], (list, tuple)):
        first = raw[0]
        if isinstance(first[0], (list, tuple)):
            first = first[0]
        if len(first) >= 2:
            lon1, lat1 = float(first[0]), float(first[1])
            course = 0.0
            if len(raw) >= 2 and isinstance(raw[1], (list, tuple)) and len(raw[1]) >= 2:
                lon2, lat2 = float(raw[1][0]), float(raw[1][1])
                dlambda = math.radians(lon2 - lon1)
                phi1, phi2 = math.radians(lat1), math.radians(lat2)
                y = math.sin(dlambda) * math.cos(phi2)
                x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
                course = round((math.degrees(math.atan2(y, x)) + 360) % 360, 1)
            return lat1, lon1, 0.0, course

    return None, None, 0.0, 0.0


class YandexTransportClient:
    VEHICLES_URL = "https://yandex.ru/maps/api/masstransit/getVehiclesInfoWithRegion"
    STOP_URL = "https://yandex.ru/maps/api/masstransit/getStopInfo"
    SEARCH_URL = "https://yandex.ru/maps/api/search"

    DEFAULT_CSRF = "6c4fbb2d14d555fb31c4d420225cb2120dc8fa4f:1789035145"
    DEFAULT_SESSION_ID = "1789035145230215-15377882495539827877-balancer-l7leveler-kubr-yp-klg-220-BAL"
    DEFAULT_S_VEHICLES = "1226992667"
    DEFAULT_S_STOPS = "3155859244"

    def __init__(self, cookie: Optional[str] = None):
        self.csrf_token = os.getenv("YANDEX_CSRF_TOKEN") or (settings.YANDEX_CSRF_TOKEN if settings else self.DEFAULT_CSRF)
        self.session_id = os.getenv("YANDEX_SESSION_ID") or (settings.YANDEX_SESSION_ID if settings else self.DEFAULT_SESSION_ID)
        self.s_vehicles = os.getenv("YANDEX_S_VEHICLES") or (settings.YANDEX_S_VEHICLES if settings else self.DEFAULT_S_VEHICLES)
        self.s_stops = os.getenv("YANDEX_S_STOPS") or (settings.YANDEX_S_STOPS if settings else self.DEFAULT_S_STOPS)

        cookie_val = os.getenv("YANDEX_COOKIE") or (settings.YANDEX_COOKIE if settings else None) or cookie or ""
        self.cookie = cookie_val

        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0",
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://yandex.ru/maps/213/moscow/transport/",
            "Connection": "keep-alive",
        }
        if self.cookie:
            self.headers["Cookie"] = self.cookie

    async def geocode_stop(self, stop_name: str, lat: Optional[float] = None, lon: Optional[float] = None) -> Optional[Dict[str, Any]]:
        stops = await self.search_stops(query=stop_name, lat=lat, lon=lon)
        return stops[0] if stops else None

    async def search_stops(
        self,
        query: str,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        clean_q = query.strip()
        search_text = clean_q if clean_q.lower().startswith("остановка") else f"остановка {clean_q}"

        params = {
            "ajax": 1,
            "csrfToken": self.csrf_token,
            "lang": "ru_RU",
            "locale": "ru_RU",
            "snippets": "masstransit/2.x",
            "text": search_text,
            "s": self.s_stops,
            "sessionId": self.session_id,
        }
        if lat is not None and lon is not None:
            params["ll"] = f"{lon},{lat}"
            params["spn"] = "0.08,0.08"

        results = []
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(self.SEARCH_URL, params=params, headers=self.headers)
                if resp.status_code == 200:
                    items = resp.json().get("data", {}).get("items", [])
                    for item in items:
                        uri = item.get("uri", "")
                        stop_id_match = re.search(r"id=([a-zA-Z0-9_-]+)", uri)
                        stop_id = stop_id_match.group(1) if stop_id_match else str(item.get("id"))
                        c = item.get("coordinates", [None, None])
                        results.append({
                            "id": stop_id,
                            "name": item.get("title") or item.get("name"),
                            "latitude": float(c[1]) if len(c) > 1 and c[1] is not None else None,
                            "longitude": float(c[0]) if len(c) > 0 and c[0] is not None else None,
                        })
        except Exception:
            pass

        return results

    async def get_vehicles(
        self,
        lat: float,
        lon: float,
        spn_lon: float = 0.04,
        spn_lat: float = 0.02,
    ) -> List[Dict[str, Any]]:
        params = {
            "ajax": 1,
            "csrfToken": self.csrf_token,
            "lang": "ru",
            "locale": "ru_RU",
            "ll": f"{lon},{lat}",
            "spn": f"{spn_lon},{spn_lat}",
            "s": self.s_vehicles,
            "sessionId": self.session_id,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(self.VEHICLES_URL, params=params, headers=self.headers)
            resp.raise_for_status()
            data = resp.json()

        vehicles = []
        now = time.time()
        for v in data.get("data", {}).get("vehicles", []):
            v_meta = v.get("properties", {}).get("VehicleMetaData", {})
            trans = v_meta.get("Transport", {})
            features = v.get("features", [])

            cur_lat, cur_lon, speed_kmh, course = None, None, 0.0, 0.0
            device_time = None

            if features:
                geom = features[0].get("geometry", {})
                cur_lat, cur_lon, _, course = extract_coords_safely(geom)
                t_meta = features[0].get("properties", {}).get("TrajectorySegmentMetaData", {})
                device_time = t_meta.get("time")
                duration = t_meta.get("duration", 0)
                coords = geom.get("coordinates", [])
                if isinstance(coords, list) and len(coords) >= 2 and duration > 0 and isinstance(coords[0], (list, tuple)):
                    d_km = haversine_km(coords[0][1], coords[0][0], coords[1][1], coords[1][0])
                    speed_kmh = round(d_km / (duration / 3600.0), 1)

            raw_id = trans.get("id", "")
            decoded_id = urllib.parse.unquote(raw_id)
            clean_uuid = decoded_id.split("|")[-1].replace("-", "").lower() if "|" in decoded_id else ""
            age_min = round((now - device_time) / 60, 1) if device_time else None

            vehicles.append({
                "source": "yandex",
                "coordinates_source": "yandex",
                "yandex_id": decoded_id,
                "clean_uuid": clean_uuid,
                "route_number": trans.get("name", "Неизвестно"),
                "carrier_name": "Мосгортранс" if "moscow" in decoded_id.lower() else "Перевозчик МО",
                "latitude": cur_lat,
                "longitude": cur_lon,
                "speed_kmh": speed_kmh,
                "course": course,
                "device_time": device_time,
                "signal_age_minutes": age_min,
                "transport_type": trans.get("type", "bus"),
            })

        return vehicles

    async def get_stop_info(self, stop_id: str) -> Dict[str, Any]:
        params = {
            "ajax": 1,
            "csrfToken": self.csrf_token,
            "id": stop_id,
            "lang": "ru",
            "locale": "ru_RU",
            "mode": "prognosis",
            "results": 20,
            "s": self.s_stops,
            "sessionId": self.session_id,
            "uri": f"ymapsbm1://transit/stop?id={stop_id}",
            "timeDependent[type]": "departure",
            "timeDependent[time]": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(self.STOP_URL, params=params, headers=self.headers)
            resp.raise_for_status()
            return resp.json()

    async def get_stop_routes(self, stop_id: str) -> Dict[str, Any]:
        """Нормализованные маршруты и расписания остановки из Яндекс.Карт."""
        try:
            info = await self.get_stop_info(stop_id)
        except Exception as e:
            return {"stop_id": stop_id, "routes_count": 0, "routes": [], "error": str(e), "source": "yandex"}

        routes = []
        transports = info.get("data", {}).get("transports", [])
        for tr in transports:
            name = tr.get("name", "")
            threads = tr.get("threads", [])
            for th in threads:
                sched = th.get("BriefSchedule", {})
                dep_time = sched.get("departureTime")
                events = sched.get("Events", [])
                routes.append({
                    "route_number": name,
                    "departure_time": dep_time,
                    "events": events,
                    "source": "yandex",
                })

        return {
            "stop_id": stop_id,
            "routes_count": len(routes),
            "routes": routes,
            "source": "yandex",
        }