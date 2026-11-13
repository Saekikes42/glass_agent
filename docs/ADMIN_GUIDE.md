# Glass Agent. Инструкция для администратора

Telegram служит основным интерфейсом настройки. Бот VK использует те же компании, роли, базу знаний и историю. Для локальной установки достаточно PostgreSQL, двух ботов и планировщика. REST API нужен для загрузки файлов, точной настройки расписаний и интеграции с Mini App.

## 1. Подготовить окружение

Для локального запуска установите Python 3.13 и Docker Desktop. Выполните команды в Windows PowerShell из корня репозитория. Запустите Docker Desktop перед командой `docker compose`. Для Linux и постоянной работы на собственной VM используйте [DEPLOY.md](DEPLOY.md).

```powershell
python -m venv .venv
$glassPython = ".\.venv\Scripts\python.exe"
& $glassPython -m pip install --extra-index-url https://download.pytorch.org/whl/cpu -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
docker compose up -d db
& $glassPython -m scripts.manage init-db
```

Заполните локальный `.env`. Если база уже настроена, сохраните её `DATABASE_URL`. В стандартном Compose PostgreSQL доступен с хоста на порту 5433.

| Переменная | Значение |
| --- | --- |
| `TG_BOT_TOKEN` | Токен Telegram-бота от BotFather |
| `ADMIN_TELEGRAM_IDS` | Числовые Telegram ID администраторов через запятую |
| `VK_BOT_TOKEN`, `VK_BOT_GROUP_ID` | Ключ сообщества с правами на личные сообщения и положительный числовой ID сообщества |
| `VK_ACCESS_TOKEN` | Ключ приложения или аккаунта с доступом к `groups.getById` и `wall.get` нужных источников. Готовый ключ проверяет и сохраняет команда `python -m scripts.vk_token --group salavatsteklo --no-browser` |
| `VK_PUBLISH_TOKEN` | Ключ пользователя с правами публикации на целевой стене VK и загрузки фото. Для отдельной компании можно использовать секрет `vk_token` |
| `AI_PROVIDER` | `chatgpt_plus` для обычной работы |

Сохраните `.env`, резервные копии PostgreSQL и `data/secrets/` в закрытом хранилище. Приложение использует ключ Fernet из `GLASS_SECRET_KEY` или `data/secrets/master.key`. Модель эмбеддингов может скачиваться при первом запуске поиска или индексации.

## 2. Подключить ChatGPT и запустить процессы

Владелец подписки Plus открывает официальный вход на компьютере, где выполняется команда. После входа CLI проверяет разрешение на подписку и делает тестовый запрос.

```powershell
& $glassPython -m scripts.chatgpt_login
& $glassPython -m scripts.chatgpt_login --status
& $glassPython -m scripts.doctor --live
```

Если вход не завершился, проверьте подключение через `--status` и повторите вход. Без разрешения на использование подписки AI-функции возвращают `AI_CONNECTION_UNAVAILABLE`. Парсинг, поиск и согласование остаются доступны. Механизм переноса авторизации на собственную VM и условия облачного размещения описаны отдельно. [Подключение ChatGPT Plus](CHATGPT_PLUS_OAUTH.md)

В каждом терминале откройте корень репозитория и выполните одну команду. Для Telegram и VK используйте по одному процессу опроса на токен или сообщество.

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m scripts.bot
.\.venv\Scripts\python.exe -m scripts.vk_bot
.\.venv\Scripts\python.exe -m scripts.scheduler
```

Здесь `scripts.bot` запускает Telegram, `scripts.vk_bot` запускает VK, а `scripts.scheduler` выполняет задания и отправляет согласованные посты. Для запуска на собственной VM с Docker, после заполнения `.env`, выполните:

```bash
docker compose up -d db
docker compose --profile app up -d --build
docker compose --profile vk up -d --build vk-bot
docker compose ps
```

При отдельном сервисе планировщика оставьте `SCHEDULER_IN_API=false`. Проверка API доступна по `http://127.0.0.1:8000/health`, Swagger — по `/docs`. Авторизованные вызовы API передают заголовок `Authorization: tma <initData>` Telegram Mini App. `API_DEV_USER_ID` применяется только для локальной разработки. [Контракт API](FRONTEND.md)

