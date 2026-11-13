# Развёртывание 24/7 на своей VM

```bash
git clone … && cd glass
test -e .env || cp .env.example .env
docker compose up -d db
docker compose --profile app up -d --build      # api (127.0.0.1:8000), bot, scheduler
docker compose logs -f bot scheduler
```

Заполните `TG_BOT_TOKEN` и `ADMIN_TELEGRAM_IDS` в `.env` перед запуском бота. Для согласований и отчётов из
дополнительных ботов настройте их ключи в том же `.env`, чтобы они были доступны API и планировщику.

* Первая сборка скачивает зависимости (~1,5 ГБ, torch для CPU); модель эмбеддингов скачивается при первом
  запросе в `data/hf-cache`.
* Миграции применяются автоматически при старте процессов. PostgreSQL блокирует параллельное применение миграций.
* API слушает только 127.0.0.1 — для Mini App поставьте перед ним nginx/Caddy с TLS и `CORS_ORIGINS`.
* `SCHEDULER_IN_API=false` (по умолчанию) — планировщик отдельным сервисом `scheduler`.
* На один токен Telegram запускайте одну реплику `bot`. Она принимает и команды, и новые публикации каналов,
  подключённых как `telegram_bot`. CLI-опрос через `getUpdates` с тем же токеном остановите перед запуском.
* После обновления согласуйте заново ранее одобренные и запланированные посты. Система сохраняет канал
  при согласовании и проверяет его вместе с содержимым перед отправкой.

## Дополнительные боты VK и MAX

Профили `vk` и `max` включаются отдельно. Они используют ту же БД и тот же каталог данных. Подготовка кабинетов и
ключей описана в [руководстве пользователя и администратора](USER_AND_ADMIN_GUIDE.md#личные-диалоги-в-vk-и-max).

**VK.** Укажите `VK_BOT_TOKEN` и положительный числовой `VK_BOT_GROUP_ID`. В сообществе включите сообщения, Bots
Long Poll и события `message_new` и `message_event`. Бот подключается исходящим запросом к API VK.

```bash
docker compose --profile vk up -d --build vk-bot
docker compose --profile vk logs -f vk-bot
```

Для одного сообщества запускайте один процесс опроса. Очередь и позиция опроса хранятся в PostgreSQL. Если
процесс остановился во время действия, проверьте его результат перед повторной отправкой команды.

**MAX.** Укажите `MAX_BOT_TOKEN` и `MAX_WEBHOOK_SECRET`. Контейнер принимает события на `0.0.0.0:8011`, порт
доступен на хосте по `127.0.0.1:8011`. Для этой конфигурации оставьте `MAX_BOT_PORT=8011`. Проксируйте внешний
`https://<домен>/max/webhook` на `http://127.0.0.1:8011/max/webhook`. На внешнем адресе нужен HTTPS на порту 443 с
доверенной полной цепочкой сертификатов. Подставьте свой домен до регистрации.

```bash
docker compose --profile max up -d --build max-bot
docker compose --profile max exec max-bot python -m scripts.max_bot --check
docker compose --profile max exec max-bot python -m scripts.max_bot --register https://example.com/max/webhook
docker compose --profile max logs -f max-bot
```

Режим `--check` проверяет доступность API и читает количество подписок. Регистрация выполняется после настройки
прокси. Для локальной разработки доступен `--poll`, если активного вебхука нет. Для уведомлений ботам VK и MAX
нужны привязанные личные аккаунты. Пользователь получает ID командой `/start` у бота. Оператор связывает его с
существующим пользователем, у которого уже есть роль в компании.

Входящие события MAX сохраняются в PostgreSQL. Ожидающие события обрабатываются после перезапуска. Если обработка
уже началась до остановки процесса, по истечении срока захвата событие помечается как ошибочное. Перед повтором
команды проверьте, успело ли выполниться запрошенное действие.

```bash
docker compose --profile app exec api python -m scripts.manage link-identity vk 123456 --telegram-id 987654
docker compose --profile app exec api python -m scripts.manage link-identity max 234567 --telegram-id 987654
```

Замените примерные ID собственными. Личные команды VK и MAX описаны в руководстве. Для администрирования
компаний используйте Telegram, API или административный CLI. Живое подключение кабинетов и отправку на каждой
площадке проверьте на своих тестовых аккаунтах. [Требования к вебхуку MAX](https://dev.max.ru/docs-api/methods/POST/subscriptions)

## ChatGPT Plus на VM (по документации OpenAI self-hosted-vms)

Redirect входа идёт на `127.0.0.1` машины с браузером, поэтому:
1. На своём компьютере (тот же код и тот же `GLASS_SECRET_KEY`): `python -m scripts.chatgpt_login`.
2. Перенести зашифрованный файл по SSH:
   `scp data/secrets/chatgpt_credentials.enc vm:glass/data/secrets/`
   (если ключ не задан в `.env` — ещё `data/secrets/master.key`). `agent_host_id` не переносить: у VM свой.
3. На VM: `docker compose exec api python -m scripts.chatgpt_login --status`.
4. Дальше токен обновляет VM; на компьютере файл удалить (refresh-токен одноразовый — два места его «сожгут»).

Ограничение OpenAI: для перенесённых сессий пока нет отдельного учёта расхода и отзыва по хосту.

## Резервные копии

```bash
docker exec glass-db pg_dump -U glass -d glass -Fc > backup_$(date +%F).dump
tar czf data_$(date +%F).tgz data/       # секреты, загруженные файлы, изображения
```
