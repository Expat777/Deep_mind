"""Граф агентов LangGraph:

    [router] --clarify--> [clarifier] --> END
        |
        sql
        v
    [sql_agent] --> [synthesizer] --> END

- router      — решает, достаточно ли вопрос чёткий для SQL, иначе → clarifier.
- clarifier   — формулирует уточняющий вопрос и ждёт следующего сообщения сессии.
- sql_agent   — генерирует SQL по схеме таблицы, выполняет его.
- synthesizer — превращает сырой результат в читаемый ответ.

Состояние хранится через MemorySaver с thread_id = session_id, поэтому clarifier
может вести диалог через несколько запросов /query.
"""

from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from app.agents.llm import clean_sql, get_llm, parse_json
from app.agents.plot_tools import render_bar_chart
from app.agents.sql_tools import build_schema, is_safe_select, run_sql
from app.db.database import SessionLocal


class GraphState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    dataset_id: int
    session_id: str
    schema: str          # текстовое описание столбцов для LLM
    projection: str      # SQL-проекция JSONB → CTE `data`
    route: str           # "sql" | "clarify"
    clarify_question: str
    sql: str
    rows: list
    answer: str
    error: str
    plot: str            # график в base64-PNG (если просили построить)


def _last_question(state: GraphState) -> str:
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            return msg.content
    return ""


def _history(state: GraphState) -> str:
    parts = []
    for msg in state["messages"]:
        role = "Пользователь" if isinstance(msg, HumanMessage) else "Система"
        parts.append(f"{role}: {msg.content}")
    return "\n".join(parts)


# --- Узлы -------------------------------------------------------------------

def prepare_node(state: GraphState) -> dict:
    """Готовит схему таблицы и SQL-проекцию по dataset_id.

    В API-сценарии эндпоинт уже передаёт schema/projection — тогда узел ничего
    не делает. В LangGraph Studio достаточно задать dataset_id, остальное узел
    вычислит сам, обратившись к БД.
    """
    if state.get("schema") and state.get("projection"):
        return {}
    db = SessionLocal()
    try:
        schema, projection = build_schema(db, state["dataset_id"])
    finally:
        db.close()
    return {"schema": schema, "projection": projection}


ROUTER_SYS = """Ты — маршрутизатор запросов к таблице данных.
Схема таблицы `data`:
{schema}

Реши, достаточно ли последнего вопроса пользователя (с учётом истории диалога),
чтобы написать к этой таблице корректный SQL.
- Если вопрос понятен и относится к данным — верни {{"route": "sql"}}.
- Если вопрос неоднозначен или не хватает деталей — верни
  {{"route": "clarify", "question": "<короткий уточняющий вопрос на русском>"}}.
Отвечай ТОЛЬКО JSON."""


def router_node(state: GraphState) -> dict:
    llm = get_llm()
    resp = llm.invoke([
        SystemMessage(ROUTER_SYS.format(schema=state["schema"])),
        HumanMessage(f"История диалога:\n{_history(state)}"),
    ])
    data = parse_json(resp.content)
    if data.get("route") == "clarify":
        question = data.get("question") or "Уточните, пожалуйста, ваш вопрос."
        return {"route": "clarify", "clarify_question": question}
    return {"route": "sql"}


def clarifier_node(state: GraphState) -> dict:
    question = state.get("clarify_question") or "Уточните, пожалуйста, ваш вопрос."
    return {"answer": question, "messages": [AIMessage(question)]}


SQL_SYS = """Ты — SQL-аналитик. Есть таблица PostgreSQL `data` со столбцами:
{schema}

Напиши ОДИН SQL-запрос (только SELECT), отвечающий на вопрос пользователя.
Правила:
- обращайся только к таблице `data`;
- не используй CTE/WITH, точку с запятой и любые изменяющие данные команды;
- верни ТОЛЬКО SQL без пояснений и markdown."""


def sql_node(state: GraphState) -> dict:
    llm = get_llm()
    resp = llm.invoke([
        SystemMessage(SQL_SYS.format(schema=state["schema"])),
        HumanMessage(_last_question(state)),
    ])
    sql = clean_sql(resp.content)
    if not is_safe_select(sql):
        return {"sql": sql, "error": "Сгенерирован небезопасный или некорректный SQL"}
    try:
        rows = run_sql(state["projection"], sql)
        return {"sql": sql, "rows": rows}
    except Exception as exc:  # noqa: BLE001
        return {"sql": sql, "error": f"Ошибка выполнения SQL: {exc}"}


SYNTH_SYS = """Ты — аналитик. По вопросу пользователя и результату SQL дай короткий,
понятный ответ на русском. Приводи конкретные числа. Без лишних предисловий.
Не вставляй ссылки и markdown-картинки — график (если нужен) формируется отдельно."""


def synthesizer_node(state: GraphState) -> dict:
    if state.get("error"):
        answer = f"Не удалось ответить на вопрос: {state['error']}"
        return {"answer": answer, "messages": [AIMessage(answer)]}

    llm = get_llm()
    resp = llm.invoke([
        SystemMessage(SYNTH_SYS),
        HumanMessage(
            f"Вопрос: {_last_question(state)}\n"
            f"SQL: {state.get('sql')}\n"
            f"Результат (JSON): {state.get('rows')}"
        ),
    ])
    answer = resp.content.strip()
    return {"answer": answer, "messages": [AIMessage(answer)]}


PLOT_KEYWORDS = ("график", "диаграм", "построй", "визуализ", "гистограм", "chart", "plot")


def _wants_plot(state: GraphState) -> bool:
    """Эвристика: просил ли пользователь построить график."""
    return any(kw in _last_question(state).lower() for kw in PLOT_KEYWORDS)


def plot_node(state: GraphState) -> dict:
    """Инструмент plot_tool: строит график по результату SQL → base64-PNG."""
    img = render_bar_chart(state.get("rows") or [], title=_last_question(state))
    return {"plot": img} if img else {}


# --- Сборка графа -----------------------------------------------------------

def _route_decision(state: GraphState) -> str:
    return state.get("route", "sql")


def _after_sql(state: GraphState) -> str:
    """После sql_agent: если просили график и есть данные — рисуем, иначе сразу синтез."""
    if state.get("error") or not state.get("rows"):
        return "synthesizer"
    return "plot_tool" if _wants_plot(state) else "synthesizer"


def build_builder() -> StateGraph:
    """Собирает граф (без компиляции). Используется и приложением, и Studio."""
    g = StateGraph(GraphState)
    g.add_node("prepare", prepare_node)
    g.add_node("router", router_node)
    g.add_node("clarifier", clarifier_node)
    g.add_node("sql_agent", sql_node)
    g.add_node("plot_tool", plot_node)
    g.add_node("synthesizer", synthesizer_node)

    g.set_entry_point("prepare")
    g.add_edge("prepare", "router")
    g.add_conditional_edges(
        "router",
        _route_decision,
        {"sql": "sql_agent", "clarify": "clarifier"},
    )
    g.add_conditional_edges(
        "sql_agent",
        _after_sql,
        {"plot_tool": "plot_tool", "synthesizer": "synthesizer"},
    )
    g.add_edge("plot_tool", "synthesizer")
    g.add_edge("synthesizer", END)
    g.add_edge("clarifier", END)
    return g


# Для FastAPI: компилируем с MemorySaver (память сессий по session_id).
graph = build_builder().compile(checkpointer=MemorySaver())

# Для LangGraph Studio / `langgraph dev`: отдаём НЕскомпилированный граф —
# dev-сервер сам добавит персистентность. Свой checkpointer тут не нужен.
studio_graph = build_builder()
