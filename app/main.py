import logging

from fastapi import FastAPI

from app.api import datasets, query
from app.db.database import Base, engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

# Для учебного проекта создаём таблицы при старте (без Alembic).
Base.metadata.create_all(bind=engine)

tags_metadata = [
    {
        "name": "datasets",
        "description": "Загрузка CSV в БД + Selectel S3, просмотр строк, presigned-ссылки на скачивание.",
    },
    {
        "name": "query",
        "description": "Вопросы на естественном языке через граф агентов LangGraph "
        "(router → sql_agent → synthesizer, + clarifier). Поддерживает построение графиков.",
    },
    {"name": "system", "description": "Служебные эндпоинты."},
]

app = FastAPI(
    title="DataMind",
    description=(
        "Мультиагентная система анализа данных.\n\n"
        "**Стек:** LangGraph · FastAPI · PostgreSQL · Selectel Object Storage.\n\n"
        "Загрузите CSV через `POST /datasets/upload`, затем задавайте вопросы на естественном "
        "языке через `POST /query` — система сгенерирует SQL, выполнит его и вернёт ответ."
    ),
    version="1.0.0",
    openapi_tags=tags_metadata,
    contact={"name": "DataMind", "url": "https://github.com/GDV-prog/Deep_mind"},
)

app.include_router(datasets.router)
app.include_router(query.router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}
