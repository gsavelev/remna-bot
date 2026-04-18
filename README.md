# Remna Bot

Телеграм-бот для выдачи персональных `subscription_url` из панели Remnawave.

Бот работает асинхронно на `aiogram`, хранит состояние в SQLite через SQLAlchemy ORM и умеет:

- проверять, состоит ли пользователь в нужном Telegram-чате;
- создавать или повторно отдавать персональную подписку Remnawave;
- назначать дефолтный internal squad при создании пользователя в Remnawave;
- сохранять данные пользователей и подписок в локальную БД;
- удалять пользователей Remnawave по `uuid` или `username` через админ-команды.

## Как это работает

Пользователь пишет боту `/start`.

Если пользователь не состоит в чате, указанном в `TG_CHAT_ID`, бот отвечает `Service forbidden.` и не продолжает диалог.

Если пользователь состоит в чате, бот:

- обновляет запись о пользователе в таблице `users`;
- создает подписку в Remnawave или возвращает уже существующую;
- отправляет пользователю `subscription_url`;
- показывает подсказку: вставить ссылку в Happ: `https://www.happ.su/main/ru`.

Администраторы, перечисленные в `TG_ADMIN_IDS`, дополнительно получают команды удаления:

- `/delete_uuid <uuid>`
- `/delete_username <username>`

## Стек

- Python 3.12
- aiogram 3
- SQLAlchemy 2
- SQLite
- Remnawave SDK
- Docker Compose

## Структура проекта

```text
.
├── src/
│   ├── app.py         # точка входа
│   ├── handlers.py    # Telegram handlers и логика бота
│   ├── database.py    # ORM-модели и доступ к БД
│   ├── rw_client.py   # клиент для Remnawave API
│   └── config.py      # чтение конфигурации из env
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

## Переменные окружения

Пример лежит в [.env.example](/Users/g/Yandex.Disk.localized/Projects/snicklefritz/remna-bot/.env.example:1).

Обязательные переменные:

- `REMNAWAVE_URL` — URL панели Remnawave.
- `REMNAWAVE_TOKEN` — API token Remnawave.
- `TG_BOT_TOKEN` — токен Telegram-бота.
- `TG_CHAT_ID` — ID чата или канала, членство в котором обязательно для доступа.
- `TG_ADMIN_IDS` — список Telegram user id администраторов через запятую.
- `DB_PATH` — путь до SQLite-файла.

Необязательные, но практически полезные:

- `REMNAWAVE_DEFAULT_INTERNAL_SQUAD_UUID` — UUID internal squad, который назначается новым пользователям по умолчанию.
- `SUBSCRIPTION_EXPIRE_DAYS` — срок жизни подписки в днях.
- `TRAFFIC_LIMIT_GB` — лимит трафика в гигабайтах.
- `TG_POLL_TIMEOUT_SECONDS` — timeout long polling для Telegram API.

Пример:

```env
REMNAWAVE_URL=https://panel.example.com/
REMNAWAVE_TOKEN=your_remnawave_token
REMNAWAVE_DEFAULT_INTERNAL_SQUAD_UUID=123e4567-e89b-12d3-a456-426614174000

SUBSCRIPTION_EXPIRE_DAYS=30
TRAFFIC_LIMIT_GB=50

TG_BOT_TOKEN=123456:telegram-token
TG_CHAT_ID=-1001234567890
TG_ADMIN_IDS=111111111,222222222
TG_POLL_TIMEOUT_SECONDS=30

DB_PATH=./data/remna-bot.db
```

## Локальный запуск

1. Создайте `.env` на основе `.env.example`.
2. Установите зависимости.
3. Запустите приложение.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m src.app
```

После старта бот начнет long polling и автоматически создаст SQLite-базу по пути из `DB_PATH`.

## Запуск через Docker Compose

1. Подготовьте `.env`.
2. Для контейнера путь `DB_PATH=./data/remna-bot.db` внутри `WORKDIR=/app` будет сохранен в `/app/data/remna-bot.db`.
3. Эта директория проброшена volume `./data:/app/data`, поэтому файл БД сохраняется на хосте.
4. Запустите сборку и сервис.

```bash
docker compose up --build -d
```

Остановить сервис:

```bash
docker compose down
```

Логи:

```bash
docker compose logs -f
```

## База данных

Бот создает две таблицы.

`users`:

- `id`
- `tg_id`
- `tg_username`
- `tg_name`
- `is_chat_member`
- `is_admin`
- `created_at`
- `updated_at`

`subscriptions`:

- `id`
- `user_tg_id`
- `uuid`
- `username`
- `path`
- `created_at`
- `updated_at`

## Команды бота

Пользовательские:

- `/start` — проверка доступа, создание или получение подписки и выдача `subscription_url`.

Админские:

- `/delete_uuid <uuid>` — удалить пользователя Remnawave по UUID.
- `/delete_username <username>` — удалить пользователя Remnawave по username.

## Поведение доступа

- Бот не открывает сервис пользователю, если тот не состоит в чате `TG_CHAT_ID`.
- Это ограничение действует на все взаимодействия с ботом, включая админ-команды.
- Результат членства также сохраняется в базе в поле `is_chat_member`.

## Docker-заметки

- При `DB_PATH=./data/remna-bot.db` SQLite-файл внутри контейнера будет лежать в `/app/data/remna-bot.db` – эта директория проброшена на хост как `./data`.
- Файл `.env` подключается автоматически через `env_file`.

## Что можно улучшить дальше

- добавить Alembic-миграции вместо `create_all`;
- вынести тексты сообщений в отдельный слой локализации;
- добавить structured logging и healthcheck для контейнера.

