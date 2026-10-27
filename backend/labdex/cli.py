"""Command line: `labdex <command>` (or `flask --app labdex <command>`).

    labdex run                         start the web server (development)
    labdex user create NAME [--admin]  create a user (asks for the password)
    labdex user password NAME          set a new password
    labdex token create USER NAME      create an API token (printed once)
    labdex import inventory.yaml       replace the inventory with a YAML file
    labdex export [-o file]            write the inventory as YAML
    labdex lint                        check conventions; exit 1 on errors
    labdex plugins                     list plugins and integrations
    labdex action INTEGRATION ACTION   run an integration action (plans are applied with --apply)
    labdex worker                      run queued jobs and the scheduler (with LABDEX_WORKER=external)
    labdex backup create|check|restore  backups (see README)
"""
from __future__ import annotations

import sys
from datetime import timedelta

import click
from flask import Flask
from flask.cli import FlaskGroup

from . import audit, inventory, lint, security, store
from .models import ApiToken, Integration, Job, User, db, utcnow

CLI = ("cli", "system")


def register(app: Flask) -> None:
    register_backup(app)
    register_plugin_tools(app)
    register_db(app)

    @app.cli.command("import")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--yes", is_flag=True, help="Don't ask before replacing the inventory.")
    def import_cmd(path, yes):
        """Replace the inventory with a YAML file (users and integrations stay)."""
        if not yes:
            click.confirm(f"Replace the whole inventory with {path}?", abort=True)
        try:
            counts = inventory.import_yaml(open(path, encoding="utf-8").read(), commit=False)
        except store.ValidationError as exc:
            for e in exc.errors:
                click.echo(f"  {e['field']}: {e['message']}", err=True)
            raise SystemExit(f"Import failed with {len(exc.errors)} error(s); nothing changed.") from exc
        audit.record("import", "inventory", path, summary=f"imported {counts['networks']} networks, {counts['hosts']} hosts, {counts['aliases']} aliases", actor=CLI)
        db.session.commit()
        click.echo(f"Imported {counts['networks']} networks, {counts['hosts']} hosts, {counts['aliases']} aliases.")

    @app.cli.command("export")
    @click.option("-o", "--output", type=click.Path(dir_okay=False), help="File to write (default: stdout).")
    def export_cmd(output):
        """Write the inventory as YAML."""
        text = inventory.export_yaml()
        if output:
            with open(output, "w", encoding="utf-8") as f:
                f.write(text)
            click.echo(f"Wrote {output}", err=True)
        else:
            click.echo(text, nl=False)

    @app.cli.command("lint")
    @click.option("--warnings-as-errors", is_flag=True)
    def lint_cmd(warnings_as_errors):
        """Check conventions; exits 1 on errors."""
        issues = lint.run()
        for i in issues:
            click.echo(f"{i['severity']:<8} {i['kind']:<8} {i['ref']:<32} {i['message']}")
        s = lint.summary(issues)
        click.echo(f"\n{s['error']} error(s), {s['warning']} warning(s), {s['info']} note(s)")
        if s["error"] or (warnings_as_errors and s["warning"]):
            sys.exit(1)

    # ------------------------------------------------------------------ users
    @app.cli.group("user")
    def user_group():
        """Manage users."""

    @user_group.command("create")
    @click.argument("username")
    @click.option("--admin", is_flag=True)
    @click.password_option()
    def user_create(username, admin, password):
        if len(password) < security.MIN_PASSWORD:
            raise SystemExit(f"password needs at least {security.MIN_PASSWORD} characters")
        if db.session.scalar(db.select(User).where(User.username == username.lower())):
            raise SystemExit("user exists")
        db.session.add(User(username=username.lower(), display_name=username, is_admin=admin,
                            password_hash=security.hash_password(password)))
        audit.record("create", "user", username.lower(), None, {"username": username.lower(), "is_admin": admin}, actor=CLI)
        db.session.commit()
        click.echo(f"Created {'admin ' if admin else ''}{username.lower()}.")

    @user_group.command("password")
    @click.argument("username")
    @click.password_option()
    def user_password(username, password):
        user = db.session.scalar(db.select(User).where(User.username == username.lower()))
        if user is None:
            raise SystemExit("no such user")
        if len(password) < security.MIN_PASSWORD:
            raise SystemExit(f"password needs at least {security.MIN_PASSWORD} characters")
        user.password_hash = security.hash_password(password)
        audit.record("update", "user", user.username, summary="password set on the command line", actor=CLI)
        db.session.commit()
        click.echo("Password updated; existing sessions are signed out.")

    @app.cli.group("token")
    def token_group():
        """Manage API tokens."""

    @token_group.command("create")
    @click.argument("username")
    @click.argument("name")
    @click.option("--read-only", is_flag=True)
    @click.option("--days", type=int, default=0, help="Expire after N days (0 = never).")
    def token_create(username, name, read_only, days):
        user = db.session.scalar(db.select(User).where(User.username == username.lower()))
        if user is None:
            raise SystemExit("no such user")
        full, prefix, hashed = security.new_token()
        db.session.add(ApiToken(user=user, name=name, prefix=prefix, token_hash=hashed, read_only=read_only,
                                expires_at=utcnow() + timedelta(days=days) if days else None))
        audit.record("create", "token", f"{user.username}/{name}", summary="API token created on the command line", actor=CLI)
        db.session.commit()
        click.echo(full)

    # ---------------------------------------------------------------- plugins
    @app.cli.command("plugins")
    def plugins_cmd():
        """List plugins and configured integrations."""
        reg = app.extensions["labdex"]["registry"]
        for s in sorted(reg.specs.values(), key=lambda s: s.id):
            click.echo(f"{s.id:<16} {s.kind:<12} {'builtin' if s.builtin else 'local':<8} {s.error or s.name}")
        for e in reg.errors:
            click.echo(f"ERROR {e['path']}: {e['error']}")
        click.echo("")
        for i in db.session.scalars(db.select(Integration).order_by(Integration.name)):
            click.echo(f"{i.name:<24} {i.plugin_id:<16} {'enabled' if i.enabled else 'disabled'}")

    @app.cli.command("action")
    @click.argument("name")
    @click.argument("action")
    @click.option("--apply", "do_apply", is_flag=True, help="Apply the plan right away.")
    def action_cmd(name, action, do_apply):
        """Run an integration action and wait for it."""
        from .plugins import jobs
        integ = db.session.scalar(db.select(Integration).where(Integration.name == name))
        if integ is None:
            raise SystemExit(f"no integration '{name}'")
        app.config["LABDEX_JOBS_SYNC"] = True
        job = jobs.start(integ, action, created_by="cli")
        db.session.expire_all()
        job = db.session.get(Job, job.id)
        if job.status == "planned":
            for c in job.plan or []:
                click.echo(f"  {c['op']:<7} {c['kind']:<8} {c['name']}  {c.get('summary', '')}")
            if do_apply:
                jobs.apply(job, "cli")
                db.session.expire_all()
                job = db.session.get(Job, job.id)
        click.echo((job.log or "").rstrip())
        click.echo(f"job #{job.id}: {job.status}")
        if job.status == "failed":
            sys.exit(1)


    @app.cli.command("worker")
    @click.option("--slots", default=2, show_default=True, help="Jobs run at the same time.")
    def worker_cmd(slots):
        """Run queued jobs and the scheduler. Set LABDEX_WORKER=external on the web server so it leaves them to this process."""
        from .plugins import jobs
        if app.config.get("LABDEX_WORKER") != "external":
            click.echo("note: LABDEX_WORKER isn't 'external' here; the web server may pick up jobs as well", err=True)
        click.echo(f"labdex worker {jobs.worker_id()}: {slots} slot(s), scheduler {'on' if app.config.get('LABDEX_SCHEDULER') else 'off'}")
        jobs.run_worker(app, slots=slots)


