import json
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from botocore.exceptions import ClientError
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import Dataset, DatasetRow
from app.schemas import DatasetOut, DownloadOut, RowOut, RowsPage
from app.storage.s3 import generate_presigned_url, upload_bytes, verify_uploaded

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.post(
    "/upload",
    response_model=DatasetOut,
    status_code=201,
    summary="Загрузить CSV",
    responses={
        400: {"description": "Не CSV / пустой файл / не удалось распарсить"},
        502: {"description": "Ошибка загрузки в S3"},
    },
)
async def upload_dataset(
    file: UploadFile,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Принимает CSV-файл, парсит строки в БД (JSONB), кладёт исходник в Selectel S3
    и фоном проверяет (`head_object`), что файл реально появился в бакете."""
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

    # Кладём исходный CSV в Selectel S3 под уникальным ключом.
    stem = Path(file.filename).stem
    s3_key = f"datasets/{stem}_{datetime.utcnow():%Y%m%d%H%M%S}.csv"
    try:
        upload_bytes(s3_key, raw, content_type="text/csv")
    except ClientError as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка загрузки в S3: {exc}")

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

    # Фоновая задача: убедиться, что файл реально появился в бакете (head_object) + лог.
    background_tasks.add_task(verify_uploaded, s3_key)
    return dataset


@router.get("", response_model=list[DatasetOut], summary="Список датасетов")
def list_datasets(db: Session = Depends(get_db)):
    """Возвращает все загруженные датасеты, новые сверху."""
    return db.scalars(select(Dataset).order_by(Dataset.created_at.desc())).all()


@router.get(
    "/{dataset_id}/rows",
    response_model=RowsPage,
    summary="Строки датасета (пагинация)",
    responses={404: {"description": "Датасет не найден"}},
)
def get_rows(
    dataset_id: int,
    limit: int = Query(50, ge=1, le=1000, description="Сколько строк вернуть"),
    offset: int = Query(0, ge=0, description="Смещение"),
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


@router.get(
    "/{dataset_id}/download",
    response_model=DownloadOut,
    summary="Ссылка на скачивание (presigned URL)",
    responses={
        404: {"description": "Датасет не найден"},
        502: {"description": "Ошибка генерации ссылки в S3"},
    },
)
def download_dataset(dataset_id: int, db: Session = Depends(get_db)):
    """Presigned URL (15 минут) на скачивание исходного CSV из S3 — прямая ссылка без авторизации."""
    dataset = db.get(Dataset, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Датасет не найден")
    try:
        url = generate_presigned_url(dataset.s3_key, expires_in=900)
    except ClientError as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка генерации ссылки: {exc}")
    return DownloadOut(dataset_id=dataset_id, s3_key=dataset.s3_key, url=url, expires_in=900)
