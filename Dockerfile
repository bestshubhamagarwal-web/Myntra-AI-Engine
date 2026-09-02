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
    LOCAL_STORE_PATH=/data/local_store.pkl \
    RES_OPTIONS="ndots:0 timeout:2 attempts:2"

WORKDIR /app

# Debian's default hosts line is `files mdns4_minimal [NOTFOUND=return] dns`.
# That never queries DNS for single-label Render hosts like dpg-xxxxx-a.
# ca-certificates: public-hostname TLS. Query API only — do not pip-install PyTorch.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
    && sed -i 's/^hosts:.*/hosts: files dns/' /etc/nsswitch.conf \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md requirements-api.txt ./
COPY src ./src
COPY migrations ./migrations
COPY prompts ./prompts

RUN pip install --upgrade pip \
    && pip install -r requirements-api.txt \
    && pip install --no-deps -e .

EXPOSE 8000

# Render injects PORT. --migrate applies SQL after Postgres attaches.
CMD ["python", "-m", "src.api", "--migrate", "--host", "0.0.0.0"]
