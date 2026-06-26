import json
import re

from langchain_openai import ChatOpenAI

from app.config import settings


def get_llm(temperature: float = 0.0) -> ChatOpenAI:
    """LLM поверх DeepSeek (OpenAI-совместимый API)."""
    if not settings.DEEPSEEK_API_KEY:
        raise RuntimeError("DEEPSEEK_API_KEY не задан в .env")
    return ChatOpenAI(
        model=settings.DEEPSEEK_MODEL,
        api_key=settings.DEEPSEEK_API_KEY,
        base_url=settings.DEEPSEEK_BASE_URL,
        temperature=temperature,
        timeout=60,
    )


def parse_json(content: str) -> dict:
    """Достаёт первый JSON-объект из ответа LLM (с защитой от ```-обёрток)."""
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


def clean_sql(content: str) -> str:
    """Убирает markdown-обёртки ```sql ... ``` вокруг SQL."""
    content = content.strip()
    fence = re.search(r"```(?:sql)?\s*(.*?)```", content, re.DOTALL | re.IGNORECASE)
    if fence:
        content = fence.group(1)
    return content.strip().rstrip(";").strip()
