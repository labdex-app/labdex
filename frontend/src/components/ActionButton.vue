<script setup>
// Runs a plugin action; asks for its parameters first when it has any.
import { ClipboardList, Play } from 'lucide-vue-next'
import { confirm } from '../confirm'
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../api'
import { toast } from '../store'
import SettingsForm from './SettingsForm.vue'

const props = defineProps({ action: Object, url: String, disabled: Boolean, primary: Boolean })
const router = useRouter()
const open = ref(false)
const busy = ref(false)
const values = reactive({})
const errors = ref({})

function click() {
  const a = props.action
  if (a.params?.length) {
    for (const f of a.params) if (!(f.key in values)) values[f.key] = f.default ?? (f.type === 'list' ? [] : f.type === 'bool' ? false : '')
    open.value = !open.value
    return
  }
  go()
}

async function go() {
  const a = props.action
  if (a.danger && !(await confirm({ title: `${a.label}?`, body: 'This changes the host right away, without a plan.', action: a.label, danger: true }))) return
  busy.value = true
  errors.value = {}
  try {
    const job = await api.post(props.url, { params: { ...values } })

    router.push(`/jobs/${job.id}`)
  } catch (e) {
    if (e instanceof ApiError && e.errors.length) errors.value = e.fields
    toast(e.message, 'error')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="ab">
    <div class="row" style="flex-wrap: nowrap">
      <!-- every action looks the same; the icon says whether it plans first or runs right away -->
      <button type="button" class="btn" :class="{ primary, danger: action.danger }" :disabled="disabled || busy" @click="click">
        <component :is="action.mode === 'plan' ? ClipboardList : Play" :size="14" class="aic" />
        <span class="ell">{{ busy ? 'Starting…' : action.label }}{{ action.params?.length ? '…' : '' }}</span>
      </button>
      <span class="small muted">
        {{ action.mode === 'plan' ? 'Plans first; nothing changes until you apply.' : 'Runs right away.' }}
        <template v-if="action.ansible">Ansible.</template> {{ action.description }}
      </span>
    </div>
    <form v-if="open" class="params" @submit.prevent="go">
      <SettingsForm :fields="action.params" v-model:values="values" :errors="errors" />
      <div class="row" style="margin-top: 10px">
        <button class="btn primary small" :disabled="busy">Run</button>
        <button type="button" class="btn ghost small" @click="open = false">Cancel</button>
      </div>
    </form>
  </div>
</template>

<style scoped>
.ab .btn { width: 220px; flex: none; justify-content: flex-start; gap: 8px; }
.ab .aic { flex: none; color: var(--muted); }
@media (max-width: 640px) { .ab .row { flex-wrap: wrap !important; } .ab .btn { width: 100%; } }
.params { margin: 10px 0 4px; padding: 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--panel); }
</style>
