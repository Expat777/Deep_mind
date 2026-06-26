import json
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Dataset, DatasetRow
from app.schemas import DatasetOut, RowOut, RowsPage

router = APIRouter(prefix="/datasets", tags=["datasets"])

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@router.post("/upload", response_model=DatasetOut, status_code=201)
async def upload_dataset(file: UploadFile, db: Session = Depends(get_db)):
    """Принимает CSV, парсит и сохраняет строки в БД (как JSONB)."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Ожидается файл с расширением .csv")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Файл пустой")

    try:
        df = pd.read_csv(BytesIO(raw))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Не удалось распарсить CSV: {exc}")

    if df.empty:
        raise HTTPException(status_code=400, detail="В CSV нет строк данных")

    # Сохраняем исходный файл на диск (в Части 3 заменим на S3).
    stem = Path(file.filename).stem
    saved_name = f"{stem}_{datetime.utcnow():%Y%m%d%H%M%S}.csv"
    file_path = UPLOAD_DIR / saved_name
    file_path.write_bytes(raw)

    # to_json -> json.loads даёт нативные python-типы (int/float/None), пригодные для JSONB.
    records = json.loads(df.to_json(orient="records"))

    dataset = Dataset(name=file.filename, file_path=str(file_path), row_count=len(records))
    db.add(dataset)
    db.flush()  # получаем dataset.id

    db.add_all(
        DatasetRow(dataset_id=dataset.id, row_data=row, row_index=i)
        for i, row in enumerate(records)
    )
    db.commit()
    db.refresh(dataset)
    return dataset


@router.get("", response_model=list[DatasetOut])
def list_datasets(db: Session = Depends(get_db)):
    """Список загруженных датасетов."""
    return db.scalars(select(Dataset).order_by(Dataset.created_at.desc())).all()


@router.get("/{dataset_id}/rows", response_model=RowsPage)
def get_rows(
    dataset_id: int,
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Пагинированный просмотр строк датасета."""
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Датасет не найден")

    total = db.scalar(
        select(func.count()).select_from(DatasetRow).where(DatasetRow.dataset_id == dataset_id)
    )
    rows = db.scalars(
        select(DatasetRow)
        .where(DatasetRow.dataset_id == dataset_id)
        .order_by(DatasetRow.row_index)
        .limit(limit)
        .offset(offset)
    ).all()

    return RowsPage(
        dataset_id=dataset_id,
        total=total or 0,
        limit=limit,
        offset=offset,
        rows=[RowOut(row_index=r.row_index, row_data=r.row_data) for r in rows],
    )
