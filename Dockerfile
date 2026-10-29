# syntax=docker/dockerfile:1.7
# Multi-arch (linux/amd64 + linux/arm64) image for the AgentGrid API.
# Build:  docker buildx build --platform linux/amd64,linux/arm64 -t agentgrid-api .

# ---------- builder: install wheels into an isolated venv ----------
FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install -r requirements.txt

# ---------- runtime: slim, non-root ----------
FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_ENV=production \
    HOST=0.0.0.0 \
    PORT=8000 \
    WEB_CONCURRENCY=1

RUN groupadd --system app && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app
WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app alembic.ini ./
COPY --chown=app:app migrations ./migrations
COPY --chown=app:app app ./app
RUN mkdir -p /app/data /app/.cache && chown -R app:app /app/data /app/.cache

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8000'), timeout=4)" || exit 1

# RUN_MIGRATIONS=true makes the server apply `alembic upgrade head` on boot.
CMD ["python", "-m", "app.server"]
