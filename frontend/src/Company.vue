<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { useScreen } from './context.js'
import { dateLabel, webLink } from './api.js'
import Icon from './Icon.vue'
import CompanyBrand from './CompanyBrand.vue'

const props = defineProps({ page:String })
const { activeCompany, company:companyApi, api, companies, choose, refreshCompanies, go, me, role, rank, meta, run, busy, error } = useScreen()
const section = ref('')
const data = ref(null)
const connection = ref(null)
let connectionPoll
const creating = ref(false)
const name = ref('')
const owner = ref('')
const memberId = ref('')
const memberRole = ref('editor')
const secretName = ref(meta.value.secret_names?.[0] || '')
const secretValue = ref('')
const secretHints = ref([])
const form = reactive({ tone:'', domains:'', banned:'', required:'', requireImage:false, maxHashtags:5, telegram:'', vk:'', max:'', telegram_discussion:'' })
const roles = { viewer:'Наблюдатель', editor:'Маркетолог', approver:'Согласующий', owner:'Владелец' }
const channelFields = [['telegram','Telegram'],['vk','VK'],['max','MAX'],['telegram_discussion','Чат обсуждений Telegram']]
const sections = { members:'/members', usage:'/usage', audit:'/audit', models:'/ai/models' }
const navigation = [['knowledge','База знаний','book'],['analytics','Аналитика','chart'],['inbox','Комментарии','chat'],['materials','AI-материалы','bolt'],['tasks','Задачи','clock']]
const split = value => value.split(/\s*,\s*/).map(word => word.trim()).filter(Boolean)

function selectSection(value) {
  run(async () => {
    section.value = value
    data.value = null
    if (value === 'models') data.value = await api(sections[value])
    else if (sections[value]) data.value = await companyApi(sections[value])
    else if (['brand','channels'].includes(value)) {
      const { settings } = await companyApi('')
      const brand = settings.brand || {}
      Object.assign(form, { tone:brand.tone || '', domains:(brand.allowed_domains || []).join(', '),
        banned:(brand.banned_words || []).join(', '), required:(brand.required_words || []).join(', '),
        requireImage:!!brand.require_image, maxHashtags:brand.max_hashtags ?? 5 })
      for (const [key] of channelFields) form[key] = settings.channels?.[key] || ''
      if (value === 'channels') secretHints.value = await companyApi('/secrets')
    }
  })
}
function saveSettings() {
  run(async () => {
    const { settings } = await companyApi('')
    if (section.value === 'brand') {
      settings.brand = { ...settings.brand, tone:form.tone, allowed_domains:split(form.domains),
        banned_words:split(form.banned), required_words:split(form.required),
        require_image:form.requireImage, max_hashtags:form.maxHashtags }
    } else {
      settings.channels = { ...settings.channels, ...Object.fromEntries(channelFields.map(([key]) => [key, form[key].trim()])) }
    }
    await companyApi('', 'PATCH', { settings })
    section.value = ''
  })
}
function create() {
  run(async () => {
    const created = await api('/companies','POST',{ name:name.value.trim(), owner_telegram_id:Number(owner.value) })
    await refreshCompanies()
    choose(created.id)
    creating.value = false
  })
}
function memberAction(telegramId) {
  if (telegramId && !window.confirm('Удалить участника?')) return
  run(async () => {
    if (telegramId) await companyApi('/members/' + telegramId, 'DELETE')
    else await companyApi('/members', 'POST', { telegram_id:Number(memberId.value), role:memberRole.value })
    memberId.value = ''
    data.value = await companyApi('/members')
  })
}
function secretAction(remove) {
  if (remove && !window.confirm('Удалить ключ интеграции?')) return
  run(async () => {
    await companyApi('/secrets/' + encodeURIComponent(secretName.value), remove ? 'DELETE' : 'PUT',
      remove ? undefined : { value:secretValue.value })
    secretValue.value = ''
    secretHints.value = await companyApi('/secrets')
  })
}
async function loadConnection() {
  connection.value = await api('/ai/connection')
  clearInterval(connectionPoll)
  if (connection.value.login?.status === 'waiting') {
    connectionPoll = setInterval(() => {
      if (!busy.value) run(loadConnection)
    }, 3000)
  }
}
function connect(disconnect = false) {
  if (disconnect && !window.confirm('Отключить ChatGPT для всей платформы?')) return
  run(async () => {
    if (disconnect) await api('/ai/disconnect','POST')
    else await api('/ai/connect','POST')
    await loadConnection()
  })
}
onMounted(() => { if (me.value?.is_admin) run(loadConnection) })
onUnmounted(() => clearInterval(connectionPoll))
</script>

