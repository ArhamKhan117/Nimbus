# Nimbus licence service.
#
# One process, one small database, no workers. `python:3.13-slim` rather than `alpine` because
# `cryptography` ships manylinux wheels and musl would mean compiling Rust in the image for no gain.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SQLITE_PATH=/data/nimbus.db

WORKDIR /srv

# Requirements first so a code change does not reinstall the dependency layer.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY web ./web

# The database lives on a mounted volume. Without one, SQLite is inside the container filesystem and
# every deploy wipes the subscriber list -- the single worst way to lose this service's data.
VOLUME ["/data"]

# Runs as a non-root user, which also means /data has to be writable by it.
RUN useradd --create-home --uid 10001 nimbus && mkdir -p /data && chown nimbus:nimbus /data
USER nimbus

EXPOSE 8080

# One worker. Sessions are stateless and the database is SQLite -- multiple workers on one SQLite
# file is how you get "database is locked" under no load at all. Scale by moving to Postgres first.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
