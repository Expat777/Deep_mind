# DataMind

Мультиагентная система анализа данных: **FastAPI + SQL + LangGraph + Selectel S3**.
Пользователь загружает CSV и задаёт вопросы на естественном языке — система через граф
агентов генерирует SQL, выполняет его и возвращает читаемый ответ.

Задание целиком — в [phase3_README.md](phase3_README.md).

## Статус реализации

- [x] **Часть 1 — FastAPI + SQL**: `/datasets/upload`, `/datasets`, `/datasets/{id}/rows`, таблица `query_log`
- [x] **Часть 2 — LangGraph агенты**: `router → sql_agent → synthesizer` (+ `clarifier`), `/query`
- [x] **Часть 3 — Selectel S3**: загрузка в бакет, presigned URL, фоновая проверка `head_object`

**Бонусы:**
- [x] `plot_tool` — агент строит график (base64-PNG) при запросе вида «построй график …»
- [x] Streaming-ответы — `POST /query/stream` (SSE) через `astream`
- [x] Docker-деплой — `Dockerfile` + `docker-compose` (app + db)
- [x] Swagger-документация с тегами, описаниями и примерами (`/docs`)

## Структура проекта

```
Deep_mind/
├── backend/              # FastAPI + агенты + S3 (всё для API)
│   ├── app/              # api/ · agents/ · db/ · storage/ · main.py
│   ├── data/             # тестовый orders.csv
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── langgraph.json    # конфиг LangGraph Studio
│   └── .env / .env.example
├── frontend/             # Streamlit-клиент (деплой на Streamlit Cloud)
├── docker-compose.yml    # оркестрация app + db (билдит ./backend)
├── README.md
└── phase3_README.md      # исходное задание
```

## Требования

- Python 3.11+
- PostgreSQL 16 (локально через Homebrew или Docker)

## Установка (бэкенд)

```bash
# 1. Виртуальное окружение + зависимости
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# 2. Переменные окружения
cp backend/.env.example backend/.env
# заполнить DEEPSEEK_API_KEY и (для Части 3) ключи Selectel
```

### База данных

**Вариант A — Homebrew (используется в этом окружении):**

```bash
brew install postgresql@16
brew services start postgresql@16
createuser datamind --pwprompt        # пароль: datamind
createdb datamind -O datamind
```

**Вариант B — только БД в Docker:**

```bash
docker compose up -d db
```

В обоих случаях `DATABASE_URL` в `backend/.env` уже настроен на
`postgresql+psycopg2://datamind:datamind@localhost:5432/datamind`.

## Запуск

### Локально (venv)

```bash
source .venv/bin/activate
cd backend && uvicorn app.main:app --reload
```

### Весь стек в Docker (app + db)

```bash
cp backend/.env.example backend/.env   # заполнить DEEPSEEK_* и SELECTEL_*
docker compose up --build              # из корня проекта
```

Поднимутся два контейнера: `datamind_db` (PostgreSQL) и `datamind_app` (FastAPI на :8000).
Внутри сети compose приложение ходит в БД по хосту `db` (см. override `DATABASE_URL` в
`docker-compose.yml`).

- Swagger UI: http://localhost:8000/docs
- Health-check: http://localhost:8000/health

## Бонусы

- **Графики** (`plot_tool`): добавьте в вопрос слово «построй график / диаграмму».
  В ответе `/query` появится поле `plot` — PNG в base64.
- **Streaming** (`POST /query/stream`): ответ приходит как Server-Sent Events, по событию
  на каждый узел графа. Пример:
  ```bash
  curl -N -X POST http://localhost:8000/query/stream \
    -H "Content-Type: application/json" \
    -d '{"dataset_id":1,"question":"Топ-3 категории по выручке, построй график","session_id":"s1"}'
  ```

## Эндпоинты (Часть 1)

| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/datasets/upload` | Загрузка CSV → строки в БД (JSONB) |
| `GET`  | `/datasets` | Список датасетов |
| `GET`  | `/datasets/{id}/rows?limit=&offset=` | Пагинированный просмотр строк |
| `GET`  | `/datasets/{id}/download` | Presigned URL (15 мин) на скачивание CSV из S3 |
| `POST` | `/query` | Вопрос на естественном языке через граф агентов |
| `POST` | `/query/stream` | То же, но потоком (SSE) — событие на каждый узел графа |
| `GET`  | `/health` | Проверка живости |

### Пример

```bash
curl -F "file=@data/orders.csv" http://localhost:8000/datasets/upload
curl http://localhost:8000/datasets
curl "http://localhost:8000/datasets/1/rows?limit=5&offset=0"

curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"dataset_id":1,"question":"Топ-3 категории по выручке","session_id":"s1"}'
```

## Граф агентов (Часть 2)

```
[router] --clarify--> [clarifier] --> END
   | sql
   v
[sql_agent] --> [synthesizer] --> END
```

- **router** — решает, достаточно ли вопрос чёткий для SQL, иначе → clarifier.
- **clarifier** — задаёт уточняющий вопрос; диалог продолжается в той же `session_id`.
- **sql_agent** — генерирует SQL по «виртуальной» схеме и выполняет его (только SELECT).
- **synthesizer** — превращает результат в читаемый ответ на русском.

Данные лежат в `dataset_rows.row_data` (JSONB). Перед выполнением SQL агента строки
проецируются в типизированную таблицу `data` через CTE — LLM пишет обычный SQL `... FROM data`.
Состояние сессий хранит `MemorySaver` (thread_id = `session_id`).

> **LLM:** проект использует OpenAI-совместимый API (DeepSeek). `DEEPSEEK_BASE_URL` и
> `DEEPSEEK_MODEL` в `.env` задают провайдера и имя модели (напр. `deepseek/deepseek-chat`
> для агрегаторов вроде Polza.ai, или `deepseek-chat` для api.deepseek.com).

## Схема БД

```sql
datasets     (id, name, s3_key, row_count, created_at)
dataset_rows (id, dataset_id, row_data JSONB, row_index)
query_log    (id, dataset_id, question, answer, created_at)
```

## Selectel Object Storage (Часть 3)

Исходные CSV хранятся не на диске инстанса, а в S3-бакете Selectel (поле `datasets.s3_key`).

- **`POST /upload`** — после парсинга кладёт файл в бакет (`boto3.put_object`) и запускает
  фоновую задачу (`BackgroundTasks`), которая проверяет наличие файла (`head_object`) и пишет в лог.
- **`GET /datasets/{id}/download`** — возвращает presigned URL на 15 минут: прямая ссылка на
  скачивание без авторизации.

Клиент S3 — [app/storage/s3.py](app/storage/s3.py). Подключение через `boto3` с
`endpoint_url`/`region_name` из `.env` (Selectel S3-совместим с AWS). Бакет и S3-ключи
создаются в панели Selectel (сервисный пользователь → ключи доступа S3).

Таблицы создаются автоматически при старте приложения (`Base.metadata.create_all`).