def register_plugin_tools(app: Flask) -> None:
    from pathlib import Path

    @app.cli.group("plugin")
    def plugin_group():
        """Sign and pack plugins (for authors and the marketplace)."""

    @plugin_group.command("keygen")
    @click.argument("private_key", type=click.Path(dir_okay=False))
    def plugin_keygen(private_key):
        """Create an Ed25519 signing key. Keep the private file secret; share the public key."""
        import os
        from .plugins.trust import keygen
        if Path(private_key).exists():
            raise SystemExit(f"{private_key} exists; not overwriting")
        pem, pub = keygen()
        fd = os.open(private_key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
        Path(private_key + ".pub").write_text(pub)
        click.echo(f"Private key: {private_key} (keep it secret)\nPublic key:  {private_key}.pub\n\n{pub}")

    @plugin_group.command("repo")
    @click.argument("source", type=click.Path(file_okay=False, exists=True))
    @click.argument("output", type=click.Path(file_okay=False))
    @click.option("--key", "key_path", type=click.Path(dir_okay=False, exists=True), required=True)
    @click.option("--name", required=True, help="The repository's name, shown when people add it.")
    @click.option("--description", default="")
    def plugin_repo(source, output, key_path, name, description):
        """Build a signed repository: sign each plugin folder in SOURCE, zip it into OUTPUT
        and write OUTPUT/index.json. Publish OUTPUT on any web server."""
        from .plugins.installer import InstallError, build_repository
        try:
            doc = build_repository(Path(source), Path(output), Path(key_path).read_bytes(), name, description)
        except (InstallError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        for p in doc["plugins"]:
            click.echo(f"  {p['id']:<20} {p['version']:<8} {p['url']}")
        click.echo(f"Signed index for '{name}' with key {doc['key_id']}: {Path(output) / 'index.json'}")

    @plugin_group.command("build-index")
    @click.argument("sources", type=click.Path(dir_okay=False, exists=True))
    @click.argument("output", type=click.Path(file_okay=False))
    @click.option("--key", "key_path", type=click.Path(dir_okay=False, exists=True), required=True)
    @click.option("--token", envvar="GITHUB_TOKEN", default="", help="A GitHub token (more API calls per hour); or set GITHUB_TOKEN.")
    def plugin_build_index(sources, output, key_path, token):
        """Build a signed Marketplace index from SOURCES (a YAML list of plugin git repositories
        and other indexes): every signed release becomes a version. Writes OUTPUT/index.json."""
        import yaml
        from .plugins.installer import InstallError, build_index_from_sources
        try:
            doc = build_index_from_sources(yaml.safe_load(Path(sources).read_text()) or {}, Path(output), Path(key_path).read_bytes(), token=token)
        except (InstallError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        for p in doc["plugins"]:
            click.echo(f"  {p['id']:<20} {p['version']:<8} {len(p.get('versions') or [])} version(s)")
        for msg in doc["problems"]:
            click.echo(f"  ! {msg}", err=True)
        click.echo(f"Signed index '{doc['name']}' with key {doc['key_id']}: {Path(output) / 'index.json'}")
        if doc["problems"] and not doc["plugins"]:
            raise SystemExit(1)

    @plugin_group.command("yank")
    @click.argument("output", type=click.Path(file_okay=False, exists=True))
    @click.argument("plugin_id")
    @click.argument("version")
    @click.option("--key", "key_path", type=click.Path(dir_okay=False, exists=True), required=True)
    @click.option("--reason", default="", help="Why it's withdrawn, shown to people who have it. Leave out to un-withdraw.")
    def plugin_yank(output, plugin_id, version, key_path, reason):
        """Withdraw one version in a repository built with `plugin repo` (it stays downloadable;
        installs warn, updates skip it) and re-sign OUTPUT/index.json."""
        from .plugins.installer import InstallError, yank
        try:
            yank(Path(output), Path(key_path).read_bytes(), plugin_id, version, reason)
        except (InstallError, ValueError, OSError) as exc:
            raise SystemExit(str(exc)) from exc
        click.echo(f"{plugin_id} {version}: {'withdrawn: ' + reason if reason else 'offered again'}")

    @plugin_group.command("fetch-repo")
    @click.argument("url")
    @click.argument("output", type=click.Path(file_okay=False))
    @click.option("--key", "key_path", type=click.Path(dir_okay=False, exists=True), required=True,
                  help="Your private key: the published index must be signed with it.")
    def plugin_fetch_repo(url, output, key_path):
        """Download a published repository (index and every archive it lists) into OUTPUT, so the
        next `plugin repo` keeps its older versions. Checks the signature and every checksum."""
        import hashlib
        from urllib.parse import urljoin
        import requests
        from cryptography.hazmat.primitives import serialization
        from .plugins.installer import versions_of
        from .plugins.trust import key_id, load_public, verify_index
        mine = key_id(serialization.load_pem_private_key(Path(key_path).read_bytes(), password=None).public_key())
        try:
            r = requests.get(url, timeout=30)
        except requests.RequestException as exc:
            raise SystemExit(f"{url}: {exc}") from exc
        if r.status_code == 404:
            click.echo("Nothing published yet.")
            return
        r.raise_for_status()
        doc = r.json()
        pub = load_public(str(doc.get("public_key", "")))
        verify_index(doc, pub)
        if key_id(pub) != mine:
            raise SystemExit(f"the published index is signed with {key_id(pub)}, not your key {mine}")
        out = Path(output)
        out.mkdir(parents=True, exist_ok=True)
        n = 0
        for p in doc.get("plugins") or []:
            for v in versions_of(p):
                if not v["url"] or "/" in v["url"] or (out / v["url"]).is_file():
                    continue
                z = requests.get(urljoin(url, v["url"]), timeout=60)
                if z.status_code != 200 or hashlib.sha256(z.content).hexdigest() != v["sha256"]:
                    click.echo(f"  skipped {v['url']} (missing or checksum differs)")
                    continue
                (out / v["url"]).write_bytes(z.content)
                n += 1
        (out / "index.json").write_text(r.text)
        click.echo(f"Fetched the index and {n} archive(s) into {out}")

    @plugin_group.command("sign")
    @click.argument("directory", type=click.Path(file_okay=False, exists=True))
    @click.option("--key", "key_path", type=click.Path(dir_okay=False, exists=True), required=True)
    @click.option("--signer", required=True, help="Your name, shown to people installing it.")
    def plugin_sign(directory, key_path, signer):
        """Write SIGNATURE.json covering every file in the plugin folder."""
        from .plugins.trust import sign
        doc = sign(Path(directory), Path(key_path).read_bytes(), signer)
        click.echo(f"Signed {doc['id']} {doc['version']}: {len(doc['files'])} files, key {doc['key_id']}")

    @plugin_group.command("pack")
    @click.argument("directory", type=click.Path(file_okay=False, exists=True))
    @click.option("-o", "--output", type=click.Path(dir_okay=False), required=True)
    def plugin_pack(directory, output):
        """Zip a plugin folder for upload or a marketplace (prints the sha256 for the index)."""
        import hashlib
        import zipfile
        root = Path(directory)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(root.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts:
                    zf.write(p, Path(root.name) / p.relative_to(root))
        click.echo(f"{output}  sha256 {hashlib.sha256(Path(output).read_bytes()).hexdigest()}")


def register_backup(app: Flask) -> None:
    from . import backup as bk

    @app.cli.group("backup")
    def backup_group():
        """Create, check and restore backups."""

    @backup_group.command("create")
    @click.option("-o", "--output", type=click.Path(dir_okay=False), required=True)
    @click.option("--passphrase", envvar="LABDEX_BACKUP_PASSPHRASE", default="", help="Or set LABDEX_BACKUP_PASSPHRASE.")
    def backup_create(output, passphrase):
        name, data, _ = bk.create(app.config["LABDEX_VERSION"], passphrase)
        with open(output, "wb") as f:
            f.write(data)
        click.echo(f"Wrote {output} ({len(data)} bytes{', encrypted' if passphrase else ', NOT encrypted'}).")

    @backup_group.command("check")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--passphrase", envvar="LABDEX_BACKUP_PASSPHRASE", default="")
    def backup_check(path, passphrase):
        import json
        try:
            files = bk.open_archive(open(path, "rb").read(), passphrase or (click.prompt("Passphrase", hide_input=True) if bk.is_encrypted(open(path, "rb").read(16)) else ""))
        except bk.BackupError as exc:
            raise SystemExit(f"Not OK: {exc}") from exc
        m = json.loads(files["manifest.json"])
        click.echo(f"OK: labdex {m['labdex']}, made {m['created']} on {m['host']}: {', '.join(sorted(files))}")

    @backup_group.command("restore")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--passphrase", envvar="LABDEX_BACKUP_PASSPHRASE", default="")
    @click.option("--inventory-only", is_flag=True, help="Only replace the inventory (keeps users, integrations, history).")
    @click.option("--yes", is_flag=True)
    def backup_restore(path, passphrase, inventory_only, yes):
        """Restore a backup. A full restore replaces the database file: stop the server first."""
        from pathlib import Path
        blob = open(path, "rb").read()
        if bk.is_encrypted(blob) and not passphrase:
            passphrase = click.prompt("Passphrase", hide_input=True)
        try:
            files = bk.open_archive(blob, passphrase)
        except bk.BackupError as exc:
            raise SystemExit(str(exc)) from exc
        if inventory_only or not ({"labdex.db", "database.json"} & set(files)):
            if not yes:
                click.confirm("Replace the inventory with the one in this backup?", abort=True)
            counts = inventory.import_yaml(files["inventory.yaml"].decode(), commit=False)
            audit.record("import", "inventory", Path(path).name, summary=f"restored from backup: {counts['hosts']} hosts", actor=CLI)
            db.session.commit()
            click.echo("Inventory restored.")
            return
        url = app.config["SQLALCHEMY_DATABASE_URI"]
        if not url.startswith("sqlite:///") or "labdex.db" not in files:
            # PostgreSQL (or a backup made on PostgreSQL): load the portable dump into this database
            if not yes:
                click.confirm(f"Stop labdex first. Replace everything in {db.engine.url.render_as_string(hide_password=True)} "
                              "with the database from this backup?", abort=True)
            import json as _json
            from . import dbdump
            try:
                with db.engine.begin() as conn:
                    counts = dbdump.load(conn, _json.loads(files["database.json"]))
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            click.echo(f"Database restored: {counts.get('host', 0)} hosts, {sum(counts.values())} rows.")
            click.echo("Secrets need the same secret.key as when the backup was made.")
            return
        target = Path(url[len("sqlite:///"):])
        if not yes:
            click.confirm(f"Stop labdex first. Replace {target} with the database from this backup?", abort=True)
        db.session.remove()
        db.engine.dispose()
        if target.exists():
            target.rename(target.with_suffix(".db.before-restore"))
        for ext in ("-wal", "-shm"):
            Path(str(target) + ext).unlink(missing_ok=True)
        target.write_bytes(files["labdex.db"])
        target.chmod(0o600)
        click.echo(f"Database restored ({target}; the old one is {target.with_suffix('.db.before-restore').name}).")
        click.echo("Secrets need the same secret.key as when the backup was made.")


def main():
    from . import create_app

    FlaskGroup(create_app=create_app)()


def register_db(app: Flask) -> None:
    @app.cli.group("db")
    def db_group():
        """Schema migrations (Alembic). The server runs 'upgrade' by itself at start-up."""

    @db_group.command("current")
    def db_current():
        """Show the schema revision of the database and the newest one."""
        from . import schema
        from .models import db
        with db.engine.connect() as conn:
            click.echo(f"database: {schema.current(conn) or 'none'}   newest: {schema.head()}   ({db.engine.url.get_backend_name()})")

    @db_group.command("upgrade")
    def db_upgrade():
        """Migrate the database to the newest schema."""
        from . import schema
        for line in schema.upgrade() or ["already up to date"]:
            click.echo(line)

    @db_group.command("copy")
    @click.option("--to", "target", required=True, help="Database URL to copy into, e.g. postgresql://labdex:pw@db/labdex")
    @click.option("--yes", is_flag=True)
    def db_copy(target, yes):
        """Copy this database into another one (SQLite → PostgreSQL or back). The target is migrated first, then emptied."""
        from sqlalchemy import create_engine
        from . import dbdump, schema
        from .models import db
        if target.startswith(("postgres://", "postgresql://")):
            target = "postgresql+psycopg://" + target.split("://", 1)[1]
        dst = create_engine(target)
        if not yes:
            click.confirm(f"Replace everything in {dst.url.render_as_string(hide_password=True)}?", abort=True)
        with db.engine.connect() as conn:
            if schema.current(conn) != schema.head():
                raise SystemExit("migrate this database first: flask --app labdex db upgrade")
            data = dbdump.dump(conn)
        from alembic import command
        with dst.begin() as conn:
            command.upgrade(schema.config(conn), "head")
        with dst.begin() as conn:
            counts = dbdump.load(conn, data)
        dst.dispose()
        click.echo(f"Copied {sum(counts.values())} rows ({counts.get('host', 0)} hosts). Point LABDEX_DB at the new database and restart.")

    @db_group.command("revision")
    @click.option("-m", "--message", required=True)
    def db_revision(message):
        """(Developers) write a new migration from the difference between models and database."""
        from alembic import command
        from . import schema
        from .models import db
        with db.engine.begin() as conn:
            command.revision(schema.config(conn), message=message, autogenerate=True)
