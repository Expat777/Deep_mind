import json
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Dataset, DatasetRow
from app.schemas import DatasetOut, DownloadOut, RowOut, RowsPage
from app.storage import s3

router = APIRouter(prefix="/datasets", tags=["datasets"])

PRESIGNED_TTL = 900  # 15 минут


@router.post("/upload", response_model=DatasetOut, status_code=201)
async def upload_dataset(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Принимает CSV: парсит строки в БД (JSONB) и кладёт исходный файл в S3."""
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

    # Загружаем исходный файл в Selectel S3 (если хранилище настроено).
    s3_key: str | None = None
    if s3.is_s3_enabled():
        stem = Path(file.filename).stem
        s3_key = f"datasets/{stem}_{datetime.utcnow():%Y%m%d%H%M%S}.csv"
        try:
            s3.upload_bytes(s3_key, raw)
        except (ClientError, BotoCoreError) as exc:
            raise HTTPException(status_code=502, detail=f"Не удалось загрузить файл в S3: {exc}")

    # to_json -> json.loads даёт нативные python-типы (int/float/None), пригодные для JSONB.
    records = json.loads(df.to_json(orient="records"))

    dataset = Dataset(name=file.filename, s3_key=s3_key, row_count=len(records))
    db.add(dataset)
    db.flush()  # получаем dataset.id

    db.add_all(
        DatasetRow(dataset_id=dataset.id, row_data=row, row_index=i)
        for i, row in enumerate(records)
    )
    db.commit()
    db.refresh(dataset)

    # Фоновая задача: убеждаемся, что файл реально появился в S3, и логируем результат.
    if s3_key:
        background_tasks.add_task(s3.verify_upload, s3_key)

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


@router.get("/{dataset_id}/download", response_model=DownloadOut)
def download_dataset(dataset_id: int, db: Session = Depends(get_db)):
    """Presigned URL (15 минут) на скачивание исходного CSV из S3."""
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Датасет не найден")
    if not dataset.s3_key:
        raise HTTPException(status_code=404, detail="Для этого датасета нет файла в S3")

    try:
        url = s3.generate_presigned_url(dataset.s3_key, expires=PRESIGNED_TTL)
    except (ClientError, BotoCoreError) as exc:
        raise HTTPException(status_code=502, detail=f"Не удалось сформировать ссылку: {exc}")

    return DownloadOut(
        dataset_id=dataset_id,
        s3_key=dataset.s3_key,
        url=url,
        expires_in=PRESIGNED_TTL,
    )