Дополнительный бот MAX запускается отдельным профилем. Для обычной работы с Telegram и VK достаточно команд выше. [Дополнительные профили](DEPLOY.md)

## 3. Создать компанию и выдать доступ

1. В личном чате Telegram-бота нажмите Start. Команда `/start` показывает ваш числовой ID. После добавления его в `ADMIN_TELEGRAM_IDS` перезапустите процессы.
2. Отправьте `/newcompany Салаватстекло`. Бот покажет `slug`, короткий код компании. Уже созданную компанию откройте через `/companies` и `/use <slug>`.
3. Попросите участника нажать Start у Telegram-бота и сообщить свой Telegram ID. В выбранной компании отправьте `/invite 123456789 editor`, подставив его ID. Для согласующего используйте `approver`, для просмотра — `viewer`, для управления компанией — `owner`.
4. Заполните продукты, аудиторию, тон и ограничения компании через `PATCH /api/companies/{id}`. При изменении `settings` передайте весь прежний объект с внесёнными правками. Загрузите PDF, DOCX, TXT или MD через `POST /api/companies/{id}/files`. Для PDF без текстового слоя предварительно выполните OCR.
5. Попросите участника проверить `/companies` и `/use <slug>`. При отзыве доступа удалите членство через `DELETE /api/companies/{id}/members/{telegram_id}`. Права меняются сразу для обоих ботов.

Для новой установки те же действия можно начать через CLI. ID в примере замените своими значениями. Для существующей компании команда `create-company` не нужна.

```powershell
& $glassPython -m scripts.manage create-company "Салаватстекло" --slug salavatsteklo
& $glassPython -m scripts.manage add-member salavatsteklo 123456789 editor
& $glassPython -m scripts.manage list
```

Для настройки отдельного параметра бренда можно использовать CLI без замены всего объекта настроек.

```powershell
& $glassPython -m scripts.manage settings salavatsteklo 'brand.tone=спокойный и деловой'
```

## 4. Подключить личные сообщения VK

