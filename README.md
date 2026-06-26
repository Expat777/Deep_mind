# 🤖 DataMind

Мультиагентная система анализа данных: **FastAPI + PostgreSQL + LangGraph + Selectel S3**.

Пользователь загружает CSV и задаёт вопросы на естественном языке — система через граф
агентов (LangGraph) генерирует SQL, выполняет его и возвращает читаемый ответ.
Исходные файлы хранятся в облачном Object Storage (Selectel S3).

```
Пользователь → FastAPI → LangGraph граф → [router | clarifier | sql_agent | synthesizer] → Ответ
                  │                                    │
                  ▼                                    ▼
            Selectel S3 (CSV)                    PostgreSQL (данные, история)
```

Полный текст задания — в [phase3_README.md](phase3_README.md).

---

## Содержание

- [Статус](#статус)
- [Стек](#стек)
- [Структура проекта](#структура-проекта)
- [Установка и запуск](#установка-и-запуск)
- [Переменные окружения](#переменные-окружения)
- [API-эндпоинты](#api-эндпоинты)
- [Примеры запросов](#примеры-запросов)
- [Часть 1 — FastAPI + SQL](#часть-1--fastapi--sql)
- [Часть 2 — LangGraph агенты](#часть-2--langgraph-агенты)
- [Часть 3 — Selectel S3](#часть-3--selectel-s3)
- [Схема БД](#схема-бд)
- [Обработка ошибок](#обработка-ошибок)

---

## Статус

- [x] **Часть 1 — FastAPI + SQL**: `/datasets/upload`, `/datasets`, `/datasets/{id}/rows`, таблица `query_log`
- [x] **Часть 2 — LangGraph агенты**: `router → sql_agent → synthesizer` (+ `clarifier`), `/query`
- [x] **Часть 3 — Selectel S3**: загрузка в бакет, `/datasets/{id}/download` (presigned URL), фоновая проверка `head_object`

---

## Стек

| Технология | Роль | Где в коде |
|---|---|---|
| **FastAPI** | REST API: загрузка, запросы, скачивание | [app/main.py](app/main.py), [app/api/](app/api/) |
| **PostgreSQL + SQLAlchemy 2.0** | Хранение датасетов, строк (JSONB), истории | [app/db/](app/db/) |
| **LangGraph + langchain-openai** | Граф агентов, роутинг, синтез ответа | [app/agents/](app/agents/) |
| **Selectel Object Storage (S3)** | Хранение исходных CSV (boto3) | [app/storage/s3.py](app/storage/s3.py) |
| **DeepSeek** (OpenAI-совместимый API) | LLM для всех узлов графа | [app/agents/llm.py](app/agents/llm.py) |

---

## Структура проекта

```
project_deepmind/
├── app/
│   ├── main.py              # точка входа FastAPI, подключение роутеров, create_all
│   ├── config.py            # настройки из .env (pydantic-settings)
│   ├── schemas.py           # Pydantic-схемы запросов/ответов
│   ├── api/
│   │   ├── datasets.py      # upload / list / rows / download
│   │   └── query.py         # /query — вызов графа агентов
│   ├── agents/
│   │   ├── graph.py         # сборка LangGraph графа и узлы
│   │   ├── llm.py           # клиент LLM + парсинг ответов
│   │   └── sql_tools.py     # схема из JSONB + безопасное выполнение SELECT
│   ├── db/
│   │   ├── database.py      # engine, session, Base
│   │   └── models.py        # SQLAlchemy-модели
│   └── storage/
│       └── s3.py            # клиент Selectel S3 (boto3)
├── data/orders.csv          # тестовый датасет
├── docker-compose.yml       # PostgreSQL 16
├── requirements.txt
├── .env.example
└── README.md
```

Ссылки на файлы: [main.py](app/main.py) · [config.py](app/config.py) · [schemas.py](app/schemas.py) · [api/datasets.py](app/api/datasets.py) · [api/query.py](app/api/query.py) · [agents/graph.py](app/agents/graph.py) · [agents/llm.py](app/agents/llm.py) · [agents/sql_tools.py](app/agents/sql_tools.py) · [db/database.py](app/db/database.py) · [db/models.py](app/db/models.py) · [storage/s3.py](app/storage/s3.py)

---

## Установка и запуск

Требования: **Python 3.11+**, **Docker** (для PostgreSQL).

```bash
# 1. Виртуальное окружение + зависимости
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Переменные окружения
cp .env.example .env
# заполнить DEEPSEEK_API_KEY и ключи Selectel (SELECTEL_ACCESS_KEY / SECRET_KEY / BUCKET)

# 3. База данных (PostgreSQL 16 в Docker)
docker compose up -d db

# 4. Запуск приложения
uvicorn app.main:app --reload
```

- Swagger UI: <http://localhost:8000/docs>
- Health-check: <http://localhost:8000/health>

Таблицы создаются автоматически при старте через `Base.metadata.create_all` — см. [main.py:11](app/main.py#L11).

---

## Переменные окружения

Читаются из `.env` через [config.py](app/config.py) (pydantic-settings). Шаблон — [.env.example](.env.example).

```env
# База данных
DATABASE_URL=postgresql+psycopg2://datamind:datamind@localhost:5432/datamind

# LLM (DeepSeek, OpenAI-совместимый API)
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com        # или агрегатор, напр. https://polza.ai/api/v1
DEEPSEEK_MODEL=deepseek-chat                       # или deepseek/deepseek-chat у агрегатора

# Selectel Object Storage (S3)
SELECTEL_ENDPOINT=https://s3.ru-6.storage.selcloud.ru   # РЕГИОНАЛЬНЫЙ эндпоинт!
SELECTEL_REGION=ru-6
SELECTEL_ACCESS_KEY=...
SELECTEL_SECRET_KEY=...
SELECTEL_BUCKET=deepmind
```

> ⚠️ **Важно про Selectel:** общий адрес `s3.selectel.ru` не работает — нужен
> **региональный** эндпоинт вида `https://s3.<регион>.storage.selcloud.ru`. Регион и имя
> бакета смотри в панели Selectel. Если ключи S3 не заданы — приложение всё равно
> запустится, но файлы не будут уходить в облако (см. `is_s3_enabled` в [s3.py:21](app/storage/s3.py#L21)).

---

## API-эндпоинты

| Метод | Путь | Описание | Код |
|---|---|---|---|
| `POST` | `/datasets/upload` | Загрузка CSV → строки в БД (JSONB) + файл в S3 | [datasets.py:24](app/api/datasets.py#L24) |
| `GET`  | `/datasets` | Список датасетов | [datasets.py](app/api/datasets.py) |
| `GET`  | `/datasets/{id}/rows?limit=&offset=` | Пагинированный просмотр строк | [datasets.py](app/api/datasets.py) |
| `GET`  | `/datasets/{id}/download` | Presigned URL на скачивание CSV из S3 (15 мин) | [datasets.py](app/api/datasets.py) |
| `POST` | `/query` | Вопрос на естественном языке через граф агентов | [query.py:15](app/api/query.py#L15) |
| `GET`  | `/health` | Проверка живости | [main.py](app/main.py) |

---

## Примеры запросов

```bash
# Загрузка CSV (файл уходит в S3, строки — в БД)
curl -F "file=@data/orders.csv" http://localhost:8000/datasets/upload

# Список датасетов
curl http://localhost:8000/datasets

# Просмотр строк
curl "http://localhost:8000/datasets/1/rows?limit=5&offset=0"

# Presigned-ссылка на скачивание исходного файла
curl http://localhost:8000/datasets/1/download

# Вопрос на естественном языке
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"dataset_id":1,"question":"Топ-3 категории по выручке","session_id":"s1"}'
```

---

## Часть 1 — FastAPI + SQL

**Загрузка CSV** ([datasets.py:24](app/api/datasets.py#L24)): файл парсится через pandas,
строки сохраняются в `dataset_rows.row_data` как JSONB. Используется
`df.to_json → json.loads`, чтобы получить нативные Python-типы, пригодные для JSONB.

**Просмотр** — список датасетов и пагинированный просмотр строк (`limit`/`offset`).

**История запросов** — каждый вопрос к `/query` пишется в таблицу `query_log`
([query.py:47](app/api/query.py#L47)).

Модели данных: [db/models.py](app/db/models.py). JSONB на PostgreSQL и обычный JSON на
прочих диалектах через `JSONType` — [models.py:11](app/db/models.py#L11).

---

## Часть 2 — LangGraph агенты

Граф из четырёх узлов с явным условным роутингом ([graph.py:144](app/agents/graph.py#L144)):

```
[router] ──clarify──▶ [clarifier] ──▶ END
   │
   sql
   ▼
[sql_agent] ──▶ [synthesizer] ──▶ END
```

| Узел | Что делает | Код |
|---|---|---|
| **router** | Решает, достаточно ли вопрос чёткий для SQL, иначе → clarifier | [graph.py:72](app/agents/graph.py#L72) |
| **clarifier** | Формулирует уточняющий вопрос, диалог продолжается в той же сессии | [graph.py:85](app/agents/graph.py#L85) |
| **sql_agent** | Генерирует SQL по схеме, проверяет безопасность, выполняет | [graph.py:100](app/agents/graph.py#L100) |
| **synthesizer** | Превращает сырой результат в читаемый ответ на русском | [graph.py:120](app/agents/graph.py#L120) |

**Память сессий.** Состояние хранит `MemorySaver` с `thread_id = session_id`
([graph.py:161](app/agents/graph.py#L161)), поэтому `clarifier` ведёт диалог через
несколько вызовов `/query`. Эндпоинт передаёт `thread_id` в конфиг графа —
[query.py:37](app/api/query.py#L37).

**Как LLM пишет SQL по JSONB.** Данные лежат в JSONB, но LLM пишет обычный
`... FROM data`. Перед выполнением запрос оборачивается в CTE `data`, который проецирует
JSONB в типизированные колонки ([sql_tools.py](app/agents/sql_tools.py)):
- `build_schema` — выводит «виртуальную» схему (имена + типы) из строк — [sql_tools.py:40](app/agents/sql_tools.py#L40);
- `is_safe_select` — пропускает только одиночный `SELECT` без изменяющих команд — [sql_tools.py:77](app/agents/sql_tools.py#L77);
- `run_sql` — выполняет SELECT поверх CTE, отдаёт до 200 строк — [sql_tools.py:87](app/agents/sql_tools.py#L87).

**LLM.** Клиент поверх DeepSeek (OpenAI-совместимый API) — [llm.py:9](app/agents/llm.py#L9).
Хелперы `parse_json` / `clean_sql` чистят ответы LLM от markdown-обёрток — [llm.py:22](app/agents/llm.py#L22).

---

## Часть 3 — Selectel S3

Клиент S3 на boto3 — [app/storage/s3.py](app/storage/s3.py). Selectel работает через
стандартный boto3, отличается только `endpoint_url` и регион.

1. **Загрузка в бакет.** При `/upload` исходный CSV отправляется в S3 под ключом
   `datasets/<имя>_<timestamp>.csv`; ключ сохраняется в `datasets.s3_key`
   ([datasets.py:54](app/api/datasets.py#L54), `upload_bytes` — [s3.py:43](app/storage/s3.py#L43)).
2. **Presigned URL.** `GET /datasets/{id}/download` отдаёт прямую ссылку на скачивание,
   действующую 15 минут (`generate_presigned_url` — [s3.py:54](app/storage/s3.py#L54)).
3. **Фоновая проверка.** После загрузки `BackgroundTasks` асинхронно вызывает
   `head_object` и логирует, появился ли файл (`verify_upload` — [s3.py:73](app/storage/s3.py#L73);
   постановка задачи — [datasets.py](app/api/datasets.py)).
4. **Мягкая деградация.** Если ключи S3 пустые, `is_s3_enabled` вернёт `False` —
   загрузка в облако пропускается, данные всё равно пишутся в БД ([s3.py:21](app/storage/s3.py#L21)).

---

## Схема БД

Определена в [db/models.py](app/db/models.py), таблицы создаются при старте.

```sql
datasets     (id, name, s3_key, row_count, created_at)
dataset_rows (id, dataset_id, row_data JSONB, row_index)
query_log    (id, dataset_id, question, answer, created_at)
```

- `datasets` — метаданные загруженных файлов; `s3_key` — ключ объекта в Selectel S3 ([models.py:14](app/db/models.py#L14)).
- `dataset_rows` — строки CSV в JSONB ([models.py:28](app/db/models.py#L28)).
- `query_log` — история вопросов и ответов ([models.py:41](app/db/models.py#L41)).

---

## Обработка ошибок

| Код | Когда | Пример |
|---|---|---|
| `400` | Битый/пустой CSV, в датасете нет строк | [datasets.py:31](app/api/datasets.py#L31) |
| `404` | Датасет не найден / нет файла в S3 | [datasets.py](app/api/datasets.py), [query.py:23](app/api/query.py#L23) |
| `500` | Ошибка выполнения графа агентов | [query.py:42](app/api/query.py#L42) |
| `502` | Ошибка S3 (загрузка / presigned) | [datasets.py](app/api/datasets.py) |
| `503` | LLM не настроен (нет `DEEPSEEK_API_KEY`) | [query.py:19](app/api/query.py#L19) |
