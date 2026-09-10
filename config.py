import os
from pydantic import BaseModel, Field


class Settings(BaseModel):
    # Сервер
    HOST: str = Field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    PORT: int = Field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    LOG_LEVEL: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "DEBUG"))

    # Пути
    STOPS_GEOJSON_PATH: str = Field(default_factory=lambda: os.getenv("STOPS_GEOJSON_PATH", "bus_stops_all.geojson"))

    # РНИС МО
    RNIS_BASE_URL: str = Field(default_factory=lambda: os.getenv("RNIS_BASE_URL", "https://portal.rnis.mosreg.ru/busajax/request"))
    RNIS_CACHE_TTL_SECONDS: int = Field(default_factory=lambda: int(os.getenv("RNIS_CACHE_TTL_SECONDS", "900")))
    RNIS_REQUEST_INTERVAL: float = Field(default_factory=lambda: float(os.getenv("RNIS_REQUEST_INTERVAL", "0.5")))

    # МосТранспорт API
    MOSCOW_STOP_V2_URL: str = Field(default_factory=lambda: os.getenv("MOSCOW_STOP_V2_URL", "https://api.moscowapp.mos.ru/v8.2/stop_v2"))
    MOSCOW_QR_STOP_URL: str = Field(default_factory=lambda: os.getenv("MOSCOW_QR_STOP_URL", "https://api.moscowapp.mos.ru/v8.2/qr-stop"))

    # Яндекс.Транспорт API
    YANDEX_CSRF_TOKEN: str = Field(default_factory=lambda: os.getenv("YANDEX_CSRF_TOKEN", ""))
    YANDEX_SESSION_ID: str = Field(default_factory=lambda: os.getenv("YANDEX_SESSION_ID", ""))
    YANDEX_S_VEHICLES: str = Field(default_factory=lambda: os.getenv("YANDEX_S_VEHICLES", ""))
    YANDEX_S_STOPS: str = Field(default_factory=lambda: os.getenv("YANDEX_S_STOPS", ""))
    YANDEX_COOKIE: str = Field(default_factory=lambda: os.getenv("YANDEX_COOKIE", ""))

    # Дефолты алгоритмов оркестратора
    DEFAULT_LAT: float = Field(default_factory=lambda: float(os.getenv("DEFAULT_LAT", "")))
    DEFAULT_LON: float = Field(default_factory=lambda: float(os.getenv("DEFAULT_LON", "")))
    DEFAULT_SEARCH_RADIUS_KM: float = Field(default_factory=lambda: float(os.getenv("DEFAULT_SEARCH_RADIUS_KM", "6.0")))
    DEFAULT_AVERAGE_SPEED_KMH: float = Field(default_factory=lambda: float(os.getenv("DEFAULT_AVERAGE_SPEED_KMH", "18.0")))
    RECONCILIATION_THRESHOLD_SECONDS: int = Field(default_factory=lambda: int(os.getenv("RECONCILIATION_THRESHOLD_SECONDS", "180")))


settings = Settings()