<template>
  <div class="stack">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="busy" class="muted" role="status">Загрузка…</p>
    <template v-if="page === 'company'">
      <button v-for="item in companies" :key="item.id" class="row" :class="{ soft:activeCompany?.id === item.id }" :aria-pressed="activeCompany?.id === item.id" :disabled="busy" @click="choose(item.id)">
        <CompanyBrand :company="item"/><Icon :name="activeCompany?.id === item.id ? 'check' : 'next'"/>
      </button>
      <div v-if="activeCompany" class="chips"><span v-if="me?.is_admin" class="pill">Администратор</span><span v-if="role" class="pill">{{ roles[role] || role }}</span></div>
      <button v-if="me?.is_admin" class="row" @click="creating = !creating"><Icon name="plus"/>Создать компанию</button>
      <form v-if="creating" class="panel stack" @submit.prevent="create">
        <label>Название<input v-model="name" required></label>
        <label>Telegram ID владельца<input v-model="owner" required inputmode="numeric" pattern="[0-9]+"></label>
        <button :disabled="busy" class="primary">Создать</button>
      </form>
      <template v-if="activeCompany">
        <h2>Настройки компании</h2>
        <section v-if="me?.is_admin" class="panel ice stack">
          <h2>Аккаунт ChatGPT Plus</h2>
          <span v-if="connection" class="pill">{{ connection.is_mock ? 'Тестовый режим' : connection.connected ? 'Подключён' : connection.login?.status === 'waiting' ? 'Ожидает входа' : connection.login?.status === 'error' ? 'Ошибка входа' : 'Не подключён' }}</span>
          <p v-if="connection?.uses_paid_api_key">Подключён API ключ. Для ChatGPT Plus войдите через кнопку ниже.</p>
          <p v-if="connection?.warning || connection?.login?.error" class="error">{{ connection.warning || connection.login.error }}</p>
          <p v-if="connection?.login?.status === 'waiting'" class="muted" role="status">Откройте ссылку и завершите вход. Статус обновится автоматически.</p>
          <a v-if="webLink(connection?.login?.authorization_url)" class="row" :href="webLink(connection.login.authorization_url)" target="_blank" rel="noopener noreferrer">Открыть вход ChatGPT <Icon name="next"/></a>
          <p v-if="connection?.login?.redirect_uri" class="muted">После входа браузер должен вернуться на {{ connection.login.redirect_uri }}. Вход работает на компьютере, где запущен backend.</p>
          <div class="actions">
            <button v-if="!connection?.login || connection.login.status !== 'waiting'" class="primary" :disabled="busy" @click="connect()">{{ connection?.connected ? 'Переподключить ChatGPT Plus' : 'Войти в ChatGPT Plus' }}</button>
            <button v-if="connection?.connected && !connection.is_mock" :disabled="busy" @click="connect(true)">Отключить</button>
            <button :disabled="busy" @click="run(loadConnection)">Проверить статус</button>
          </div>
        </section>
        <button v-if="rank >= 3" class="row" :disabled="busy" @click="selectSection('brand')"><Icon name="brand"/>Бренд <Icon name="next"/></button>
        <button v-if="rank >= 3" class="row" :disabled="busy" @click="selectSection('channels')"><Icon name="posts"/>Каналы <Icon name="next"/></button>
        <button class="row" :disabled="busy" @click="selectSection('members')"><Icon name="members"/>Участники <Icon name="next"/></button>
        <div class="stack module-list">
          <button v-for="[route,label,icon] in navigation" :key="route" class="row" @click="go(route)"><Icon :name="icon"/>{{ label }} <Icon name="next"/></button>
          <button v-if="!me?.is_admin && rank >= 3" class="row" :disabled="busy" @click="selectSection('usage')"><Icon name="chart"/>Расход AI <Icon name="next"/></button>
          <button v-if="!me?.is_admin && rank >= 3" class="row" :disabled="busy" @click="selectSection('audit')"><Icon name="book"/>Журнал <Icon name="next"/></button>
        </div>
      </template>
      <button v-if="me?.is_admin" class="row soft" @click="go('admin')">Администрирование <Icon name="next"/></button>
    </template>
    <template v-else-if="me?.is_admin">
      <button class="row" :disabled="busy" @click="selectSection('models')"><Icon name="posts"/>Модели <Icon name="next"/></button>
      <button v-if="activeCompany" class="row" :disabled="busy" @click="selectSection('usage')"><Icon name="chart"/>Расход AI <Icon name="next"/></button>
      <button v-if="activeCompany" class="row" :disabled="busy" @click="selectSection('audit')"><Icon name="book"/>Журнал <Icon name="next"/></button>
    </template>
    <p v-else class="panel">Раздел доступен администратору.</p>
    <section v-if="section" class="panel stack" :aria-busy="busy">
      <div class="row"><h2>{{ { brand:'Бренд', channels:'Каналы', members:'Участники', models:'Модели', usage:'Расход AI', audit:'Журнал' }[section] }}</h2><button @click="section = ''" :disabled="busy">Закрыть</button></div>
      <form v-if="['brand','channels'].includes(section)" class="stack" @submit.prevent="saveSettings">
        <template v-if="section === 'brand'">
          <label>Тон общения<input v-model="form.tone"></label>
          <label>Разрешённые домены через запятую<input v-model="form.domains"></label>
          <label>Запрещённые слова через запятую<input v-model="form.banned"></label>
          <label>Обязательные слова через запятую<input v-model="form.required"></label>
          <label>Максимум хэштегов<input type="number" min="0" v-model.number="form.maxHashtags" required></label>
          <label class="inline"><input type="checkbox" v-model="form.requireImage">Требовать изображение</label>
        </template>
        <label v-for="[key,label] in section === 'channels' ? channelFields : []" :key="key">{{ label }}<input v-model="form[key]"></label>
        <button :disabled="busy" class="primary">Сохранить</button>
      </form>
      <form v-if="section === 'channels'" class="stack" @submit.prevent="secretAction(false)">
        <h3>Ключи интеграций</h3>
        <p v-for="item in secretHints" :key="item.name">{{ item.name }} · {{ item.hint }}</p>
        <label>Интеграция<select v-model="secretName"><option v-for="key in meta.secret_names" :key="key">{{ key }}</option></select></label>
        <label>Новый ключ<input type="password" autocomplete="off" v-model="secretValue" required></label>
        <div class="actions"><button :disabled="busy || !secretName">Сохранить ключ</button><button type="button" :disabled="busy || !secretHints.some(item => item.name === secretName)" @click="secretAction(true)">Удалить ключ</button></div>
      </form>
      <template v-if="section === 'members' && data">
        <div v-for="item in data" :key="item.telegram_id" class="row"><span>{{ item.name || item.telegram_id }}<small>{{ roles[item.role] || item.role }}</small></span><button v-if="rank >= 3 && item.telegram_id && item.telegram_id !== me?.telegram_id" :disabled="busy" @click="memberAction(item.telegram_id)">Удалить</button></div>
        <form v-if="rank >= 3" class="stack" @submit.prevent="memberAction()">
          <label>Telegram ID<input v-model="memberId" required inputmode="numeric" pattern="[0-9]+"></label>
          <label>Роль<select v-model="memberRole"><option v-for="key in meta.roles" :key="key" :value="key">{{ roles[key] }}</option></select></label>
          <button class="primary" :disabled="busy">Сохранить участника</button>
        </form>
      </template>
      <template v-if="section === 'models' && data"><p v-if="data.note">{{ data.note }}</p><p v-for="(model,index) in data.models" :key="index">{{ typeof model === 'string' ? model : model.id || model.name }}</p></template>
      <template v-if="section === 'usage' && data"><p v-if="!data.length" class="muted">Нет данных.</p><p v-for="item in data" :key="item.provider">{{ item.provider }} · {{ item.calls }} вызовов за {{ item.days }} дн. · {{ item.input_tokens + item.output_tokens }} токенов</p></template>
      <template v-if="section === 'audit' && data"><p v-if="!data.length" class="muted">Событий пока нет.</p><div v-for="item in data" :key="item.id" class="row"><span>{{ item.action }}<small>{{ dateLabel(item.created_at) }}</small></span></div></template>
    </section>
  </div>
</template>
