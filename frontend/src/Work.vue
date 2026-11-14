<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useScreen } from './context.js'
import { dateLabel, platformName, statusName } from './api.js'
import Icon from './Icon.vue'

const props = defineProps({ page:String })
const { company, rank, meta, run, busy, error } = useScreen()
const analytics = ref(null)
const report = ref('')
const days = ref(7)
const competitors = ref(null)
const rows = ref([])
const status = ref('')
const selected = ref('')
const reply = ref('')
const item = computed(() => rows.value.find(row => row.id === selected.value))
const kind = ref('')
const result = ref(null)
const mock = ref(false)
const shortTopic = ref('')
const shortSceneCount = ref(4)
const shortDuration = ref(4)
const shortPackage = ref(null)
const shortImageUrls = ref([])
const shortVideoUrl = ref('')
const shortDrafts = ref([])
const shortDraftScript = ref('')
const materials = [['strategy','Стратегия','target'], ['content-plan','Контент-план','calendar'], ['video-script','Сценарий видео','video'], ['topics','Темы','idea']]
const fields = reactive({ topic:'', focus:'', period:'неделя', duration:30, count:5 })
const taskKinds = { report:'Отчёт', sync_sources:'Синхронизация источников', collect_results:'Сбор результатов', collect_comments:'Сбор комментариев', agent:'Поручение AI' }
const taskId = ref(null)
const task = reactive({ kind:'report', title:'', time:'09:00', tz:Intl.DateTimeFormat().resolvedOptions().timeZone, weekdays:[], interval:60, mode:'daily', params:{}, prompt:'', notify:true })
const dayNames = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс']
const day = ref(null)
const taskRows = computed(() => rows.value.filter(row => day.value === null || !row.schedule.weekdays?.length || row.schedule.weekdays.includes(day.value)))
const channels = computed(() => Object.entries(analytics.value?.channels || {}).filter(([name]) => !name.endsWith('_competitors')))
const postingHours = computed(() => {
  const counts = Array(24).fill(0)
  for (const [, channel] of channels.value) {
    for (const [hour, count] of channel.posting_hours_utc || []) counts[hour] += count
  }
  return counts
})
const peakHour = computed(() => Math.max(...postingHours.value))
const hourPath = computed(() => postingHours.value.map((count, hour) =>
  (hour ? "L" : "M") + (hour * 12 + 6) + "," + (91 - count / (peakHour.value || 1) * 78)
).join(" "))
const maxPosts = computed(() => Math.max(1, ...channels.value.map(([, metrics]) => metrics.posts)))
const formatNum = value => new Intl.NumberFormat('ru').format(value)
const display = value => typeof value === 'string' ? value : JSON.stringify(value, null, 2)

