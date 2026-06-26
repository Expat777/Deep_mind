# Общий образ для двух сервисов: api (FastAPI) и streamlit (UI).
# Команда запуска переопределяется в docker-compose для каждого сервиса.
FROM python:3.11-slim

WORKDIR /app

# Зависимости ставим отдельным слоем — кэшируется, пока не менялся requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 8000 — FastAPI, 8501 — Streamlit
EXPOSE 8000 8501
