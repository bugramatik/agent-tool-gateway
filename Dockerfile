FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system gateway && adduser --system --ingroup gateway gateway

COPY pyproject.toml README.md ./
COPY gateway ./gateway
COPY catalog ./catalog

RUN pip install --upgrade pip && pip install .

USER gateway

ENV GATEWAY_HOST=0.0.0.0 \
    GATEWAY_PORT=8080 \
    GATEWAY_CATALOG_PATH=/app/catalog/tools.yaml \
    GATEWAY_LOG_LEVEL=info

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"

CMD ["python", "-m", "gateway.main"]