function openComment(row) {
  selected.value = row?.id || ''
  reply.value = row?.reply_text || ''
}
async function load() {
  if (props.page === 'analytics') analytics.value = await company('/analytics')
  if (props.page === 'tasks') rows.value = await company('/tasks')
  if (props.page === 'inbox') {
    rows.value = await company('/inbox' + (status.value ? '?status=' + encodeURIComponent(status.value) : ''))
    openComment(rows.value.find(row => row.id === selected.value) || rows.value[0])
  }
  if (props.page === 'shorts') shortDrafts.value = (await company('/shorts/drafts')).drafts || []
}
function commentAction(action) {
  const path = '/inbox/' + item.value.id
  if (action === 'send' && !window.confirm('Отправить ответ в ' + platformName(item.value.platform) + '?')) return
  run(async () => {
    if (['save','send'].includes(action) && rank.value >= 1 && reply.value.trim() && reply.value !== item.value.reply_text) {
      openComment(await company(path, 'PATCH', { reply_text:reply.value.trim() }))
    }
    if (action !== 'save') openComment(await company(path + '/' + action, 'POST'))
    await load()
  })
}
function generate() {
  run(async () => {
    const data = await company('/content/' + kind.value, 'POST', fields)
    result.value = data.result
    mock.value = !!data.ai?.is_mock
  })
}
function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}
function requestTelegramDownload(file) {
  const webApp = window.Telegram?.WebApp
  if (typeof webApp?.downloadFile === 'function') {
    webApp.downloadFile({ url:file.url, file_name:file.filename }, accepted => {
      if (!accepted) webApp.showAlert?.('Скачивание отменено.')
    })
  } else if (typeof webApp?.openLink === 'function') {
    webApp.openLink(file.url)
  } else {
    window.open(file.url, '_blank', 'noopener')
  }
}
function downloadShortProject() {
  if (!shortPackage.value?.fcpxml) return
  run(async () => {
    if (shortPackage.value.id) {
      const file = await company('/shorts/' + encodeURIComponent(shortPackage.value.id) + '/download-link',
        'POST', { format:'fcpxml' })
      requestTelegramDownload(file)
    } else {
      downloadBlob(new Blob([shortPackage.value.fcpxml], { type:'application/xml' }),
        shortPackage.value.filename || 'glass-agent-shorts.fcpxml')
    }
  })
}
function downloadShortVideo() {
  if (!shortVideoUrl.value) return
  run(async () => {
    if (shortPackage.value?.id) {
      const file = await company('/shorts/' + encodeURIComponent(shortPackage.value.id) + '/download-link',
        'POST', { format:'mp4' })
      requestTelegramDownload(file)
    } else {
      const response = await fetch(shortVideoUrl.value)
      downloadBlob(await response.blob(), shortPackage.value?.video || 'glass-agent-shorts.mp4')
    }
  })
}
async function showShortDraft(draft) {
  for (const item of shortImageUrls.value) URL.revokeObjectURL(item.url)
  shortImageUrls.value = []
  if (shortVideoUrl.value) URL.revokeObjectURL(shortVideoUrl.value)
  shortVideoUrl.value = ''
  const project = await company('/shorts/project', 'POST', {
    topic:draft.topic, files:draft.scenes.map(scene => scene.image), seconds_per_shot:draft.seconds_per_scene || 4
  })
  shortPackage.value = { ...draft, ...project }
  shortTopic.value = draft.topic || ''
  shortDraftScript.value = draft.script || ''
  shortDuration.value = draft.seconds_per_scene || 4
  for (const scene of draft.scenes || []) {
    const blob = await company('/shorts/images/' + encodeURIComponent(scene.image), 'GET', undefined, { blob:true })
    shortImageUrls.value.push({ ...scene, url:URL.createObjectURL(blob) })
  }
  if (draft.video) {
    const blob = await company('/shorts/videos/' + encodeURIComponent(draft.video), 'GET', undefined, { blob:true })
    shortVideoUrl.value = URL.createObjectURL(blob)
  }
}
function openShortDraft(draft) {
  run(() => showShortDraft(draft))
}
function assembleShortDraft() {
  run(async () => {
    const draft = shortPackage.value
    const assembled = await company('/shorts/' + encodeURIComponent(draft.id) + '/assemble', 'POST', {
      topic:shortTopic.value.trim() || draft.topic || 'Короткий ролик', script:shortDraftScript.value,
      seconds_per_scene:shortDuration.value
    })
    await showShortDraft({ ...draft, ...assembled })
    shortDrafts.value = (await company('/shorts/drafts')).drafts || []
  })
}
function generateShortPackage() {
  run(async () => {
    shortPackage.value = null
    if (shortVideoUrl.value) URL.revokeObjectURL(shortVideoUrl.value)
    shortVideoUrl.value = ''
    for (const item of shortImageUrls.value) URL.revokeObjectURL(item.url)
    shortImageUrls.value = []
    const data = await company('/shorts/generate', 'POST',
      { topic:shortTopic.value, scene_count:shortSceneCount.value, seconds_per_scene:shortDuration.value })
    shortPackage.value = { ...data, filename:'glass-agent-shorts.fcpxml' }
    shortDraftScript.value = data.script || ''
    mock.value = !!data.ai?.is_mock
    for (const scene of data.scenes) {
      const blob = await company('/shorts/images/' + encodeURIComponent(scene.image), 'GET', undefined, { blob:true })
      shortImageUrls.value.push({ ...scene, url:URL.createObjectURL(blob) })
    }
    const video = await company('/shorts/videos/' + encodeURIComponent(data.video), 'GET', undefined, { blob:true })
    shortVideoUrl.value = URL.createObjectURL(video)
    shortDrafts.value = (await company('/shorts/drafts')).drafts || []
  })
}
function editTask(row) {
  const schedule = row?.schedule || {}
  taskId.value = row?.id || ''
  Object.assign(task, { kind:row?.kind || 'report', title:row?.title || '', time:schedule.daily_at || '09:00',
    tz:schedule.tz || Intl.DateTimeFormat().resolvedOptions().timeZone, weekdays:[...(schedule.weekdays || [])],
    interval:schedule.every_minutes || 60, mode:schedule.every_minutes ? 'interval' : 'daily',
    params:row?.params || {}, prompt:row?.params?.prompt || '', notify:row?.notify ?? true })
}
function saveTask() {
  run(async () => {
    const id = taskId.value
    const { kind, mode, time, tz, weekdays, interval, params, prompt, title, notify } = task
    await company('/tasks' + (id ? '/' + id : ''), id ? 'PATCH' : 'POST', {
      ...(id ? {} : { kind }), title, notify, params:kind === 'agent' ? { ...params, prompt } : params,
      schedule:mode === 'interval' ? { every_minutes:interval } : { daily_at:time, tz, ...(weekdays.length ? { weekdays } : {}) }
    })
    taskId.value = null
    await load()
  })
}
function taskAction(row, action) {
  if (action === 'delete' && !window.confirm('Удалить задачу?')) return
  run(async () => {
    const path = '/tasks/' + row.id
    if (action === 'toggle') await company(path, 'PATCH', { enabled:!row.enabled })
    if (action === 'run') await company(path + '/run', 'POST')
    if (action === 'delete') await company(path, 'DELETE')
    await load()
  })
}
onMounted(() => run(load))
onUnmounted(() => {
  for (const item of shortImageUrls.value) URL.revokeObjectURL(item.url)
  if (shortVideoUrl.value) URL.revokeObjectURL(shortVideoUrl.value)
})
</script>

