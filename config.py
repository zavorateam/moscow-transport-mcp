import os
from pathlib import Path
from pydantic import BaseModel, Field

# 1. Автоматическая подгрузка .env (через python-dotenv или встроенный парсер)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

env_path = Path(".env")
if env_path.exists():
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v


# 2. Безопасные конвертеры (не падают на пустых строках)
def get_str(key: str, default: str) -> str:
    val = os.getenv(key)
    return val.strip() if val and val.strip() else default


def get_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if val and val.strip():
        try:
            return int(val.strip())
        except ValueError:
            return default
    return default


def get_float(key: str, default: float) -> float:
    val = os.getenv(key)
    if val and val.strip():
        try:
            return float(val.strip())
        except ValueError:
            return default
    return default


# 3. Конфигурация приложения
class Settings(BaseModel):
    # Сервер
    HOST: str = Field(default_factory=lambda: get_str("HOST", "0.0.0.0"))
    PORT: int = Field(default_factory=lambda: get_int("PORT", 8000))
    LOG_LEVEL: str = Field(default_factory=lambda: get_str("LOG_LEVEL", "INFO"))

    # Пути
    STOPS_GEOJSON_PATH: str = Field(default_factory=lambda: get_str("STOPS_GEOJSON_PATH", "bus_stops_all.geojson"))

    # РНИС МО
    RNIS_BASE_URL: str = Field(default_factory=lambda: get_str("RNIS_BASE_URL", "https://portal.rnis.mosreg.ru/busajax/request"))
    RNIS_CACHE_TTL_SECONDS: int = Field(default_factory=lambda: get_int("RNIS_CACHE_TTL_SECONDS", 900))
    RNIS_REQUEST_INTERVAL: float = Field(default_factory=lambda: get_float("RNIS_REQUEST_INTERVAL", 0.5))

    # МосТранспорт API
    MOSCOW_STOP_V2_URL: str = Field(default_factory=lambda: get_str("MOSCOW_STOP_V2_URL", "https://api.moscowapp.mos.ru/v8.2/stop_v2"))
    MOSCOW_QR_STOP_URL: str = Field(default_factory=lambda: get_str("MOSCOW_QR_STOP_URL", "https://api.moscowapp.mos.ru/v8.2/qr-stop"))

    # Яндекс.Транспорт API
    YANDEX_CSRF_TOKEN: str = Field(default_factory=lambda: get_str("YANDEX_CSRF_TOKEN", "6c4fbb2d14d555fb31c4d420225cb2120dc8fa4f:1789035145"))
    YANDEX_SESSION_ID: str = Field(default_factory=lambda: get_str("YANDEX_SESSION_ID", "1789035145230215-15377882495539827877-balancer-l7leveler-kubr-yp-klg-220-BAL"))
    YANDEX_S_VEHICLES: str = Field(default_factory=lambda: get_str("YANDEX_S_VEHICLES", "1226992667"))
    YANDEX_S_STOPS: str = Field(default_factory=lambda: get_str("YANDEX_S_STOPS", "3155859244"))
    YANDEX_COOKIE: str = Field(default_factory=lambda: get_str("YANDEX_COOKIE", ""))

    # Дефолты алгоритмов оркестратора
    DEFAULT_LAT: float = Field(default_factory=lambda: get_float("DEFAULT_LAT", 55.7558))
    DEFAULT_LON: float = Field(default_factory=lambda: get_float("DEFAULT_LON", 37.6173))
    DEFAULT_SEARCH_RADIUS_KM: float = Field(default_factory=lambda: get_float("DEFAULT_SEARCH_RADIUS_KM", 6.0))
    DEFAULT_AVERAGE_SPEED_KMH: float = Field(default_factory=lambda: get_float("DEFAULT_AVERAGE_SPEED_KMH", 18.0))
    RECONCILIATION_THRESHOLD_SECONDS: int = Field(default_factory=lambda: get_int("RECONCILIATION_THRESHOLD_SECONDS", 180))


settings = Settings()