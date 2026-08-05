# Multi-stage: Node builds Mini App, Python runs FastAPI + bot

FROM node:22-alpine AS frontend
WORKDIR /frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini ./alembic.ini
COPY backend ./backend
COPY bot ./bot
COPY scripts ./scripts
COPY tests ./tests
COPY pytest.ini ./pytest.ini
COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && sed -i 's/\r$//' /entrypoint.sh

COPY --from=frontend /backend/static ./backend/static

EXPOSE 8000
CMD ["/entrypoint.sh"]
