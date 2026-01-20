# tg-reminder

Телеграм-бот для задач и напоминаний с записью напрямую в Notion.

## Возможности
- Любое сообщение сразу превращается в задачу
- Напоминания только если они указаны
- Повторяемые задачи (ежедневно/еженедельно/каждые N дней)
- Парсинг текста и сроков через Perplexity (опционально)
- Запись задач в Notion
- Доступ только для разрешенных пользователей

## Требования
- Python 3.11+
- Токен Telegram бота

## Установка
```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## Запуск
```bash
set BOT_TOKEN=ваш_токен
set PERPLEXITY_API_KEY=ключ_если_есть
set NOTION_TOKEN=токен_если_есть
set NOTION_DB_ID=база_если_есть
set ALLOWED_USER_IDS=123456789,987654321
python -m src.bot
```

## Запуск в Docker
```bash
docker compose up --build
```

Переменные можно задать через `.env` рядом с `docker-compose.yml`.
Пример лежит в `env.sample`:
```
BOT_TOKEN=ваш_токен
PERPLEXITY_API_KEY=ключ_если_есть
NOTION_TOKEN=токен_если_есть
NOTION_DB_ID=база_если_есть
BOT_TZ=Europe/Moscow
ALLOWED_USER_IDS=123456789,987654321
```

## Примеры сообщений
- `Купить билеты завтра в 18:00 напомни за 2 часа`
- `Еженедельный отчет каждый понедельник 10:00 напомни за 1 час`

## Примечания
- Часовой пояс фиксирован: Europe/Moscow (можно изменить через `BOT_TZ`)
- Если напоминание не указано, бот не отправит уведомление
- `NOTION_TOKEN`, `NOTION_DB_ID` и `ALLOWED_USER_IDS` обязательны для запуска
