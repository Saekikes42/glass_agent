<script setup>
import { computed, onMounted, onUnmounted, provide, ref, watch } from 'vue'
import { request, telegram } from './api.js'
import { appKey } from './context.js'
import Icon from './Icon.vue'
import CompanyBrand from './CompanyBrand.vue'
import Feed from './Feed.vue'
import Post from './Post.vue'
import Knowledge from './Knowledge.vue'
import Work from './Work.vue'
import Company from './Company.vue'

const pages = {
  home:['Главная', Feed], chat:['Чат', Feed], posts:['Публикации', Feed],
  new:['Новый пост', Post], review:['Согласование', Post], knowledge:['База знаний', Knowledge],
  analytics:['Аналитика', Work], inbox:['Комментарии', Work], materials:['AI-материалы', Work],
  tasks:['Задачи', Work], shorts:['Shorts / TikTok', Work], company:['Компании', Company], admin:['Администрирование', Company]
}
const tabs = [['home','Главная'], ['chat','Чат'], ['posts','Публикации'], ['company','Компания']]
const modules = [
  ['knowledge', 'book'],
  ['analytics', 'chart'],
  ['inbox', 'chat'],
  ['materials', 'bolt'],
  ['shorts', 'video'],
  ['tasks', 'calendar'],
  ['admin', 'shield']
]
const route = ref('home')
const id = ref('')
const filter = ref('')
const menu = ref(false)
const me = ref(null)
const meta = ref({})
const companies = ref([])
const selected = ref('')
const company = computed(() => companies.value.find(item => item.id === selected.value))
const role = computed(() => me.value?.is_admin ? 'owner' : company.value?.role)
const rank = computed(() => ['viewer','editor','approver','owner'].indexOf(role.value))
const loading = ref(true)
const failure = ref('')
const native = !!telegram?.initData
const parent = { review:'posts', new:'posts', knowledge:'company', analytics:'company', inbox:'company', materials:'company', shorts:'company', tasks:'company', admin:'company' }
const active = computed(() => parent[route.value] || route.value)

function readRoute() {
  const [page, argument = ''] = location.hash.slice(1).split('/')
  route.value = Object.hasOwn(pages, page) && (page !== 'review' || /^[\da-f-]{36}$/i.test(argument)) ? page : 'home'
  id.value = route.value === 'review' ? argument : ''
  filter.value = route.value === 'posts' && ['draft','pending_approval'].includes(argument) ? argument : ''
  menu.value = false
}
function go(page, argument = '') {
  location.hash = page + (argument ? '/' + argument : '')
}
function back() {
  go(parent[route.value] || 'home')
}
function choose(value) {
  selected.value = value
  localStorage.setItem('glass-company', value)
}
async function refreshCompanies() {
  companies.value = await request('/api/companies')
  if (!company.value) choose(companies.value.find(item => item.id === localStorage.getItem('glass-company'))?.id || companies.value[0]?.id || '')
}
async function init() {
  loading.value = true
  failure.value = ''
  try {
    [me.value, meta.value] = await Promise.all([request('/api/me'), request('/api/meta')])
    await refreshCompanies()
  } catch (error) {
    failure.value = error.message
  } finally {
    loading.value = false
  }
}
provide(appKey, { request, go, me, meta, companies, company, choose, refreshCompanies, rank, role })
readRoute()
watch(route, page => {
  window.scrollTo(0, 0)
  if (native) page === 'home' ? telegram.BackButton?.hide() : telegram.BackButton?.show()
}, { immediate:true })
onMounted(() => {
  window.addEventListener('hashchange', readRoute)
  if (native) {
    telegram.ready()
    telegram.expand()
    telegram.setHeaderColor?.('#f3f7f9')
    telegram.BackButton?.onClick(back)
  }
  init()
})
onUnmounted(() => {
  window.removeEventListener('hashchange', readRoute)
  telegram?.BackButton?.offClick(back)
})
</script>

<template>
  <div class="shell">
    <header v-if="!native" class="chrome">
      <button class="icon-button" aria-label="Назад" @click="back"><Icon name="back"/></button>
      <strong>Glass Agent</strong>
      <button class="icon-button" aria-label="Разделы" :aria-expanded="menu" @click="menu = !menu"><Icon name="more"/></button>
    </header>
    <main>
      <div class="page-title">
        <h1>{{ pages[route][0] }}</h1>
        <CompanyBrand v-if="route === 'home' && company" :company="company" full/>
        <button v-if="native || route === 'company'" class="icon-button" aria-label="Разделы" :aria-expanded="menu" @click="menu = !menu"><Icon name="menu"/></button>
      </div>
      <nav v-if="menu" class="section-menu" aria-label="Разделы приложения">
        <button v-for="[page, icon] in modules.filter(([page]) => page !== 'admin' || me?.is_admin)"
          :key="page" type="button" class="section-menu-item" :aria-current="route === page ? 'page' : undefined"
          @click="menu = false; go(page)">
          <span class="section-menu-icon"><Icon :name="icon"/></span>
          <span>{{ pages[page][0] }}</span>
          <Icon name="next"/>
        </button>
      </nav>
      <p v-if="loading" class="panel muted" role="status">Загрузка…</p>
      <div v-else-if="failure" class="panel"><p role="alert">{{ failure }}</p><button @click="init">Повторить</button></div>
      <component v-else-if="company || ['company','admin'].includes(route)"
        :is="pages[route][1]" :key="route + ':' + id + ':' + filter + ':' + selected" :page="route" :id="id" :initial-filter="filter"/>
      <section v-else class="panel ice stack">
        <h2>Доступ к компании</h2>
        <p>Чтобы открыть рабочий стол, передайте свой Telegram ID администратору Glass Agent.</p>
        <p v-if="me?.telegram_id">Ваш Telegram ID <strong>{{ me.telegram_id }}</strong></p>
        <button type="button" @click="init">Проверить доступ</button>
      </section>
    </main>
    <nav class="bottom" aria-label="Разделы приложения">
      <button v-for="[page, label] in tabs" :key="page" :aria-current="active === page ? 'page' : undefined" @click="go(page)">
        <Icon :name="page"/>{{ label }}
      </button>
    </nav>
  </div>
</template>
