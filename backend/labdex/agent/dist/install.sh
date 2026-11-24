#!/bin/sh
# Install the labdex agent on this host (Debian, Ubuntu, Proxmox VE, Fedora, RHEL and friends).
#
#   curl -fsSLo install.sh https://lantern.example.net/api/agents/install.sh
#   sudo sh install.sh --url https://lantern.example.net --token <one-time token> [--sha256 <of the agent>]...
#        [--allow "patch reboot"] [--root] [--ca-file /path/ca.pem] [--python]
#        [--ansible [--ansible-become] [--packages "python3-apt acl"]]
#   sudo sh install.sh --uninstall [--purge]
#
# --allow      write capabilities this host accepts (patch, reboot, ansible). Default: none, read-only.
# --root       run the agent as root instead of its own user. The hub must allow root for this host too.
# --python     install the Python agent instead of the Go one. By default it installs the Go agent:
#              one static binary, no Python needed for the agent itself. --sha256 may be given once
#              per architecture; the binary for this host's must match one of them. Ansible jobs
#              still need Python on the host. (--go is the default and still accepted.)
# --ansible    let labdex run Ansible here through the agent's link (Go agent, Ubuntu only for now).
#              Adds ansible to --allow and sets up sshd for one user, labdex-ansible: it logs in only
#              with a short-lived certificate from labdex's SSH CA, only from 127.0.0.1 (the agent's
#              pipe), without a password, TTY or forwarding. Installs python3 and openssh-server.
#              Without --ansible a reinstall takes that set-up away again (the user stays).
# --ansible-become  let those runs become root (sudo without a password for labdex-ansible).
#              labdex must allow it for this host too. Without it, playbooks run unprivileged.
# --packages   distribution packages the host's Ansible modules need, installed with apt.
# --uninstall  stop and remove the agent: its service, sudo rules, config and program. Its state
#              (key, certificate, audit log) stays in /var/lib/labdex-agent unless you add --purge,
#              which also removes the labdex-agent and labdex-ansible users. Revoke the agent in labdex as well.
# Read it before you run it: it creates a user, a sudo rule file, a config and a systemd service.
# It installs packages only when something is missing: sudo (with --python also python3 3.11+).
# The Python agent runs from its own venv, /opt/labdex-agent/venv, with cryptography from PyPI. Where
# PyPI or the venv module's pip can't be had, the venv uses the distribution's python3-cryptography
# instead. The agent brings its other libraries along.
set -eu

URL="" TOKEN="${LABDEX_AGENT_TOKEN:-}" SHA="" ALLOW="" ROOT=0 CA="" UNINSTALL=0 PURGE=0 GO=1
ANSIBLE=0 BECOME=0 PACKAGES=""
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
    --uninstall) UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    --ansible) ANSIBLE=1; shift ;;
    --ansible-become) BECOME=1; shift ;;
    --packages) PACKAGES="$PACKAGES $2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ "$(id -u)" = 0 ] || { echo "run it as root (sudo)" >&2; exit 1; }

