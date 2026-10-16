# WB SERP Monitor - one image serves three roles (init-db / collector / web); compose picks the role by `command`.
# There is deliberately no "# syntax=" line: it would pull docker/dockerfile from Docker Hub at build time.
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Europe/Moscow \
    WBMON_DB_PATH=/data/wbmon.sqlite3

# Same uid/gid in every role: SQLite WAL needs every process to create/open -wal/-shm in /data.
# The home directory is writable because Streamlit writes into ~/.streamlit.
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --create-home --home-dir /home/app --shell /usr/sbin/nologin app \
 && mkdir -p /data \
 && chown app:app /data

WORKDIR /app
# .dockerignore is an allowlist (pyproject.toml, src, .streamlit), so this copies only what the image needs.
COPY . /app

# Mirror hook for hosts that cannot reach pypi.org (pip reads PIP_INDEX_URL from the environment).
# A build ARG is visible in `docker history`: pass a mirror URL WITHOUT credentials.
ARG PIP_INDEX_URL=https://pypi.org/simple
# The smoke checks fail the build, not the 3 a.m. run, when package data or tz data is missing.
RUN pip install --no-cache-dir ".[web]" \
 && wbmon --help > /dev/null \
 && python -c "from wbmon.queries import load_bundled; assert len(load_bundled()) == 70" \
 && python -c "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Moscow')"

USER app
EXPOSE 8501
# Exec form: PID 1 is the Python process and receives SIGTERM. Healthchecks live in compose.yaml.
CMD ["wbmon", "scheduler"]
