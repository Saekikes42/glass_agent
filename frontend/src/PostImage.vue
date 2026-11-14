<script setup>
import { ref, watch } from 'vue'
import { request } from './api.js'

const props = defineProps({ url:String, revision:Number, decorative:Boolean })
const source = ref('')
watch(() => [props.url, props.revision], async ([url], _, cleanup) => {
  source.value = ''
  if (!url?.startsWith('/api/')) return
  const controller = new AbortController()
  let objectUrl
  cleanup(() => {
    controller.abort()
    if (objectUrl) URL.revokeObjectURL(objectUrl)
  })
  try {
    const blob = await request(url, 'GET', undefined, { signal:controller.signal, blob:true })
    if (!controller.signal.aborted) source.value = objectUrl = URL.createObjectURL(blob)
  } catch {
    // Keep the layout usable when the attachment is temporarily unavailable.
  }
}, { immediate:true })
</script>

<template>
  <img v-if="source" :src="source" :alt="decorative ? '' : 'Изображение публикации'">
  <img v-else-if="decorative" src="/facade.webp" alt="">
  <p v-else-if="url" class="muted">Изображение недоступно</p>
</template>
