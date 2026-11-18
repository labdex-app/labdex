#!/bin/sh
# Install the labdex agent on this host (Debian, Ubuntu, Proxmox VE, Fedora, RHEL and friends).
#
#   curl -fsSLo install.sh https://lantern.example.net/api/agents/install.sh
#   sudo sh install.sh --url https://lantern.example.net --token <one-time token> [--sha256 <of the agent>]
#        [--allow "patch reboot"] [--root] [--ca-file /path/ca.pem]
#
# --allow   write capabilities this host accepts (patch, reboot, ansible). Default: none, read-only.
# --root    run the agent as root instead of its own user. The hub must allow root for this host too.
# Read it before you run it: it creates a user, a sudo rule file, a config and a systemd service.
set -eu

URL="" TOKEN="${LABDEX_AGENT_TOKEN:-}" SHA="" ALLOW="" ROOT=0 CA=""
while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --sha256) SHA="$2"; shift 2 ;;
    --allow) ALLOW="$2"; shift 2 ;;
    --root) ROOT=1; shift ;;
    --ca-file) CA="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" = 0 ] || { echo "run it as root (sudo)" >&2; exit 1; }
[ -n "$URL" ] && [ -n "$TOKEN" ] || { echo "give --url and --token" >&2; exit 2; }
case "$URL" in https://*) ;; *) echo "the hub URL must be https://" >&2; exit 2 ;; esac
for cap in $ALLOW; do
  case "$cap" in patch|reboot|ansible) ;; *) echo "unknown capability: $cap" >&2; exit 2 ;; esac
done
URL="${URL%/}"
CURL="curl -fsS --proto =https --tlsv1.2"
[ -n "$CA" ] && CURL="$CURL --cacert $CA"

# Python 3.11+ with venv; cryptography and simple-websocket from the distribution where it has them
if command -v apt-get >/dev/null; then
  apt-get install -y -q python3 python3-venv python3-cryptography sudo >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q python3 python3-cryptography sudo >/dev/null
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "needs Python 3.11 or newer" >&2; exit 1; }

install -d -m 0755 /opt/labdex-agent
[ -x /opt/labdex-agent/venv/bin/python ] || python3 -m venv --system-site-packages /opt/labdex-agent/venv
/opt/labdex-agent/venv/bin/python -c 'import cryptography, simple_websocket' 2>/dev/null ||
  /opt/labdex-agent/venv/bin/pip install -q "cryptography>=3.4" "simple-websocket>=1.0"

TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
$CURL -o "$TMP" "$URL/api/agents/download/labdex-agent.pyz"
if [ -n "$SHA" ]; then
  echo "$SHA  $TMP" | sha256sum -c --quiet - || { echo "the agent doesn't match --sha256" >&2; exit 1; }
fi
install -m 0644 "$TMP" /opt/labdex-agent/labdex-agent.pyz
AGENT="/opt/labdex-agent/venv/bin/python /opt/labdex-agent/labdex-agent.pyz"

if [ "$ROOT" = 1 ]; then
  RUNAS=root
else
  RUNAS=labdex-agent
  id -u labdex-agent >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin labdex-agent
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

# the sudo rules: read-only lines always, write lines only for --allow; checked before they go live
if [ "$RUNAS" != root ]; then
  $AGENT --config /etc/labdex-agent/agent.conf sudoers --user labdex-agent > /etc/sudoers.d/labdex-agent.new
  chmod 0440 /etc/sudoers.d/labdex-agent.new
  visudo -cf /etc/sudoers.d/labdex-agent.new >/dev/null
  mv /etc/sudoers.d/labdex-agent.new /etc/sudoers.d/labdex-agent
fi

printf '%s\n' "$TOKEN" | runuser -u "$RUNAS" -- $AGENT --config /etc/labdex-agent/agent.conf enrol --token -

$CURL -o /etc/systemd/system/labdex-agent.service "$URL/api/agents/download/labdex-agent.service"
if [ "$RUNAS" = root ]; then
  install -d /etc/systemd/system/labdex-agent.service.d
  printf '[Service]\nUser=root\nGroup=root\n' > /etc/systemd/system/labdex-agent.service.d/root.conf
fi
systemctl daemon-reload
systemctl enable --now labdex-agent
echo "labdex agent installed and running as $RUNAS; check: systemctl status labdex-agent"
