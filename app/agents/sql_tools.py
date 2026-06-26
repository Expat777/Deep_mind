"""Инструменты SQL-агента: вывод схемы из JSONB и безопасное выполнение SELECT.

Данные хранятся в `dataset_rows.row_data` (JSONB), а не отдельными колонками.
Чтобы LLM мог писать обычный SQL, мы:
  1) выводим «виртуальную» схему (имена столбцов + типы) из строк датасета;
  2) оборачиваем сгенерированный SELECT в CTE `data`, который проецирует JSONB
     в типизированные колонки. LLM пишет `... FROM data`, ничего не зная про JSONB.
"""

import datetime
import re
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.database import engine
from app.db.models import DatasetRow

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Запрещённые ключевые слова — выполняем только read-only SELECT.
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|merge)\b",
    re.IGNORECASE,
)


def _infer_type(values: list) -> str:
    """Определяет тип столбца по выборке непустых значений."""
    if not values:
        return "text"
    if all(not isinstance(v, bool) and isinstance(v, (int, float)) for v in values):
        return "numeric"
    if all(isinstance(v, str) and DATE_RE.match(v) for v in values):
        return "date"
    return "text"


def build_schema(db: Session, dataset_id: int) -> tuple[str, str]:
    """Возвращает (текстовое описание схемы для LLM, SQL-проекцию CTE `data`)."""
    rows = db.scalars(
        select(DatasetRow.row_data)
        .where(DatasetRow.dataset_id == dataset_id)
        .order_by(DatasetRow.row_index)
        .limit(50)
    ).all()
    if not rows:
        raise ValueError("В датасете нет строк")

    columns = list(rows[0].keys())
    selects, descr = [], []
    for col in columns:
        values = [r[col] for r in rows if r.get(col) is not None]
        col_type = _infer_type(values)

        key = col.replace("'", "''")          # экранируем для строкового литерала
        alias = '"' + col.replace('"', '""') + '"'
        if col_type == "numeric":
            expr = f"(row_data->>'{key}')::numeric"
        elif col_type == "date":
            expr = f"(row_data->>'{key}')::date"
        else:
            expr = f"row_data->>'{key}'"

        selects.append(f"{expr} AS {alias}")
        descr.append(f"- {col} ({col_type})")

    projection = (
        f"SELECT {', '.join(selects)} "
        f"FROM dataset_rows WHERE dataset_id = {int(dataset_id)}"
    )
    schema_text = "\n".join(descr)
    return schema_text, projection


def is_safe_select(sql: str) -> bool:
    """Пропускает только одиночный SELECT без изменяющих данные конструкций."""
    s = sql.strip().rstrip(";").strip()
    if ";" in s:                      # несколько стейтментов
        return False
    if not s.lower().startswith("select"):
        return False
    return _FORBIDDEN.search(s) is None


def run_sql(projection: str, llm_sql: str, limit: int = 200) -> list[dict]:
    """Выполняет SELECT агента поверх CTE `data`, отдаёт до `limit` строк."""
    full_sql = f"WITH data AS (\n{projection}\n)\n{llm_sql.rstrip(';')}"
    with engine.connect() as conn:
        result = conn.execute(text(full_sql))
        keys = list(result.keys())
        return [dict(zip(keys, row)) for row in result.fetchmany(limit)]


def json_safe(value):
    """Приводит результат SQL к JSON-сериализуемому виду (date/Decimal и т.д.)."""
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return value
