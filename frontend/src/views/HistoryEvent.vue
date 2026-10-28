<script setup>
import { confirm } from '../confirm'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'
import Diff from '../components/Diff.vue'
import { changed, stamp, toast } from '../store'

const props = defineProps({ id: String })
const router = useRouter()
const e = ref(null)
const raw = ref(false)

onMounted(async () => {
  try { e.value = await api.get(`/api/history/${props.id}`) } catch (err) { toast(err.message, 'error') }
})

async function revert() {
  const what = e.value.action === 'create' ? `delete ${e.value.object_name} again`
    : e.value.action === 'delete' ? `restore ${e.value.object_name}` : `put ${e.value.object_name} back the way it was before this change`
  if (!(await confirm({ title: `Revert: ${what}?`, action: 'Revert', danger: true }))) return
  try {
    const n = await api.post(`/api/history/${props.id}/revert`)
    toast('Reverted')
    await changed()
    router.push(`/history/${n.id}`)
  } catch (err) { toast(err.message, 'error') }
}
</script>

<template>
  <div class="page">
    <template v-if="e">
      <div class="page-head">
        <RouterLink to="/history" class="btn ghost small">← History</RouterLink>
        <h1><span class="op" :class="`op-${e.action}`" style="font-size: 18px">{{ e.action }}</span> <span class="faint">{{ e.object_type }}</span> <span class="mono">{{ e.object_name }}</span></h1>
        <span class="grow"></span>
        <button v-if="e.revertible" class="btn" @click="revert">Revert this change</button>
      </div>
      <div class="row small muted" style="margin: -8px 0 16px">
        <span>{{ stamp(e.ts) }}</span><span>· {{ e.actor }} ({{ e.actor_kind }})</span><span v-if="e.ip">· from {{ e.ip }}</span>
      </div>
      <div class="card">
        <p class="mt-0">{{ e.summary }}</p>
        <Diff :rows="e.diff" />
        <p><a href="#" class="small" @click.prevent="raw = !raw">{{ raw ? 'Hide' : 'Show' }} full snapshots</a></p>
        <div v-if="raw" class="grid2">
          <div><h3>Before</h3><pre class="code">{{ e.before ? JSON.stringify(e.before, null, 2) : '—' }}</pre></div>
          <div><h3>After</h3><pre class="code">{{ e.after ? JSON.stringify(e.after, null, 2) : '—' }}</pre></div>
        </div>
      </div>
      <p v-if="e.revertible" class="hint">Reverting records a new change; nothing in the history is lost. It's checked against your permissions and the current inventory, so a revert can fail if something else changed since.</p>
    </template>
  </div>
</template>
