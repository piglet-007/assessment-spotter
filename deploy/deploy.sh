#!/usr/bin/env bash
# Install or update the app on an Ubuntu 22.04/24.04 server. Safe to re-run after every update,
# and written to share the server with other apps: it never edits other nginx sites, never
# replaces the system Node.js, and refuses ports that something else already uses.
#
#   sudo ./deploy/deploy.sh --check        # read-only preflight: what's installed, which ports are free
#   sudo ./deploy/deploy.sh                # serve on http://SERVER_IP:8080 (no domain needed)
#   sudo DOMAIN=routes.example.com EMAIL=you@example.com ./deploy/deploy.sh   # domain + HTTPS
#
# Variables:
#   HERE_API_KEY  written to backend/.env on the first run
#   DOMAIN        serve on this (sub)domain on ports 80/443 alongside the other sites
#   EMAIL         for the HTTPS certificate (optional)
#   PUBLIC_PORT   port for the site when there is no DOMAIN (default 8080)
#   APP_PORT      internal port for gunicorn, only reachable from the server (default 8765)
#   PUBLIC_HOST   IP or hostname to allow when there is no DOMAIN (detected otherwise)
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE=truck-routing
SERVICE_USER=truckroute
CACHE_DIR=/var/cache/truck-routing
ENV_FILE="$APP_DIR/backend/.env"
NODE_DIR="$APP_DIR/.node"  # private Node.js for the frontend build, if the system's is too old
DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-}"
PUBLIC_PORT="${PUBLIC_PORT:-8080}"
APP_PORT="${APP_PORT:-8765}"
MIN_NODE_MAJOR=22
CHECK_ONLY=false
[ "${1:-}" = "--check" ] && CHECK_ONLY=true

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mWarning: %s\033[0m\n' "$*"; }
die() { printf '\n\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo."
[ -f "$APP_DIR/backend/manage.py" ] || die "run from a checkout of the repository."

# Who is listening on a TCP port ("" if free).
port_owner() { ss -Hltnp "sport = :$1" 2>/dev/null | grep -o 'users:(("[^"]*"' | head -1 | cut -d'"' -f2 || true; }
our_gunicorn_running() { systemctl is-active --quiet "$SERVICE" 2>/dev/null; }
node_major() { "${1:-node}" -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0; }

# ---------------------------------------------------------------- preflight
say "Preflight"
. /etc/os-release && echo "OS:     $PRETTY_NAME"
echo "Memory: $(free -m | awk '/^Mem:/ {print $7 " MB available of " $2 " MB"}')"
echo "nginx:  $(command -v nginx >/dev/null && nginx -v 2>&1 | cut -d/ -f2 || echo 'not installed')"
echo "node:   $(command -v node >/dev/null && node --version || echo 'not installed') (needs >= $MIN_NODE_MAJOR for the build; a private copy is used otherwise)"
echo "python: $(python3 --version 2>&1 | cut -d' ' -f2)"
for port in 80 443 "$PUBLIC_PORT" "$APP_PORT"; do
  owner="$(port_owner "$port")"
  echo "port $port: ${owner:-free}"
done
if [ -d /etc/nginx/sites-enabled ]; then
  echo "nginx sites enabled: $(ls /etc/nginx/sites-enabled | tr '\n' ' ')"
fi

problems=()
web_owner="$(port_owner 80)"
if [ -n "$web_owner" ] && [ "$web_owner" != "nginx" ]; then
  problems+=("port 80 is served by '$web_owner', not nginx; this script only configures nginx.")
fi
app_owner="$(port_owner "$APP_PORT")"
if [ -n "$app_owner" ] && ! our_gunicorn_running; then
  problems+=("APP_PORT $APP_PORT is used by '$app_owner'; choose another, e.g. APP_PORT=8766.")
fi
if [ -z "$DOMAIN" ]; then
  public_owner="$(port_owner "$PUBLIC_PORT")"
  if [ -n "$public_owner" ] && ! grep -qs "listen $PUBLIC_PORT;" "/etc/nginx/sites-enabled/$SERVICE"; then
    problems+=("PUBLIC_PORT $PUBLIC_PORT is used by '$public_owner'; choose another, e.g. PUBLIC_PORT=8081.")
  fi
else
  resolved="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
  echo "$DOMAIN resolves to: ${resolved:-nothing yet}"
  [ -n "$resolved" ] || problems+=("$DOMAIN does not resolve yet; add a DNS A record for it first.")
  grep -Rqs "server_name[^;]*\b$DOMAIN\b" /etc/nginx/sites-enabled --exclude="$SERVICE" &&
    problems+=("another nginx site already uses server_name $DOMAIN.")
fi

if [ ${#problems[@]} -gt 0 ]; then
  for p in "${problems[@]}"; do printf '\033[31m- %s\033[0m\n' "$p"; done
  die "fix the problems above and run again."
fi
echo "No conflicts found."
if $CHECK_ONLY; then
  echo "Preflight only; nothing was changed."
  exit 0
fi

# ---------------------------------------------------------------- packages
say "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip nginx curl ca-certificates xz-utils >/dev/null

NODE_BIN="$(command -v node || true)"
if [ -z "$NODE_BIN" ] || [ "$(node_major "$NODE_BIN")" -lt "$MIN_NODE_MAJOR" ]; then
  NODE_BIN="$NODE_DIR/bin/node"
  if [ "$(node_major "$NODE_BIN")" -lt "$MIN_NODE_MAJOR" ]; then
    # Download Node into the app folder instead of replacing the system's, which other apps may use.
    case "$(uname -m)" in x86_64) arch=x64 ;; aarch64) arch=arm64 ;; *) die "unsupported CPU $(uname -m)" ;; esac
    base="https://nodejs.org/dist/latest-v${MIN_NODE_MAJOR}.x"
    tarball="$(curl -fsSL "$base/SHASUMS256.txt" | grep -o "node-v[0-9.]*-linux-$arch.tar.xz" | head -1)"
    tmp="$(mktemp -d)"
    curl -fsSL "$base/$tarball" -o "$tmp/$tarball"
    (cd "$tmp" && curl -fsSL "$base/SHASUMS256.txt" | grep " $tarball\$" | sha256sum -c - >/dev/null) ||
      die "Node.js download failed its checksum."
    rm -rf "$NODE_DIR" && mkdir -p "$NODE_DIR"
    tar -xJf "$tmp/$tarball" -C "$NODE_DIR" --strip-components=1
    rm -rf "$tmp"
  fi
fi
echo "Using node $("$NODE_BIN" --version) from $NODE_BIN"

# ---------------------------------------------------------------- backend
say "Service user and cache directory"
id -u "$SERVICE_USER" >/dev/null 2>&1 ||
  useradd --system --no-create-home --shell /usr/sbin/nologin "$SERVICE_USER"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 750 "$CACHE_DIR"
sudo -u "$SERVICE_USER" test -r "$APP_DIR/backend/manage.py" ||
  die "user $SERVICE_USER cannot read $APP_DIR (home directories are private). Put the code in /srv/truck-routing."

say "Backend"
python3 -m venv "$APP_DIR/backend/.venv"
"$APP_DIR/backend/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/backend/.venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"

if [ ! -f "$ENV_FILE" ]; then
  if [ -n "$DOMAIN" ]; then
    host="$DOMAIN"
  else
    # Behind NAT the public IP is not on any interface, so ask an echo service.
    host="${PUBLIC_HOST:-$(curl -fsS --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')}"
  fi
  cat >"$ENV_FILE" <<EOF
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(50))')
DJANGO_ALLOWED_HOSTS=$host,localhost,127.0.0.1
DJANGO_CACHE_DIR=$CACHE_DIR
HERE_API_KEY=${HERE_API_KEY:-}
EOF
  echo "Wrote $ENV_FILE (allowed host: $host)"
