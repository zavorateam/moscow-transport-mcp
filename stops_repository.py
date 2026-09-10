import json
import logging
import math
import os
import re
from typing import Any, Dict, List, Optional

try:
    from config import settings
except ImportError:
    try:
        from .config import settings
    except ImportError:
        settings = None

logger = logging.getLogger(__name__)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def normalize_str(s: str) -> str:
    return re.sub(r'[«»"\'\.,\(\)]', ' ', str(s or "")).lower().strip()


class StopsRepository:
    def __init__(self, filepath: Optional[str] = None):
        if filepath is not None:
            self.filepath = filepath
        elif settings and getattr(settings, "STOPS_GEOJSON_PATH", None):
            self.filepath = settings.STOPS_GEOJSON_PATH
        else:
            self.filepath = "bus_stops_all.geojson"

        self.stops_by_name: Dict[str, List[Dict[str, Any]]] = {}
        self.stops_by_id: Dict[str, Dict[str, Any]] = {}
        self.stops_by_route: Dict[str, List[Dict[str, Any]]] = {}
        self.spatial: List[Dict[str, Any]] = []
        self._loaded = False
        self.load()

    def load(self):
        target_path = self.filepath
        if not os.path.exists(target_path):
            alt_path = "bus_stops_all.json"
            if os.path.exists(alt_path):
                target_path = alt_path
            else:
                return

        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            raw_items = []
            if isinstance(data, dict) and data.get("type") == "FeatureCollection":
                for feat in data.get("features", []):
                    props = feat.get("properties", {})
                    # Распаковываем properties.attributes data.mos.ru
                    if "attributes" in props and isinstance(props["attributes"], dict):
                        props = {**props, **props["attributes"]}
                    coords = feat.get("geometry", {}).get("coordinates", [None, None])
                    f_id = feat.get("id") or props.get("uuid")
                    raw_items.append((props, coords, f_id))
            elif isinstance(data, list):
                for item in data:
                    props = item.get("Cells") or item.get("attributes") or item
                    coords = (
                        item.get("geoData", {}).get("coordinates")
                        or props.get("geoData", {}).get("coordinates", [None, None])
                    )
                    raw_items.append((props, coords, item.get("uuid") or props.get("uuid")))

            for props, coords, feat_uuid in raw_items:
                lon = float(coords[0]) if (coords and coords[0] is not None) else float(props.get("Longitude_WGS84", 0) or 0)
                lat = float(coords[1]) if (coords and len(coords) > 1 and coords[1] is not None) else float(props.get("Latitude_WGS84", 0) or 0)
                if not lat or not lon:
                    continue

                routes_str = props.get("RouteNumbers") or ""
                routes = []
                for r in str(routes_str).replace(",", ";").split(";"):
                    r = r.strip()
                    if not r:
                        continue
                    routes.append(r)
                    # Если маршрут с префиксом "А" (А811 -> 811, Ас951 -> с951), добавляем очищенную версию
                    if len(r) > 1 and r[0].lower() == 'а':
                        routes.append(r[1:])

                internal_id = str(props.get("ID") or "")
                global_id = props.get("global_id")
                stop_uuid = str(feat_uuid or props.get("uuid") or props.get("global_id") or "")

                st_name = (props.get("StationName") or "").strip()
                tech_name = (props.get("Name") or "").strip()
                main_name = st_name or tech_name or ""

                stop_entry = {
                    "name": main_name,
                    "station_name": st_name or main_name,
                    "tech_name": tech_name,
                    "lat": lat,
                    "lon": lon,
                    "coordinates": {
                        "lat": lat,
                        "lon": lon,
                        "source": "bus_stops_all.geojson",
                    },
                    "uuid": stop_uuid if (stop_uuid and len(stop_uuid) == 36) else None,
                    "stop_id": internal_id or str(global_id or ""),
                    "internal_id": internal_id or None,
                    "routes": list(dict.fromkeys(routes)),
                    "global_id": global_id,
                    "direction": props.get("Direction") or "",
                    "extra": {
                        "adm_area": props.get("AdmArea") or "",
                        "district": props.get("District") or "",
                        "pavilion": str(props.get("Pavilion", "")).lower() in ("true", "да", "1"),
                    },
                }

                # Индексируем StationName ("Метромост") и техническое имя
                indexed_names = set()
                for n in (main_name, st_name, tech_name):
                    nk = n.strip().lower()
                    if nk and nk not in indexed_names:
                        self.stops_by_name.setdefault(nk, []).append(stop_entry)
                        indexed_names.add(nk)

                if internal_id:
                    self.stops_by_id[internal_id] = stop_entry
                if global_id:
                    self.stops_by_id[str(global_id)] = stop_entry
                if stop_entry["uuid"]:
                    self.stops_by_id[stop_entry["uuid"]] = stop_entry

                for r in routes:
                    self.stops_by_route.setdefault(r.lower(), []).append(stop_entry)

                self.spatial.append(stop_entry)

            self._loaded = True
        except Exception as e:
            logger.error(f"Ошибка чтения {target_path}: {e}")

    def find_stop(self, name_or_id: str, route_number: Optional[str] = None) -> Optional[Dict[str, Any]]:
        key = str(name_or_id).strip()
        if not key:
            return None
        if key in self.stops_by_id:
            return self.stops_by_id[key]

        lower_key = key.lower()
        r_clean = route_number.strip().lower() if route_number else None

        # Ищем всех кандидатов с таким именем (все направления/павильоны)
        candidates = []
        if lower_key in self.stops_by_name:
            candidates = self.stops_by_name[lower_key]
        else:
            norm_key = normalize_str(lower_key)
            for name, stops in self.stops_by_name.items():
                if norm_key in normalize_str(name):
                    candidates.extend(stops)
                    break

        if not candidates:
            return None

        # Умный выбор платформы: если указан маршрут (например 824),
        # выбираем именно тот павильон, через который этот маршрут действительно проходит!
        if r_clean:
            for c in candidates:
                if any(r_clean == r.lower() or r_clean in r.lower() for r in c.get("routes", [])):
                    return c

        return candidates[0]

    def get_stops_for_route(self, route_number: str) -> List[Dict[str, Any]]:
        return self.stops_by_route.get(route_number.strip().lower(), [])

    def search_stops(
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
        results = []
        q_tokens = [normalize_str(t) for t in query.split()] if query else []
        r_num = route_number.strip().lower() if route_number else None
        adm = adm_area.strip().lower() if adm_area else None
        distr = district.strip().lower() if district else None

        for stop in self.spatial:
            s_lat = stop["lat"]
            s_lon = stop["lon"]

            if bbox and len(bbox) == 4:
                min_lat, min_lon, max_lat, max_lon = bbox
                if not (min_lat <= s_lat <= max_lat and min_lon <= s_lon <= max_lon):
                    continue

            d_km = None
            if lat is not None and lon is not None:
                d_km = round(haversine_km(lat, lon, s_lat, s_lon), 3)
                if radius_km is not None and d_km > radius_km:
                    continue

            if q_tokens:
                name_text = normalize_str(f"{stop['name']} {stop.get('station_name', '')} {stop.get('tech_name', '')} {stop.get('direction', '')}")
                if not all(t in name_text for t in q_tokens):
                    continue

            if r_num:
                if not any(r_num == r.lower() or r_num in r.lower() for r in stop["routes"]):
                    continue

            if has_pavilion_only is True and not stop.get("extra", {}).get("pavilion"):
                continue

            if adm and adm not in stop.get("extra", {}).get("adm_area", "").lower():
                continue

            if distr and distr not in stop.get("extra", {}).get("district", "").lower():
                continue

            item = dict(stop)
            if d_km is not None:
                item["distance_km"] = d_km
            results.append(item)

        if lat is not None and lon is not None:
            results.sort(key=lambda x: x.get("distance_km", 999))

        return results[:limit]