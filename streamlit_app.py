"""Streamlit-фронтенд для DataMind.

Тонкий UI поверх FastAPI: ничего не считает сам, только дёргает REST-эндпоинты
бэкенда через HTTP. Адрес API берётся из переменной окружения API_URL
(по умолчанию http://localhost:8000) — поэтому тот же файл работает и локально,
и на сервере: достаточно поменять API_URL.
"""

import base64
import os
import uuid

import requests
import streamlit as st

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = 120  # /query с LLM может думать долго

st.set_page_config(page_title="DataMind", page_icon="🤖", layout="wide")


# --- helpers ----------------------------------------------------------------

def api_get(path: str, **params):
    r = requests.get(f"{API_URL}{path}", params=params, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def api_post_file(path: str, file) -> dict:
    files = {"file": (file.name, file.getvalue(), "text/csv")}
    r = requests.post(f"{API_URL}{path}", files=files, timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(r.json().get("detail", r.text))
    return r.json()


def api_post_json(path: str, payload: dict) -> dict:
    r = requests.post(f"{API_URL}{path}", json=payload, timeout=TIMEOUT)
    if not r.ok:
        raise RuntimeError(r.json().get("detail", r.text))
    return r.json()


@st.cache_data(ttl=10)
def fetch_datasets():
    return api_get("/datasets")


# --- session state ----------------------------------------------------------

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())[:8]
if "chat" not in st.session_state:
    st.session_state.chat = []  # список (role, text, meta)


# --- sidebar ----------------------------------------------------------------

with st.sidebar:
    st.title("🤖 DataMind")
    st.caption("UI поверх FastAPI + LangGraph + Selectel S3")

    # Health-check бэкенда
    try:
        api_get("/health")
        st.success(f"API доступен: {API_URL}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"API недоступен ({API_URL}): {exc}")
        st.stop()

    st.divider()
    st.subheader("📤 Загрузка CSV")
    up = st.file_uploader("CSV-файл", type=["csv"])
    if up and st.button("Загрузить", use_container_width=True):
        try:
            res = api_post_file("/datasets/upload", up)
            st.success(f"Загружено: id={res['id']}, строк={res['row_count']}")
            if res.get("s3_key"):
                st.caption(f"В S3: {res['s3_key']}")
            else:
                st.warning("Файл не ушёл в S3 (хранилище не настроено)")
            fetch_datasets.clear()
        except Exception as exc:  # noqa: BLE001
            st.error(f"Ошибка загрузки: {exc}")

    st.divider()
    st.caption(f"session_id: `{st.session_state.session_id}`")
    if st.button("🔄 Новый диалог", use_container_width=True):
        st.session_state.session_id = str(uuid.uuid4())[:8]
        st.session_state.chat = []
        st.rerun()


# --- выбор датасета ---------------------------------------------------------

try:
    datasets = fetch_datasets()
except Exception as exc:  # noqa: BLE001
    st.error(f"Не удалось получить список датасетов: {exc}")
    st.stop()

if not datasets:
    st.info("Пока нет датасетов. Загрузите CSV в боковой панели слева.")
    st.stop()

labels = {f"#{d['id']} · {d['name']} ({d['row_count']} строк)": d for d in datasets}
choice = st.selectbox("Датасет", list(labels.keys()))
ds = labels[choice]
dataset_id = ds["id"]


tab_data, tab_chat = st.tabs(["📊 Данные", "💬 Вопросы"])


# --- вкладка: просмотр данных + скачивание -----------------------------------

with tab_data:
    col1, col2 = st.columns([1, 1])
    with col1:
        limit = st.slider("Сколько строк показать", 5, 200, 20)
    with col2:
        st.write("")
        st.write("")
        if st.button("🔗 Получить ссылку на скачивание (S3)"):
            try:
                dl = api_get(f"/datasets/{dataset_id}/download")
                st.success("Presigned URL (действует 15 минут):")
                st.markdown(f"[Скачать {ds['name']}]({dl['url']})")
                st.code(dl["url"], language="text")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Ошибка: {exc}")

    try:
        page = api_get(f"/datasets/{dataset_id}/rows", limit=limit, offset=0)
        st.caption(f"Всего строк: {page['total']}")
        st.dataframe([r["row_data"] for r in page["rows"]], use_container_width=True)
    except Exception as exc:  # noqa: BLE001
        st.error(f"Ошибка чтения строк: {exc}")


# --- вкладка: чат с графом агентов -------------------------------------------

with tab_chat:
    st.caption(
        "Задайте вопрос на естественном языке. Если он неоднозначен — система "
        "переспросит (clarifier), и контекст сохранится в рамках session_id."
    )

    # история диалога
    for role, text, meta in st.session_state.chat:
        with st.chat_message(role):
            st.markdown(text)
            if meta and meta.get("chart"):
                st.image(base64.b64decode(meta["chart"]), use_container_width=True)
            if meta and meta.get("sql"):
                with st.expander("SQL и данные"):
                    st.code(meta["sql"], language="sql")
                    if meta.get("rows"):
                        st.dataframe(meta["rows"], use_container_width=True)

    prompt = st.chat_input("Например: Топ-3 категории по выручке")
    if prompt:
        st.session_state.chat.append(("user", prompt, None))
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Думаю…"):
                try:
                    res = api_post_json(
                        "/query",
                        {
                            "dataset_id": dataset_id,
                            "question": prompt,
                            "session_id": st.session_state.session_id,
                        },
                    )
                    answer = res.get("answer", "(пустой ответ)")
                    if res.get("needs_clarification"):
                        answer = f"❓ {answer}"
                    st.markdown(answer)
                    meta = {
                        "sql": res.get("sql"),
                        "rows": res.get("rows"),
                        "chart": res.get("chart"),
                    }
                    if meta["chart"]:
                        st.image(base64.b64decode(meta["chart"]), use_container_width=True)
                    if meta["sql"]:
                        with st.expander("SQL и данные"):
                            st.code(meta["sql"], language="sql")
                            if meta["rows"]:
                                st.dataframe(meta["rows"], use_container_width=True)
                    st.session_state.chat.append(("assistant", answer, meta))
                except Exception as exc:  # noqa: BLE001
                    err = f"Ошибка: {exc}"
                    st.error(err)
                    st.session_state.chat.append(("assistant", err, None))
