#!/usr/bin/env bash
# Один раз на чистом Ubuntu VPS: пакеты, venv, systemd (+ nginx если есть домен).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/pyBot}"
DOMAIN="${DOMAIN:-}"
RUN_USER="${RUN_USER:-root}"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx unzip curl

mkdir -p "$APP_DIR"
cd "$APP_DIR"

if [[ ! -f main.py ]]; then
  echo "ERROR: в $APP_DIR нет main.py — сначала залей код (zip/scp)."
  exit 1
fi

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install --upgrade pip
# На VPS без GPU — CPU-сборки (CUDA забьёт диск)
REQ=requirements-cpu.txt
[[ -f "$REQ" ]] || REQ=requirements.txt
pip install -r "$REQ"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Создан .env из .env.example — заполни TELEGRAM_BOT_TOKEN и LLM_API_KEY."
fi

# systemd
SERVICE_SRC="$APP_DIR/deploy/pybot.service"
if [[ -f "$SERVICE_SRC" ]]; then
  sed "s/User=www-data/User=$RUN_USER/; s/Group=www-data/Group=$RUN_USER/" \
    "$SERVICE_SRC" > /etc/systemd/system/pybot.service
  systemctl daemon-reload
  systemctl enable pybot
fi

if [[ -n "$DOMAIN" ]]; then
  sed "s/YOUR_DOMAIN/$DOMAIN/g" "$APP_DIR/deploy/nginx-pybot.conf" \
    > /etc/nginx/sites-available/pybot
  ln -sf /etc/nginx/sites-available/pybot /etc/nginx/sites-enabled/pybot
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
  systemctl reload nginx
  certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
    --register-unsafely-without-email || true
  # прописать публичный URL в .env если пусто/placeholder
  if grep -qE 'BILLING_PUBLIC_BASE_URL=(https://YOUR_DOMAIN)?$' .env \
    || grep -q 'YOUR_DOMAIN' .env; then
    sed -i "s|^BILLING_PUBLIC_BASE_URL=.*|BILLING_PUBLIC_BASE_URL=https://$DOMAIN|" .env
  fi
else
  echo "DOMAIN не задан — nginx/HTTPS пропущены. Бот всё равно можно запустить."
fi

systemctl restart pybot || true
systemctl --no-pager status pybot || true
echo "Готово. Логи: journalctl -u pybot -f"
echo "Health (если nginx+домен): curl -s https://\$DOMAIN/health"
