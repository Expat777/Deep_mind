# DataMind — фронтенд (Streamlit)

Тонкий клиент к API DataMind. Запускается на **Streamlit Community Cloud**
и ходит к удалённому API (FastAPI на Selectel) по сети.

## Деплой на Streamlit Community Cloud

1. Зайди на **share.streamlit.io**, войди через GitHub.
2. **Create app → Deploy a public app from GitHub**.
3. Заполни:
   - **Repository:** `GDV-prog/Deep_mind`
   - **Branch:** `GDV-prog`
   - **Main file path:** `frontend/streamlit_app.py`
4. (Опционально) **Advanced settings → Secrets** — задай адрес API:
   ```toml
   API_URL = "http://161.104.48.96:8000"
   ```
   Если не задавать — используется значение по умолчанию из кода (его всегда можно
   поменять в поле «API URL» в сайдбаре уже в работающем приложении).
5. **Deploy**. Через минуту получишь публичный URL вида `https://<app>.streamlit.app`.

Зависимости ставятся из [requirements.txt](requirements.txt) в этой папке.

## Локальный запуск (для разработки)

```bash
pip install -r frontend/requirements.txt
streamlit run frontend/streamlit_app.py
```

## Возможности

- 📤 Загрузка CSV (→ БД + Selectel S3)
- 📊 Просмотр датасетов, строк и presigned-ссылок на скачивание
- 💬 Вопросы агенту с историей диалога (clarifier), SQL, таблицей и графиками (`plot`)
