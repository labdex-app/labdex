# ---- web UI ---------------------------------------------------------------
# Built once on the build machine's own platform: the result is the same files for every
# architecture, so an arm64 image doesn't build the UI under emulation.
FROM --platform=$BUILDPLATFORM node:22.12-alpine AS ui
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# vite writes to ../backend/labdex/static
RUN mkdir -p /src/backend/labdex && npm run build

# ---- app --------------------------------------------------------------------
FROM python:3.12-slim
# what the GHCR package page and `docker inspect` show
LABEL org.opencontainers.image.title="labdex" \
      org.opencontainers.image.description="A small homelab inventory with zones, naming rules, IPAM, plans you approve and plugins." \
      org.opencontainers.image.source="https://github.com/labdex-app/labdex" \
      org.opencontainers.image.documentation="https://labdex-app.github.io/labdex/" \
      org.opencontainers.image.licenses="AGPL-3.0-only"
# nmap for service discovery (TCP connect scans, no root); git for the YAML mirror.
# ansible-core is optional: docker build --build-arg ANSIBLE=1 .
ARG ANSIBLE=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LABDEX_DATA=/data \
    LABDEX_CONTAINER=1 \
    HOME=/tmp
WORKDIR /app
# System packages and Python dependencies first: they change rarely, so this layer comes
# from the build cache unless pyproject.toml (or ANSIBLE) changes.
COPY backend/pyproject.toml ./
RUN apt-get update && apt-get install -y --no-install-recommends nmap git openssh-client \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p labdex && echo '__version__ = "0"' > labdex/__init__.py \
 && pip install --no-cache-dir ".[server,postgres,uptimekuma]" && pip uninstall -y labdex \
 && if [ "$ANSIBLE" = "1" ]; then pip install --no-cache-dir "ansible-core>=2.17"; fi \
 && rm -rf labdex build *.egg-info \
 && useradd --system --uid 10001 --home-dir /data labdex \
 && mkdir -p /data/plugins && chown -R labdex /data
# then labdex itself (quick: no dependencies to fetch)
COPY backend/labdex ./labdex
COPY CHANGELOG.md ./labdex/CHANGELOG.md
COPY --from=ui /src/backend/labdex/static ./labdex/static
RUN pip install --no-cache-dir --no-deps . && rm -rf build *.egg-info
COPY examples /app/examples

USER labdex
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')" || exit 1
# One process with threads: SQLite likes a single writer.
CMD ["gunicorn", "--workers", "1", "--threads", "16", "--worker-tmp-dir", "/tmp", \
     "--bind", "0.0.0.0:8080", "--access-logfile", "-", "labdex:create_app()"]
