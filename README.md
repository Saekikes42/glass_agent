# Glass Agent

Glass Agent — помощник для маркетинговой команды. В Telegram Mini App и чате с ботом команда работает с базой знаний, готовит контент, согласует публикации и настраивает задачи. Backend написан на FastAPI, Mini App — на Vue 3, JavaScript и Vite. PostgreSQL с pgvector хранит данные компаний и индекс поиска.

## Возможности

- **База знаний и AI-чат.** Поиск по материалам компании, ответы со ссылками на источники, синхронизация сайта и соцсетей, загрузка документов.
- **Публикации.** Черновики, изображения, согласование, возврат на доработку, расписание, публикация и статус отправки.
- **Shorts / TikTok.** Сценарий, изображения сцен, русская озвучка, музыка и вертикальный MP4. Сохранённый черновик можно открыть повторно. FCPXML доступен для ручного монтажа в DaVinci Resolve.
- **Работа команды.** Роли и отдельные данные компаний, аналитика, комментарии, AI-материалы и регулярные задачи. Доступные действия зависят от роли и подключения площадки.

## Быстрый запуск на Windows

Понадобятся Git, Python 3.12 или 3.13, Node.js 22.12+ (рекомендуется актуальная LTS), npm и Docker Desktop. Откройте PowerShell:

```powershell
git clone https://github.com/Saekikes42/glass_agent.git
cd glass_agent
python -m venv .venv
$glassPython = ".\.venv\Scripts\python.exe"
& $glassPython -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d --wait db
& $glassPython -m scripts.manage init-db
& $glassPython -m scripts.doctor
npm.cmd --prefix frontend ci
```

Первое обращение к поиску может загрузить модель эмбеддингов. Если PostgreSQL уже запущен отдельно, укажите его адрес в `DATABASE_URL` и проверьте наличие расширения pgvector. Для Compose на хосте порт базы — **5433**.

## Настройки

Задавайте параметры в локальном `.env` в корне проекта.

| Переменная | Назначение |
| --- | --- |
| `TG_BOT_TOKEN` | Токен бота из BotFather. Его используют бот, публикация и проверка подписи Telegram Mini App. |
| `ADMIN_TELEGRAM_IDS` | Числовые Telegram ID администраторов через запятую. Доступ к конкретной компании выдаётся отдельно. |
| `AI_PROVIDER` | `chatgpt_plus` для настроенного подключения или `mock_dev` для разработки с тестовыми ответами. |
| `MINI_APP_URL` | Внешний HTTPS-адрес Mini App. |
| `MINI_APP_STATIC_DIR` | `frontend/dist`, если FastAPI отдаёт собранный интерфейс. Для Vite оставьте пустым. |
| `API_DEV_USER_ID` | Вход без Telegram для локальной разработки. Укажите ID существующего участника компании. Для публичного сервера оставьте пустым. |
| `SCHEDULER_IN_API` | `true` для планировщика внутри API. Если запускаете `scripts.scheduler`, оставьте `false`. |
| `CODEX_IMAGE_EXECUTABLE` | Полный путь к исполняемому файлу Codex CLI для генерации изображений. |
| `DATA_DIR` | Каталог рабочих данных. По умолчанию `data`. |
| `GLASS_SECRET_KEY`, `MASTER_KEY_PATH` | Ключ шифрования или путь к файлу ключа. Сохраняйте ключ при переносе установки. |

### API и локальный интерфейс

В первом терминале из корня проекта:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

Во втором:

```powershell
npm.cmd --prefix frontend run dev
```

