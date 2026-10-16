#!/usr/bin/env bash
# Install or update the app on an Ubuntu 22.04/24.04 VPS. Safe to re-run after every update.
#
#   sudo ./deploy/deploy.sh                                   # serve on the server's IP over HTTP
#   sudo DOMAIN=routes.example.com EMAIL=you@example.com ./deploy/deploy.sh   # plus HTTPS
#
# Optional variables: HERE_API_KEY (first run only), PUBLIC_HOST (IP or hostname to allow
# when no DOMAIN is set; detected automatically otherwise).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE=truck-routing
SERVICE_USER=truckroute
CACHE_DIR=/var/cache/truck-routing
ENV_FILE="$APP_DIR/backend/.env"
DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-}"
MIN_NODE_MAJOR=22

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run with sudo."
[ -f "$APP_DIR/backend/manage.py" ] || die "run from a checkout of the repository."

say "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip nginx curl ca-certificates >/dev/null
node_major() { node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0; }
if [ "$(node_major)" -lt "$MIN_NODE_MAJOR" ]; then
  # Ubuntu's own Node is too old for Vite; use NodeSource's LTS packages.
  curl -fsSL "https://deb.nodesource.com/setup_${MIN_NODE_MAJOR}.x" | bash - >/dev/null
  apt-get install -y -qq nodejs >/dev/null
fi
echo "python $(python3 --version | cut -d' ' -f2), node $(node --version), $(nginx -v 2>&1)"

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
    # On clouds with NAT (GCP, AWS) the public IP is not on any interface, so ask.
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
fi
chown "root:$SERVICE_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"
grep -q '^HERE_API_KEY=.\+' "$ENV_FILE" ||
  die "set HERE_API_KEY in $ENV_FILE, then run this script again."
grep -q '^DJANGO_DEBUG=false' "$ENV_FILE" ||
  echo "Warning: DJANGO_DEBUG is not false in $ENV_FILE."
(cd "$APP_DIR/backend" && .venv/bin/python manage.py check --deploy --fail-level ERROR >/dev/null)

say "Frontend"
(cd "$APP_DIR/frontend" && npm ci --no-audit --no-fund --loglevel=error && npm run build >/dev/null)
echo "Built $APP_DIR/frontend/dist"

say "gunicorn service"
sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@SERVICE_USER@|$SERVICE_USER|g" -e "s|@CACHE_DIR@|$CACHE_DIR|g" \
  "$APP_DIR/deploy/truck-routing.service" >"/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1
systemctl restart "$SERVICE"

say "nginx"
server_name="${DOMAIN:-_}"
sed -e "s|@APP_DIR@|$APP_DIR|g" -e "s|@SERVER_NAME@|$server_name|g" \
  "$APP_DIR/deploy/nginx.conf" >"/etc/nginx/sites-available/$SERVICE"
ln -sf "/etc/nginx/sites-available/$SERVICE" "/etc/nginx/sites-enabled/$SERVICE"
# Without a domain this site is the catch-all, so the stock default site must go.
[ -n "$DOMAIN" ] || rm -f /etc/nginx/sites-enabled/default
# nginx (www-data) must be able to read the built files.
chmod o+rx "$APP_DIR" "$APP_DIR/frontend"
chmod -R o+rX "$APP_DIR/frontend/dist"
nginx -t -q
systemctl reload nginx

if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 'Nginx Full' >/dev/null
fi

if [ -n "$DOMAIN" ]; then
  say "HTTPS certificate for $DOMAIN"
  apt-get install -y -qq certbot python3-certbot-nginx >/dev/null
  email_args=(--register-unsafely-without-email)
  [ -n "$EMAIL" ] && email_args=(-m "$EMAIL")
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --redirect "${email_args[@]}"
fi

say "Health check"
for _ in $(seq 20); do
  curl -fsS http://127.0.0.1:8000/api/health/ >/dev/null 2>&1 && break
  sleep 1
done
curl -fsS http://127.0.0.1:8000/api/health/ >/dev/null ||
  die "gunicorn not responding; see: journalctl -u $SERVICE -n 50"
# Through nginx too (with a domain and HTTPS this is a redirect, which also counts).
curl -fsS -o /dev/null -H "Host: ${DOMAIN:-localhost}" http://127.0.0.1/api/health/ ||
  die "nginx is not proxying /api/; see: /var/log/nginx/error.log"
echo "API healthy."
echo "Deployed. Open: ${DOMAIN:+https://$DOMAIN}${DOMAIN:-http://$(grep '^DJANGO_ALLOWED_HOSTS=' "$ENV_FILE" | cut -d= -f2 | cut -d, -f1)}"
