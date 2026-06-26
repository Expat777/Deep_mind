"""DataMind — Streamlit-фронтенд к API (FastAPI на Selectel).

Запускается на Streamlit Community Cloud, ходит к удалённому API по HTTP.
Адрес API берётся из st.secrets["API_URL"] (можно задать в настройках приложения)
или из поля в сайдбаре.
"""

import base64

import pandas as pd
import requests
import streamlit as st

DEFAULT_API_URL = "http://161.104.48.96:8000"

st.set_page_config(page_title="DataMind", page_icon="🤖", layout="wide")


# --- Настройки / адрес API --------------------------------------------------

def _default_api() -> str:
    try:
        return st.secrets["API_URL"]
    except Exception:
        return DEFAULT_API_URL


if "api_url" not in st.session_state:
    st.session_state.api_url = _default_api()

with st.sidebar:
    st.title("🤖 DataMind")
    st.session_state.api_url = st.text_input(
        "API URL", value=st.session_state.api_url
    ).rstrip("/")
    api = st.session_state.api_url

    # Индикатор доступности API
    try:
        if requests.get(f"{api}/health", timeout=5).ok:
            st.success("API доступен")
        else:
            st.error("API вернул ошибку")
    except Exception:
        st.error("Нет связи с API")
    st.caption(f"Swagger: {api}/docs")


# --- Хелперы ----------------------------------------------------------------

def fetch_datasets() -> list[dict]:
    try:
        r = requests.get(f"{api}/datasets", timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:  # noqa: BLE001
        st.error(f"Не удалось получить список датасетов: {exc}")
        return []


def dataset_label(d: dict) -> str:
    return f"#{d['id']} · {d['name']} ({d['row_count']} строк)"


# --- Вкладки ----------------------------------------------------------------

tab_upload, tab_data, tab_chat = st.tabs(
    ["📤 Загрузка", "📊 Датасеты", "💬 Вопрос агенту"]
)

# === Загрузка ===
with tab_upload:
    st.header("Загрузка CSV")
    st.write("Файл сохраняется в БД и в Selectel S3 на стороне API.")
    uploaded = st.file_uploader("Выберите CSV-файл", type=["csv"])
    if uploaded and st.button("Загрузить", type="primary"):
        with st.spinner("Загружаем…"):
            try:
                files = {"file": (uploaded.name, uploaded.getvalue(), "text/csv")}
                r = requests.post(f"{api}/datasets/upload", files=files, timeout=60)
                if r.ok:
                    d = r.json()
                    st.success(f"Готово! Датасет #{d['id']} · {d['row_count']} строк")
                else:
                    st.error(f"Ошибка {r.status_code}: {r.text}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Сбой запроса: {exc}")

# === Датасеты ===
with tab_data:
    st.header("Загруженные датасеты")
    datasets = fetch_datasets()
    if not datasets:
        st.info("Пока нет датасетов — загрузите CSV на вкладке «Загрузка».")
    else:
        st.dataframe(datasets, use_container_width=True, hide_index=True)
        chosen = st.selectbox(
            "Датасет для просмотра", datasets, format_func=dataset_label
        )
        if chosen:
            col1, col2 = st.columns(2)
            limit = col1.number_input("limit", 1, 1000, 50)
            offset = col2.number_input("offset", 0, 10_000, 0, step=10)
            if st.button("Показать строки"):
                try:
                    r = requests.get(
                        f"{api}/datasets/{chosen['id']}/rows",
                        params={"limit": limit, "offset": offset},
                        timeout=30,
                    )
                    r.raise_for_status()
                    page = r.json()
                    rows = [row["row_data"] for row in page["rows"]]
                    st.caption(f"Всего строк: {page['total']}")
                    st.dataframe(pd.DataFrame(rows), use_container_width=True)
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Ошибка: {exc}")

            if st.button("Получить ссылку на скачивание (S3)"):
                try:
                    r = requests.get(
                        f"{api}/datasets/{chosen['id']}/download", timeout=30
                    )
                    r.raise_for_status()
                    info = r.json()
                    st.link_button(
                        "⬇️ Скачать CSV (ссылка на 15 минут)", info["url"]
                    )
                    st.code(info["s3_key"], language="text")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Ошибка: {exc}")

# === Вопрос агенту ===
with tab_chat:
    st.header("Вопрос агенту")
    datasets = fetch_datasets()
    if not datasets:
        st.info("Сначала загрузите датасет.")
    else:
        chosen = st.selectbox(
            "Датасет", datasets, format_func=dataset_label, key="chat_ds"
        )

        # История диалога и session_id (для clarifier)
        st.session_state.setdefault("messages", [])
        st.session_state.setdefault("session_id", "st-" + str(id(st.session_state)))

        c1, c2 = st.columns([3, 1])
        c1.caption(f"session_id: `{st.session_state.session_id}`")
        if c2.button("🗑 Новый диалог"):
            st.session_state.messages = []
            st.session_state.session_id = "st-" + str(pd.Timestamp.now().value)
            st.rerun()

        # Показ истории
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg.get("sql"):
                    with st.expander("SQL"):
                        st.code(msg["sql"], language="sql")
                if msg.get("plot"):
                    st.image(base64.b64decode(msg["plot"]))
                if msg.get("rows"):
                    st.dataframe(pd.DataFrame(msg["rows"]), use_container_width=True)

        # Ввод
        prompt = st.chat_input("Спросите: «Топ-3 категории по выручке» или «построй график …»")
        if prompt:
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            with st.chat_message("assistant"):
                with st.spinner("Думаю…"):
                    try:
                        r = requests.post(
                            f"{api}/query",
                            json={
                                "dataset_id": chosen["id"],
                                "question": prompt,
                                "session_id": st.session_state.session_id,
                            },
                            timeout=120,
                        )
                        if r.ok:
                            d = r.json()
                            st.markdown(d["answer"])
                            assistant_msg = {"role": "assistant", "content": d["answer"]}
                            if d.get("sql"):
                                with st.expander("SQL"):
                                    st.code(d["sql"], language="sql")
                                assistant_msg["sql"] = d["sql"]
                            if d.get("plot"):
                                st.image(base64.b64decode(d["plot"]))
                                assistant_msg["plot"] = d["plot"]
                            if d.get("rows"):
                                st.dataframe(
                                    pd.DataFrame(d["rows"]), use_container_width=True
                                )
                                assistant_msg["rows"] = d["rows"]
                            st.session_state.messages.append(assistant_msg)
                        else:
                            st.error(f"Ошибка {r.status_code}: {r.text}")
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Сбой запроса: {exc}")
