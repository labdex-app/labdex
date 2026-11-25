#!/bin/sh
# Install the labdex agent on this host (Debian, Ubuntu, Proxmox VE, Fedora, RHEL and friends).
#
#   curl -fsSLo install.sh https://lantern.example.net/api/agents/install.sh
#   sudo sh install.sh --url https://lantern.example.net --token <one-time token> [--sha256 <of the agent>]...
#        [--allow "patch reboot"] [--root] [--ca-file /path/ca.pem] [--python]
#        [--ssh-keys --ssh-users "admin deploy" [--ssh-keys-only]]
#   sudo sh install.sh --uninstall [--purge]
#
# --allow      write capabilities this host accepts (patch, reboot, ansible). Default: none, read-only.
# --root       run the agent as root instead of its own user. The hub must allow root for this host too.
# --python     install the Python agent instead of the Go one. By default it installs the Go agent:
#              one static binary, no Python needed for the agent itself. --sha256 may be given once
#              per architecture; the binary for this host's must match one of them. Ansible jobs
#              still need Python on the host. (--go is the default and still accepted.)
# --ssh-keys   let labdex manage SSH login keys (Go agent; adds ssh_keys to --allow). labdex's keys
#              go into root-owned /etc/ssh/labdex/<user>.keys and <user>.break-glass, never into
#              ~/.ssh/authorized_keys; an sshd drop-in (05-labdex-keys.conf) adds them to
#              AuthorizedKeysFile, checked with sshd -t before sshd reloads.
# --ssh-users  the only users labdex may manage keys for (ssh_users in agent.conf). Required with
#              --ssh-keys. Give root only if labdex should manage root's keys.
# --ssh-keys-only  sshd reads only labdex's files, not ~/.ssh/authorized_keys: labdex decides who
#              logs in. Set a break-glass key in labdex and apply it before you use this.
# --uninstall  stop and remove the agent: its service, sudo rules, config and program. Its state
#              (key, certificate, audit log) stays in /var/lib/labdex-agent unless you add --purge,
#              which also removes the labdex-agent user. Revoke the agent in labdex as well.
# Read it before you run it: it creates a user, a sudo rule file, a config and a systemd service.
# It installs packages only when something is missing: sudo (with --python also python3 3.11+).
# The Python agent runs from its own venv, /opt/labdex-agent/venv, with cryptography from PyPI. Where
# PyPI or the venv module's pip can't be had, the venv uses the distribution's python3-cryptography
# instead. The agent brings its other libraries along.
set -eu

URL="" TOKEN="${LABDEX_AGENT_TOKEN:-}" SHA="" ALLOW="" ROOT=0 CA="" UNINSTALL=0 PURGE=0 GO=1
SSHKEYS=0 SSHUSERS="" SSHONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="$2"; shift 2 ;;
    --token) TOKEN="$2"; shift 2 ;;
    --sha256) SHA="$SHA $2"; shift 2 ;;
    --allow) ALLOW="$2"; shift 2 ;;
    --root) ROOT=1; shift ;;
    --go) GO=1; shift ;;
    --python) GO=0; shift ;;
    --ca-file) CA="$2"; shift 2 ;;
    --ssh-keys) SSHKEYS=1; shift ;;
    --ssh-users) SSHUSERS="$2"; shift 2 ;;
    --ssh-keys-only) SSHKEYS=1; SSHONLY=1; shift ;;
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
  if [ -f /etc/ssh/sshd_config.d/05-labdex-keys.conf ]; then
    # left in place on purpose: removing it could lock out whoever logs in with labdex's keys
    echo "kept /etc/ssh/sshd_config.d/05-labdex-keys.conf and /etc/ssh/labdex: remove both and reload sshd"
    echo "once nobody needs labdex's SSH keys (check that you can still log in first)."
  fi
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
  case "$cap" in patch|reboot|ansible) ;; ssh_keys) SSHKEYS=1 ;; *) echo "unknown capability: $cap" >&2; exit 2 ;; esac
