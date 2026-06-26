from datetime import datetime

from pydantic import BaseModel


class DatasetOut(BaseModel):
    id: int
    name: str
    row_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class RowOut(BaseModel):
    row_index: int
    row_data: dict


class RowsPage(BaseModel):
    dataset_id: int
    total: int
    limit: int
    offset: int
    rows: list[RowOut]


class QueryIn(BaseModel):
    dataset_id: int
    question: str
    session_id: str = "default"


class QueryOut(BaseModel):
    session_id: str
    answer: str
    needs_clarification: bool
    sql: str | None = None
    rows: list[dict] | None = None
