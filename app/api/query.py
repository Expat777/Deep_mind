from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import HumanMessage
from sqlalchemy.orm import Session

from app.agents.graph import graph
from app.agents.sql_tools import build_schema, json_safe
from app.config import settings
from app.db.database import get_db
from app.db.models import Dataset, QueryLog
from app.schemas import QueryIn, QueryOut

router = APIRouter(tags=["query"])


@router.post("/query", response_model=QueryOut)
def query(payload: QueryIn, db: Session = Depends(get_db)):
    """Прогоняет вопрос через граф агентов и пишет результат в query_log."""
    if not settings.DEEPSEEK_API_KEY:
        raise HTTPException(status_code=503, detail="LLM не настроен: задайте DEEPSEEK_API_KEY")

    dataset = db.get(Dataset, payload.dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Датасет не найден")

    try:
        schema_text, projection = build_schema(db, payload.dataset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    initial = {
        "messages": [HumanMessage(payload.question)],
        "dataset_id": payload.dataset_id,
        "session_id": payload.session_id,
        "schema": schema_text,
        "projection": projection,
    }
    config = {"configurable": {"thread_id": payload.session_id}}

    try:
        final = graph.invoke(initial, config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Ошибка графа агентов: {exc}")

    answer = final.get("answer", "")
    needs_clarification = final.get("route") == "clarify"

    db.add(QueryLog(dataset_id=payload.dataset_id, question=payload.question, answer=answer))
    db.commit()

    return QueryOut(
        session_id=payload.session_id,
        answer=answer,
        needs_clarification=needs_clarification,
        sql=final.get("sql"),
        rows=json_safe(final.get("rows")) if final.get("rows") else None,
    )
