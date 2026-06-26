from datetime import datetime

from pydantic import BaseModel, Field


class DatasetOut(BaseModel):
    id: int = Field(description="ID датасета")
    name: str = Field(description="Исходное имя файла")
    row_count: int = Field(description="Количество строк данных")
    created_at: datetime = Field(description="Время загрузки")

    model_config = {
        "from_attributes": True,
        "json_schema_extra": {
            "example": {
                "id": 1,
                "name": "orders.csv",
                "row_count": 100,
                "created_at": "2024-03-05T10:15:00",
            }
        },
    }


class RowOut(BaseModel):
    row_index: int = Field(description="Порядковый номер строки в файле (с 0)")
    row_data: dict = Field(description="Строка CSV как объект ключ→значение")


class RowsPage(BaseModel):
    dataset_id: int
    total: int = Field(description="Всего строк в датасете")
    limit: int
    offset: int
    rows: list[RowOut]


class DownloadOut(BaseModel):
    dataset_id: int
    s3_key: str = Field(description="Ключ объекта в бакете Selectel")
    url: str = Field(description="Presigned URL на скачивание (действует ограниченное время)")
    expires_in: int = Field(description="Срок жизни ссылки в секундах")

    model_config = {
        "json_schema_extra": {
            "example": {
                "dataset_id": 1,
                "s3_key": "datasets/orders_20240305101500.csv",
                "url": "https://datamind-files.s3.ru-7.storage.selcloud.ru/datasets/orders_20240305101500.csv?X-Amz-Algorithm=...",
                "expires_in": 900,
            }
        }
    }


class QueryIn(BaseModel):
    dataset_id: int = Field(description="ID датасета, к которому задаётся вопрос", examples=[1])
    question: str = Field(
        description="Вопрос на естественном языке",
        examples=["Топ-3 категории по выручке"],
    )
    session_id: str = Field(
        default="default",
        description="ID сессии — для диалога с уточнениями (clarifier) через несколько запросов",
        examples=["abc123"],
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "dataset_id": 1,
                "question": "Какой средний чек по городам?",
                "session_id": "abc123",
            }
        }
    }


class QueryOut(BaseModel):
    session_id: str
    answer: str = Field(description="Готовый ответ на русском языке")
    needs_clarification: bool = Field(
        description="True, если граф ушёл в clarifier и ждёт уточнения в этой сессии"
    )
    sql: str | None = Field(default=None, description="SQL, сгенерированный агентом")
    rows: list[dict] | None = Field(default=None, description="Сырой результат SQL")
    plot: str | None = Field(
        default=None, description="График в виде base64-PNG (если в вопросе просили построить)"
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "session_id": "abc123",
                "answer": "Топ-3 категории по выручке:\n1. Электроника — 1 526 400 ₽ ...",
                "needs_clarification": False,
                "sql": "SELECT category, SUM(total) AS revenue FROM data GROUP BY category ORDER BY revenue DESC LIMIT 3",
                "rows": [{"category": "Электроника", "revenue": 1526400}],
                "plot": None,
            }
        }
    }