1. В тестовом сообществе VK включите сообщения сообщества и Bots Long Poll API. Установите версию API, совпадающую с `VK_API_VERSION` (в примере 5.199), и события `message_new`, `message_event`.
2. Создайте ключ сообщества для сообщений, сохраните его в `VK_BOT_TOKEN` и заполните `VK_BOT_GROUP_ID`. Перезапустите VK-бота. [Руководство VK по Long Poll](https://github.com/VKCOM/vk-php-sdk#61-long-poll)
3. Попросите участника отправить `/start` в личные сообщения сообщества и сообщить показанный VK ID. Проверьте принадлежность обоих аккаунтов и привяжите его к существующему пользователю.

```powershell
& $glassPython -m scripts.manage link-identity vk 123456789 --telegram-id 987654321
```

4. Попросите участника выполнить в VK `/companies` и `/use salavatsteklo`. Тестовое сообщество ранее настроенной локальной установки называется «Glass Agent — тест». Для повторного подключения к нему используйте сохранённый `.env` и команду запуска VK-бота.

## 5. Загрузить источники

В выбранной компании владелец может выполнить команды Telegram-бота.

```text
/addsource website https://salstek.ru/
/addsource telegram_web https://t.me/salstek
/addsource vk https://vk.ru/salavatsteklo
/sync
```

Для отдельных запусков через CLI сначала добавьте только отсутствующие источники, затем выполните синхронизацию. По умолчанию поиск идёт по собственным материалам компании. Для отдельного конкурентного источника добавьте `--competitor` при подключении.

```powershell
& $glassPython -m scripts.manage add-source salavatsteklo website https://salstek.ru/
& $glassPython -m scripts.manage add-source salavatsteklo telegram_web https://t.me/salstek
& $glassPython -m scripts.manage add-source salavatsteklo vk https://vk.ru/salavatsteklo
& $glassPython -m scripts.manage sync salavatsteklo --type vk
& $glassPython -m scripts.manage sync salavatsteklo
& $glassPython -m scripts.manage search salavatsteklo "светопропускание Ultraview R"
```

**Закрытый канал Telegram.** В Telegram Desktop откройте доступный вам канал и экспортируйте историю этого чата в JSON. Для импорта источник должен указывать на локальный `result.json`, доступный процессу приложения.

```powershell
& $glassPython -m scripts.manage add-source salavatsteklo telegram_export "C:\path\to\result.json"
& $glassPython -m scripts.manage sync salavatsteklo --type telegram_export
```

Для регулярного сбора доступной истории задайте `TG_API_ID` и `TG_API_HASH` с сайта Telegram и выполните `python -m scripts.telegram_login` для аккаунта, который имеет доступ. После сохранения `TG_SESSION_PATH` добавьте источник типа `telegram` и запустите синхронизацию. Наличие ссылки-приглашения само по себе не даёт доступ к истории. Для получения новых сообщений собственного канала добавьте бота в канал и настройте источник `telegram_bot` с полным числовым ID канала; источник подключает администратор платформы. [Получение Telegram API ID](https://core.telegram.org/api/obtaining_api_id)

## 6. Настроить публикации, комментарии и отчёты

В выбранной компании укажите тестовые каналы или сообщества. Добавьте Telegram-бота в тестовый канал администратором с правом публикации. Для VK у пользователя, чей ключ используется, должны быть права публикации на целевой стене и доступа к загрузке фото. Команда ниже принимает этот ключ как секрет компании.

```text
/setchannel telegram @test_channel
/setchannel vk club123456789
```

```powershell
& $glassPython -m scripts.manage secret salavatsteklo vk_token
& $glassPython -m scripts.doctor --live --vk-publish-company salavatsteklo
```

Первая команда запросит секрет без отображения значения. Вторая читает права `wall`, `photos` и доступ ключа к настроенному сообществу. Она подходит для проверки перед тестовой отправкой и не создаёт постов.

После изменения выбранной площадки, канала, текста, изображения или времени отправьте свежую редакцию на согласование. Для комментариев Telegram подключите бота к группе обсуждения и укажите `/setchannel telegram_discussion -1001234567890`. Сбор комментариев VK выполняет задача `collect_comments`. Ответы проверяются через Telegram `/inbox` или API. Автоответы можно разрешить отдельной настройкой `settings.permissions.auto_reply`. По умолчанию она выключена.

```powershell
& $glassPython -m scripts.manage tasks salavatsteklo
& $glassPython -m scripts.manage run-task salavatsteklo report
& $glassPython -m scripts.manage report salavatsteklo --days 7
```

Чтобы изменить расписание, используйте `PATCH /api/companies/{id}/tasks/{task_id}` от имени владельца. Пример расписания на понедельник, 09:00 Екатеринбурга — `{"daily_at":"09:00","weekdays":[0],"tz":"Asia/Yekaterinburg"}`. Для проверки запуска без сообщения задайте `{"every_minutes":5}`, оставьте планировщик запущенным и дождитесь обновления `last_run_at`. Команда `run-task` проверяет ручной запуск.

## 7. Проверить передачу пользователю

1. Проверьте доступ к своей компании и отсутствие чужой компании с отдельного аккаунта.
2. Отправьте вопрос с запросом источника и уточнение. После перезапуска повторите уточнение в том же контексте.
3. Создайте тестовый черновик с изображением. Верните его на доработку с комментарием. Проверьте обновлённую редакцию.
4. Отклоните один тестовый материал и проверьте отсутствие отправки. Для отдельного, разрешённого к публикации тестового поста проверьте сохранённый внешний ID и итоговое уведомление.
5. Проверьте срабатывание расписания без запроса в чате. Остановите тестовые процессы через Ctrl+C, если постоянный запуск ещё не настроен.

При сбоях начните с `python -m scripts.doctor`, журналов процессов и `last_error` источника или публикации. Если результат отправки неопределён, сначала проверьте целевую площадку. Перед развёртыванием сохраните резервную копию PostgreSQL и каталога `data/`. [Инструкция пользователя](USER_GUIDE.md) · [Проверки проекта](PROJECT_CHANGES.md) · [Развёртывание и резервные копии](DEPLOY.md)
