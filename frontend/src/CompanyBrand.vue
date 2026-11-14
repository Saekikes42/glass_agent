<script setup>
import Icon from './Icon.vue'

defineProps({
  company: { type:Object, required:true },
  full: Boolean
})
</script>

<template>
  <span class="brand">
    <img v-if="company.slug === 'salavatsteklo' && full" class="brand-logo" src="/salstek-logo.png" :alt="company.name">
    <template v-else>
      <img v-if="company.slug === 'salavatsteklo'" class="brand-icon" src="/salstek-icon.png" alt="">
      <Icon v-else name="company" class="brand-icon"/>
      {{ company.name }}
    </template>
  </span>
</template>
