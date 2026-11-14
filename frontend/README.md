# Glass Agent · Telegram Mini App

12 мобильных экранов на Vue 3, JavaScript и Vite. Фронтенд работает с существующим FastAPI.

## Локальный запуск

Запустите базу, API и создайте компанию по [инструкциям backend](../docs/FRONTEND.md). Для локальной работы вне Telegram используйте описанный там `API_DEV_USER_ID` только в режиме разработки. Для фактической отправки запланированных постов запустите также scheduler.

Из папки `frontend`:

```powershell
npm.cmd ci
npm.cmd run dev
```

Откройте http://localhost:5173. Vite проксирует `/api` на `http://127.0.0.1:8000`. Для другого адреса задайте `GLASS_API_PROXY` в `.env.local`.

## Запуск в Telegram

1. Выполните `npm.cmd run build`. Разместите `dist` в корне HTTPS-домена.
2. На том же домене проксируйте `/api/*` к FastAPI. Для отдельного origin укажите `VITE_API_ORIGIN` при сборке и добавьте origin Mini App в `CORS_ORIGINS` backend.
3. Укажите HTTPS-адрес в `MINI_APP_URL` backend и откройте Mini App из бота. Фронтенд передаёт подписанный `initData` через заголовок `Authorization: tma …`.

Примеры фотографий обрезаны из предоставленного макета и используются для оформления. В карточке согласования отображается изображение самой публикации, если оно есть. Аналитика строится из агрегатов API по каналам. Вход ChatGPT с callback на `127.0.0.1` выполняется на машине, где запущен API.