done
if [ "$SSHKEYS" = 1 ]; then
  [ "$GO" = 1 ] || { echo "--ssh-keys needs the Go agent (leave out --python)" >&2; exit 2; }
  [ -n "$SSHUSERS" ] || { echo "--ssh-keys needs --ssh-users: the users labdex may manage keys for" >&2; exit 2; }
  SSHUSERS="$(echo "$SSHUSERS" | tr ',' ' ')"
  for u in $SSHUSERS; do
    echo "$u" | grep -Eq '^[a-z_][a-z0-9_.-]{0,31}$' || { echo "--ssh-users: $u is not a user name" >&2; exit 2; }
  done
  command -v sshd >/dev/null || [ -x /usr/sbin/sshd ] || { echo "--ssh-keys: no sshd on this host" >&2; exit 2; }
  case " $ALLOW " in *" ssh_keys "*) ;; *) ALLOW="${ALLOW:+$ALLOW }ssh_keys" ;; esac
elif [ -n "$SSHUSERS" ]; then
  echo "--ssh-users goes with --ssh-keys" >&2; exit 2
fi
[ -z "$CA" ] || [ -r "$CA" ] || { echo "can't read --ca-file $CA" >&2; exit 2; }
URL="${URL%/}"

# packages, only what is missing
need=""
if [ "$GO" = 0 ]; then
  command -v python3 >/dev/null || need="$need python3"
fi
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
if [ "$GO" = 0 ]; then
  python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || { echo "needs Python 3.11 or newer" >&2; exit 1; }
else
  case "$(uname -m)" in
    x86_64|amd64) GOARCH=amd64 ;;
    aarch64|arm64) GOARCH=arm64 ;;
    *) echo "no Go agent for $(uname -m); add --python for the Python agent" >&2; exit 1 ;;
  esac
  command -v curl >/dev/null || command -v python3 >/dev/null || { echo "needs curl to download the agent" >&2; exit 1; }
fi

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
if [ "$GO" = 1 ]; then
  fetch "$URL/api/agents/download/labdex-agent-linux-$GOARCH" "$TMP" ||
    { echo "labdex serves no Go agent for $GOARCH; add --python for the Python agent" >&2; exit 1; }
else
  fetch "$URL/api/agents/download/labdex-agent.pyz" "$TMP"
fi
if [ -n "$SHA" ]; then
  got="$(sha256sum "$TMP" | cut -d' ' -f1)"
  match=0
  for want in $SHA; do [ "$got" = "$want" ] && match=1; done
  [ "$match" = 1 ] || { echo "the agent doesn't match --sha256" >&2; exit 1; }
fi
install -d -m 0755 /opt/labdex-agent
# a package the distribution has, installed only when missing; false when it can't be
pkg() {
  if command -v apt-get >/dev/null; then apt-get install -y -q "$@" >/dev/null 2>&1
  elif command -v dnf >/dev/null; then dnf install -y -q "$@" >/dev/null 2>&1
  else return 1; fi
}
VENV=/opt/labdex-agent/venv
if [ "$GO" = 1 ]; then
  rm -rf "$VENV"
  rm -f /opt/labdex-agent/labdex-agent.pyz
  install -m 0755 "$TMP" /opt/labdex-agent/labdex-agent
  ln -sf /opt/labdex-agent/labdex-agent /usr/local/bin/labdex-agent
