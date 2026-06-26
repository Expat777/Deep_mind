from fastapi import FastAPI

from app.api import datasets, query
from app.db.database import Base, engine

# Для учебного проекта создаём таблицы при старте (без Alembic).
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="DataMind",
    description="Мультиагентная система анализа данных (LangGraph + FastAPI + SQL + Selectel)",
    version="0.1.0",
)

app.include_router(datasets.router)
app.include_router(query.router)


@app.get("/health", tags=["system"])
def health():
    return {"status": "ok"}
