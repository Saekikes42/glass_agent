# Glass Agent — backend AI-маркетолога

Автономный AI-маркетолог для нескольких компаний: Telegram-бот и REST API для Mini App, база знаний компании
(сайт, каналы, документы), агент с инструментами, согласование и публикация в Telegram / VK / MAX, регулярные
задачи и отчёты. Модель — **только ChatGPT Plus через официальный Sign in with ChatGPT**, без платного OpenAI API.

Требования: [GLASS_AGENT_CODEX_PROMPT.md](GLASS_AGENT_CODEX_PROMPT.md), ТЗ хакатона `doc-1794511527.pdf`.

## Документация

| Документ | Для кого |
|---|---|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | пользователю: Telegram и VK, поручения, согласование и отчёты |
| [docs/ADMIN_GUIDE.md](docs/ADMIN_GUIDE.md) | администратору: запуск, роли, источники, публикации и диагностика |
| [docs/PROJECT_CHANGES.md](docs/PROJECT_CHANGES.md) | изменения с исходного коммита, реальные проверки и условия запуска |
| [docs/HANDOFF.md](docs/HANDOFF.md) | участнику проекта: что готово, как подключить ChatGPT/VK/MAX, карта кода, как расширять |
| [docs/FRONTEND.md](docs/FRONTEND.md) + [docs/openapi.json](docs/openapi.json) | разработчику Mini App (Vue 3) |
| [docs/NEW_COMPANY.md](docs/NEW_COMPANY.md) | инструкция по подключению новой компании |
| [docs/FEATURES_AND_LIMITS.md](docs/FEATURES_AND_LIMITS.md) | функции и ограничения, соответствие критериям приёмки ТЗ |
| [docs/CHATGPT_PLUS_OAUTH.md](docs/CHATGPT_PLUS_OAUTH.md) | проверка способа подключения ChatGPT Plus |
| [docs/COSTS.md](docs/COSTS.md) | оценка месячных затрат |
| [docs/ROADMAP.md](docs/ROADMAP.md) | доработки для коммерческого использования |
| [docs/DEPLOY.md](docs/DEPLOY.md) | развёртывание 24/7 на VM |
| [docs/DEMO.md](docs/DEMO.md) | сценарий демонстрации |

## Быстрый старт в Windows PowerShell

Установите Python 3.13 и запустите Docker Desktop. Выполняйте команды из корня репозитория.

```powershell
python -m venv .venv
$glassPython = ".\.venv\Scripts\python.exe"
& $glassPython -m pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d db
& $glassPython -m scripts.manage init-db
& $glassPython -m scripts.doctor
```

Заполните `TG_BOT_TOKEN` и `ADMIN_TELEGRAM_IDS` в `.env`. Для функций AI выполните вход через
`& $glassPython -m scripts.chatgpt_login`. В каждом терминале запустите `.\.venv\Scripts\Activate.ps1`
и выполните одну из команд.

```text
python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
python -m scripts.bot
python -m scripts.scheduler
```

Документация API доступна по адресу `http://127.0.0.1:8000/docs`. Для локального режима с тестовой моделью
см. [FRONTEND.md](docs/FRONTEND.md). Для тестов один раз создайте отдельную БД командой
`docker compose exec db createdb -U glass glass_test` и запустите `python -m pytest -q`. Фикстуры пересоздают
таблицы в тестовой БД. Для запуска всех процессов в Docker см. [DEPLOY.md](docs/DEPLOY.md).

## Статус