else
  rm -f /opt/labdex-agent/labdex-agent
  install -m 0644 "$TMP" /opt/labdex-agent/labdex-agent.pyz
  # the agent's own venv, made anew: a current cryptography from PyPI, apart from the system's packages
  rm -rf "$VENV"
  python3 -c 'import ensurepip' 2>/dev/null || pkg python3-venv || true
  if python3 -m venv "$VENV" >/dev/null 2>&1 &&
     "$VENV/bin/python" -m pip install -q --disable-pip-version-check "cryptography>=42" >/dev/null 2>&1; then
    echo "Python agent: venv $VENV, cryptography $("$VENV/bin/python" -c 'import cryptography; print(cryptography.__version__)') from PyPI"
  else
    # no pip for venvs, or no PyPI: the venv sees the distribution's cryptography instead
    rm -rf "$VENV"
    python3 -c 'import cryptography' 2>/dev/null || pkg python3-cryptography ||
      { echo "needs cryptography: PyPI isn't reachable and python3-cryptography couldn't be installed" >&2; exit 1; }
    python3 -m venv --without-pip --system-site-packages "$VENV"
    echo "Python agent: venv $VENV with the distribution's cryptography $("$VENV/bin/python" -c 'import cryptography; print(cryptography.__version__)') (no PyPI)"
  fi
  rm -f /usr/local/bin/labdex-agent  # a symlink from a Go install: don't write through it
  printf '#!/bin/sh\nexec /opt/labdex-agent/venv/bin/python -I /opt/labdex-agent/labdex-agent.pyz "$@"\n' > /usr/local/bin/labdex-agent
  chmod 0755 /usr/local/bin/labdex-agent
fi
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
  [ "$SSHKEYS" = 0 ] || echo "ssh_users = $SSHUSERS"
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

# SSH keys: labdex's files beside the users' own (or instead, with --ssh-keys-only), checked first
if [ "$SSHKEYS" = 1 ]; then
  install -d -m 0755 -o root -g root /etc/ssh/labdex
  SSHD="$(command -v sshd || echo /usr/sbin/sshd)"
  DROPIN=/etc/ssh/sshd_config.d/05-labdex-keys.conf
  if [ "$SSHONLY" = 1 ]; then FILES="/etc/ssh/labdex/%u.keys /etc/ssh/labdex/%u.break-glass"
  else FILES=".ssh/authorized_keys .ssh/authorized_keys2 /etc/ssh/labdex/%u.keys /etc/ssh/labdex/%u.break-glass"; fi
  grep -Eqi '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/' /etc/ssh/sshd_config ||
    { echo "--ssh-keys: /etc/ssh/sshd_config doesn't include sshd_config.d/; add AuthorizedKeysFile $FILES yourself" >&2; exit 1; }
  install -d -m 0755 /etc/ssh/sshd_config.d
  [ ! -f "$DROPIN" ] || cp -p "$DROPIN" "$DROPIN.bak"
  printf '# labdex: SSH keys managed through the agent (install.sh --ssh-keys)\nAuthorizedKeysFile %s\n' "$FILES" > "$DROPIN"
  chmod 0644 "$DROPIN"
  if ! "$SSHD" -t; then
    if [ -f "$DROPIN.bak" ]; then mv "$DROPIN.bak" "$DROPIN"; else rm -f "$DROPIN"; fi
    echo "sshd -t failed with the labdex drop-in; put back as it was" >&2; exit 1
  fi
  rm -f "$DROPIN.bak"
  got="$("$SSHD" -T 2>/dev/null | awk '$1 == "authorizedkeysfile" {$1 = ""; print substr($0, 2)}')"
  [ "$got" = "$FILES" ] || echo "warning: sshd uses AuthorizedKeysFile $got (an earlier file sets it first); labdex will report it" >&2
  systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true
fi

printf '%s\n' "$TOKEN" | runuser -u "$RUNAS" -- $AGENT --config /etc/labdex-agent/agent.conf enrol --token -

fetch "$URL/api/agents/download/labdex-agent.service" "$UNIT"
chmod 0644 "$UNIT"
rm -rf "$UNIT.d"
if [ "$RUNAS" = root ]; then
  install -d "$UNIT.d"
  printf '[Service]\nUser=root\nGroup=root\n' > "$UNIT.d/root.conf"
fi
if [ "$GO" = 1 ]; then
  install -d "$UNIT.d"
  printf '[Service]\nExecStart=\nExecStart=/opt/labdex-agent/labdex-agent run\n' > "$UNIT.d/go.conf"
fi
systemctl daemon-reload
systemctl enable --now labdex-agent
systemctl restart labdex-agent  # a reinstall: start the new program
echo "labdex agent installed and running as $RUNAS; check: systemctl status labdex-agent, labdex-agent health"
echo "remove it again with: sudo sh install.sh --uninstall"
