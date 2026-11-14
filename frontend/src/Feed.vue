<script setup>
import { onMounted, ref } from 'vue'
import { useScreen } from './context.js'
import { dateLabel, platformName, statusName, webLink } from './api.js'
import Icon from './Icon.vue'
import PostImage from './PostImage.vue'

const props = defineProps({ page:String, initialFilter:String })
const { activeCompany, company, go, rank, meta, busy, error, run } = useScreen()
const posts = ref([])
const history = ref([])
const status = ref(props.initialFilter || '')
const message = ref('')
const more = ref(false)
const filters = [['','Все'], ['draft','Черновики'], ['pending_approval','На согласовании']]

async function listPosts(value) {
  await run(async () => {
    const rows = await company('/posts' + (value ? '?status=' + encodeURIComponent(value) : ''))
    status.value = value
    posts.value = rows
  })
}
async function loadHistory(before) {
  const rows = await company('/chat/history?limit=50' + (before ? '&before_id=' + before : ''))
  more.value = rows.length === 50
  history.value = [...rows].sort((a,b) => a.id - b.id).concat(before ? history.value : [])
}
function send() {
  const text = message.value.trim()
  if (!text) return
  run(async () => {
    const reply = await company('/chat', 'POST', { message:text })
    history.value.push({ id:reply.user_message_id, role:'user', content:text },
      { id:reply.message_id, role:'assistant', content:reply.answer, is_mock:reply.is_mock,
        meta:{ citations:reply.citations, created_post_ids:reply.created_post_ids } })
    message.value = ''
  })
}
function load() {
  if (props.page === 'chat') run(() => loadHistory())
  else listPosts(props.page === 'home' ? 'pending_approval' : status.value)
}
onMounted(load)
</script>

<template>
  <section v-if="page === 'home'" class="hero">
    <h2>AI-маркетолог</h2><p>Контент, анализ, задачи<br>для {{ activeCompany.name }}</p>
  </section>
  <div v-if="page === 'home'" class="stack">
    <button v-if="rank >= 1" class="primary row" @click="go('new')"><Icon name="bolt"/>Создать черновик <Icon name="next"/></button>
    <div class="actions"><button @click="go('chat')"><Icon name="chat"/>Чат</button><button @click="go('posts','pending_approval')"><Icon name="approval"/>Согласование</button></div>
  </div>
  <p v-if="error" class="error" role="alert">{{ error }} <button @click="load">Повторить</button></p>
  <template v-if="page === 'chat'">
    <button v-if="more" :disabled="busy" @click="run(() => loadHistory(history[0].id))">Ранее</button>
    <p v-if="!history.length && !busy" class="muted">Напишите, какой материал нужно подготовить.</p>
    <div class="messages" aria-live="polite">
      <div v-for="entry in history" :key="entry.id" class="bubble" :class="entry.role">
        <span v-if="entry.is_mock" class="pill">Тестовый режим</span>
        <p class="copy">{{ entry.content }}</p><small>{{ dateLabel(entry.created_at) }}</small>
        <div v-if="entry.meta?.citations?.length" class="chips">
          <template v-for="(citation, index) in entry.meta.citations" :key="index">
            <a v-if="webLink(citation?.url)" class="pill" :href="webLink(citation.url)" target="_blank" rel="noopener noreferrer">{{ citation.title || 'Источник' }}</a>
            <span v-else class="pill">{{ citation?.title || citation?.citation || 'Источник' }}</span>
          </template>
        </div>
        <button v-for="postId in entry.meta?.created_post_ids || []" :key="postId" class="row" @click="go('review',postId)">Открыть черновик <Icon name="next"/></button>
      </div>
    </div>
    <p v-if="busy" role="status" class="muted">Подождите…</p>
    <form v-if="rank >= 1" class="composer" @submit.prevent="send">
      <textarea v-model="message" rows="1" required placeholder="Опишите задачу…" aria-label="Сообщение"></textarea>
      <button class="primary icon-button" aria-label="Отправить" :disabled="busy"><Icon name="send"/></button>
    </form>
  </template>
  <template v-else>
    <div v-if="page === 'posts'" class="stack">
      <div class="chips">
        <button v-for="[value,label] in filters" :key="value" :class="{ primary:status === value }" :disabled="busy" @click="listPosts(value)">{{ label }}</button>
        <select aria-label="Другие статусы" :value="filters.some(([value]) => value === status) ? '' : status" :disabled="busy" @change="listPosts($event.target.value)">
          <option value="">Другие статусы</option><option v-for="value in (meta.post_statuses || []).filter(value => !filters.some(([key]) => value === key))" :key="value" :value="value">{{ statusName(value) }}</option>
        </select>
      </div>
      <button v-if="rank >= 1" class="primary row" @click="go('new')"><Icon name="plus"/>Новый пост</button>
    </div>
    <section :class="page === 'home' ? 'panel ice stack' : 'stack'">
      <h2 v-if="page === 'home'">На согласовании</h2>
      <p v-if="busy" role="status" class="muted">Загрузка…</p>
      <p v-else-if="!posts.length && !error" class="muted">Публикаций пока нет.</p>
      <button v-for="item in (page === 'home' ? posts.slice(0, 2) : posts)" :key="item.id" class="post-row row" @click="go('review',item.id)">
        <PostImage :url="item.image_url" :revision="item.revision" decorative/>
        <span class="post-label"><small v-if="page === 'posts'" class="pill" :class="item.status">{{ statusName(item.status) }}</small><strong>{{ item.topic || item.text.slice(0, 95) }}</strong><small class="pill">{{ platformName(item.platform) }}</small></span>
        <Icon name="next"/>
      </button>
    </section>
  </template>
</template>
