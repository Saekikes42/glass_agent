<script setup>
import {
  BookOpen, Building2, ChartNoAxesColumnIncreasing, Check, ChevronLeft, ChevronRight,
  CircleCheck, Clock3, Ellipsis, House, Layers3, Link2, Menu, MessageCircle, Plus,
  Send, Sparkles, Target, CalendarDays, Clapperboard, Lightbulb, Pencil, Tag, Users, ShieldCheck
} from 'lucide-vue-next'

defineProps({ name:String })

const icons = {
  home: House,
  chat: MessageCircle,
  posts: Layers3,
  company: Building2,
  next: ChevronRight,
  back: ChevronLeft,
  plus: Plus,
  send: Send,
  check: Check,
  approval: CircleCheck,
  link: Link2,
  bolt: Sparkles,
  chart: ChartNoAxesColumnIncreasing,
  menu: Menu,
  more: Ellipsis,
  book: BookOpen,
  clock: Clock3,
  target: Target,
  calendar: CalendarDays,
  video: Clapperboard,
  idea: Lightbulb,
  edit: Pencil,
  brand: Tag,
  members: Users,
  shield: ShieldCheck
}
</script>

<template>
  <component :is="icons[name]" v-if="icons[name]" class="icon" :stroke-width="1.8" aria-hidden="true"/>
</template>
