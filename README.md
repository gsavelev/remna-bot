**Язык / Language:** Русский **|** [English](./docs/README.en_US.md)

# Telegram бот для Remnawave

## Описание проекта

Этот проект представляет собой Telegram-бота для выдачи персональных VPN-подписок через панель [Remnawave](https://github.com/remnawave). Бот проверяет членство пользователя в заданном чате, создаёт подписку в Remnawave и отправляет пользователю `subscription_url` для импорта в VPN-клиент.

Основные возможности:

- Проверка членства в обязательном чате / группе
- Создание и повторная выдача подписки Remnawave (`subscription_url`)
- Назначение internal squad по умолчанию при создании пользователя
- Ограничение срока действия подписки и лимита трафика
- Административные действия: добавление подписки по `tg_id` / `username` и удаление пользователя в Remnawave

## Установка и настройка

### Предварительные требования

- Python 3.12+
- Панель Remnawave с API-токеном
- Telegram-бот (созданный через [@BotFather](https://t.me/BotFather))
- Telegram-чат или группа, в которой должны состоять пользователи бота (бот должен быть добавлен в чат и иметь права видеть участников)

### Настройка переменных окружения

Обязательные параметры в `.env`:

- `REMNAWAVE_URL` — URL панели Remnawave (например: `https://panel.example.com/`)
- `REMNAWAVE_TOKEN` — API-токен Remnawave
- `TG_BOT_TOKEN` — токен Telegram-бота от @BotFather
- `TG_CHAT_ID` — ID чата / группы, членство в котором обязательно для доступа
- `TG_ADMIN_IDS` — ID администраторов через запятую
- `DB_PATH` — путь к файлу SQLite (например: `./data/remna-bot.db`)

Дополнительные параметры:

- `REMNAWAVE_DEFAULT_INTERNAL_SQUAD_UUID` — UUID internal squad для новых пользователей
- `SUBSCRIPTION_EXPIRE_DAYS` — срок жизни подписки в днях (по умолчанию: `30`)
- `SUBSCRIPTION_RESET_STRATEGY` – через какой период времени следует сбрасывать статистику использования трафика пользователем
- `TRAFFIC_LIMIT_GB` — лимит трафика в гигабайтах (если не задан — без лимита)
- `TG_POLL_TIMEOUT_SECONDS` — timeout long polling для Telegram API (по умолчанию: `30`)
- `VPN_CLIENT_DOWNLOAD_URL` — ссылка на страницу загрузки VPN-клиента (по умолчанию: [Happ](https://www.happ.su/main/ru))

Пример `.env` — в [.env.example](./.env.example).

### Установка из репозитория

1. Клонируйте репозиторий:

```bash
git clone https://github.com/gsavelev/remna-bot.git
cd remna-bot
```

2. Установите зависимости:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Настройте переменные окружения:

```bash
cp .env.example .env
# отредактируйте .env своими значениями
```

4. Запустите бота:

```bash
python -m src.app
```

При первом запуске SQLite-база создаётся автоматически по пути из `DB_PATH`.

### Установка из Packages (GitHub Container Registry)

Этот вариант удобен для запуска на сервере без локальной установки Python.

1. Скачайте образ из GHCR:

```bash
docker pull ghcr.io/gsavelev/remna-bot:latest
```

2. Клонируйте репозиторий:

```bash
git clone https://github.com/gsavelev/remna-bot.git
cd remna-bot
```

3. Подготовьте файл окружения:

```bash
cp .env.example .env
# отредактируйте ./.env своими значениями
```

4. Запустите контейнер:

```bash
docker run -d --name remna-bot \
  --restart unless-stopped \
  --env-file ./.env \
  -v "$(pwd)/data:/app/data" \
  --label com.centurylinklabs.watchtower.enable=true \
  ghcr.io/gsavelev/remna-bot:latest
```

5. (Опционально) Автоматическое обновление через `Watchtower`:\
Если запустить `watchtower`, он будет подтягивать обновлённый образ и перезапускать контейнер. Так как в примере уже добавлен label `com.centurylinklabs.watchtower.enable=true`, контейнер будет обновляться автоматически. Официальная документация: [https://containrrr.dev/watchtower/](https://containrrr.dev/watchtower/)

## Техническая архитектура

### Файловая структура

```
./
├── src/
│   ├── app.py              # Точка входа, инициализация и запуск polling
│   ├── config.py           # Загрузка и валидация конфигурации (Pydantic)
│   ├── database.py         # ORM-модели и асинхронный доступ к SQLite
│   ├── rw_client.py        # Обёртка над Remnawave SDK
│   └── handlers.py         # Обработчики команд и callback-кнопок
├── docs/
│   └── README.en_US.md     # Документация на английском языке
├── data/
│   └── remna-bot.db        # SQLite (создаётся при первом запуске)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

### База данных

Проект использует `SQLite` с `SQLAlchemy 2` (асинхронный драйвер `aiosqlite`). Схема создаётся через `create_all` при старте.

1. **`users`** — пользователи Telegram:
   - `tg_id` — ID пользователя в Telegram
   - `tg_username` — username в Telegram
   - `tg_name` — отображаемое имя
   - `is_chat_member` — состоит ли в обязательном чате
   - `is_admin` — флаг администратора (по `TG_ADMIN_IDS`)
   - `created_at`, `updated_at` — метки времени

2. **`subscriptions`** — подписки Remnawave:
   - `user_tg_id` — связь с `users.tg_id`
   - `uuid` — UUID пользователя в Remnawave
   - `username` — username в Remnawave
   - `path` — путь подписки из `subscription_url`
   - `created_at`, `updated_at` — метки времени

### Основные компоненты

#### 1. `app.py`

Главный файл приложения:

- загружает конфигурацию из переменных окружения;
- инициализирует БД и клиент Remnawave;
- запускает long polling бота.

#### 2. `config.py`

Загрузка и валидация конфигурации через Pydantic:

- `RemnawaveConfig` — URL, токен и optional internal squad;
- `TelegramConfig` — токен бота, чат, админы, лимиты подписки, путь к БД.

#### 3. `database.py`

Модели `User` и `Subscription`, функции upsert и удаления записей.

#### 4. `rw_client.py`

Класс `RemnawaveUserManager` для работы с Remnawave API через официальный SDK:

- создание пользователя;
- получение по UUID или username;
- удаление пользователя.

#### 5. `handlers.py`

Класс `RemnaTelegramBot`:

- команда `/start` — проверка доступа и выдача подписки;
- inline-кнопки для администраторов;

## Интеграция с Remnawave

Бот взаимодействует с панелью через [Remnawave SDK](https://pypi.org/project/remnawave/):

1. Аутентификация по API-токену (`REMNAWAVE_TOKEN`)
2. Создание пользователя с параметрами срока, лимитом трафика, `telegram_id` и internal squads
3. Получение `subscription_url` из ответа API
4. Повторное использование существующей подписки, если запись есть в локальной БД и пользователь найден в Remnawave

Имя пользователя в Remnawave формируется из `telegram_username` и `tg_id` (до 36 символов).

## Безопасность

- Секреты и токены хранятся только в переменных окружения
- Валидация конфигурации через Pydantic (`extra = forbid`)
- Доступ к боту ограничен членством в `TG_CHAT_ID`
- Административные действия доступны только ID из `TG_ADMIN_IDS`

## Возможные проблемы и решения

1. **Ошибки подключения к Remnawave** — проверьте `REMNAWAVE_URL`, токен и доступность панели с хоста, где запущен бот.
2. **Ошибки базы данных** — проверьте права на запись в каталог `data` (или путь из `DB_PATH`).
3. **Всем отвечает `denied`** — убедитесь, что `TG_CHAT_ID` верный, бот добавлен в чат и может вызывать `getChatMember`.
4. **Подписка не создаётся** — проверьте лимиты панели, корректность `REMNAWAVE_DEFAULT_INTERNAL_SQUAD_UUID` и логи контейнера / процесса.

---

*Дополнительно: [aiogram](https://docs.aiogram.dev/en/latest/), [Remnawave](https://github.com/remnawave).*
