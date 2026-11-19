<script setup>
// Inbox › Checks: every convention finding in a list, the picked one beside it with what it's
// about, the other findings on the same object, and what to do (open it, or accept it with a
// reason). Findings never block a save; `labdex lint` exits non-zero on errors for CI.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowUpRight, CircleCheck } from 'lucide-vue-next'
import { useQuery } from '../query'
import { api } from '../api'
import { changed, state, toast } from '../store'
import Field from '../components/Field.vue'
import Modal from '../components/Modal.vue'

const route = useRoute()
const router = useRouter()
const { data } = useQuery('/api/lint', { onError: (e) => toast(e.message, 'error') })
const dlg = ref(null)
const busy = ref(false)
watch(data, (d) => { if (d) state.lint = d.summary })

const all = computed(() => data.value?.issues || [])
const open = computed(() => all.value.filter((i) => i.accepted === undefined))
const FILTERS = [
  { key: 'all', label: 'All', test: (i) => i.accepted === undefined },
  { key: 'error', label: 'Errors', test: (i) => i.accepted === undefined && i.severity === 'error' },
  { key: 'warning', label: 'Warnings', test: (i) => i.accepted === undefined && i.severity === 'warning' },
  { key: 'info', label: 'Info', test: (i) => i.accepted === undefined && i.severity === 'info' },
  { key: 'accepted', label: 'Accepted', test: (i) => i.accepted !== undefined },
]
const filter = computed(() => FILTERS.find((f) => f.key === route.query.show) || FILTERS[0])
const count = (f) => all.value.filter(f.test).length
const rows = computed(() => all.value.filter(filter.value.test))
const idOf = (i) => `${i.kind}:${i.ref}:${i.code}`
const selected = computed(() => rows.value.find((i) => idOf(i) === route.query.item) || rows.value[0] || null)
const sameObject = computed(() => selected.value ? all.value.filter((i) => i !== selected.value && i.kind === selected.value.kind && i.ref === selected.value.ref) : [])

function show(key) { router.replace({ query: { ...route.query, show: key === 'all' ? undefined : key, item: undefined } }) }
function back() { const { item: _, ...rest } = route.query; router.replace({ query: rest }) }
function pick(i) { router.replace({ query: { ...route.query, item: idOf(i) } }) }

const DOT = { error: 'err', warning: 'warn', info: 'info' }
const key = (i) => ({ code: i.code, kind: i.kind, ref: i.ref })
const link = (i) => (i.kind === 'host' ? `/hosts/${i.ref}` : i.kind === 'network' ? `/networks/${i.ref}` : '/aliases')
const KIND = { host: 'Host', network: 'Network', alias: 'Alias' }

async function accept() {
  busy.value = true
  try {
    await api.post('/api/lint/accept', { ...key(dlg.value), reason: dlg.value.reason })
    dlg.value = null
    await changed()
    toast('Accepted')
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    busy.value = false
  }
}

async function unaccept(i) {
  try {
    await api.del('/api/lint/accept', key(i))
    await changed()
    toast('Counts again')
  } catch (e) {
    toast(e.message, 'error')
  }
}
</script>

