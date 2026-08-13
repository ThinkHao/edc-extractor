# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    EDC_EXTRACTOR_CONFIG=/app/config.ini \
    EDC_SCHEDULER_DB=/app/data/scheduler.db

WORKDIR /app
RUN useradd --create-home --shell /usr/sbin/nologin edc

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY edc_extractor ./edc_extractor
COPY config.ini.example ./config.ini.example
COPY README.md ./README.md
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

RUN mkdir -p /app/data \
    && chown -R edc:edc /app

USER edc
EXPOSE 8081
VOLUME ["/app/data"]

CMD ["python", "-m", "edc_extractor.web"]
