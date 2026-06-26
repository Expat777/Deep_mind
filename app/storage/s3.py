"""Клиент Selectel Object Storage (S3-совместимый API через boto3)."""

import logging
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings

logger = logging.getLogger("datamind.s3")


@lru_cache(maxsize=1)
def get_s3_client():
    """boto3-клиент Selectel S3. Кэшируется (создаётся один раз)."""
    return boto3.client(
        "s3",
        endpoint_url=settings.SELECTEL_ENDPOINT,
        region_name=settings.SELECTEL_REGION,
        aws_access_key_id=settings.SELECTEL_ACCESS_KEY,
        aws_secret_access_key=settings.SELECTEL_SECRET_KEY,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
        ),
    )


def upload_bytes(key: str, data: bytes, content_type: str = "text/csv") -> None:
    """Загружает байты в бакет под ключом key."""
    get_s3_client().put_object(
        Bucket=settings.SELECTEL_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
    )


def object_exists(key: str) -> bool:
    """Проверяет наличие объекта в бакете (head_object)."""
    try:
        get_s3_client().head_object(Bucket=settings.SELECTEL_BUCKET, Key=key)
        return True
    except ClientError:
        return False


def delete_object(key: str) -> None:
    """Удаляет объект из бакета."""
    get_s3_client().delete_object(Bucket=settings.SELECTEL_BUCKET, Key=key)


def generate_presigned_url(key: str, expires_in: int = 900) -> str:
    """Presigned URL на скачивание (по умолчанию 15 минут)."""
    return get_s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.SELECTEL_BUCKET, "Key": key},
        ExpiresIn=expires_in,
    )


def verify_uploaded(key: str) -> None:
    """Фоновая проверка: появился ли файл в S3 после загрузки. Пишет в лог."""
    if object_exists(key):
        logger.info("S3 OK: объект '%s' подтверждён в бакете '%s'", key, settings.SELECTEL_BUCKET)
    else:
        logger.warning("S3 MISS: объект '%s' не найден в бакете '%s'", key, settings.SELECTEL_BUCKET)
