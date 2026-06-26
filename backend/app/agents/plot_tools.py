"""Построение графика по результату SQL → base64-PNG (инструмент plot_tool)."""

import base64
import io

import matplotlib

matplotlib.use("Agg")  # без GUI, рендер в память
import matplotlib.pyplot as plt  # noqa: E402


def _as_number(value):
    """Пытается привести значение к float, иначе None."""
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pretty(name: str) -> str:
    """Делает имя столбца человекочитаемым: order_count -> «Order count»."""
    return name.replace("_", " ").strip().capitalize()


def render_bar_chart(rows: list[dict], title: str | None = None) -> str | None:
    """Строит столбчатую диаграмму: ось X — первый текстовый столбец,
    ось Y — первый числовой. Возвращает PNG в base64 (без data-URI префикса).

    Если title не задан — формируется по данным («<метрика> по <разрез>»)."""
    if not rows:
        return None

    columns = list(rows[0].keys())
    # X — первый нечисловой столбец, Y — первый числовой.
    x_col = next((c for c in columns if _as_number(rows[0].get(c)) is None), columns[0])
    y_col = next((c for c in columns if _as_number(rows[0].get(c)) is not None), columns[-1])

    labels = [str(r.get(x_col)) for r in rows]
    values = [_as_number(r.get(y_col)) or 0 for r in rows]

    if not title:
        title = f"{_pretty(y_col)} по «{_pretty(x_col)}»"

    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.bar(labels, values, color="#4C72B0")
    ax.set_xlabel(_pretty(x_col))
    ax.set_ylabel(_pretty(y_col))
    ax.set_title(title[:80])
    plt.xticks(rotation=30, ha="right")
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()