UNIT=/etc/systemd/system/labdex-agent.service
SSHD_DROPIN=/etc/ssh/sshd_config.d/10-labdex-ansible.conf
SSH_CA=/etc/ssh/labdex-ansible-ca.pub
SSH_PRINCIPALS=/etc/ssh/labdex-ansible-principals
BECOME_RULE=/etc/sudoers.d/labdex-ansible
# sshd -t and -T want its privilege separation folder, which a socket-activated sshd lacks until used
sshd_bin() { [ -d /run/sshd ] || install -d -m 0755 /run/sshd 2>/dev/null || true; command -v sshd || echo /usr/sbin/sshd; }
# sshd takes a new config only once it checks out; a running sshd reloads, a stopped one is left
sshd_reload() {
  "$(sshd_bin)" -t || { echo "sshd's config doesn't check out (sshd -t): fix it, then run this again" >&2; return 1; }
  systemctl try-reload-or-restart ssh 2>/dev/null || systemctl try-reload-or-restart sshd 2>/dev/null || true
}
# Ansible through the agent off: sshd no longer trusts labdex's CA, no become rule
ansible_off() {
  rm -f "$BECOME_RULE" "$BECOME_RULE.new"
  if [ -e "$SSHD_DROPIN" ] || [ -e "$SSH_CA" ] || [ -e "$SSH_PRINCIPALS" ]; then
    rm -f "$SSHD_DROPIN" "$SSH_CA" "$SSH_PRINCIPALS"
    sshd_reload || true
  fi
}
if [ "$UNINSTALL" = 1 ]; then
  ansible_off
  if [ -f "$UNIT" ]; then
    systemctl disable --now labdex-agent 2>/dev/null || true
  fi
  rm -f "$UNIT" /etc/sudoers.d/labdex-agent /etc/sudoers.d/labdex-agent.new /usr/local/bin/labdex-agent
  rm -rf "$UNIT.d" /etc/labdex-agent /opt/labdex-agent
  systemctl daemon-reload 2>/dev/null || true
  if [ "$PURGE" = 1 ]; then
    rm -rf /var/lib/labdex-agent /var/lib/private/labdex-agent
    if id -u labdex-agent >/dev/null 2>&1; then userdel labdex-agent; fi
    if id -u labdex-ansible >/dev/null 2>&1; then userdel labdex-ansible; fi
    rm -rf /var/lib/labdex-ansible
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
if [ "$ANSIBLE" = 1 ]; then
  [ "$GO" = 1 ] || { echo "--ansible needs the Go agent: leave out --python" >&2; exit 2; }
  # Ubuntu first; Debian and the rest follow once they've been tried
  ( . /etc/os-release 2>/dev/null; case " ${ID:-} ${ID_LIKE:-} " in *" ubuntu "*) exit 0 ;; *) exit 1 ;; esac ) ||
    { echo "--ansible supports Ubuntu only for now" >&2; exit 2; }
  case " $ALLOW " in *" ansible "*) ;; *) ALLOW="${ALLOW:+$ALLOW }ansible" ;; esac
else
  [ "$BECOME" = 0 ] || { echo "--ansible-become needs --ansible" >&2; exit 2; }
  [ -z "$PACKAGES" ] || { echo "--packages needs --ansible" >&2; exit 2; }
fi
for p in $PACKAGES; do
  # package names only: nothing apt would read as an option, a path or a version
  case "$p" in [a-z0-9]*) ;; *) echo "not a package name: $p" >&2; exit 2 ;; esac
  case "$p" in *[!a-z0-9+.-]*) echo "not a package name: $p" >&2; exit 2 ;; esac
done

# packages, only what is missing
need=""
if [ "$GO" = 0 ]; then
  command -v python3 >/dev/null || need="$need python3"
fi
command -v sudo >/dev/null || need="$need sudo"
if [ "$ANSIBLE" = 1 ]; then
  # what Ansible needs on the host: python3 for its modules, sshd for the agent's pipe
  command -v python3 >/dev/null || need="$need python3"
  [ -x "$(sshd_bin)" ] || need="$need openssh-server"
  need="$need$PACKAGES"
fi
if [ -n "$need" ]; then
  echo "installing:$need"
  if command -v apt-get >/dev/null; then
    # a fresh host may have no package lists yet
    apt-get install -y -q $need >/dev/null 2>&1 || { apt-get update -q >/dev/null && apt-get install -y -q $need >/dev/null; }
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
if [ "$ANSIBLE" = 1 ]; then
  # before enrolling, so a sealed vault doesn't use up the token
  SSH_CA_TMP="$(mktemp)"
  trap 'rm -f "$TMP" "$SSH_CA_TMP"' EXIT
  fetch "$URL/api/agents/ansible-ca.pub" "$SSH_CA_TMP" ||
    { echo "labdex didn't hand out its SSH CA for Ansible (is its vault sealed? an administrator unseals it)" >&2; exit 1; }
  case "$(head -c 12 "$SSH_CA_TMP")" in "ssh-ed25519 ") ;; *) echo "labdex's SSH CA doesn't look like one" >&2; exit 1 ;; esac
  # the port the agent's pipe reaches sshd on
  SSH_PORT="$("$(sshd_bin)" -T 2>/dev/null | awk '$1 == "port" { print $2; exit }')"
  SSH_PORT="${SSH_PORT:-22}"
  "$(sshd_bin)" -T 2>/dev/null | awk '$1 == "listenaddress" { print $2 }' |
    grep -Eq '^(0\.0\.0\.0|\[::\]|127\.0\.0\.1|\[::1\]):' ||
    echo "warning: sshd doesn't seem to listen on 127.0.0.1, where the agent's pipe connects (ListenAddress)" >&2
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
  [ "$ANSIBLE" = 0 ] || echo "ssh_port = $SSH_PORT"
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

