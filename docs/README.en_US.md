**Language:** [Русский](../README.md) **|** English

# Telegram bot for Remnawave

## Project description

This project is a Telegram bot that issues personal VPN subscriptions through the [Remnawave](https://github.com/remnawave) panel. The bot checks that the user belongs to a required chat, creates a subscription in Remnawave, and sends the user a `subscription_url` to import into a VPN client.

Main features:

- Enforce membership in a required chat / group
- Create and re-issue Remnawave subscriptions (`subscription_url`)
- Assign a default internal squad when creating users
- Configure subscription expiry and traffic limits
- Admin actions: add a subscription by `tg_id` / `username` and delete Remnawave users

## Installation and setup

### Prerequisites

- Python 3.12+
- Remnawave panel with an API token
- Telegram bot (created via [@BotFather](https://t.me/BotFather))
- Telegram chat or group where bot users must be members (the bot must be added to the chat and able to read member status)

### Environment variables

Required settings in `.env`:

- `REMNAWAVE_URL` — Remnawave panel URL (e.g. `https://panel.example.com/`)
- `REMNAWAVE_TOKEN` — Remnawave API token
- `TG_BOT_TOKEN` — Telegram bot token from @BotFather
- `TG_CHAT_ID` — chat / group ID; membership is required for access
- `TG_ADMIN_IDS` — administrator Telegram user IDs, comma-separated
- `DB_PATH` — SQLite file path (e.g. `./data/remna-bot.db`)

Optional settings:

- `REMNAWAVE_DEFAULT_INTERNAL_SQUAD_UUID` — internal squad UUID for new users
- `SUBSCRIPTION_EXPIRE_DAYS` — subscription lifetime in days (default: `30`)
- `SUBSCRIPTION_RESET_STRATEGY` – how often the user's traffic usage statistics should be reset
- `TRAFFIC_LIMIT_GB` — traffic cap in gigabytes (omit for unlimited)
- `TG_POLL_TIMEOUT_SECONDS` — Telegram long polling timeout (default: `30`)
- `VPN_CLIENT_DOWNLOAD_URL` — VPN client download page (default: [Happ](https://www.happ.su/main/ru))

See [.env.example](../.env.example) for a template.

### Install from repository

1. Clone the repository:

```bash
git clone https://github.com/gsavelev/remna-bot.git
cd remna-bot
```

2. Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Configure environment:

```bash
cp .env.example .env
# edit .env with your values
```

4. Run the bot:

```bash
python -m src.app
```

On first start, the SQLite database is created automatically at `DB_PATH`.

### Install from Packages (GitHub Container Registry)

This option is convenient for running on a server without a local Python install.

1. Pull the image from GHCR:

```bash
docker pull ghcr.io/gsavelev/remna-bot:latest
```

2. Clone the repository:

```bash
git clone https://github.com/gsavelev/remna-bot.git
cd remna-bot
```

3. Prepare the environment file:

```bash
cp .env.example .env
# edit ./.env with your values
```

4. Run the container:

```bash
docker run -d --name remna-bot \
  --restart unless-stopped \
  --env-file ./.env \
  -v "$(pwd)/data:/app/data" \
  --label com.centurylinklabs.watchtower.enable=true \
  ghcr.io/gsavelev/remna-bot:latest
```

5. (Optional) Automatic updates with `Watchtower`:\
If you run `watchtower`, it will pull updated images and restart the container. Because the example already includes the label `com.centurylinklabs.watchtower.enable=true`, the container will be updated automatically. Official docs: [https://containrrr.dev/watchtower/](https://containrrr.dev/watchtower/)

## Technical architecture

### File layout

```
./
├── src/
│   ├── app.py              # Entry point, init, and polling
│   ├── config.py           # Env loading and validation (Pydantic)
│   ├── database.py         # ORM models and async SQLite access
│   ├── rw_client.py        # Remnawave SDK wrapper
│   └── handlers.py         # Command and callback handlers
├── docs/
│   └── README.en_US.md     # English documentation
├── data/
│   └── remna-bot.db        # SQLite (created on first run)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

### Database

The project uses `SQLite` with `SQLAlchemy 2` (async driver `aiosqlite`). The schema is created via `create_all` on startup.

1. **`users`** — Telegram users:
   - `tg_id` — Telegram user ID
   - `tg_username` — Telegram username
   - `tg_name` — display name
   - `is_chat_member` — member of the required chat
   - `is_admin` — admin flag (from `TG_ADMIN_IDS`)
   - `created_at`, `updated_at` — timestamps

2. **`subscriptions`** — Remnawave subscriptions:
   - `user_tg_id` — foreign key to `users.tg_id`
   - `uuid` — Remnawave user UUID
   - `username` — Remnawave username
   - `path` — path segment from `subscription_url`
   - `created_at`, `updated_at` — timestamps

### Main components

#### 1. `app.py`

Application entry point:

- loads configuration from environment variables;
- initializes the database and Remnawave client;
- starts bot long polling.

#### 2. `config.py`

Pydantic-based configuration:

- `RemnawaveConfig` — URL, token, optional internal squad;
- `TelegramConfig` — bot token, chat, admins, subscription limits, DB path.

#### 3. `database.py`

`User` and `Subscription` models, upsert and delete helpers.

#### 4. `rw_client.py`

`RemnawaveUserManager` wraps the official SDK:

- create user;
- fetch by UUID or username;
- delete user.

#### 5. `handlers.py`

`RemnaTelegramBot`:

- `/start` — access check and subscription delivery;
- inline buttons for administrators;

## Remnawave integration

The bot talks to the panel via the [Remnawave SDK](https://pypi.org/project/remnawave/):

1. API token authentication (`REMNAWAVE_TOKEN`)
2. User creation with expiry, traffic limit, `telegram_id`, and internal squads
3. Reading `subscription_url` from the API response
4. Reusing an existing subscription when a local record exists and the user is still present in Remnawave

Remnawave usernames are derived from `telegram_username` and `tg_id` (max 36 characters).

## Security

- Secrets and tokens live only in environment variables
- Configuration validated with Pydantic (`extra = forbid`)
- Bot access limited to members of `TG_CHAT_ID`
- Admin actions restricted to IDs in `TG_ADMIN_IDS`

## Troubleshooting

1. **Remnawave connection errors** — check `REMNAWAVE_URL`, the token, and network reachability from the host running the bot.
2. **Database errors** — verify write permissions on the `data` directory (or the path in `DB_PATH`).
3. **Everyone gets `denied`** — confirm `TG_CHAT_ID`, that the bot is in the chat, and that it can call `getChatMember`.
4. **Subscription not created** — check panel limits, `REMNAWAVE_DEFAULT_INTERNAL_SQUAD_UUID`, and container / process logs.

---

*See also: [aiogram](https://docs.aiogram.dev/en/latest/), [Remnawave](https://github.com/remnawave).*