elif [ -n "$DOMAIN" ] && ! grep -q "^DJANGO_ALLOWED_HOSTS=.*\b$DOMAIN\b" "$ENV_FILE"; then
  sed -i "s|^DJANGO_ALLOWED_HOSTS=|DJANGO_ALLOWED_HOSTS=$DOMAIN,|" "$ENV_FILE"
  echo "Added $DOMAIN to DJANGO_ALLOWED_HOSTS"
fi
chown "root:$SERVICE_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"
grep -q '^HERE_API_KEY=.\+' "$ENV_FILE" ||
  die "set HERE_API_KEY in $ENV_FILE, then run this script again."
grep -q '^DJANGO_DEBUG=false' "$ENV_FILE" || warn "DJANGO_DEBUG is not false in $ENV_FILE."
(cd "$APP_DIR/backend" && .venv/bin/python manage.py check --deploy --fail-level ERROR >/dev/null)

# ---------------------------------------------------------------- frontend
say "Frontend"
(
  cd "$APP_DIR/frontend"
  export PATH="$(dirname "$NODE_BIN"):$PATH"
  npm ci --no-audit --no-fund --loglevel=error
  npm run build >/dev/null
)
echo "Built $APP_DIR/frontend/dist"

# ---------------------------------------------------------------- services
say "gunicorn service (127.0.0.1:$APP_PORT)"
sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
  -e "s|@CACHE_DIR@|$CACHE_DIR|g" -e "s|@APP_PORT@|$APP_PORT|g" \
  "$APP_DIR/deploy/truck-routing.service" >"/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
