import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional
import httpx

try:
    from config import settings
except ImportError:
    try:
        from ..config import settings
    except ImportError:
        settings = None


class MoscowTransportClient:
    def __init__(self):
        self.stop_v2_url = settings.MOSCOW_STOP_V2_URL if settings else "https://api.moscowapp.mos.ru/v8.2/stop_v2"
        self.qr_stop_url = settings.MOSCOW_QR_STOP_URL if settings else "https://api.moscowapp.mos.ru/v8.2/qr-stop"
        self.STOP_V2_URL = self.stop_v2_url
        self.QR_STOP_URL = self.qr_stop_url

        self.headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:154.0) Gecko/20100101 Firefox/154.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
            "Origin": "https://moscowtransport.app",
            "Referer": "https://moscowtransport.app/",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        }

    async def get_stop_v2(self, stop_uuid: str) -> Dict[str, Any]:
        url = f"{self.stop_v2_url}/{stop_uuid}"
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.get(url, headers=self.headers)
            resp.raise_for_status()
            return self._parse_response(resp)

    async def get_stop_qr_forecast(
        self, internal_id: str, lon: float, lat: float
    ) -> Dict[str, Any]:
        url = f"{self.qr_stop_url}/{internal_id}/stop"
        params = {"p": f"{lon},{lat}"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params, headers=self.headers)
            resp.raise_for_status()
            return self._parse_response(resp)

    async def get_stop_routes(
        self, stop_uuid: str, internal_id: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None
    ) -> Dict[str, Any]:
        """Унифицированное получение маршрутов для Московского транспорта."""
        res = None
        if stop_uuid and len(str(stop_uuid)) == 36:
            try:
                res = await self.get_stop_v2(str(stop_uuid))
            except Exception:
                pass

        if not res and internal_id and lat is not None and lon is not None:
            try:
                res = await self.get_stop_qr_forecast(str(internal_id), lon, lat)
            except Exception:
                pass

        routes = []
        if res and "routes" in res:
            for r in res["routes"]:
                f_list = r.get("forecasts", [])
                closest_f = f_list[0] if f_list else {}
                routes.append({
                    "route_number": r.get("route_number"),
                    "destination": r.get("destination"),
                    "arrival_seconds": closest_f.get("time_seconds"),
                    "arrival_minutes": closest_f.get("time_minutes"),
                    "is_realtime": closest_f.get("by_telemetry", False),
                    "is_electrobus": r.get("is_electrobus", False),
                    "vehicle_id": closest_f.get("vehicle_tm_id"),
                    "forecasts": f_list,
                })

        return {
            "stop_id": stop_uuid or internal_id,
            "stop_name": res.get("name") if res else None,
            "coordinates": {"lat": res.get("lat"), "lon": res.get("lon")} if res else None,
            "routes_count": len(routes),
            "routes": routes,
            "source": "moscow_transport",
        }

    def _parse_response(self, resp: httpx.Response) -> Dict[str, Any]:
        try:
            data = resp.json()
            return self._parse_json_v2(data)
        except Exception:
            return self._parse_xml(resp.text)

    def _parse_json_v2(self, data: Dict[str, Any]) -> Dict[str, Any]:
        routes: List[Dict[str, Any]] = []
        for rp in data.get("routePath", []) or []:
            r_num = str(rp.get("number") or rp.get("routeNumber") or "")
            dest = rp.get("lastStopName") or ""
            is_electro = bool(rp.get("electrobus", False))
            color = rp.get("color")

            forecasts: List[Dict[str, Any]] = []
            for ef in rp.get("externalForecast", []) or []:
                sec = int(ef.get("time", 0) or 0)
                forecasts.append(
                    {
                        "time_seconds": sec,
                        "time_minutes": round(sec / 60, 1),
                        "by_telemetry": ef.get("byTelemetry") == 1,
                        "vehicle_tm_id": str(ef.get("tmId") or ""),
                        "route_path_id": ef.get("routePathId"),
                    }
                )

            routes.append(
                {
                    "route_number": r_num,
                    "destination": dest,
                    "is_electrobus": is_electro,
                    "color": color,
                    "forecasts": forecasts,
                }
            )

        return {
            "uuid": data.get("id"),
            "internal_id": str(data.get("internal_id") or ""),
            "name": data.get("name"),
            "lat": data.get("lat"),
            "lon": data.get("lon"),
            "routes": routes,
            "raw_format": "json_v2",
        }

    def _parse_xml(self, xml_text: str) -> Dict[str, Any]:
        root = ET.fromstring(xml_text)
        stop = root.find("StopDetail")
        if stop is None:
            return {"error": "StopDetail not found", "routes": []}

        routes: List[Dict[str, Any]] = []
        for rf in stop.findall("routePath/StopRouteForecast"):
            num = rf.findtext("number")
            last_stop = rf.findtext("lastStopName")
            is_electrobus = rf.findtext("electrobus") == "true"
            color = rf.findtext("color")

            forecasts: List[Dict[str, Any]] = []
            for tf in rf.findall("externalForecast/TimeForecast"):
                sec_txt = tf.findtext("time", "0") or "0"
                sec = int(sec_txt)
                telemetry = tf.findtext("byTelemetry") == "1"
                forecasts.append(
                    {
                        "time_seconds": sec,
                        "time_minutes": round(sec / 60, 1),
                        "by_telemetry": telemetry,
                        "vehicle_tm_id": tf.findtext("tmId"),
                    }
                )

            routes.append(
                {
                    "route_number": num,
                    "destination": last_stop,
                    "is_electrobus": is_electrobus,
                    "color": color,
                    "forecasts": forecasts,
                }
            )

        lat_txt = stop.findtext("lat")
        lon_txt = stop.findtext("lon")
        return {
            "uuid": stop.findtext("id"),
            "internal_id": stop.findtext("internal_id") or "",
            "name": stop.findtext("name"),
            "lat": float(lat_txt) if lat_txt else None,
            "lon": float(lon_txt) if lon_txt else None,
            "routes": routes,
            "raw_format": "xml",
        }