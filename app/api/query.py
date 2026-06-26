import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from sqlalchemy.orm import Session

from app.agents.graph import graph
from app.agents.sql_tools import build_schema, json_safe
from app.config import settings
from app.db.database import get_db
from app.db.models import Dataset, QueryLog
from app.schemas import QueryIn, QueryOut

router = APIRouter(tags=["query"])


def _prepare_run(payload: QueryIn, db: Session) -> tuple[dict, dict]:
    """Общая валидация для /query и /query/stream: возвращает (initial_state, config)."""
    if not settings.DEEPSEEK_API_KEY:
        raise HTTPException(status_code=503, detail="LLM не настроен: задайте DEEPSEEK_API_KEY")

    if db.get(Dataset, payload.dataset_id) is None:
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
    return initial, config


@router.post(
    "/query",
    response_model=QueryOut,
    summary="Задать вопрос агенту",
    responses={
        404: {"description": "Датасет не найден"},
        400: {"description": "Датасет пуст"},
        503: {"description": "LLM не настроен (нет DEEPSEEK_API_KEY)"},
    },
)
def query(payload: QueryIn, db: Session = Depends(get_db)):
    """Прогоняет вопрос через граф агентов (router → sql_agent → synthesizer, + clarifier)
    и пишет вопрос/ответ в `query_log`.

    Диалог с уточнениями: при неоднозначном вопросе вернётся `needs_clarification=true`
    и встречный вопрос — пришлите ответ тем же `session_id`."""
    initial, config = _prepare_run(payload, db)

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
        plot=final.get("plot"),
    )


def _sse(data: dict) -> str:
    """Форматирует событие как Server-Sent Event."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post(
    "/query/stream",
    summary="Задать вопрос агенту (streaming, SSE)",
    responses={
        404: {"description": "Датасет не найден"},
        400: {"description": "Датасет пуст"},
        503: {"description": "LLM не настроен (нет DEEPSEEK_API_KEY)"},
    },
)
async def query_stream(payload: QueryIn, db: Session = Depends(get_db)):
    """То же, что `/query`, но ответ приходит потоком (`text/event-stream`):
    по событию на каждый узел графа (`prepare`, `router`, `sql_agent`, `plot_tool`,
    `synthesizer`/`clarifier`). Финальное событие — `done`."""
    initial, config = _prepare_run(payload, db)

    async def event_gen():
        final_answer, needs_clarification = "", False
        try:
            async for chunk in graph.astream(initial, config, stream_mode="updates"):
                for node, update in chunk.items():
                    update = update or {}
                    event = {"node": node}
                    if update.get("sql"):
                        event["sql"] = update["sql"]
                    if update.get("error"):
                        event["error"] = update["error"]
                    if update.get("plot"):
                        event["plot_generated"] = True  # сам PNG в финале, чтобы не раздувать события
                    if update.get("answer"):
                        event["answer"] = update["answer"]
                        final_answer = update["answer"]
                    if node == "clarifier":
                        needs_clarification = True
                    yield _sse(event)

            db.add(
                QueryLog(dataset_id=payload.dataset_id, question=payload.question, answer=final_answer)
            )
            db.commit()
            yield _sse({"event": "done", "needs_clarification": needs_clarification})
        except Exception as exc:  # noqa: BLE001
            yield _sse({"event": "error", "detail": str(exc)})

    return StreamingResponse(event_gen(), media_type="text/event-stream")