if [ "$ANSIBLE" = 1 ]; then
  # the user Ansible logs in as: no password (a '*' that sshd doesn't take for a locked account),
  # a home of its own for Ansible's temporary files
  id -u labdex-ansible >/dev/null 2>&1 ||
    useradd --system --user-group --create-home --home-dir /var/lib/labdex-ansible --shell /bin/sh labdex-ansible
  usermod -p '*' labdex-ansible
  chmod 0700 /var/lib/labdex-ansible
  # the name labdex knows this host by: the certificates' principal is labdex-ansible@<it>
  HOSTNAME_IN_LABDEX="$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["host"])' /var/lib/labdex-agent/credentials.json)"
  install -m 0644 "$SSH_CA_TMP" "$SSH_CA"
  printf 'from="127.0.0.1,::1" labdex-ansible@%s\n' "$HOSTNAME_IN_LABDEX" > "$SSH_PRINCIPALS"
  chmod 0644 "$SSH_PRINCIPALS"
  install -d -m 0755 /etc/ssh/sshd_config.d
  grep -Eqi '^[[:space:]]*Include[[:space:]]+/etc/ssh/sshd_config\.d/' /etc/ssh/sshd_config ||
    { echo "/etc/ssh/sshd_config doesn't include /etc/ssh/sshd_config.d/: add that Include, then run this again" >&2; exit 1; }
  # a Match block in an included file ends with the file: nothing here touches other users
  cat > "$SSHD_DROPIN" <<SSHD
# labdex: Ansible through the agent (install.sh --ansible). install.sh --uninstall removes it.
Match User labdex-ansible
    TrustedUserCAKeys $SSH_CA
    AuthorizedPrincipalsFile $SSH_PRINCIPALS
    AuthorizedKeysFile none
    AuthenticationMethods publickey
    PasswordAuthentication no
    KbdInteractiveAuthentication no
    PermitTTY no
    PermitUserRC no
    X11Forwarding no
    AllowAgentForwarding no
    AllowTcpForwarding no
    AllowStreamLocalForwarding no
    PermitTunnel no
    GatewayPorts no
SSHD
  chmod 0644 "$SSHD_DROPIN"
  sshd_reload || { rm -f "$SSHD_DROPIN"; exit 1; }
  if [ "$BECOME" = 1 ]; then
    printf '%s\n' "# labdex: Ansible through the agent may become root here (install.sh --ansible-become)" \
      'Defaults:labdex-ansible !requiretty' 'labdex-ansible ALL=(ALL:ALL) NOPASSWD: ALL' > "$BECOME_RULE.new"
    chmod 0440 "$BECOME_RULE.new"
    visudo -cf "$BECOME_RULE.new" >/dev/null
    mv "$BECOME_RULE.new" "$BECOME_RULE"
  else
    rm -f "$BECOME_RULE"
  fi
else
  ansible_off
fi

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
if [ "$ANSIBLE" = 1 ]; then
  if [ "$BECOME" = 1 ]; then how="and may become root"; else how="without become"; fi
  echo "Ansible through the agent: sshd on port $SSH_PORT takes labdex-ansible@$HOSTNAME_IN_LABDEX, $how"
fi
echo "remove it again with: sudo sh install.sh --uninstall"
