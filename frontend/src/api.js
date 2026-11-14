const base = (import.meta.env.VITE_API_ORIGIN || '').replace(/\/$/, '')

export const telegram = window.Telegram?.WebApp

export class ApiError extends Error {
  constructor(status, data) {
    const details = data?.error || {}
    super(status === 401 ? 'Откройте приложение из Telegram заново.' :
      [details.message, details.reason, ...(details.fields || []).map(field => field.msg)].filter(Boolean).join(' · ') ||
      (typeof data?.detail === 'string' ? data.detail : 'Ошибка API (' + status + ')'))
    this.status = status
  }
}

export async function request(path, method = 'GET', body, { signal, blob = false } = {}) {
  const headers = {}
  if (telegram?.initData) headers.Authorization = 'tma ' + telegram.initData
  if (body !== undefined && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json'
    body = JSON.stringify(body)
  }
  let response
  try {
    response = await fetch(base + path, { method, headers, body, signal })
  } catch (error) {
    if (error.name === 'AbortError') throw error
    throw new Error('Нет связи с сервером. Проверьте подключение и повторите.')
  }
  if (!response.ok) throw new ApiError(response.status, await response.json().catch(() => null))
  if (blob) return response.blob()
  if (response.status === 204) return null
  if (!response.headers.get('content-type')?.includes('application/json')) {
    throw new Error('Сервер вернул неожиданный ответ. Проверьте адрес API.')
  }
  return response.json()
}

export const platformName = value => ({ telegram:'Telegram', vk:'VK', max:'MAX' })[value] || value
export const statusName = value => ({
  draft:'Черновик', pending_approval:'На согласовании', revision:'На доработке', approved:'Согласовано',
  scheduled:'Запланировано', publishing:'Публикуется', published:'Опубликовано', failed:'Ошибка',
  rejected:'Отклонено', new:'Новый', draft_ready:'Черновик ответа', replied:'Отвечено', ignored:'Скрыт',
  syncing:'Синхронизация', ok:'Готово', error:'Ошибка'
})[value] || value
export const dateLabel = value => value ? new Date(value).toLocaleString('ru', { dateStyle:'medium', timeStyle:'short' }) : ''
export const isoDate = value => value ? new Date(value).toISOString() : null
export function localDate(value) {
  if (!value) return ''
  const date = new Date(value)
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16)
}
export function webLink(value) {
  try {
    const url = new URL(value)
    return ['https:', 'http:'].includes(url.protocol) ? url.href : ''
  } catch {
    return ''
  }
}
