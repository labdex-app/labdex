# Install

labdex is one container (or one Python package) with a SQLite database in its data
folder. PostgreSQL is optional.

## Docker

```sh
docker run -d --name labdex -p 8080:8080 -v labdex-data:/data \
  -e LABDEX_COOKIE_SECURE=true ghcr.io/labdex-app/labdex:1
```

Open <http://127.0.0.1:8080>. On an empty database the first visit asks you to create
the administrator (or set `LABDEX_ADMIN_USER` and `LABDEX_ADMIN_PASSWORD`).

The image runs as an unprivileged user with a read-only root filesystem; only `/data`
is written. Put labdex behind your reverse proxy over HTTPS.

!!! tip "Verify the image"
    Release images are signed with cosign (keyless, GitHub Actions OIDC) and carry an
    SBOM. See [Security](../operate/security.md#verifying-a-release).

## Docker Compose

The repository's `docker-compose.yml` starts labdex, and with
`--profile postgres` a PostgreSQL next to it. A commented `worker` service runs jobs
in their own container ([Jobs and the worker](../operate/jobs.md)).

```sh
git clone https://github.com/labdex-app/labdex && cd labdex
docker compose up -d
docker compose exec labdex labdex import --yes /app/examples/homelab.yaml   # optional sample data
```

## Without Docker

```sh
cd frontend && npm ci && npm run build && cd ..   # builds the UI into backend/labdex/static
cd backend && pip install -e ".[server]"          # optional: ".[postgres,uptimekuma]", nmap, ansible-core
labdex user create admin --admin
gunicorn --workers 1 --threads 16 -b 127.0.0.1:8080 "labdex:create_app()"
```

Python 3.11 or newer. `nmap` is needed for the nmap plugin, `ansible-core` for the
Ansible runner and the login banner plugin.
