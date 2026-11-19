#!/bin/sh
# Install the labdex agent on this host (Debian, Ubuntu, Proxmox VE, Fedora, RHEL and friends).
#
#   curl -fsSLo install.sh https://lantern.example.net/api/agents/install.sh
#   sudo sh install.sh --url https://lantern.example.net --token <one-time token> [--sha256 <of the agent>]
#        [--allow "patch reboot"] [--root] [--ca-file /path/ca.pem]
#   sudo sh install.sh --uninstall [--purge]
#
# --allow      write capabilities this host accepts (patch, reboot, ansible). Default: none, read-only.
# --root       run the agent as root instead of its own user. The hub must allow root for this host too.
# --uninstall  stop and remove the agent: its service, sudo rules, config and program. Its state
#              (key, certificate, audit log) stays in /var/lib/labdex-agent unless you add --purge,
#              which also removes the labdex-agent user. Revoke the agent in labdex as well.
# Read it before you run it: it creates a user, a sudo rule file, a config and a systemd service.
# It installs packages only when something is missing: python3 (3.11+), its cryptography package
# and sudo. The agent brings its other libraries along, so nothing comes from PyPI.
set -eu

URL="" TOKEN="${LABDEX_AGENT_TOKEN:-}" SHA="" ALLOW="" ROOT=0 CA="" UNINSTALL=0 PURGE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --sha256) SHA="$2"; shift 2 ;;
    --allow) ALLOW="$2"; shift 2 ;;
    --root) ROOT=1; shift ;;
    --ca-file) CA="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" = 0 ] || { echo "run it as root (sudo)" >&2; exit 1; }

UNIT=/etc/systemd/system/labdex-agent.service
if [ "$UNINSTALL" = 1 ]; then
  if [ -f "$UNIT" ]; then
    systemctl disable --now labdex-agent 2>/dev/null || true
  fi
  rm -f "$UNIT" /etc/sudoers.d/labdex-agent /etc/sudoers.d/labdex-agent.new /usr/local/bin/labdex-agent
  rm -rf "$UNIT.d" /etc/labdex-agent /opt/labdex-agent
  systemctl daemon-reload 2>/dev/null || true
  if [ "$PURGE" = 1 ]; then
    rm -rf /var/lib/labdex-agent /var/lib/private/labdex-agent
    if id -u labdex-agent >/dev/null 2>&1; then userdel labdex-agent; fi
    echo "labdex agent removed, with its state and user. Revoke it in labdex too (the host's Agent tab)."
  else
    echo "labdex agent removed; its state is still in /var/lib/labdex-agent (--purge removes it)."
    echo "Revoke it in labdex too (the host's Agent tab)."
  fi
  exit 0
fi

[ -n "$URL" ] && [ -n "$TOKEN" ] || { echo "give --url and --token" >&2; exit 2; }
case "$URL" in https://*) ;; *) echo "the hub URL must be https://" >&2; exit 2 ;; esac
for cap in $ALLOW; do
  case "$cap" in patch|reboot|ansible) ;; *) echo "unknown capability: $cap" >&2; exit 2 ;; esac
done
[ -z "$CA" ] || [ -r "$CA" ] || { echo "can't read --ca-file $CA" >&2; exit 2; }
URL="${URL%/}"

# packages, only what is missing
need=""
command -v python3 >/dev/null || need="$need python3"
python3 -c 'import cryptography' 2>/dev/null || need="$need python3-cryptography"
command -v sudo >/dev/null || need="$need sudo"
if [ -n "$need" ]; then
  echo "installing:$need"
  if command -v apt-get >/dev/null; then
    apt-get install -y -q $need >/dev/null
  elif command -v dnf >/dev/null; then
    dnf install -y -q $need >/dev/null
  else
    echo "please install:$need" >&2; exit 1
  fi
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "needs Python 3.11 or newer" >&2; exit 1; }

# downloads: curl where there is one, else Python (never plain http, never a redirect elsewhere)
fetch() {
  if command -v curl >/dev/null; then
    if [ -n "$CA" ]; then curl -fsS --proto =https --tlsv1.2 --cacert "$CA" -o "$2" "$1"
    else curl -fsS --proto =https --tlsv1.2 -o "$2" "$1"; fi
  else
    python3 -I - "$1" "$2" "$CA" <<'PY'
import ssl, sys, urllib.request
url, out, ca = sys.argv[1:4]
assert url.startswith("https://")
ctx = ssl.create_default_context(cafile=ca or None)
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None
opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPSHandler(context=ctx))
with opener.open(url, timeout=60) as r, open(out, "wb") as f:
    f.write(r.read())
PY
  fi
}

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
fetch "$URL/api/agents/download/labdex-agent.pyz" "$TMP"
if [ -n "$SHA" ]; then
  echo "$SHA  $TMP" | sha256sum -c --quiet - || { echo "the agent doesn't match --sha256" >&2; exit 1; }
fi
install -d -m 0755 /opt/labdex-agent
rm -rf /opt/labdex-agent/venv  # left by an older install script
install -m 0644 "$TMP" /opt/labdex-agent/labdex-agent.pyz
printf '#!/bin/sh\nexec /usr/bin/python3 -I /opt/labdex-agent/labdex-agent.pyz "$@"\n' > /usr/local/bin/labdex-agent
chmod 0755 /usr/local/bin/labdex-agent
AGENT=/usr/local/bin/labdex-agent

if [ "$ROOT" = 1 ]; then
  RUNAS=root
else
  RUNAS=labdex-agent
  id -u labdex-agent >/dev/null 2>&1 || useradd --system --user-group --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin labdex-agent
fi

install -d -m 0755 /etc/labdex-agent
{
  echo "[agent]"
  echo "url = $URL"
  echo "allow = $ALLOW"
  echo "ca_file = $CA"
  echo "state_dir = /var/lib/labdex-agent"
} > /etc/labdex-agent/agent.conf
chmod 0644 /etc/labdex-agent/agent.conf
install -d -m 0700 -o "$RUNAS" -g "$RUNAS" /var/lib/labdex-agent
# an earlier install as the other user: the files must belong to this one
chown -R "$RUNAS:$RUNAS" /var/lib/labdex-agent

# the sudo rules: read-only lines always, write lines only for --allow; checked before they go live
if [ "$RUNAS" != root ]; then
  $AGENT --config /etc/labdex-agent/agent.conf sudoers --user labdex-agent > /etc/sudoers.d/labdex-agent.new
  chmod 0440 /etc/sudoers.d/labdex-agent.new
  visudo -cf /etc/sudoers.d/labdex-agent.new >/dev/null
  mv /etc/sudoers.d/labdex-agent.new /etc/sudoers.d/labdex-agent
else
  rm -f /etc/sudoers.d/labdex-agent
fi

printf '%s\n' "$TOKEN" | runuser -u "$RUNAS" -- $AGENT --config /etc/labdex-agent/agent.conf enrol --token -

fetch "$URL/api/agents/download/labdex-agent.service" "$UNIT"
chmod 0644 "$UNIT"
rm -rf "$UNIT.d"
if [ "$RUNAS" = root ]; then
  install -d "$UNIT.d"
  printf '[Service]\nUser=root\nGroup=root\n' > "$UNIT.d/root.conf"
fi
systemctl daemon-reload
systemctl enable --now labdex-agent
systemctl restart labdex-agent  # a reinstall: start the new program
echo "labdex agent installed and running as $RUNAS; check: systemctl status labdex-agent, labdex-agent health"
echo "remove it again with: sudo sh install.sh --uninstall"