<template>
  <div class="page ld" :class="{ picked: !!route.query.item }">
    <section class="card flush list-pane" aria-label="Findings">
      <div class="lp-head">
        <h1>Checks</h1>
        <span class="grow"></span>
        <span v-if="data" class="small faint mono">{{ open.length }} open</span>
      </div>
      <div class="filters lp-filters" role="group" aria-label="Show">
        <button v-for="f in FILTERS.filter((x) => x.key === 'all' || x === filter || count(x))" :key="f.key" class="chip" :class="{ on: filter.key === f.key }" :aria-pressed="filter.key === f.key" @click="show(f.key)">
          {{ f.label }}<span class="n">{{ count(f) }}</span></button>
      </div>
      <div v-if="!data" class="lp-empty faint">Loading…</div>
      <div v-else-if="!rows.length" class="lp-empty">
        <CircleCheck :size="22" class="ok-ic" />
        <p class="m-0"><b>{{ filter.key === 'accepted' ? 'Nothing accepted' : 'Everything follows the conventions' }}</b></p>
        <p class="small muted m-0">Checks run over the whole inventory after every change.</p>
      </div>
      <ul v-else class="rows">
        <li v-for="i in rows" :key="idOf(i)">
          <button class="row-btn" :class="{ on: selected === i }" :aria-current="selected === i ? 'true' : undefined" @click="pick(i)">
            <span class="dot" :class="DOT[i.severity] || 'unknown'" aria-hidden="true"></span>
            <span class="rb-main">
              <span class="rb-title mono">{{ i.ref }}</span>
              <span class="small muted clamp">{{ i.message }}</span>
            </span>
            <span class="rb-side"><span>{{ i.kind }}</span><span>{{ i.code }}</span></span>
          </button>
        </li>
      </ul>
    </section>

    <section class="card detail-pane" aria-label="The selected finding">
      <button class="btn small ld-back" @click="back">‹ Checks</button>
      <template v-if="selected">
        <div class="label">{{ KIND[selected.kind] || selected.kind }} · {{ selected.code }}</div>
        <h2 class="d-title"><span class="mono">{{ selected.ref }}</span>
          <span class="sev" :class="`sev-${selected.severity}`">{{ selected.severity }}</span></h2>
        <p class="d-msg">{{ selected.message }}</p>
        <p v-if="selected.accepted !== undefined" class="accepted"><b>Accepted:</b> {{ selected.accepted || 'no reason given' }}<br>
          <span class="small muted">It doesn't count in <code>labdex lint</code> or on the Overview.</span></p>
        <div class="d-acts">
          <RouterLink :to="link(selected)" class="btn">Open {{ (KIND[selected.kind] || selected.kind).toLowerCase() }} <ArrowUpRight :size="14" /></RouterLink>
          <template v-if="state.meta?.is_admin">
            <button v-if="selected.accepted === undefined" class="btn" @click="dlg = { ...selected, reason: '' }">Accept…</button>
            <button v-else class="btn" @click="unaccept(selected)">Count again</button>
          </template>
        </div>

        <template v-if="sameObject.length">
          <h3 class="label sect">Also on {{ selected.ref }}</h3>
          <ul class="others">
            <li v-for="o in sameObject" :key="idOf(o)">
              <button class="linkish" @click="pick(o)"><span class="dot" :class="DOT[o.severity]" aria-hidden="true"></span>
                <span class="mono small">{{ o.code }}</span> {{ o.message }}<span v-if="o.accepted !== undefined" class="faint small"> (accepted)</span></button>
            </li>
          </ul>
        </template>
        <p class="hint mt-3">Checks never block a save. <code>labdex lint</code> exits non-zero on errors, so CI can gate on them.</p>
      </template>
      <p v-else class="empty">Nothing to look at here.</p>
    </section>

    <Modal v-if="dlg" title="Accept this finding" :busy="busy" @close="dlg = null">
      <p class="small muted">{{ dlg.ref }}: {{ dlg.message }}<br>It stays on this page, but no longer counts in <code>labdex lint</code> or on the Overview. The reason goes into Activity.</p>
      <Field label="Why is it fine?"><input v-model.trim="dlg.reason" maxlength="200" placeholder="Legacy printer, stays in the old range until the move"></Field>
      <template #foot>
        <button class="btn" @click="dlg = null">Cancel</button>
        <button class="btn primary" :disabled="!dlg.reason || busy" @click="accept">Accept</button>
      </template>
    </Modal>
  </div>
</template>

<style scoped>
.ok-ic { color: var(--ok); }
.clamp { display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.rb-side { max-width: 120px; }
.rb-side span { overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.d-title .mono { font-size: calc(19px * var(--text)); overflow-wrap: anywhere; }
.d-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin: 4px 0 10px; font-size: calc(19px * var(--text)); }
.d-msg { font-size: calc(15px * var(--text)); margin: 0 0 14px; max-width: 70ch; }
.accepted { border-left: 3px solid var(--line); padding: 4px 0 4px 12px; margin: 0 0 14px; }
.d-acts { display: flex; gap: 8px; flex-wrap: wrap; }
.sect { margin: 24px 0 8px; }
.others { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.linkish { background: none; border: 0; padding: 0; font: inherit; color: var(--ink); text-align: left; cursor: pointer; display: inline-flex; gap: 8px; align-items: baseline; }
.linkish:hover { color: var(--accent-soft); }
</style>
