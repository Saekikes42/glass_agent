<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useScreen } from './context.js'
import { statusName, webLink } from './api.js'
import Icon from './Icon.vue'

const { company, rank, me, meta, run, busy, error, signal } = useScreen()
const tab = ref('sources')
const rows = ref([])
const files = ref([])
const form = ref(false)
const type = ref('website')
const reference = ref('')
const competitor = ref(false)
const query = ref('')
const scope = ref('own')
const hits = ref(null)
let timer

async function sources() {
  rows.value = await company('/sources')
  clearTimeout(timer)
  if (!signal.aborted && rows.value.some(row => row.status === 'syncing')) timer = setTimeout(poll, 2000)
}
async function poll() {
  if (busy.value) timer = setTimeout(poll, 2000)
  else await run(sources)
}
function load(next = tab.value) {
  run(async () => {
    if (next === 'sources') await sources()
    else files.value = await company('/files')
    tab.value = next
  })
}
function add() {
  run(async () => {
    await company('/sources', 'POST', { type:type.value, ref:reference.value.trim(), is_competitor:competitor.value })
    form.value = false
    reference.value = ''
    await sources()
  })
}
function sourceAction(item, sync) {
  if (!sync && !window.confirm('Удалить источник?')) return
  run(async () => {
    await company('/sources/' + item.id + (sync ? '/sync' : ''), sync ? 'POST' : 'DELETE')
    await sources()
  })
}
function upload(event) {
  const file = event.target.files[0]
  if (!file) return
  const data = new FormData()
  data.append('file', file)
  event.target.value = ''
  run(async () => {
    await company('/files', 'POST', data)
    files.value = await company('/files')
  })
}
function deleteFile(file) {
  if (!window.confirm('Удалить документ?')) return
  run(async () => {
    await company('/files/' + encodeURIComponent(file.name), 'DELETE')
    files.value = await company('/files')
  })
}
function search() {
  run(async () => { hits.value = await company('/knowledge/search','POST',{ query:query.value.trim(), scope:scope.value }) })
}
onMounted(() => load())
onUnmounted(() => clearTimeout(timer))
</script>

<template>
  <div class="stack" :class="{ 'knowledge-page': true }">
    <div class="actions"><button :class="{ primary:tab === 'sources' }" :disabled="busy" @click="load('sources')">Источники</button><button :class="{ primary:tab === 'files' }" :disabled="busy" @click="load('files')">Документы</button></div>
    <form class="stack" @submit.prevent="search">
      <div class="actions search"><input aria-label="Поиск по базе знаний" v-model="query" required maxlength="1000" placeholder="Поиск…"><button :disabled="busy">Найти</button></div>
      <select v-model="scope" aria-label="Область поиска"><option value="own">Материалы компании</option><option value="competitors">Конкуренты</option><option value="all">Все материалы</option></select>
    </form>
    <p v-if="error" class="error" role="alert">{{ error }} <button @click="load()">Повторить</button></p>
    <p v-if="busy" role="status" class="muted">Загрузка…</p>
    <div v-if="hits !== null" class="stack">
      <div class="row"><h2>Результаты</h2><button @click="hits = null">Закрыть</button></div>
      <p v-if="!hits.length" class="muted">Совпадений нет.</p>
      <article v-for="hit in hits" :key="hit.chunk_id" class="panel">
        <a v-if="webLink(hit.url)" :href="webLink(hit.url)" target="_blank" rel="noopener noreferrer">{{ hit.title || hit.url }}</a><strong v-else>{{ hit.title }}</strong><p>{{ hit.text }}</p>
      </article>
    </div>
    <template v-else-if="tab === 'sources'">
      <p v-if="!rows.length && !busy && !error" class="muted">Источников пока нет.</p>
      <details v-for="item in rows" :key="item.id" class="panel">
        <summary><Icon name="link"/> {{ item.name || item.ref }} <small class="pill">{{ statusName(item.status) }}</small></summary>
        <div class="stack"><p>{{ item.ref }}</p><p v-if="item.last_error" class="error">{{ item.last_error }}</p>
          <div v-if="rank >= 1" class="actions"><button v-if="item.type !== 'telegram_bot' && item.type !== 'file'" :disabled="busy || item.status === 'syncing'" @click="sourceAction(item,true)">Синхронизировать</button><button :disabled="busy" @click="sourceAction(item,false)">Удалить</button></div>
        </div>
      </details>
      <button v-if="rank >= 1" class="row soft" @click="form = !form"><Icon name="plus"/>Добавить источник</button>
      <form v-if="form" class="panel stack" @submit.prevent="add">
        <label>Тип<select v-model="type"><option v-for="value in (meta.source_types || []).filter(value => value !== 'telegram_bot' || me?.is_admin)" :key="value">{{ value }}</option></select></label>
        <label>{{ type === 'telegram_bot' ? 'Числовой ID канала' : 'Адрес источника' }}<input v-model="reference" required maxlength="1000"></label>
        <label class="inline"><input v-model="competitor" type="checkbox">Источник конкурента</label>
        <button class="primary" :disabled="busy">Сохранить</button>
      </form>
    </template>
    <div v-if="hits === null && tab === 'sources'" class="glass-art" aria-hidden="true"></div>
    <template v-else>
      <label v-if="rank >= 1">Загрузить документ<input type="file" :disabled="busy" @change="upload"></label>
      <p v-if="!files.length && !busy" class="muted">Документов пока нет.</p>
      <div v-for="file in files" :key="file.name" class="panel row"><span>{{ file.name }}<small>{{ Math.ceil(file.size / 1024) }} КБ</small></span><button v-if="rank >= 1" :disabled="busy" @click="deleteFile(file)">Удалить</button></div>
    </template>
  </div>
</template>
