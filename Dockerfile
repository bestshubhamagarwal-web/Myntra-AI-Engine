FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    API_HOST=0.0.0.0 \
    REQUIRE_POSTGRES=true \
    HF_HOME=/data/models \
    RAW_STORE_PATH=/data/raw \
    REVIEW_DUMP_PATH=/data/review \
    REPORTS_PATH=/data/reports \
    LOCK_PATH=/data/locks \
    LOCAL_STORE_PATH=/data/local_store.pkl

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY migrations ./migrations
COPY prompts ./prompts

RUN pip install --upgrade pip \
    && pip install torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install -e . --extra-index-url https://download.pytorch.org/whl/cpu

EXPOSE 8000

# Render injects PORT. --migrate waits for pgvector then applies SQL.
# API_SHARED_SECRET is required because this binds 0.0.0.0.
CMD ["sh", "-c", "python -m src.cli serve --migrate --host 0.0.0.0"]
