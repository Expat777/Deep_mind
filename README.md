# DataMind

Мультиагентная система анализа данных: **FastAPI + SQL + LangGraph + Selectel S3**.
Пользователь загружает CSV и задаёт вопросы на естественном языке — система через граф
агентов генерирует SQL, выполняет его и возвращает читаемый ответ.

Задание целиком — в [phase3_README.md](phase3_README.md).

## Статус реализации

- [x] **Часть 1 — FastAPI + SQL**: `/datasets/upload`, `/datasets`, `/datasets/{id}/rows`, таблица `query_log`
- [x] **Часть 2 — LangGraph агенты**: `router → sql_agent → synthesizer` (+ `clarifier`), `/query`
- [ ] **Часть 3 — Selectel S3**: загрузка в бакет, presigned URL, фоновая проверка

## Требования

- Python 3.11+
- PostgreSQL 16 (локально через Homebrew или Docker)

## Установка

```bash
# 1. Виртуальное окружение + зависимости
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Переменные окружения
cp .env.example .env
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

**Вариант B — Docker:**

```bash
docker compose up -d db
```

В обоих случаях `DATABASE_URL` в `.env` уже настроен на
`postgresql+psycopg2://datamind:datamind@localhost:5432/datamind`.

## Запуск

```bash
source .venv/bin/activate
uvicorn app.main:app --reload
```

- Swagger UI: http://localhost:8000/docs
- Health-check: http://localhost:8000/health

## Эндпоинты (Часть 1)

| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/datasets/upload` | Загрузка CSV → строки в БД (JSONB) |
| `GET`  | `/datasets` | Список датасетов |
| `GET`  | `/datasets/{id}/rows?limit=&offset=` | Пагинированный просмотр строк |
| `POST` | `/query` | Вопрос на естественном языке через граф агентов |
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
datasets     (id, name, file_path, row_count, created_at)
dataset_rows (id, dataset_id, row_data JSONB, row_index)
query_log    (id, dataset_id, question, answer, created_at)
```

Таблицы создаются автоматически при старте приложения (`Base.metadata.create_all`).
