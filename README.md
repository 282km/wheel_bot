# Telegram-бот «Колесо»

Лёгкий монолит на **Python**: один процесс поднимает **HTTP-сервер** (Starlette + Uvicorn) для Telegram **webhook**, статики WebApp и JSON API. Хранение — **SQLite** (WAL).

## Возможности

- **Обычные пользователи** в целевом чате: команда `/stat` или текст **«Статистика»** → выбор периода → сводка (число колёс, сумма заносов, топы).
- **Админы** в личке: кнопка **«Управление колесом»** открывает WebApp — участники, сбор текущего состава перетаскиванием, занос, суммы призов, запуск спина.
- **Суперадмины** (задаются в `.env`): вкладка «Админы» в WebApp — выдача/снятие прав администратора (кроме bootstrap-суперадминов из `.env`).

После спина бот отправляет в целевой чат **анонс со списком участников**, затем **одно видео (MP4)** со всеми раундами кручения (если на сервере есть `ffmpeg`, иначе GIF) и итоговое текстовое сообщение.

## Требования

- Python **3.11+** (рекомендуется).
- **ffmpeg** в `PATH` на сервере (для цветного MP4 колеса; без него — запасной GIF).
- Публичный **HTTPS** URL до этого сервера (для WebApp). На слабом VPS удобны **Caddy/nginx + Let’s Encrypt** или туннель (**Cloudflare Tunnel**, **ngrok**) на время настройки.

## Установка

```bash
cd wheel_bot
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt
copy .env.example .env          # Windows: copy
# cp .env.example .env
```

Заполните `.env` (см. комментарии в `.env.example`):

- `BOT_TOKEN` — у [@BotFather](https://t.me/BotFather).
- `SESSION_SECRET` — длинная случайная строка.
- `PUBLIC_BASE_URL` — внешний **https://…** без завершающего `/` (тот же хост и порт, что доступны из интернета, если без reverse proxy — как есть; за прокси обычно указывают публичный домен).
- `WEBHOOK_PATH` — путь webhook (по умолчанию `/telegram/webhook`).
- `WEBHOOK_SECRET` — секретный токен для заголовка `X-Telegram-Bot-Api-Secret-Token`.
- `TARGET_CHAT_ID` — id группы/супергруппы (часто отрицательный). Узнать можно, добавив [@userinfobot](https://t.me/userinfobot) или через логирование `message.chat.id`.
- `SUPERADMIN_IDS` — числовые Telegram user id через запятую.
- при необходимости `DATABASE_PATH` (по умолчанию `./data/app.db` рядом с проектом).

### BotFather

1. Создайте бота, получите токен.
2. **Menu Button → Configure menu button** (опционально): URL = `{PUBLIC_BASE_URL}/webapp/` — или просто пользуйтесь кнопкой из лички у бота.

### Права бота в чате

Добавьте бота в целевую группу. Нужно право **отправлять сообщения** и **использовать анимации** (GIF). Команды могут быть отключены в группе — для статистики достаточно текста «Статистика» или `/stat`.

## Запуск

Из каталога проекта (где лежит `requirements.txt`):

```bash
.venv\Scripts\activate
python -m wheel_bot
```

По умолчанию HTTP слушает `HTTP_HOST`:`HTTP_PORT` (8080), там же отдаются `/webapp/` и `/api/`.

Проверка здоровья: `GET /health`.

Если за **обратным прокси** (nginx, Caddy) при открытии сайта видите **«Rejected request from RFC1918 IP to public server address»**, это защита **Uvicorn**: нужно доверять заголовкам прокси. В проекте по умолчанию включено `forwarded_allow_ips=*` (или задайте `FORWARDED_ALLOW_IPS` в `.env`). Убедитесь, что прокси передаёт `Host`, `X-Forwarded-For`, `X-Forwarded-Proto`.

### Без nginx на своём ПК

1. **HTTPS прямо в Uvicorn** (ничего кроме бота ставить не нужно): в `.env` укажите пути к файлам сертификата **`SSL_CERTFILE`** (полная цепочка, например `fullchain.pem`) и **`SSL_KEYFILE`** (приватный ключ, например `privkey.pem`). Поставьте **`HTTP_PORT=443`** (или другой порт, тогда в `PUBLIC_BASE_URL` укажите порт: `https://домен:8443`) и пробросьте этот порт на роутере на ваш ПК. На Windows для порта **443** иногда нужен запуск терминала **от администратора**.

2. **Cloudflare Tunnel** (`cloudflared`): не обязательно открывать порты наружу; туннель ведёт на `http://127.0.0.1:8080`. В `.env` **`PUBLIC_BASE_URL`** = выданный Cloudflare HTTPS-адрес. Сертификат на ПК для этого варианта не обязателен.

3. **Один бинарник Caddy** (проще nginx): слушает 443 с авто/вашим сертификатом и проксирует на `127.0.0.1:8080` — по желанию.

## Роли

- Пользователь впервые пишет боту → в БД создаётся запись с ролью **user**.
- Id из `SUPERADMIN_IDS` при старте получают роль **superadmin** (если записи ещё не было).
- Админов добавляет суперадмин во вкладке WebApp «Админы» (по числовому **user id**).

## Ограничения и заметки

- Участника с историей в колёсах **нельзя удалить** — только редактировать ник/описание.
- Ник на покерок **уникален** без учёта регистра.
- Спин отправляет анимации в чат из `TARGET_CHAT_ID`; состав и суммы берутся из WebApp.
- Время периодов в статистике — **UTC** (границы месяца/года по UTC).

## Трансляция стола (/live)

OBS на ноутбуке шлёт RTMP на VDS (**MediaMTX**), nginx отдаёт HLS по HTTPS, бот по команде `/live` показывает плеер.

1. На VDS: `sudo bash scripts/install_mediamtx.sh`, фрагмент `deploy/nginx-live.conf.snippet` в nginx.
2. Секреты: `bash scripts/generate_live_secrets.sh` — path и пароль RTMP в `.env` и `/etc/mediamtx.yml`.
3. OBS: RTMP с логином/паролем, path из `LIVE_STREAM_PATH` (не используйте «poker»).
4. `git pull` и `sudo systemctl restart wheel-bot`.

Плеер: `{PUBLIC_BASE_URL}/live/`. Проверка: `GET /api/live/status`.

### Безопасность трансляции

- **RTMP:** `publishUser` / `publishPass` в `/etc/mediamtx.yml`; порт **1935** — по возможности только ваш IP.
- **HLS:** MediaMTX слушает `127.0.0.1:8888`; снаружи только через nginx `/hls/`.
- **Path:** случайное имя (`table_…`), совпадает в `.env` и MediaMTX.
- **nginx:** `limit_req_zone` для `/hls/` (см. комментарий в `deploy/nginx-live.conf.snippet`).
- Публичная ссылка HLS по-прежнему доступна тем, у кого она есть — для закрытого доступа нужны токены (отдельная задача).

## Структура

- `wheel_bot/` — код бота, API, рендер GIF.
- `static/webapp/` — статический фронт WebApp (без сборки).
- `static/live/` — страница просмотра HLS-трансляции.
- `data/app.db` — SQLite (создаётся автоматически).