Vite откроет интерфейс на [localhost:5173](http://localhost:5173), API доступен на [localhost:8000/docs](http://localhost:8000/docs), состояние сервера — на [localhost:8000/health](http://localhost:8000/health). Если API слушает другой порт, задайте `GLASS_API_PROXY` в `frontend/.env.local`.

### Бот и планировщик

Запустите в отдельных терминалах из корня проекта:

```powershell
.\.venv\Scripts\python.exe -m scripts.bot
```

```powershell
.\.venv\Scripts\python.exe -m scripts.scheduler
```

Для одного токена запускайте один процесс опроса Telegram. Если согласованные посты остаются в расписании, проверьте работу планировщика.

### Mini App в Telegram

1. Выполните `npm.cmd --prefix frontend run build`.
2. В `.env` задайте `MINI_APP_STATIC_DIR=frontend/dist`, оставьте `API_DEV_USER_ID` пустым и запустите API из корня проекта.
3. Настройте HTTPS через обратный прокси или туннель, например `ngrok http 8000`. Проверьте `/health` по внешнему адресу. Укажите выданный HTTPS-адрес в `MINI_APP_URL`.
4. В BotFather задайте этот HTTPS-адрес для кнопки меню бота. Откройте приложение через эту кнопку: Telegram передаст подписанные данные пользователя, а API проверит доступ к компании.

При отдельном размещении фронтенда настройте проксирование `/api` к бэкенду. Для разных origin укажите `VITE_API_ORIGIN` при сборке и добавьте origin интерфейса в `CORS_ORIGINS`.

## Первое использование

1. Напишите боту `/start`. Администратор может создать компанию командой `/newcompany Демо`. Существующую компанию можно выбрать через `/companies` и `/use <slug>`.
2. Если Mini App показывает «Нет доступных компаний», возьмите Telegram ID с экрана доступа и добавьте пользователя командой `python -m scripts.manage add-member <slug> <telegram_id> editor`. Допустимые роли: `owner`, `approver`, `editor`, `viewer`.
3. В **Базе знаний** добавьте источник и запустите синхронизацию. Для первой проверки подойдут разрешённая страница сайта или тестовый документ.
4. Создайте черновик, согласуйте его и выберите время публикации. Для Telegram добавьте бота в целевой канал администратором с правом публикации и задайте канал через `/setchannel telegram @имя_канала`. Для закрытого канала используйте числовой ID вида `-100…`. Токены и права VK и MAX настраиваются отдельно.

### Источники

Поддерживаются сайты, публичное веб-превью Telegram, Telegram Bot API, история через Telethon, JSON из экспорта Telegram Desktop, стены VK и документы PDF/DOCX/TXT/MD. Для Telethon нужны `TG_API_ID`, `TG_API_HASH` и сессия аккаунта с доступом к источнику. Для VK нужен ключ чтения. Экспорт Telegram обновляется при повторной загрузке свежего файла.

```powershell
.\.venv\Scripts\python.exe -m scripts.manage add-source demo website https://example.com/
.\.venv\Scripts\python.exe -m scripts.manage sync demo
.\.venv\Scripts\python.exe -m scripts.manage search demo "тема для поиска"
```

Замените `demo` и адрес источника на свои.

## Подключение AI

Для реальных ответов текущий коннектор использует `AI_PROVIDER=chatgpt_plus`. На машине с браузером выполните:

```powershell
.\.venv\Scripts\python.exe -m scripts.chatgpt_login
.\.venv\Scripts\python.exe -m scripts.chatgpt_login --status
```

Вход сохраняет зашифрованные данные локально. На сервере без браузера настройте авторизацию по руководству. При ошибке доступа к `data/secrets` проверьте, что API и команда входа работают под пользователем с доступом к тому же файлу ключа и каталогу учётных данных. [Подключение ChatGPT](docs/CHATGPT_PLUS_OAUTH.md)

Для изображений можно использовать шаблоны Pillow или подключить Codex CLI. Для Codex выполните вход на машине сервера и укажите `CODEX_IMAGE_EXECUTABLE`. Процесс API должен видеть этот путь и данные авторизации Codex в своём окружении.

### Shorts / TikTok

1. В Mini App откройте **Shorts / TikTok**, укажите тему, число сцен и длительность.
2. Агент создаёт сценарий и генерирует кадры через Codex CLI. FFmpeg из зависимости `imageio-ffmpeg` собирает MP4. Для голоса сервер отправляет текст озвучки в Microsoft Edge TTS. Требуется исходящий доступ к `speech.platform.bing.com:443`.
3. После сборки MP4 доступен в сохранённых черновиках компании. Для черновика с сохранёнными кадрами можно собрать MP4 повторно. В Telegram скачивание готовых материалов использует штатный диалог, когда его поддерживает клиент.
4. Для ручного монтажа экспортируйте FCPXML. Он ссылается на файлы сцен на машине сервера. При импорте на другом компьютере перенесите исходники и привяжите их в Resolve.

## Развёртывание и данные

Docker Compose позволяет запустить бэкенд, бота и планировщик командой `docker compose --profile app up -d --build`. При таком запуске держите `SCHEDULER_IN_API=false`. Соберите фронтенд отдельно и подключите его к контейнеру или отдавайте через внешний прокси. Для Codex CLI внутри контейнера отдельно обеспечьте наличие исполняемого файла и его авторизации. Для постоянного HTTPS и резервных копий используйте [руководство по развёртыванию](docs/DEPLOY.md).

Храните вне Git `.env`, ключи, сессии, выгрузки, журналы и медиа компаний. Для восстановления установки нужны резервная копия PostgreSQL, каталог `DATA_DIR` и ключи шифрования. Если меняете URL туннеля, обновите `MINI_APP_URL` и адрес кнопки в BotFather.

## Проверки

```powershell
npm.cmd --prefix frontend run build
docker compose exec db createdb -U glass glass_test
.\.venv\Scripts\python.exe -m pytest -q
```

Тестовую базу создают один раз. Если задаёте `TEST_DATABASE_URL`, используйте отдельную тестовую БД: тесты пересоздают таблицы.

## Документация

| Раздел | Руководство |
| --- | --- |
| Работа пользователей | [Telegram](docs/TELEGRAM_BOT_GUIDE.md), [VK](docs/VK_BOT_GUIDE.md), [общее руководство](docs/USER_GUIDE.md) |
| Компании и доступы | [Администрирование](docs/ADMIN_GUIDE.md), [новая компания](docs/NEW_COMPANY.md) |
| Интерфейс и API | [Фронтенд](frontend/README.md), [контракт API](docs/FRONTEND.md) |
| Эксплуатация | [Развёртывание](docs/DEPLOY.md), [функции и ограничения](docs/FEATURES_AND_LIMITS.md) |
