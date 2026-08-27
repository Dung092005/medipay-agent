# syntax=docker/dockerfile:1
# ---- Stage 1: Build ----
FROM python:3.11-slim-bookworm AS builder

WORKDIR /app
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

COPY requirements/runtime.lock .
RUN python -m venv "${VIRTUAL_ENV}" \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --require-hashes -r runtime.lock \
    && rm -rf "${VIRTUAL_ENV}/bin"/pip* "${VIRTUAL_ENV}/bin"/easy_install* \
        "${VIRTUAL_ENV}/lib/python3.11/site-packages"/pip* \
        "${VIRTUAL_ENV}/lib/python3.11/site-packages"/setuptools* \
        "${VIRTUAL_ENV}/lib/python3.11/site-packages"/wheel*

# ---- Stage 2: Production ----
# Distroless has no shell/package manager and currently carries no Scout CVEs;
# the builder remains a normal Python image for deterministic wheel installs.
FROM gcr.io/distroless/python3-debian12:nonroot

WORKDIR /app
ENV PYTHONPATH="/opt/venv/lib/python3.11/site-packages" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

COPY --from=builder --chown=65532:65532 \
    /opt/venv/lib/python3.11/site-packages /opt/venv/lib/python3.11/site-packages

# Copy only online runtime code; corpus/eval/frontend are separate artifacts.
COPY --chown=65532:65532 src ./src

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD ["/usr/bin/python3.11", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '8000') + '/health', timeout=5)"]

# Supabase schema is applied separately through Supabase SQL migrations.

ENTRYPOINT ["/usr/bin/python3.11"]
CMD ["-m", "src.runtime_entrypoint"]