<template>
  <div class="stack" :class="{ 'materials-page': page === 'materials' }">
    <p v-if="error" class="error" role="alert">{{ error }} <button v-if="page !== 'materials'" @click="run(load)">Повторить</button></p>
    <p v-if="busy" role="status" class="muted">Выполняется…</p>
    <template v-if="page === 'analytics'">
      <div v-if="analytics" class="panel stack">
        <h2>{{ formatNum(channels.reduce((sum,[,value]) => sum + value.posts, 0)) }} публикаций в источниках</h2>
        <p v-if="!channels.length" class="muted">Нет данных по каналам.</p>
        <figure v-if="peakHour" class="activity">
          <figcaption>Публикации по часам, UTC</figcaption>
          <svg viewBox="0 0 288 96" role="img" aria-label="Распределение публикаций по часам UTC">
            <path :d="hourPath + ' L282,96 L6,96 Z'" fill="#d0f1ef"/>
            <path :d="hourPath" fill="none" stroke="#008e85" stroke-width="2" stroke-linejoin="round"/>
          </svg>
          <div class="row"><small>00:00</small><small>06:00</small><small>12:00</small><small>18:00</small><small>23:00</small></div>
        </figure>
        <div v-for="[name, metrics] in channels" :key="name" class="metric">
          <span>{{ platformName(name) }}</span><strong>{{ formatNum(metrics.posts) }}</strong>
          <meter :value="metrics.posts" :max="maxPosts" :aria-label="platformName(name) + ': публикации'"></meter>
          <small v-if="metrics.views?.avg != null">В среднем {{ formatNum(metrics.views.avg) }} просмотров</small>
        </div>
      </div>
      <form class="panel stack" @submit.prevent="run(async () => { report = (await company('/reports?days=' + days)).text })">
        <label>Период отчёта, дней<input v-model.number="days" type="number" min="1" max="365" required></label>
        <button :disabled="busy">Сформировать отчёт</button>
      </form>
      <article v-if="report" class="panel copy">{{ report }}</article>
      <button class="row soft" :disabled="busy" @click="run(async () => { competitors = await company('/analytics/competitors') })"><Icon name="chart"/>Отчёт по конкурентам <Icon name="next"/></button>
      <div v-if="competitors" class="panel stack">
        <p v-if="competitors.note">{{ competitors.note }}</p>
        <div v-for="(entry,name) in competitors.note ? {} : competitors" :key="name" class="stack">
          <h2>{{ platformName(name) }}</h2><p v-if="entry.own">Свои публикации: {{ entry.own.posts }}</p>
          <p v-for="(stats, source) in entry.competitors" :key="source">{{ source }} · {{ stats.posts }} публикаций <span v-if="stats.views?.avg != null"> · {{ formatNum(stats.views.avg) }} просмотров в среднем</span></p>
        </div>
      </div>
    </template>
    <template v-else-if="page === 'inbox'">
      <select v-model="status" aria-label="Статус комментария" :disabled="busy" @change="run(load)">
        <option value="">Все комментарии</option><option v-for="value in meta.inbox_statuses" :key="value" :value="value">{{ statusName(value) }}</option>
      </select>
      <p v-if="!rows.length && !busy" class="muted">Комментариев пока нет.</p>
      <select v-if="rows.length > 1" :value="selected" aria-label="Выбрать комментарий" :disabled="busy" @change="openComment(rows.find(row => row.id === $event.target.value))">
        <option v-for="row in rows" :key="row.id" :value="row.id">{{ row.author_name }} · {{ row.text.slice(0, 60) }}</option>
      </select>
      <template v-if="item">
        <div class="bubble assistant"><strong>{{ item.author_name || platformName(item.platform) }}</strong><p class="copy">{{ item.text }}</p><small>{{ dateLabel(item.received_at) }} · {{ statusName(item.status) }}</small></div>
        <template v-if="rank >= 1 && !['replied','ignored'].includes(item.status)">
          <label class="panel ice">Черновик ответа<textarea v-model="reply" rows="4" maxlength="4000" :disabled="busy"/></label>
          <div class="actions"><button :disabled="busy" @click="commentAction('draft')">Черновик с AI</button><button :disabled="busy || !reply.trim()" @click="commentAction('save')">Сохранить</button></div>
          <button v-if="rank >= 2" class="row primary" :disabled="busy || !reply.trim()" @click="commentAction('send')"><Icon name="send"/>Отправить ответ <Icon name="next"/></button>
          <button :disabled="busy" @click="commentAction('ignore')">Скрыть комментарий</button>
        </template>
        <p v-else-if="item.reply_text" class="panel ice copy">{{ item.reply_text }}</p>
      </template>
    </template>
    <template v-else-if="page === 'materials'">
      <button v-for="[value,label,icon] in materials" :key="value" class="row" :aria-pressed="kind === value" :disabled="busy || rank < 1" @click="kind = value; result = null"><span class="tile-icon"><Icon :name="icon"/></span>{{ label }} <Icon name="next"/></button>
      <form v-if="kind" class="panel stack" @submit.prevent="generate">
        <label v-if="kind === 'strategy'">Фокус<input v-model="fields.focus"></label>
        <label v-if="kind === 'content-plan'">Период<input v-model="fields.period" required></label>
        <template v-if="kind === 'video-script'"><label>Тема<input v-model="fields.topic" required></label><label>Длительность, сек.<input v-model.number="fields.duration" type="number" min="10" max="180" required></label></template>
        <label v-if="kind === 'topics'">Количество<input v-model.number="fields.count" type="number" min="1" max="15" required></label>
        <button :disabled="busy" class="primary">Сгенерировать</button>
      </form>
      <div v-if="result != null" class="panel"><span v-if="mock" class="pill">Тестовый режим</span><p class="copy">{{ display(result?.text ?? result) }}</p></div>
      <div v-if="page === 'materials' && result == null" class="glass-art" aria-hidden="true"></div>
    </template>
    <template v-else-if="page === 'shorts'">
      <p class="muted">Агент создаст сценарий и кадры, добавит русскую озвучку и фоновую музыку, затем соберёт готовый вертикальный MP4.</p>
      <form class="panel stack" @submit.prevent="generateShortPackage">
        <label>Тема ролика<input v-model.trim="shortTopic" required maxlength="500" placeholder="Например, как выбрать стеклопакет"></label>
        <label>Число сцен<input v-model.number="shortSceneCount" type="number" min="2" max="6" required></label>
        <label>Длительность сцены, сек.<input v-model.number="shortDuration" type="number" min="2" max="15" required></label>
        <button class="primary" :disabled="busy || !shortTopic">Создать ролик</button>
      </form>
      <section v-if="shortDrafts.length" class="panel stack">
        <h2>Сохранённые черновики</h2>
        <button v-for="draft in shortDrafts" :key="draft.id" class="row" :disabled="busy" @click="openShortDraft(draft)">
          <span><strong>{{ draft.topic }}</strong><small>{{ draft.scenes.length }} сцен · {{ draft.status === 'ready' ? 'MP4 готов' : 'сохранены кадры' }}</small></span>
          <Icon name="next"/>
        </button>
      </section>
      <p v-if="busy && page === 'shorts'" class="muted" role="status">Создаю сценарий и кадры, записываю озвучку и собираю ролик. Это займёт несколько минут.</p>
      <article v-if="shortPackage" class="panel stack">
        <span v-if="mock" class="pill">Тестовый режим</span>
        <h2>Готовый ролик</h2>
        <video v-if="shortVideoUrl" class="short-video" :src="shortVideoUrl" controls playsinline preload="metadata"></video>
        <button v-if="shortVideoUrl" class="primary" @click="downloadShortVideo">Скачать готовый MP4</button>
        <template v-if="shortPackage.status === 'images_only'">
          <p class="muted">Кадры восстановлены. Текст сценария из старой версии не сохранился — укажите свою тему в форме выше и добавьте озвучку или оставьте поле пустым.</p>
          <label>Текст озвучки<textarea v-model="shortDraftScript" maxlength="12000" rows="5" placeholder="Можно оставить пустым: будет озвучена тема и названия сцен"/></label>
          <button class="primary" :disabled="busy" @click="assembleShortDraft">Собрать MP4 из сохранённых кадров</button>
        </template>
        <h2 v-if="shortPackage.script">Закадровый текст</h2><p v-if="shortPackage.script" class="copy">{{ shortPackage.script }}</p>
        <p v-if="shortPackage.caption" class="copy"><strong>Подпись</strong><br>{{ shortPackage.caption }}</p>
        <h2>Сцены</h2>
        <div class="short-scenes">
          <figure v-for="scene in shortImageUrls" :key="scene.image" class="short-scene">
            <img :src="scene.url" :alt="scene.title">
            <figcaption><strong>{{ scene.title }}</strong><small>{{ scene.image }}</small></figcaption>
          </figure>
        </div>
        <details><summary>Проект для ручного монтажа в DaVinci Resolve</summary><button class="row" @click="downloadShortProject">Скачать FCPXML</button></details>
      </article>
    </template>
    <template v-else>
      <div class="chips days">
        <button v-for="(label,index) in dayNames" :key="label" :class="{ primary:day === index }" :aria-pressed="day === index" @click="day = day === index ? null : index">{{ label }}</button>
      </div>
      <p v-if="!taskRows.length && !busy" class="muted">Задач нет.</p>
      <details v-for="row in taskRows" :key="row.id" class="panel">
        <summary><Icon :name="row.enabled ? 'check' : 'clock'"/> {{ row.title }} <small>{{ row.schedule.daily_at || (row.schedule.every_minutes + ' мин.') }}</small></summary>
        <div class="stack">
          <p v-if="row.next_run_at">Следующий запуск: {{ dateLabel(row.next_run_at) }}</p>
          <p v-if="row.last_status" class="copy">{{ statusName(row.last_status) }} · {{ display(row.last_result) }}</p>
          <div class="actions"><button v-if="rank >= 1" :disabled="busy || row.running" @click="taskAction(row,'run')">Запустить</button><button v-if="rank >= 3" :disabled="busy" @click="taskAction(row,'toggle')">{{ row.enabled ? 'Отключить' : 'Включить' }}</button></div>
          <div v-if="rank >= 3" class="actions"><button :disabled="busy" @click="editTask(row)">Редактировать</button><button :disabled="busy || row.running" @click="taskAction(row,'delete')">Удалить</button></div>
        </div>
      </details>
      <button v-if="rank >= 3 && taskId === null" class="primary row" @click="editTask()"><Icon name="plus"/>Новая задача <Icon name="next"/></button>
      <form v-if="taskId !== null" class="panel stack" @submit.prevent="saveTask">
        <label>Название<input v-model="task.title" required></label>
        <label>Тип<select v-model="task.kind" :disabled="!!taskId"><option v-for="value in meta.task_kinds" :key="value" :value="value">{{ taskKinds[value] || value }}</option></select></label>
        <label v-if="task.kind === 'agent'">Поручение<textarea v-model="task.prompt" required/></label>
        <label>Расписание<select v-model="task.mode"><option value="daily">По дням недели</option><option value="interval">Через интервал</option></select></label>
        <label v-if="task.mode === 'interval'">Минуты<input v-model.number="task.interval" type="number" min="5" required></label>
        <template v-else><label>Время<input v-model="task.time" type="time" required></label><label>Часовой пояс<input v-model="task.tz" required></label>
          <div class="chips"><label v-for="(label,index) in dayNames" :key="label" class="inline"><input type="checkbox" v-model="task.weekdays" :value="index">{{ label }}</label></div>
        </template>
        <label class="inline"><input type="checkbox" v-model="task.notify">Присылать уведомления</label>
        <div class="actions"><button class="primary" :disabled="busy">Сохранить</button><button type="button" @click="taskId = null">Отмена</button></div>
      </form>
    </template>
  </div>
</template>
