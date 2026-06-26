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
from app.agents.sql_tools import is_safe_select, run_sql


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
понятный ответ на русском. Приводи конкретные числа. Без лишних предисловий."""


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


# --- Сборка графа -----------------------------------------------------------

def _route_decision(state: GraphState) -> str:
    return state.get("route", "sql")


def build_graph():
    g = StateGraph(GraphState)
    g.add_node("router", router_node)
    g.add_node("clarifier", clarifier_node)
    g.add_node("sql_agent", sql_node)
    g.add_node("synthesizer", synthesizer_node)

    g.set_entry_point("router")
    g.add_conditional_edges(
        "router",
        _route_decision,
        {"sql": "sql_agent", "clarify": "clarifier"},
    )
    g.add_edge("sql_agent", "synthesizer")
    g.add_edge("synthesizer", END)
    g.add_edge("clarifier", END)

    return g.compile(checkpointer=MemorySaver())


# Компилируем один раз при импорте; checkpointer держит состояние сессий в памяти.
graph = build_graph()
