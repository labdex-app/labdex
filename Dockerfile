# ---- web UI ---------------------------------------------------------------
FROM node:22.12-alpine AS ui
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# vite writes to ../backend/labdex/static
RUN mkdir -p /src/backend/labdex && npm run build

# ---- app --------------------------------------------------------------------
FROM python:3.12-slim
# nmap for service discovery (TCP connect scans, no root); git for the YAML mirror.
# ansible-core is optional: docker build --build-arg ANSIBLE=1 .
ARG ANSIBLE=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LABDEX_DATA=/data \
    HOME=/tmp
WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/labdex ./labdex
COPY CHANGELOG.md ./labdex/CHANGELOG.md
COPY --from=ui /src/backend/labdex/static ./labdex/static
RUN apt-get update && apt-get install -y --no-install-recommends nmap git openssh-client \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir ".[server,postgres,uptimekuma]" \
 && if [ "$ANSIBLE" = "1" ]; then pip install --no-cache-dir "ansible-core>=2.17"; fi \
 && useradd --system --uid 10001 --home-dir /data labdex \
 && mkdir -p /data/plugins && chown -R labdex /data
COPY examples /app/examples

USER labdex
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')" || exit 1
# One process with threads: SQLite likes a single writer.
CMD ["gunicorn", "--workers", "1", "--threads", "16", "--worker-tmp-dir", "/tmp", \
     "--bind", "0.0.0.0:8080", "--access-logfile", "-", "labdex:create_app()"]
