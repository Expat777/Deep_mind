"""Клиент Selectel Object Storage (S3-совместимый API через boto3).

Selectel S3 работает через стандартный boto3 — отличается только endpoint_url.
Если ключи не заданы в .env, хранилище считается выключенным: загрузка CSV
по-прежнему работает (данные пишутся в БД), но файл в облако не уходит.
"""

import logging
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger("datamind.s3")


def is_s3_enabled() -> bool:
    """S3 настроен, только если заданы оба ключа и имя бакета."""
    return bool(
        settings.SELECTEL_ACCESS_KEY
        and settings.SELECTEL_SECRET_KEY
        and settings.SELECTEL_BUCKET
    )


@lru_cache(maxsize=1)
def get_s3_client():
    """Ленивый boto3-клиент (создаётся один раз)."""
    return boto3.client(
        "s3",
        endpoint_url=settings.SELECTEL_ENDPOINT,
        region_name=settings.SELECTEL_REGION,
        aws_access_key_id=settings.SELECTEL_ACCESS_KEY,
        aws_secret_access_key=settings.SELECTEL_SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )


def upload_bytes(key: str, data: bytes, content_type: str = "text/csv") -> None:
    """Кладёт байты в бакет под ключом key. Бросает ClientError при ошибке."""
    get_s3_client().put_object(
        Bucket=settings.SELECTEL_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    logger.info("Загружен объект s3://%s/%s (%d байт)", settings.SELECTEL_BUCKET, key, len(data))


def generate_presigned_url(key: str, expires: int = 900) -> str:
    """Presigned URL на скачивание (по умолчанию 15 минут = 900 секунд)."""
    return get_s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.SELECTEL_BUCKET, "Key": key},
        ExpiresIn=expires,
    )


def object_exists(key: str) -> bool:
    """Проверяет наличие объекта через head_object."""
    try:
        get_s3_client().head_object(Bucket=settings.SELECTEL_BUCKET, Key=key)
        return True
    except ClientError:
        return False


def verify_upload(key: str) -> None:
    """Фоновая проверка: убеждаемся, что файл реально появился в S3, и логируем итог."""
    if object_exists(key):
        logger.info("Проверка S3: объект %s на месте ✓", key)
    else:
        logger.error("Проверка S3: объект %s НЕ найден после загрузки ✗", key)
