import { inject, ref, onUnmounted } from 'vue'

export const appKey = Symbol('glass-agent')

export function useScreen() {
  const app = inject(appKey)
  const companyId = app.company.value?.id
  const controller = new AbortController()
  const busy = ref(false)
  const error = ref('')
  onUnmounted(() => controller.abort())

  const api = (path, method, body, options = {}) =>
    app.request('/api' + path, method, body, { signal: controller.signal, ...options })
  const company = (path, method, body, options) => api('/companies/' + companyId + path, method, body, options)

  async function run(action) {
    if (busy.value) return
    busy.value = true
    error.value = ''
    try {
      return await action()
    } catch (failure) {
      if (!controller.signal.aborted) error.value = failure.message
    } finally {
      busy.value = false
    }
  }

  return { ...app, activeCompany: app.company, api, company, busy, error, run, signal:controller.signal }
}
