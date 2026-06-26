# 🤖 Phase 3 Project — DataMind

> Мультиагентная система анализа данных на базе LangGraph + FastAPI + SQL + Selectel

## Что вы будете строить

**DataMind** — это API-сервис, в котором пользователь загружает CSV-файлы или задаёт вопросы на естественном языке, а система разбирает запрос через цепочку LangGraph-агентов, обращается к базе данных, и возвращает структурированный ответ.

```
Пользователь → FastAPI → LangGraph Graph → [SQL Agent | Analyst Agent] → Ответ
                                  ↕
                           PostgreSQL + Selectel S3
```

---

## Стек

| Технология | Роль в проекте |
|---|---|
| **LangGraph** | Граф агентов: роутинг, выполнение инструментов, синтез ответа |
| **FastAPI** | REST API: загрузка файлов, отправка запросов, история сессий |
| **PostgreSQL / SQLite** | Хранение загруженных данных, истории запросов, логов агентов |
| **Selectel Object Storage** | Хранение исходных CSV-файлов (S3-совместимый API) |

---

## Датасет

В папке [`data/`](data/) лежит файл `orders.csv` — выдуманные данные интернет-магазина (500 строк):

```
order_id, date, city, category, product, quantity, price, total
1001, 2024-03-05, Москва, Электроника, Наушники, 2, 3200, 6400
1002, 2024-03-06, Казань, Одежда, Куртка, 1, 5800, 5800
...
```

Именно этот файл используйте при разработке и тестировании. Агент должен уметь отвечать на вопросы вроде:
- *«Какой средний чек по городам за март?»*
- *«Топ-3 категории по выручке»*
- *«Сколько заказов было в Казани?»*

---

## Задания

### Часть 1 — FastAPI + SQL (база)

1. Реализуйте endpoint `POST /upload` — принимает CSV-файл, парсит его и сохраняет строки в таблицу базы данных (файл `orders.csv` из папки `data/` — ваш тестовый кейс).
2. Реализуйте endpoint `GET /datasets` — возвращает список загруженных датасетов (название, дата, кол-во строк).
3. Реализуйте endpoint `GET /datasets/{id}/rows?limit=&offset=` — пагинированный просмотр строк таблицы.
4. Добавьте таблицу `query_log` — каждый вопрос пользователя и ответ системы должны сохраняться с временной меткой и `dataset_id`.

**Ожидаемая схема БД:**
```sql
datasets (id, name, file_path, row_count, created_at)
dataset_rows (id, dataset_id, row_data JSONB, row_index)
query_log (id, dataset_id, question, answer, created_at)
```

---

### Часть 2 — LangGraph агенты (ядро)

Постройте граф с минимум тремя узлами:

```
[router] → [sql_agent] → [synthesizer]
               ↑
          [clarifier]  ← если вопрос неоднозначен
```

- **`router`** — определяет, достаточно ли вопрос чёткий, чтобы писать SQL. Если нет — отправляет в `clarifier`.
- **`clarifier`** — задаёт уточняющий вопрос и ждёт следующего сообщения в сессии.
- **`sql_agent`** — на основе схемы таблицы и вопроса генерирует SQL, выполняет его, возвращает результат.
- **`synthesizer`** — форматирует сырой результат из БД в читаемый ответ.

Реализуйте endpoint `POST /query`:
```json
{
  "dataset_id": 1,
  "question": "Какой средний чек по городам за март?",
  "session_id": "abc123"
}
```

Граф должен хранить `session_id` в состоянии, чтобы `clarifier` мог вести диалог через несколько запросов.

---

### Часть 3 — Selectel Object Storage (новая тема)

Вы уже умеете создавать облачные серверы в Selectel. Теперь познакомитесь с другим продуктом — **Object Storage**. Это хранилище файлов в облаке: вместо того чтобы хранить загруженные CSV прямо на сервере (и рисковать потерять их при перезапуске), вы отправляете их в S3-совместимое хранилище.

**Зачем это нужно на практике:** сервер stateless, хранилище — вечное. Файлы доступны по URL, легко отдаются пользователям, не засоряют диск инстанса.

**Как подключиться:** Selectel S3 работает через стандартный `boto3` — тот же пакет, что и для AWS S3, только с другим `endpoint_url`.

```python
import boto3

s3 = boto3.client(
    "s3",
    endpoint_url="https://s3.selectel.ru",
    aws_access_key_id=settings.SELECTEL_ACCESS_KEY,
    aws_secret_access_key=settings.SELECTEL_SECRET_KEY,
)
```

**Задачи:**

1. Создайте бакет в панели Selectel и подключите `boto3` в проекте.
2. Измените `POST /upload`: после парсинга CSV отправляйте исходный файл в S3. В таблице `datasets` замените колонку `file_path` на `s3_key`.
3. Реализуйте endpoint `GET /datasets/{id}/download` — генерирует presigned URL (ссылка, которая действует 15 минут) на скачивание файла из S3. Пользователь должен получить прямую ссылку без авторизации.
4. Добавьте фоновую задачу (`BackgroundTasks`): после каждой загрузки асинхронно проверяйте, что файл действительно появился в S3 (`s3.head_object`), и логируйте результат.

---

### Бонус (по желанию)

- [ ] Добавьте инструмент `plot_tool` в граф — агент может попросить построить график, и вы возвращаете base64-encoded PNG в ответе.
- [ ] Streaming-ответы через `StreamingResponse` в FastAPI + `astream` в LangGraph.
- [ ] Деплой на Selectel Cloud (VPS или контейнер) с `docker-compose.yml`.
- [ ] Swagger-документация с примерами запросов для каждого endpoint.

---

## Критерии приёмки

| # | Критерий | Обязательно |
|---|---|---|
| 1 | Загрузка CSV сохраняет данные в БД и файл в S3 | ✅ |
| 2 | `POST /query` возвращает ответ через LangGraph граф | ✅ |
| 3 | Граф имеет минимум 3 узла с явным роутингом | ✅ |
| 4 | История запросов пишется в `query_log` | ✅ |
| 5 | Presigned URL для скачивания работает | ✅ |
| 6 | Код покрыт базовой обработкой ошибок (400/404/500) | ✅ |
| 7 | `README` с инструкцией по запуску и `.env.example` | ✅ |

---

## Структура проекта (рекомендуемая)

```
project/
├── app/
│   ├── api/          # FastAPI роутеры
│   ├── agents/       # LangGraph граф и узлы
│   ├── db/           # SQLAlchemy модели и миграции
│   ├── storage/      # Клиент Selectel S3
│   └── main.py
├── .env.example
├── docker-compose.yml
└── README.md
```

---

## Быстрый старт

```bash
cp .env.example .env
# заполните SELECTEL_ACCESS_KEY, SELECTEL_SECRET_KEY, BUCKET_NAME, DATABASE_URL, OPENAI_API_KEY

docker-compose up -d db
uvicorn app.main:app --reload
```

---

## Переменные окружения

```env
DATABASE_URL=postgresql://user:pass@localhost:5432/datamind
DEEPSEEK_API_KEY=sk-...

# Selectel S3
SELECTEL_ENDPOINT=https://s3.selectel.ru
SELECTEL_ACCESS_KEY=
SELECTEL_SECRET_KEY=
SELECTEL_BUCKET=datamind-files
```

---

> Дедлайн и формат сдачи — уточните у ментора.  
> Вопросы кидайте в общий чат или открывайте Issue в этом репо.
