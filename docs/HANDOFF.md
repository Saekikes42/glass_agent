# Передача проекта: что готово и что сделать участнику

Документ для участника, который разворачивает Glass Agent или дорабатывает backend и Mini App. Основные площадки — Telegram и VK. MAX запускается отдельно при наличии доступа.
Требования описаны в `GLASS_AGENT_CODEX_PROMPT.md` и `doc-1794511527.pdf`.
[Критерии приёмки](FEATURES_AND_LIMITS.md) · [Изменения с начала работы](PROJECT_CHANGES.md)

## 1. Состояние на момент передачи

| Часть | Состояние |
|---|---|
| Backend (FastAPI, PostgreSQL + pgvector, Alembic) | Реализован. Миграции и автотесты запускаются в CI |
| Парсеры: сайт, Telegram (веб-превью / экспорт / Telethon / Bot API), VK, PDF/DOCX/TXT | Реальные данные проверены для сайта, открытого Telegram, экспорта закрытого Telegram и стены VK |
| RAG (локальные эмбеддинги e5-small, гибридный поиск RRF, изоляция компаний) | Реализован. Сохранённые материалы загружаются командой `import-jsonl` |
| Агент + 19 инструментов, память, сводки, лимиты | Автотесты; с настоящим ChatGPT проверены ответы по RAG, история и создание черновика с изображением |
| Подключение ChatGPT Plus (Sign in with ChatGPT, OAuth + PKCE) | Реальный вход и ответ модели проверены; подробности в [результатах проверки](FEATURES_AND_LIMITS.md#проверка-на-ноутбуке-13112026) |
| Согласование и публикация Telegram | В живой переписке ChatGPT подготовил текст и PNG, пользователь вернул черновик на доработку и согласовал новую редакцию. Сообщение № 4 отправлено в GlassAITest, уведомление получено |
| Публикация VK и MAX | Проверена с тестовыми транспортами. Публикация в реальном сообществе VK и канале MAX предстоит |
| Регулярные задачи, отчёты, сбор результатов и комментариев | Плановый отчёт выполнен по таймеру и сохранён в PostgreSQL. Остальные сценарии покрыты автотестами |
| Telegram-бот (клиент + админ) | Живой клиентский сценарий проверен, административные команды покрыты автотестами |
| Личные боты VK и MAX | Диалог VK проверен в тестовом сообществе. MAX проверен с тестовым транспортом |
| REST API + initData + роли + OpenAPI | ✅ `docs/openapi.json` |
| Vue 3 Mini App | Фронтенд предстоит реализовать. Подготовлен [контракт API](FRONTEND.md) |

## 2. Первый запуск

Установите зависимости и поднимите PostgreSQL по [инструкции администратора](ADMIN_GUIDE.md).
Для диагностики запустите `python -m scripts.doctor` в активированной виртуальной среде.

Заполните в `.env`: `TG_BOT_TOKEN` (бот от @BotFather), `ADMIN_TELEGRAM_IDS` (свой Telegram ID — бот пишет
его на /start). `GLASS_SECRET_KEY` — ключ шифрования токенов (пусто = создастся `data/secrets/master.key`;
**не теряйте его**: без ключа сохранённые токены не расшифровать).

## 3. Подключить ChatGPT Plus (ваш аккаунт)

```text
python -m scripts.chatgpt_login
python -m scripts.chatgpt_login --test
python -m scripts.doctor --live
```

* Вход работает только когда браузер и backend на одной машине (redirect на `http://127.0.0.1:1455`).
  Для сервера: войти локально и перенести файл — [DEPLOY.md](DEPLOY.md).
* `CHATGPT_MODEL` пусто — берётся первая модель из `/v1/models` аккаунта.
* Подробности, ограничения подписки и почему облачный SaaS требует заявки в OpenAI — [CHATGPT_PLUS_OAUTH.md](CHATGPT_PLUS_OAUTH.md).
* `OPENAI_API_KEY` не используется нигде, это проверяет тест. Не добавляйте платный API.

**Проверка после входа**
1. `chatgpt_login --test` — модель отвечает.
2. В боте: «подготовь пост про энергосберегающее стекло» — агент должен вызвать `create_post_draft`,
   `prepare_post_image`, `submit_for_approval`. Если модель отвечает не JSON, разбор
   (`app/ai/agent.py: parse_action`) отдаст текст как финальный ответ — смотрите `meta.steps` сообщения.
3. Промпты агента — `app/ai/context_builder.py` (AGENT_RULES) и `app/ai/tools.py` (GEN_RULES, post_rules).
   Подстройка промптов под реальную модель — ожидаемая доработка.
4. Возможное улучшение: нативные function tools вместо JSON в тексте (документация разрешает функции в namespaces).

## 4. Подключить VK

1. **Чтение стены.** В `VK_ACCESS_TOKEN` задайте сервисный ключ приложения или пользовательский токен
   для `groups.getById` и `wall.get`. `python -m scripts.vk_token` проверит готовый ключ. Для ссылки OAuth
   создайте приложение VK и укажите его `VK_APP_ID`. [Схема `wall.get`](https://github.com/VKCOM/vk-api-schema/blob/master/wall/methods.json#L496)
2. **Бот в личных сообщениях.** Для `VK_BOT_TOKEN` нужен ключ сообщества с правами на сообщения.
   Укажите `VK_BOT_GROUP_ID`, включите Bots Long Poll и события `message_new`, `message_event`.
   [Настройка личного бота](ADMIN_GUIDE.md#4-подключить-личные-сообщения-vk)
3. **Публикация и ответы.** Нужен ключ пользователя — администратора сообщества с правами `wall,photos,offline`.
   Сохраните ключ как секрет компании командой `python -m scripts.manage secret <slug> vk_token`
   или как `VK_PUBLISH_TOKEN` в локальном `.env`. Команда `scripts.vk_token` проверяет чтение и записывает
   только `VK_ACCESS_TOKEN`.
4. Канал: `python -m scripts.manage settings <slug> channels.vk=club123456` (или в боте `/setchannel vk club123456`).
5. Выполните `python -m scripts.doctor --live` и синхронизацию источника `vk`.
   `python -m scripts.doctor --live --vk-publish-company <slug>` проверяет права ключа на целевое сообщество без отправки.
   Право на публикацию проверяйте тестовым постом с `platform=vk` в отдельном сообществе.

Чтение стены проверено на `salavatsteklo`. Личные события и кнопка отклонения проверены в «Glass Agent — тест».
Живая публикация требует отдельной проверки с ключом пользователя.
Код чтения в `app/parsers/vk_parser.py`, личного диалога в `app/bot/vk_bot.py`, публикации в
`app/services/platforms.py`, комментариев в `app/services/inbox.py`.

## 5. Подключить MAX

1. Для бота нужен верифицированный профиль организации, ИП или самозанятого. После модерации сохраните токен как
   `MAX_BOT_TOKEN` в `.env`. [Условия регистрации](https://dev.max.ru/help/chatbots)
2. Для личного чата запустите отдельный профиль, настройте HTTPS-вебхук с секретом и привяжите ID пользователя.
   [Развёртывание MAX](DEPLOY.md#дополнительные-боты-vk-и-max)
3. Для публикации добавьте бота администратором тестового канала, узнайте `chat_id` и задайте `channels.max`.

Личный диалог, согласование и публикация покрыты автотестами. Вебхук проверен локально в Docker. В текущей
установке нет бизнес-профиля и рабочего токена для живой проверки. Сбор комментариев к публикациям MAX остаётся в плане.

## 6. Карта кода

```
app/
  ai/            connectors/ (chatgpt_plus, dev_mock, base), auth/ (oauth, flow, token_*),
                 agent.py (цикл агента), tools.py (инструменты), context_builder.py, orchestrator.py (чат компании)
  api/           main.py (приложение, формат ошибок), routes.py (эндпоинты), deps.py (initData, роли), schemas.py
  bot/           bot.py (Telegram), vk_bot.py, max_bot.py, social.py (диалог и роли), inbox.py (очередь событий)
  db/            models.py (все таблицы), session.py (подключение, миграции)
  parsers/       website, telegram_*, vk, file
  rag/           chunking, embeddings (e5), index (гибридный поиск)
  services/      posts (статусы и согласование), checks (проверка перед публикацией), publisher + platforms
                 (Telegram/VK/MAX), tasks (регулярные задачи), inbox (комментарии), analytics (статистика, отчёты),
                 memory, usage (расход AI), secrets (токены компаний), images (шаблоны Pillow), sources
scripts/         manage (CLI), doctor, chatgpt_login, vk_token, bot, scheduler, parse_sources, telegram_watch
migrations/      Alembic
tests/           pytest (нужна БД glass_test на порту 5433)
```

Профиль `app` запускает API, Telegram-бота и планировщик. Профили VK и MAX запускаются отдельно. [Развёртывание](DEPLOY.md)

## 7. Как расширять

* **Новый инструмент агента** — функция с декоратором `@tool(...)` в `app/ai/tools.py`; компания берётся из
  `ctx.company_id` (никогда не из аргументов модели); вызовы модели — только через `ctx.llm()/ctx.complete()`
  (учёт расхода и лимиты).
* **Новая площадка** — класс с `async publish(post, target) -> {"message_id", "url", "chat_id"}` в
  `app/services/platforms.py`, ветка в `PublisherHub.resolve`, лимит текста в `services/checks.py`,
  значение в `posts.PLATFORMS`.
* **Новый вид регулярной задачи** — функция `_run_<kind>` и запись в `RUNNERS` в `app/services/tasks.py`.
* **Изменение схемы БД** — правка `app/db/models.py`, затем
  `python -m alembic revision --autogenerate -m "..."`. Проверьте файл в `migrations/versions/` и примените
  миграции через `python -m scripts.manage init-db`.
* **Новый эндпоинт** — `app/api/routes.py`, доступ через `Depends(access("<роль>"))`, схема ответа в `schemas.py`,
  затем `python -m scripts.manage export-openapi docs/openapi.json` для фронтенда.

## 8. Правила проекта (не нарушать)

* Только ChatGPT Plus через Sign in with ChatGPT. Никаких `OPENAI_API_KEY`, cookie, неофициальных endpoint'ов.
* Тестовая модель (`MockConnector`, `AI_PROVIDER=mock_dev`) всегда помечена `is_mock=true` и «[ТЕСТОВАЯ МОДЕЛЬ]».
* Согласование сохраняет редакцию и канал. Правка текста, картинки, площадки, канала или предложенного времени
  возвращает материал на согласование. После обновления ранее одобренные и запланированные посты нужно согласовать заново.
* Основной Telegram-бот получает обновления диалога и каналов. Привязка `telegram_bot` делается по числовому ID
  канала `-100…`. Запускайте один процесс `getUpdates` на токен.
* Автоответы на комментарии — только при `settings.permissions.auto_reply=true` компании.
* Данные компании — только по `company_id` из авторизованного запроса.
* Тесты не ходят во внешние сервисы: фикстура `no_real_credentials` обнуляет токены из `.env`.

## 9. Известные ограничения и что доработать первым

1. Проверить отправку на тестовую стену VK с пользовательским ключом.
2. Проверить агента на наборе типовых поручений (см. раздел 3).
3. Для MAX предстоит живая проверка личного бота и публикации. Сбор комментариев к публикациям MAX ещё не реализован.
4. Бот хранит выбор компании в `users.prefs`; загрузка документов файлом в боте пока не сделана (только API).
5. SSRF-защита парсера сайта проверяет IP до запроса; закрепление адреса на время соединения (DNS rebinding) — не сделано.
6. Полный список для коммерческой версии — [ROADMAP.md](ROADMAP.md).
