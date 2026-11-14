<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useScreen } from './context.js'
import { dateLabel, isoDate, localDate, platformName, statusName, webLink } from './api.js'
import Icon from './Icon.vue'
import PostImage from './PostImage.vue'

const props = defineProps({ page:String, id:String })
const { company, go, rank, meta, me, busy, error, run } = useScreen()
const post = ref(null)
const editing = ref(false)
const manual = ref(false)
const withImage = ref(true)
const action = ref('')
const comment = ref('')
const autoRevise = ref(false)
const result = ref(null)
const generatingImage = ref(false)
const form = reactive({ topic:'', platform:'telegram', text:'', time:'', target:'' })
const template = ref('square')
const confirmPublish = ref(false)
const adapt = ref('vk')
const path = '/posts/' + props.id
const editable = computed(() => rank.value >= 1 && ['draft','revision','approved','pending_approval','scheduled','rejected','failed'].includes(post.value?.status))
const customImagePrompt = ref(null)
const defaultImagePrompt = computed(() => [
  'Создай одну иллюстрацию для публикации в соцсетях. Без надписей и водяных знаков.',
  post.value?.topic && 'Тема: ' + post.value.topic,
  post.value?.text && 'Контекст публикации: ' + post.value.text.slice(0, 1800)
].filter(Boolean).join('\n\n'))
const imagePrompt = computed({
  get: () => customImagePrompt.value ?? defaultImagePrompt.value,
  set: value => { customImagePrompt.value = value }
})

function uploadImage(event) {
  const file = event.target.files[0]
  event.target.value = ''
  if (!file) return
  const data = new FormData()
  data.append('file', file)
  run(async () => setPost(await company(path + '/image/upload', 'POST', data)))
}

async function generateImage() {
  generatingImage.value = true
  try {
    await mutate('image/codex', { prompt:imagePrompt.value.trim() })
  } finally {
    generatingImage.value = false
  }
}

function setPost(data) {
  post.value = data
  Object.assign(form, { text:data.text, platform:data.platform, time:localDate(data.proposed_at), target:data.target_chat_id || '' })
}
async function load() {
  setPost(await company(path))
}
function create() {
  run(async () => {
    const values = { topic:form.topic.trim(), platform:form.platform, proposed_at:isoDate(form.time) }
    const data = await company(manual.value ? '/posts' : '/posts/generate', 'POST',
      { ...values, ...(manual.value ? { text:form.text.trim() } : { image:withImage.value, submit:false }) })
    go('review', data.id)
  })
}
function save() {
  run(async () => {
    setPost(await company(path, 'PATCH', { text:form.text, platform:form.platform,
      proposed_at:isoDate(form.time), ...(form.target !== (post.value.target_chat_id || '') ? { target_chat_id:form.target.trim() } : {}) }))
    editing.value = false
    result.value = null
  })
}
function mutate(name, data) {
  return run(async () => {
    result.value = null
    try {
      setPost(await company(path + '/' + name, 'POST', data))
      action.value = ''
      comment.value = ''
    } catch (failure) {
      if (failure.status === 409 && name === 'approve') {
        try { await load() } catch { /* Preserve the original conflict. */ }
      }
      throw failure
    }
  })
}
function inspect(name, method = 'GET') {
  run(async () => { result.value = await company(path + '/' + name, method) })
}
function adaptPost() {
  run(async () => {
    const response = await company(path + '/adapt?platform=' + encodeURIComponent(adapt.value), 'POST')
    const id = response.result?.post_id
    if (id) go('review', id)
    else go('posts', 'draft')
  })
}
onMounted(() => { if (props.page === 'review') run(load) })
</script>

