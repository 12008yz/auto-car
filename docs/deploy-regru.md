# Деплой pyBot на VPS (Reg.ru / Ubuntu)

Чеклист **до и после** загрузки на сервер. Локально перед upload уже готовы: лимиты, чеки UnitPay (email + cashItems), шаблоны `deploy/`.

## 0. Перед загрузкой (локально)

- [x] Дневные лимиты включены (`BILLING_OPEN_ACCESS=0`)
- [x] Оплата UnitPay спрашивает email и передаёт `cashItems` для кассы
- [x] HTTP: `/health`, `/billing/unitpay`
- [ ] Есть домен (или будете использовать IP + позже домен)
- [ ] ИП / НПД оформлены для заявки в UnitPay
- [ ] В `.env` на сервере будут ключи (локально можно ещё без UnitPay)

Не заливайте на сервер папки `.venv/`, `data/`, `.env` из Windows в открытом виде через публичный репозиторий. `.env` копируйте отдельно по SCP/SFTP.

## 1. Сервер Reg.ru

Рекомендуется Ubuntu 22.04+, 1–2 GB RAM (модель embeddings тяжёлая — лучше **2+ GB**).

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx git
sudo mkdir -p /opt/pyBot
sudo chown $USER:$USER /opt/pyBot
```

Загрузите код (git clone или sftp) в `/opt/pyBot`.

```bash
cd /opt/pyBot
python3 -m venv .venv
source .venv/bin/activate
# На VPS без видеокарты — CPU-зависимости (не requirements.txt с CUDA)
pip install -r requirements-cpu.txt
cp .env.example .env
nano .env   # токены, UnitPay, BILLING_PUBLIC_BASE_URL
```

> Если `pip` качает `nvidia-cudnn` на сотни MB — ты поставил CUDA-пакет. Останови, удали `.venv`, ставь `requirements-cpu.txt`.


## 2. `.env` на сервере (минимум)

```env
TELEGRAM_BOT_TOKEN=...
LLM_API_KEY=...
BILLING_OPEN_ACCESS=0
BILLING_HTTP_HOST=127.0.0.1
BILLING_HTTP_PORT=8088
BILLING_PUBLIC_BASE_URL=https://YOUR_DOMAIN

# После одобрения проекта UnitPay:
UNITPAY_PUBLIC_KEY=...
UNITPAY_SECRET_KEY=...
UNITPAY_PROJECT_ID=...
UNITPAY_RECEIPT_NDS=none
```

## 3. Nginx + HTTPS

```bash
sudo cp deploy/nginx-pybot.conf /etc/nginx/sites-available/pybot
sudo nano /etc/nginx/sites-available/pybot   # YOUR_DOMAIN
sudo ln -sf /etc/nginx/sites-available/pybot /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d YOUR_DOMAIN
```

Проверка: `curl -s https://YOUR_DOMAIN/health` → `ok`.

## 4. systemd

```bash
sudo cp deploy/pybot.service /etc/systemd/system/pybot.service
# В unit-файле User=/Group= должны совпадать с владельцем /opt/pyBot
# (если заливали под своим user — поставьте его вместо www-data).
sudo nano /etc/systemd/system/pybot.service
sudo systemctl daemon-reload
sudo systemctl enable --now pybot
sudo systemctl status pybot
```

Логи: `journalctl -u pybot -f`

## 5. UnitPay (заявка / касса)

Перед модерацией UnitPay нужны:

1. Домен + HTTPS на VPS (nginx из `deploy/nginx-pybot.conf` проксирует весь сайт на `:8088`).
2. В `.env` на сервере реквизиты ИП и бота — шаблон:
   [`deploy/merchant-for-server.env`](../deploy/merchant-for-server.env)
   (ИНН/ОГРНИП Чикасова уже проставлены; допиши `BOT_USERNAME`, email, телефон, домен).
3. Проверка страниц:
   - `https://ДОМЕН/` — описание и цены  
   - `/offer` `/privacy` `/refund` `/security` `/payment` `/contacts`  
   - `https://ДОМЕН/health` → `ok`  
   - handler: `https://ДОМЕН/billing/unitpay`
4. В кабинете UnitPay: проект = сайт (этот домен) или Telegram-бот + ссылка на сайт с офертой.
5. После одобрения: касса **Юнит.Чеки**, ключи `UNITPAY_*` в `.env`, `systemctl restart pybot`.
6. Тест: `/pay` → email → оплата → чек → `/balance`. В боте также `/legal`.

## 6. Telegram недоступен с VPS (ТСПУ)

Если `curl https://api.telegram.org` таймаутит — это фильтрация, не баг бота.

Бесплатный обход: **Cloudflare WARP** в режиме SOCKS-прокси на `127.0.0.1:40000`, в `.env`:

```env
TELEGRAM_PROXY=socks5://127.0.0.1:40000
```

SSH при этом не ломается (прокси только для бота).

## 7. Что не забыть

- Firewall: 80/443 открыты; 8088 снаружи **не** открывать (только localhost).
- `UNITPAY_SKIP_IP_CHECK` в проде не включать.
- Оферта/реквизиты ИП — часто просят при модерации UnitPay (сайт или сообщение в боте).
