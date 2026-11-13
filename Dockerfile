# syntax=docker/dockerfile:1
FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
# torch только для CPU — образ меньше; для GPU уберите --extra-index-url
RUN --mount=type=cache,target=/root/.cache/pip pip install --timeout 120 --retries 5 --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
COPY app ./app
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini ./
RUN useradd -m glass && mkdir -p /app/data /app/logs && chown -R glass /app
USER glass
EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