<template>
  <p v-if="error" class="error" role="alert">{{ error }} <button v-if="page === 'review' && !post" @click="run(load)">Повторить</button></p>
  <p v-if="busy" role="status" class="muted">Выполняется…</p>
  <form v-if="page === 'new'" class="stack" @submit.prevent="create">
    <fieldset class="stack" :disabled="busy || rank < 1">
      <label>Тема<input v-model="form.topic" maxlength="1000" required placeholder="Стекло в современной архитектуре"></label>
      <div><strong>Площадка</strong><div class="chips"><button v-for="value in meta.platforms" :key="value" type="button" :class="{ primary:form.platform === value }" :aria-pressed="form.platform === value" @click="form.platform = value">{{ platformName(value) }}</button></div></div>
      <label class="inline"><input type="checkbox" v-model="manual">Написать вручную</label>
      <label v-if="manual">Текст<textarea v-model="form.text" rows="6" required maxlength="16000"/></label>
      <template v-else><img v-if="withImage" class="cover" src="/facade.webp" alt="Пример оформления публикации"><label class="inline"><input type="checkbox" v-model="withImage">Создать изображение</label></template>
      <label>Предлагаемая дата<input type="datetime-local" v-model="form.time"></label>
      <button class="primary row"><Icon name="bolt"/>{{ manual ? 'Сохранить черновик' : 'Сгенерировать' }} <Icon name="next"/></button>
    </fieldset>
  </form>
  <div v-else-if="post" class="stack">
    <div class="post-cover"><PostImage :url="post.image_url" :revision="post.revision"/></div>
    <h2>{{ post.topic || 'Публикация' }}</h2>
    <div class="chips"><span class="pill">{{ platformName(post.platform) }}</span><span class="pill" :class="post.status">{{ statusName(post.status) }}</span><span v-if="post.proposed_at" class="pill">{{ dateLabel(post.proposed_at) }}</span></div>
    <p class="muted">Канал: {{ post.target_chat_id || 'по настройкам компании' }}</p>
    <p v-if="post.scheduled_at" class="muted">Публикация: {{ dateLabel(post.scheduled_at) }}</p>
    <p v-if="post.review_comment" class="panel ice copy">{{ post.review_comment }}</p>
    <p v-if="post.last_error" class="error">{{ post.last_error }}</p>
    <form v-if="editing" class="stack" @submit.prevent="save">
      <fieldset class="stack" :disabled="busy">
        <label>Текст<textarea v-model="form.text" required maxlength="16000" rows="7"/></label>
        <label>Площадка<select v-model="form.platform"><option v-for="value in meta.platforms" :key="value" :value="value">{{ platformName(value) }}</option></select></label>
        <label>ID канала<input v-model="form.target"></label>
        <label>Дата<input v-model="form.time" type="datetime-local"></label>
        <div class="actions"><button class="primary">Сохранить</button><button type="button" @click="editing = false; setPost(post)">Отмена</button></div>
      </fieldset>
    </form>
    <template v-else>
      <p class="copy">{{ post.text }}</p>
      <div v-if="post.status === 'pending_approval' && rank >= 2" class="stack">
        <button class="primary row" :disabled="busy || !post.review_token" @click="mutate('approve', { review_token:post.review_token })"><Icon name="check"/>Согласовать <Icon name="next"/></button>
        <button class="row" :disabled="busy" @click="action = 'request-changes'"><Icon name="edit"/>На доработку <Icon name="next"/></button>
        <button :disabled="busy" @click="action = 'reject'">Отклонить</button>
      </div>
      <form v-if="action" class="panel stack" @submit.prevent="mutate(action, action === 'reject' ? { comment } : { comment, auto_revise:autoRevise })">
        <label>Комментарий<textarea v-model.trim="comment" :required="action === 'request-changes'" maxlength="4000"/></label>
        <label v-if="action === 'request-changes'" class="inline"><input type="checkbox" v-model="autoRevise">Доработать с AI</label>
        <div class="actions"><button class="primary" :disabled="busy">{{ action === 'reject' ? 'Отклонить публикацию' : 'Отправить комментарий' }}</button><button type="button" @click="action = ''">Отмена</button></div>
      </form>
      <div v-if="editable" class="actions"><button :disabled="busy" @click="editing = true">Редактировать</button><button :disabled="busy" @click="inspect('check','POST')">Проверить</button></div>
      <button v-if="rank >= 1 && ['draft','revision'].includes(post.status)" class="primary" :disabled="busy" @click="mutate('submit', { proposed_at:post.proposed_at })">Отправить на согласование</button>
      <form v-if="rank >= 2 && post.content_approved && ['approved','scheduled','failed'].includes(post.status)" class="panel stack" @submit.prevent="mutate('schedule', { at:isoDate(form.time) })">
        <label>Дата публикации<input type="datetime-local" v-model="form.time" required></label>
        <button class="primary" :disabled="busy">Запланировать</button>
        <button v-if="!confirmPublish" type="button" :disabled="busy" @click="confirmPublish = true">Опубликовать сейчас</button>
        <div v-else class="stack">
          <p>Опубликовать согласованный материал сейчас?</p>
          <div class="actions">
            <button type="button" class="primary" :disabled="busy" @click="confirmPublish = false; mutate('schedule', { at:null })">Подтвердить</button>
            <button type="button" :disabled="busy" @click="confirmPublish = false">Отмена</button>
          </div>
        </div>
      </form>
      <details v-if="rank >= 1" class="panel">
        <summary>Изображение и адаптация</summary>
        <div class="stack">
          <div v-if="editable" class="stack">
            <label>Запрос для генерации в ChatGPT<textarea v-model="imagePrompt" rows="4" maxlength="4000" :disabled="generatingImage"></textarea></label>
            <button v-if="customImagePrompt !== null" type="button" :disabled="generatingImage" @click="customImagePrompt = null">Восстановить исходный запрос</button>
            <button v-if="me?.is_admin" type="button" class="primary" :disabled="busy || !imagePrompt.trim()" @click="generateImage">
              {{ generatingImage ? 'Генерация через Codex…' : 'Сгенерировать с аккаунтом ChatGPT' }}
            </button>
            <a href="https://chatgpt.com/" target="_blank" rel="noopener noreferrer">Открыть ChatGPT</a>
            <label>Загрузить готовое изображение<input type="file" accept="image/png,image/jpeg,image/webp" :disabled="busy" @change="uploadImage"></label>
          </div>
          <div v-if="editable" class="actions"><select aria-label="Шаблон изображения" v-model="template"><option v-for="value in meta.image_templates" :key="value">{{ value }}</option></select><button :disabled="busy" @click="mutate('image',{ template })">Создать изображение</button></div>
          <div class="actions"><select aria-label="Площадка для адаптации" v-model="adapt"><option v-for="value in meta.platforms" :key="value" :value="value">{{ platformName(value) }}</option></select><button :disabled="busy" @click="adaptPost">Адаптировать</button></div>
        </div>
      </details>
      <button v-if="post.status === 'published'" :disabled="busy" @click="inspect('analytics')">Статистика публикации</button>
      <a v-if="webLink(post.external_url)" :href="webLink(post.external_url)" target="_blank" rel="noopener noreferrer">Открыть опубликованный пост</a>
    </template>
    <div v-if="result" class="panel ice copy">
      <template v-if="'ok' in result"><strong>{{ result.ok ? 'Проверка пройдена' : 'Проверка не пройдена' }}</strong><p v-for="line in [...(result.errors || []), ...(result.warnings || [])]" :key="line">{{ line }}</p></template>
      <template v-else>{{ result.note || JSON.stringify(result.metrics || result, null, 2) }}</template>
    </div>
  </div>
</template>
