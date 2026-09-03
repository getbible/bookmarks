#!/usr/bin/env bash
# Host installer for the getBible bookmarks API (Debian/Ubuntu, run as root).
#
#   sudo deploy/install.sh [--repo-url URL] [--push-url URL] [--branch main] [--home /srv/getbible-bookmarks]
#
# Creates the service user, a hashed-lock virtualenv, the checkout, the release
# root, the environment file, the systemd units and installs the nginx site into
# sites-available (enabling it and obtaining TLS certificates is left to you).
# Re-running is safe: existing files are kept unless they are ours to regenerate.
set -euo pipefail

# The initial clone uses anonymous HTTPS so no key is needed yet; pushes use the
# SSH deploy key that is added afterwards.
REPO_URL="https://github.com/getbible/bookmarks.git"
PUSH_URL="git@github.com:getbible/bookmarks.git"
BRANCH="main"
HOME_DIR="/srv/getbible-bookmarks"
USER_NAME="getbible-bookmarks"
CONF_DIR="/etc/getbible-bookmarks"
PYTHON="${PYTHON:-python3}"

while [ $# -gt 0 ]; do
  case "$1" in
    --repo-url) REPO_URL="$2"; shift 2 ;;
    --push-url) PUSH_URL="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --home) HOME_DIR="$2"; shift 2 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }
command -v git >/dev/null || { echo "git is required" >&2; exit 1; }
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "python 3.11+ is required" >&2; exit 1; }

SOURCE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO_DIR="$HOME_DIR/repo"
WWW_DIR="$HOME_DIR/www"
VENV_DIR="$HOME_DIR/venv"

if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --home-dir "$HOME_DIR" --shell /usr/sbin/nologin "$USER_NAME"
fi
install -d -m 0755 -o root -g root "$HOME_DIR"
install -d -m 0755 -o "$USER_NAME" -g "$USER_NAME" "$WWW_DIR"
install -d -m 0750 -o root -g "$USER_NAME" "$CONF_DIR"

if [ ! -d "$REPO_DIR/.git" ]; then
  sudo -u "$USER_NAME" git clone --branch "$BRANCH" "$REPO_URL" "$REPO_DIR"
fi
sudo -u "$USER_NAME" git -C "$REPO_DIR" remote set-url --push origin "$PUSH_URL"
sudo -u "$USER_NAME" git -C "$REPO_DIR" config --local user.name "getBible Bookmarks Service"
sudo -u "$USER_NAME" git -C "$REPO_DIR" config --local user.email "bookmarks@getbible.net"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON" -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
"$VENV_DIR/bin/python" -m pip install --quiet --require-hashes -r "$REPO_DIR/requirements.txt"
"$VENV_DIR/bin/python" -m pip install --quiet --no-deps "$REPO_DIR"

if [ ! -f "$CONF_DIR/env" ]; then
  install -m 0640 -o root -g "$USER_NAME" "$SOURCE_DIR/deploy/env.template" "$CONF_DIR/env"
  echo "Wrote $CONF_DIR/env — review it, then add the deploy key and known_hosts it references."
fi
if [ ! -f "$CONF_DIR/contributors.json" ]; then
  install -m 0600 -o "$USER_NAME" -g "$USER_NAME" /dev/null "$CONF_DIR/contributors.json"
  printf '{\n  "schema_version": 1,\n  "contributors": []\n}\n' > "$CONF_DIR/contributors.json"
  chown "$USER_NAME:$USER_NAME" "$CONF_DIR/contributors.json"
fi

install -m 0644 "$SOURCE_DIR/deploy/systemd/getbible-bookmarks-api.service" /etc/systemd/system/
install -m 0644 "$SOURCE_DIR/deploy/systemd/getbible-bookmarks-sync.service" /etc/systemd/system/
install -m 0644 "$SOURCE_DIR/deploy/systemd/getbible-bookmarks-sync.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now getbible-bookmarks-sync.timer
systemctl enable getbible-bookmarks-api.service

if [ -d /etc/nginx/sites-available ]; then
  install -m 0644 "$SOURCE_DIR/deploy/nginx/bookmarks.getbible.net.conf" /etc/nginx/sites-available/
  echo "Installed nginx site to /etc/nginx/sites-available/bookmarks.getbible.net.conf (not enabled)."
fi

sudo -u "$USER_NAME" env BOOKMARKS_REPO_DIR="$REPO_DIR" BOOKMARKS_RELEASE_DIR="$WWW_DIR" \
  BOOKMARKS_BIN="$VENV_DIR/bin/getbible-bookmarks" BOOKMARKS_GIT_REMOTE="" \
  "$REPO_DIR/deploy/deploy.sh"

cat <<MSG

Next steps:
  1. Put a write-capable deploy key at $CONF_DIR/deploy_key (mode 0600, owner $USER_NAME)
     and GitHub's host key in $CONF_DIR/known_hosts (ssh-keyscan github.com), then check
     $CONF_DIR/env. Fetches use $REPO_URL; pushes use $PUSH_URL with that key.
  2. Enrol contributors:
       sudo -u $USER_NAME $VENV_DIR/bin/getbible-bookmarks tokens --file $CONF_DIR/contributors.json \\
         create --id <id> --name "<Git author name>" --email <git author email> --role contributor
  3. systemctl start getbible-bookmarks-api && systemctl status getbible-bookmarks-api
  4. Enable the nginx site, obtain certificates, nginx -t, systemctl reload nginx.
MSG
