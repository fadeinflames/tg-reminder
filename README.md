# tg-reminder

Телеграм-бот для задач и напоминаний с записью напрямую в Notion.

## Возможности
- Любое сообщение сразу превращается в задачу
- Напоминания только если они указаны
- Повторяемые задачи (ежедневно/еженедельно/каждые N дней)
- Парсинг текста и сроков через Perplexity (опционально)
- Запись задач в Notion
- Доступ только для разрешенных пользователей
- Автосводки задач в 10:00, 15:00 и 19:00 по МСК
- Проверка закрытых задач каждые `SYNC_INTERVAL_MINUTES`

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
set NOTION_PAGE_ID=страница_если_есть
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
NOTION_PAGE_ID=страница_если_есть
BOT_TZ=Europe/Moscow
ALLOWED_USER_IDS=123456789,987654321
NOTION_PROP_NAME=Name
NOTION_PROP_STATUS=Status
NOTION_STATUS_VALUE=Open
NOTION_PROP_DUE=Due
NOTION_PROP_REPEAT=Repeat
NOTION_PROP_DONE=Done
SYNC_INTERVAL_MINUTES=5
```

## Примеры сообщений
- `Купить билеты завтра в 18:00 напомни за 2 часа`
- `Еженедельный отчет каждый понедельник 10:00 напомни за 1 час`

## Примечания
- Часовой пояс фиксирован: Europe/Moscow (можно изменить через `BOT_TZ`)
- Если напоминание не указано, бот не отправит уведомление
- `NOTION_TOKEN`, `NOTION_DB_ID` и `ALLOWED_USER_IDS` обязательны для запуска
- Нужен либо `NOTION_DB_ID`, либо `NOTION_PAGE_ID`
- Для базы Notion чекбокс “Готово” задается через `NOTION_PROP_DONE`
- Если поля в базе Notion называются иначе, поменяй `NOTION_PROP_*`
- Частоту синхронизации закрытия меняй через `SYNC_INTERVAL_MINUTES`