| Часть | Состояние |
|---|---|
| Подключение ChatGPT Plus (OAuth + PKCE, токены, лимиты, 503-повтор) | 🟢 реальный вход и ответ модели; обновление/ошибки проверены автотестами |
| Компании, роли, секреты, журнал, расход AI, миграции | ✅ |
| Постоянный чат компании, память (4 вида), сводки | ✅ |
| Агент: 19 инструментов, лимиты шагов/вызовов/времени/дня | ✅ автотесты; 🟢 живые RAG, контекст, черновик и изображение |
| Парсеры: сайт 🟢, Telegram 🟢, VK 🟢, PDF/DOCX/TXT | ✅ (🟢 — проверено на реальных данных) |
| RAG: локальные эмбеддинги, гибридный поиск, изоляция | ✅ 🟢 |
| Посты: проверки, согласование (одобрить / доработка / отклонить), публикация | 🟢 живой Telegram от поручения и доработки по кнопке до публикации с PNG; живое отклонение в VK. Публикация VK и MAX проверена с тестовыми транспортами |
| Регулярные задачи: отчёты, обновление знаний, результаты, комментарии, поручения агенту | ✅ |
| Комментарии и ответы (автоответ — по разрешению) | ✅ Telegram, VK; MAX — нет |
| Telegram-бот (клиент и настройка компаний без кода) | 🟢 живой клиентский сценарий, настройки компании покрыты автотестами |
| Личные боты VK и MAX (диалог, роли, согласование, отчёты) | ✅ автотесты; 🟢 личный диалог VK проверен в тестовом сообществе, MAX ожидает живой проверки |
| REST API + initData + OpenAPI | ✅ |
| Vue 3 Mini App | Фронтенд предстоит реализовать. Подготовлен [контракт API](docs/FRONTEND.md) |

В живом Telegram-диалоге ChatGPT Plus взял факт из тестового документа, подготовил текст и вызвал инструмент
для PNG. Бот принял доработку и согласование через кнопки, отправил сообщение № 4 в тестовый канал GlassAITest
и прислал уведомление. Проверены чтение стены `salavatsteklo` и живой диалог VK. Для публикации в VK нужен
отдельный ключ пользователя. MAX доступен отдельным профилем, для живого запуска нужен токен модерации.
Из закрытого канала «В команде I „СалаватСтекло“» импортированы все 917 сообщений: после группировки
альбомов получены 423 документа и 661 фрагмент RAG. Проверены поиск, изоляция компаний и повторный
импорт без дублей. Это снимок истории из Telegram Desktop; регулярный сбор через Telethon ещё не подключён.
Автотесты используют моки сетей и **тестовую модель**, помеченную `is_mock=true` / «[ТЕСТОВАЯ МОДЕЛЬ]»,
и не обращаются к внешним сервисам.

## Парсеры (CLI)

```text
python -m scripts.manage create-company "Салаватстекло" --slug salavatsteklo
python -m scripts.manage import-jsonl salavatsteklo
python -m scripts.manage add-source salavatsteklo website https://salstek.ru/
python -m scripts.manage add-source salavatsteklo telegram_web https://t.me/salstek
python -m scripts.manage sync salavatsteklo
python -m scripts.manage search salavatsteklo "светопропускание Ultraview R"
```

Создайте компанию один раз. `import-jsonl` загружает сохранённые данные из `data/salavatsteklo` в PostgreSQL
и строит индекс RAG. Для другого каталога укажите `--directory`.

* Сайт: httpx + BeautifulSoup + trafilatura, sitemap, robots.txt, SSRF-защита, лимиты, повторная синхронизация.
* Telegram: открытый канал — `telegram_web` без ключей, для отдельного наблюдения — `python -m scripts.telegram_watch`.
  Доступную историю закрытого канала можно загрузить через Telethon (`TG_API_ID`, `TG_API_HASH` и сессия)
  или экспорт JSON из Telegram Desktop. Для новых постов добавьте источник `telegram_bot` с числовым ID
  канала вида `-1001234567890`. Обновления Bot API получает основной бот.
* VK: для `groups.getById` и `wall.get` задайте `VK_ACCESS_TOKEN` с сервисным ключом приложения или пользовательским
  токеном. `python -m scripts.vk_token` принимает готовый ключ, а для получения по ссылке OAuth нужен `VK_APP_ID`.
  Ключ сообщества с правами на сообщения используется в `VK_BOT_TOKEN`. [Схема `wall.get`](https://github.com/VKCOM/vk-api-schema/blob/master/wall/methods.json#L496)
* Документы: PDF / DOCX / TXT / MD — загрузка через API или источник `file`.

После обновления согласуйте заново ранее одобренные и запланированные посты. При новом согласовании
система сохраняет редакцию и выбранный канал.
