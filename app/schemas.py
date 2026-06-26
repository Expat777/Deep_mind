from datetime import datetime

from pydantic import BaseModel


class DatasetOut(BaseModel):
    id: int
    name: str
    s3_key: str | None = None
    row_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class DownloadOut(BaseModel):
    dataset_id: int
    s3_key: str
    url: str
    expires_in: int


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
    chart: str | None = None  # PNG-график в base64, если его просили построить