systemctl restart "$SERVICE"

say "nginx site"
if [ -n "$DOMAIN" ]; then
  listen="listen 80;\n    listen [::]:80;"
  server_name="$DOMAIN"
else
  listen="listen $PUBLIC_PORT;\n    listen [::]:$PUBLIC_PORT;"
  server_name="_"
fi
site="/etc/nginx/sites-available/$SERVICE"
# Keep certbot's HTTPS additions on re-runs: only rewrite the site if it isn't certified yet.
if ! grep -qs "managed by Certbot" "$site"; then
  sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@SERVER_NAME@|$server_name|g" \
    -e "s|@APP_PORT@|$APP_PORT|g" -e "s|@LISTEN@|$listen|g" \
    "$APP_DIR/deploy/nginx.conf" >"$site"
fi
ln -sf "$site" "/etc/nginx/sites-enabled/$SERVICE"
# nginx (www-data) must be able to read the built files.
chmod o+rx "$APP_DIR" "$APP_DIR/frontend"
chmod -R o+rX "$APP_DIR/frontend/dist"
nginx -t 2>&1 | grep -v "syntax is ok\|test is successful" || true
nginx -t -q || die "nginx config test failed; other sites were not touched. Remove $site to roll back."
systemctl reload nginx

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  if [ -n "$DOMAIN" ]; then ufw allow 'Nginx Full' >/dev/null; else ufw allow "$PUBLIC_PORT/tcp" >/dev/null; fi
fi

if [ -n "$DOMAIN" ] && ! grep -qs "managed by Certbot" "$site"; then
  say "HTTPS certificate for $DOMAIN"
  apt-get install -y -qq certbot python3-certbot-nginx >/dev/null
  email_args=(--register-unsafely-without-email)
  [ -n "$EMAIL" ] && email_args=(-m "$EMAIL")
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect "${email_args[@]}"
fi

# ---------------------------------------------------------------- verify
say "Health check"
for _ in $(seq 20); do
  curl -fsS "http://127.0.0.1:$APP_PORT/api/health/" >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS "http://127.0.0.1:$APP_PORT/api/health/" >/dev/null ||
  die "gunicorn not responding; see: journalctl -u $SERVICE -n 50"
if [ -n "$DOMAIN" ]; then
  # With HTTPS this is a redirect, which also counts as success.
  curl -fsS -o /dev/null -H "Host: $DOMAIN" http://127.0.0.1/api/health/
  url="https://$DOMAIN"
else
  curl -fsS -o /dev/null "http://127.0.0.1:$PUBLIC_PORT/api/health/"
  url="http://$(grep '^DJANGO_ALLOWED_HOSTS=' "$ENV_FILE" | cut -d= -f2 | cut -d, -f1):$PUBLIC_PORT"
fi
echo "API healthy."
echo
echo "Deployed: $url"
