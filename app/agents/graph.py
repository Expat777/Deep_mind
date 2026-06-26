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

import base64
import io
from typing import Annotated, TypedDict

import matplotlib
matplotlib.use("Agg")  # без GUI, рендерим в PNG
import matplotlib.pyplot as plt  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage  # noqa: E402
from langgraph.checkpoint.memory import MemorySaver  # noqa: E402
from langgraph.graph import END, StateGraph  # noqa: E402
from langgraph.graph.message import add_messages  # noqa: E402

from app.agents.llm import clean_sql, get_llm, parse_json  # noqa: E402
from app.agents.sql_tools import is_safe_select, run_sql  # noqa: E402

# Сколько последних обменов (вопрос+ответ) держим в контексте.
HISTORY_TURNS = 5


class GraphState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    dataset_id: int
    session_id: str
    schema: str          # текстовое описание столбцов для LLM
    projection: str      # SQL-проекция JSONB → CTE `data`
    route: str           # "sql" | "clarify"
    clarify_question: str
    plot: bool           # пользователь просит построить график
    chart_type: str      # "bar" | "line" | "pie"
    sql: str
    rows: list
    answer: str
    chart: str           # PNG-график в base64
    error: str


def _last_question(state: GraphState) -> str:
    for msg in reversed(state["messages"]):
        if isinstance(msg, HumanMessage):
            return msg.content
    return ""


def _history(state: GraphState) -> str:
    """Последние HISTORY_TURNS обменов (= 2*N сообщений) — скользящее окно памяти."""
    msgs = state["messages"][-2 * HISTORY_TURNS:]
    parts = []
    for msg in msgs:
        role = "Пользователь" if isinstance(msg, HumanMessage) else "Система"
        parts.append(f"{role}: {msg.content}")
    return "\n".join(parts)


# --- Узлы -------------------------------------------------------------------

ROUTER_SYS = """Ты — маршрутизатор запросов к таблице данных.
Схема таблицы `data`:
{schema}

Реши, достаточно ли последнего вопроса пользователя (с учётом истории диалога),
чтобы написать к этой таблице корректный SQL. Верни JSON с полями:
- "route": "sql" если вопрос понятен и относится к данным; "clarify" если он
  неоднозначен или не хватает деталей;
- "question": короткий уточняющий вопрос на русском (только при route="clarify");
- "plot": true, если пользователь просит ПОСТРОИТЬ ГРАФИК/диаграмму/визуализацию,
  иначе false;
- "chart_type": "bar" (столбчатая), "line" (линейная) или "pie" (круговая) —
  подходящий тип графика, если plot=true (по умолчанию "bar").

Отвечай ТОЛЬКО JSON."""


def router_node(state: GraphState) -> dict:
    llm = get_llm()
    resp = llm.invoke([
        SystemMessage(ROUTER_SYS.format(schema=state["schema"])),
        HumanMessage(f"История диалога:\n{_history(state)}"),
    ])
    data = parse_json(resp.content)
    plot = bool(data.get("plot"))
    chart_type = data.get("chart_type") if data.get("chart_type") in {"bar", "line", "pie"} else "bar"
    if data.get("route") == "clarify":
        question = data.get("question") or "Уточните, пожалуйста, ваш вопрос."
        return {"route": "clarify", "clarify_question": question, "plot": plot, "chart_type": chart_type}
    return {"route": "sql", "plot": plot, "chart_type": chart_type}


def clarifier_node(state: GraphState) -> dict:
    question = state.get("clarify_question") or "Уточните, пожалуйста, ваш вопрос."
    return {"answer": question, "messages": [AIMessage(question)]}


SQL_SYS = """Ты — SQL-аналитик. Есть таблица PostgreSQL `data` со столбцами:
{schema}

Напиши ОДИН SQL-запрос (только SELECT), отвечающий на вопрос пользователя.
Правила:
- обращайся только к таблице `data`;
- для фильтров по текстовым столбцам используй РЕГИСТРОНЕЗАВИСИМОЕ сравнение
  через ILIKE (например `city ILIKE 'москва'`), потому что формулировка
  пользователя может отличаться по регистру от значений в данных;
- не используй CTE/WITH, точку с запятой и любые изменяющие данные команды;
- верни ТОЛЬКО SQL без пояснений и markdown."""


def sql_node(state: GraphState) -> dict:
    llm = get_llm()
    resp = llm.invoke([
        SystemMessage(SQL_SYS.format(schema=state["schema"])),
        HumanMessage(
            f"Контекст диалога (для разрешения ссылок вроде «а в Питере?»):\n"
            f"{_history(state)}\n\nТекущий вопрос: {_last_question(state)}"
        ),
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


def plot_node(state: GraphState) -> dict:
    """Строит график из результата SQL и возвращает PNG в base64.

    Берёт первые два столбца результата: первый — подписи (X), второй — значения (Y).
    """
    rows = state.get("rows") or []
    if not rows:
        return {}
    cols = list(rows[0].keys())
    if len(cols) < 2:
        return {}

    xcol, ycol = cols[0], cols[1]
    labels = [str(r[xcol]) for r in rows]
    try:
        values = [float(r[ycol]) for r in rows]
    except (TypeError, ValueError):
        return {}  # второй столбец не числовой — график не строим

    chart_type = state.get("chart_type", "bar")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    if chart_type == "line":
        ax.plot(labels, values, marker="o")
    elif chart_type == "pie":
        ax.pie(values, labels=labels, autopct="%1.1f%%")
    else:
        ax.bar(labels, values, color="#4C78A8")

    if chart_type != "pie":
        ax.set_xlabel(xcol)
        ax.set_ylabel(ycol)
        plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    ax.set_title(_last_question(state)[:80])
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
    plt.close(fig)
    return {"chart": base64.b64encode(buf.getvalue()).decode("ascii")}


# --- Сборка графа -----------------------------------------------------------

def _route_decision(state: GraphState) -> str:
    return state.get("route", "sql")


def _plot_decision(state: GraphState) -> str:
    """После синтеза: рисуем график, только если его просили и есть данные."""
    if state.get("plot") and state.get("rows") and not state.get("error"):
        return "plot"
    return "end"


def build_graph():
    g = StateGraph(GraphState)
    g.add_node("router", router_node)
    g.add_node("clarifier", clarifier_node)
    g.add_node("sql_agent", sql_node)
    g.add_node("synthesizer", synthesizer_node)
    g.add_node("plot_agent", plot_node)

    g.set_entry_point("router")
    g.add_conditional_edges(
        "router",
        _route_decision,
        {"sql": "sql_agent", "clarify": "clarifier"},
    )
    g.add_edge("sql_agent", "synthesizer")
    g.add_conditional_edges(
        "synthesizer",
        _plot_decision,
        {"plot": "plot_agent", "end": END},
    )
    g.add_edge("plot_agent", END)
    g.add_edge("clarifier", END)

    return g.compile(checkpointer=MemorySaver())


# Компилируем один раз при импорте; checkpointer держит состояние сессий в памяти.
graph = build_graph()
