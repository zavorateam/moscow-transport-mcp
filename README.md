# moscow-transport-mcp

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![Model Context Protocol](https://img.shields.io/badge/MCP-Protocol-purple)](https://modelcontextprotocol.io)
[![Tests](https://img.shields.io/badge/Tests-156%20passed-brightgreen)](#тестирование)
[![Integration Tests](https://img.shields.io/badge/E2E%20Live-36%2F36%20passed-success)](#сквозное-тестирование-агента-agent_testpy)

**Moscow Transport MCP** — интеллектуальный гибридный шлюз для AI-агентов (MCP) и микросервисов (REST), агрегирующий данные пассажирского транспорта **Москвы и Московской области** в реальном времени.

Шлюз непрерывно склеивает данные из трёх независимых систем, устраняет расхождения, выбирает нужные павильоны при многостороннем движении и предоставляет агентам детерминированные инструменты для планирования поездок, мониторинга автопарка и информирования пассажиров.

---

## 🧭 Архитектура и источники данных

```text
                     ┌────────────────────────────────────────────────────────┐
                     │            AI-АГЕНТ (Claude / Cursor / LLM)            │
                     └───────────────────────────┬────────────────────────────┘
                                                 │ (MCP / REST)
                                                 ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│                                   Moscow Transport MCP                                       │
│                                                                                              │
│   ┌───────────────────────────┬───────────────────────────┬──────────────────────────────┐   │
│   │   1. Discovery & Search   │   2. Fleet & Amenities    │   3. Fusion & Dispatch       │   │
│   │   (Поиск остановок, BBox, │   (GPS, скорость, кондей, │   (Сверка табло, расчет ETA, │   │
│   │    умный выбор платформы) │    низкий пол, госномер)  │    live vs static график)    │   │
│   └─────────────┬─────────────┴─────────────┬─────────────┴──────────────┬───────────────┘   │
└─────────────────┼───────────────────────────┼────────────────────────────┼───────────────────┘
                  │                           │                            │
                  ▼                           ▼                            ▼
        ┌────────────────────┐       ┌───────────────────┐        ┌───────────────────┐
        │Московский транспорт│       │  РНИС Мособласть  │        │  Яндекс.Транспорт │
        │ (ГУП Мосгортранс)  │       │  (Балашиха и МО)  │        │                   │
        └────────────────────┘       └───────────────────┘        └───────────────────┘
```

1. **Московский транспорт (ГУП «Мосгортранс» / data.mos.ru):**
   - Локальный реестр `bus_stops_all.geojson` (12 500+ остановок Москвы с распаковкой `properties.attributes`).
   - Официальный API `stop_v2` и `qr-stop` (секундные прогнозы прибытия по датчикам телеметрии).
2. **РНИС Московской области (Минтранс МО):**
   - Прямой доступ к телеметрии бортов (3 500+ онлайн-автобусов в реальном времени).
   - Поиск остановок по географическому BoundingBox (`stop_point.list`).
   - Официальные табло остановок МО (`stop_point.routes`) с оставшимися остановками и ETA.
   - Паспортные характеристики бортов: наличие кондиционера, низкий пол/пандус, электронные табло, валидаторы.
3. **Яндекс.Транспорт (Masstransit API):**
   - Живой трекинг физических бортов, расчет курса (азимута), скорости и координат.
   - Независимый геокодинг и топология маршрутов.

---

## ⚡ Ключевые возможности

- **Интроспекция для LLM (`get_filter_options`):** Агент может заранее запросить реестр перевозчиков, марок ТС и доступных опций, исключая галлюцинации в фильтрах.
- **Умный выбор павильона (Platform Disambiguation):** Если у остановки несколько платформ в разные стороны (например, «Метромост» в центр и от центра), шлюз автоматически выберет павильон, через который проходит запрошенный маршрут.
- **Бесшовное объединение Москвы и Подмосковья:** Если остановки нет в московском GeoJSON, шлюз на лету находит её в РНИС МО (по UUID или BBox).
- **Сверка прогнозов (Reconciliation):** Сравнение официального времени прибытия оператора с фактическим положением борта на карте с приоритизацией достоверного источника.
- **Non-destructive Data Policy:** Никакие данные не отбрасываются. Если у борта отсутствует госномер или статус кондиционера, возвращается `null`, а живые координаты и скорость сохраняются.

---

## 🛠️ Набор инструментов агента (Tooling / API)

| Инструмент | REST Эндпоинт | Назначение |
| :--- | :--- | :--- |
| `get_filter_options` | `GET /filter-options` | Автокомплит и списки доступных перевозчиков, марок, удобств. |
| `search_stops` | `POST /stops`, `GET /stops` | Поиск остановок по названию, маршруту, координатам, BBox, павильону. |
| `get_stop_board` | `POST /schedule`, `GET /schedule` | Живое табло остановки (все прибывающие рейсы, прогноз, `is_realtime`). |
| `get_bus_eta` | `POST /eta`, `GET /eta` | Точечный ETA конкретного автобуса со сверкой источников. |
| `search_online_buses` | `POST /buses`, `GET /buses` | Поиск бортов на карте (GPS, скорость, кондей, низкий пол, радиус). |
| `get_route_details` | `GET /routes/{route}` | Трасса маршрута: цепочка остановок и активные автобусы на нитке. |
| `get_vehicle_card` | `GET /vehicle` | Досье на конкретный борт по госномеру или UUID. |
| `get_system_status` | `GET /count` | Мониторинг онлайна телеметрии и загруженных справочников. |

---

## Быстрый старт

### 1. Клонирование и установка зависимостей

```bash
git clone <repo-url>
cd transport-mcp

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements.txt
```

> **Требования к окружению:** Python 3.10+, `fastapi`, `uvicorn`, `httpx`, `pydantic`, `pytest`, `pytest-asyncio`, `mcp` (опционально для MCP-сервера).

### 2. Конфигурация окружения (`.env`)

Создайте файл `.env` в корне проекта (или скопируйте `.env.example`):

```ini
# Сервер
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=DEBUG

# Путь к московскому реестру остановок
STOPS_GEOJSON_PATH=bus_stops_all.geojson

# РНИС МО (Московская область)
RNIS_BASE_URL=https://portal.rnis.mosreg.ru/busajax/request
RNIS_CACHE_TTL_SECONDS=900
RNIS_REQUEST_INTERVAL=0.5

# Московский транспорт
MOSCOW_STOP_V2_URL=https://api.moscowapp.mos.ru/v8.2/stop_v2
MOSCOW_QR_STOP_URL=https://api.moscowapp.mos.ru/v8.2/qr-stop

# Яндекс.Транспорт (опционально)
YANDEX_CSRF_TOKEN=6c4fbb2d14d555fb31c4d420225cb2120dc8fa4f:1789035145
YANDEX_SESSION_ID=1789035145230215-15377882495539827877-balancer-l7leveler-kubr-yp-klg-220-BAL
YANDEX_S_VEHICLES=1226992667
YANDEX_S_STOPS=3155859244
YANDEX_COOKIE=

# Параметры алгоритмов оркестратора
DEFAULT_LAT=55.2518
DEFAULT_LON=37.7173
DEFAULT_SEARCH_RADIUS_KM=6.0
DEFAULT_AVERAGE_SPEED_KMH=18.0
RECONCILIATION_THRESHOLD_SECONDS=180
```

### 3. Запуск сервера

#### Вариант А: REST API (для тестирования через curl / веб)
```bash
python server.py --http
```
Документация Swagger/OpenAPI доступна по адресу: [http://localhost:8000/docs](http://localhost:8000/docs)

#### Вариант Б: MCP-сервер (режим stdio для AI-агентов)
```bash
python server.py
```

---

## 🤖 Подключение к AI-агентам (MCP Setup)

### Claude Desktop
Добавьте блок в файл конфигурации `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "transit-agent": {
      "command": "/путь/к/проекту/.venv/bin/python",
      "args": ["/путь/к/проекту/server.py"],
      "env": {
        "PYTHONPATH": "/путь/к/проекту"
      }
    }
  }
}
```

### Cursor / Windsurf
В настройках `Features` → `MCP Servers` добавьте сервер:
- **Type:** `command`
- **Command:** `python server.py` (с указанием виртуального окружения).

---

## Примеры запросов (curl)

### 1. Интроспекция фильтров
```bash
# Получить список перевозчиков, содержащих слово "авто"
curl -s -G 'http://localhost:8000/filter-options' \
  --data-urlencode 'category=carriers' \
  --data-urlencode 'query=авто' | jq .
```

### 2. Поиск остановок
```bash
# Москва: найти павильоны «Метромост»
curl -s -X POST 'http://localhost:8000/stops' \
  -H 'Content-Type: application/json' \
  -d '{"query": "Метромост"}' | jq .

# Область: радиусный поиск остановок в МО через РНИС BBox
curl -s -X POST 'http://localhost:8000/stops' \
  -H 'Content-Type: application/json' \
  -d '{"lat": 55.7971, "lon": 37.9398, "radius_km": 2.0}' | jq .
```

### 3. Табло остановки
```bash
# Живое табло остановки «пл. Славы» в Балашихе
curl -s 'http://localhost:8000/schedule?stop_id=a6466a64-eb47-11e7-90c9-136fdbe30654' | jq .
```

### 4. Точный ETA автобуса
```bash
# Запрос ETA для маршрута 22к в Балашихе с привязкой ближайшего борта
curl -s -X POST 'http://localhost:8000/eta' \
  -H 'Content-Type: application/json' \
  -d '{"route": "22к", "stop_id": "a6466a64-eb47-11e7-90c9-136fdbe30654"}' | jq .

# Запрос по маршруту 49 на Верхних котлах
curl -s -X POST 'http://localhost:8000/eta' \
  -H 'Content-Type: application/json' \
  -d '{"route": "49", "stop_name": "Верхние котлы"}' | jq .
```

### 5. Поиск активных автобусов на карте
```bash
# Показать только автобусы с кондиционером на маршруте 110
curl -s 'http://localhost:8000/buses?route=110&air_conditioned_only=true' | jq .

# Поиск бортов в радиусе 5 км с автоматической сортировкой по возрастанию расстояния
curl -s 'http://localhost:8000/buses?lat=55.7971&lon=37.9398&radius_km=5&limit=5' | jq .
```

### 6. Досье конкретного автобуса
```bash
curl -s -G 'http://localhost:8000/vehicle' \
  --data-urlencode 'state_number=у342ат790' | jq .
```

---

## 🧪 Тестирование

### Unit-тестирование (pytest)
Проект покрыт подробными тестами, проверяющими логику геометрии, rate-лимитеры, нормализацию идентификаторов и сверку расписаний:

```bash
pytest -s
```

Конфигурация `pytest.ini`:
```ini
[pytest]
asyncio_mode = auto
log_cli = true
log_cli_level = DEBUG
log_cli_format = %(asctime)s [%(levelname)-8s] %(name)s: %(message)s
log_cli_date_format = %H:%M:%S
addopts = -ra -q
pythonpath = .
```

### Сквозное тестирование агента (`agent_test.py`)
Для проверки работоспособности на **живой боевой телеметрии** разработан скрипт `agent_test.py`, который просто имитирует REST запросы на одинаковых случайновыбранных данных:

```bash
# Запуск сквозного прогона (36 сценариев)
python agent_test.py

# Подробный вывод каждого запроса и тела ответов
python agent_test.py -v

# Экспорт всех curl-команд в исполняемый bash-скрипт run_curls.sh
python agent_test.py --dump-sh
```

---

## 📂 Структура репозитория

```text
├── config.py                      # Типизированная конфигурация на Pydantic Settings
├── bus_stops_all.geojson          # Реестр остановочных пунктов Москвы (data.mos.ru)
├── stops_repository.py            # Поисковый индекс остановок (Spatial, ID, StationName)
├── transport_service.py           # Оркестратор и бизнес-логика инструментов агента
├── server.py                      # Точка входа: FastAPI + MCP сервер
├── agent_test.py                  # Автоматизированный E2E-сьют проверки curl-сценариев
├── pytest.ini                     # Конфигурация запуска тестов
├── requirements.txt               # Зависимости проекта
├── clients/                       # Изолированные клиенты внешних транспортных API
│   ├── __init__.py
│   ├── moscow_transport_client.py # Клиент stop_v2 и qr-stop Московского транспорта
│   ├── rnis_client.py             # Клиент РНИС МО (каталог, GPS, stop_point.list/routes)
│   └── yandex_client.py           # Клиент Яндекс.Карт (Masstransit API)
└── tests/                         # Набор модульных тестов
    ├── test_moscow_transport_client.py
    ├── test_rnis_client.py
    ├── test_stops_repository.py
    ├── test_transport_service.py
    └── test_yandex_client.py
```

## Лицензия

MIT License. Свободно для использования, модификации и интеграции в AI-агентов.

---

### Перспективы

- Сделать нормальный `transport_service.py`, так как текущая реализация неэффективно работает со всеми тремя источниками