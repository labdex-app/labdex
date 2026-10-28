<script setup>
// Labels with QR codes for hosts, cables (one per end), a switch's or patch panel's ports, and
// racks; for label printers (one label per page) and A4 sheets. Scanning a code opens the
// thing in labdex. Printed from the browser: the preview is what comes out.
import { Printer, X } from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import HostPicker from '../components/HostPicker.vue'
import QrCode from '../components/QrCode.vue'
import { cableLabels, FORMATS, formatById, hostLabel, portLabels, QR_MIN_MM, rackLabel, sheetSlot } from '../labels'
import { cableHex } from '../links'
import { useQuery } from '../query'
import { state } from '../store'

const route = useRoute()
const router = useRouter()
const set = (patch) => router.replace({ query: { ...route.query, ...patch } })
const list = (k) => [].concat(route.query[k] || []).filter(Boolean)
const KINDS = [['hosts', 'Hosts'], ['cables', 'Cables'], ['ports', 'Ports'], ['racks', 'Racks']]
const what = computed(() => (KINDS.some(([k]) => k === route.query.what) ? route.query.what : 'hosts'))
const fmtId = ref(route.query.fmt || (() => { try { return localStorage.getItem('labdex-label-format') } catch { return null } })() || FORMATS[0].id)
watch(fmtId, (v) => { set({ fmt: v }); try { localStorage.setItem('labdex-label-format', v) } catch { /* private mode */ } })
const fmt = computed(() => formatById(fmtId.value))
const withQr = computed(() => route.query.qr !== '0')
const skip = computed(() => Math.max(0, Math.min(200, parseInt(route.query.skip || '0', 10) || 0)))

// where a scanned code leads: the public address (Settings › General), else this one
const base = computed(() => state.meta?.public_url || window.location.origin)

const hostsQ = useQuery('/api/hosts?fields=name,uid,fun,zone,interfaces,function')
const cablesQ = useQuery('/api/cables')
const racksQ = useQuery('/api/racks')
const hosts = computed(() => hostsQ.data.value || [])
const cables = computed(() => cablesQ.data.value?.cables || [])
const racks = computed(() => racksQ.data.value?.racks || [])

// hosts
const chosenHosts = computed(() => list('host'))
const zones = computed(() => [...new Set(hosts.value.map((h) => h.zone))].sort())
const addHosts = (names) => set({ host: [...new Set([...chosenHosts.value, ...names])] })
const dropHost = (n) => set({ host: chosenHosts.value.filter((x) => x !== n) })
const zonePick = ref('')
watch(zonePick, (z) => { if (z) { addHosts(hosts.value.filter((h) => h.zone === z).map((h) => h.name)); zonePick.value = '' } })
// cables and racks: ticked ids
const chosenCables = computed(() => new Set(list('cable')))
const chosenRacks = computed(() => new Set(list('rack')))
function toggle(key, id) {
  const s = new Set(list(key)); if (s.has(id)) s.delete(id); else s.add(id)
  set({ [key]: [...s] })
}
const allCables = () => set({ cable: chosenCables.value.size === cables.value.length ? [] : cables.value.map((c) => String(c.id)) })
// ports: one host, every cabled port
const portHost = computed(() => route.query.port || '')

const items = computed(() => {
  const b = base.value
  if (what.value === 'hosts') return chosenHosts.value.map((n) => hosts.value.find((h) => h.name === n)).filter(Boolean).map((h) => hostLabel(h, b))
  if (what.value === 'cables') return cables.value.filter((c) => chosenCables.value.has(String(c.id))).flatMap((c) => cableLabels(c, b))
  if (what.value === 'ports') return portHost.value ? portLabels(portHost.value, cables.value, b) : []
  return racks.value.filter((r) => chosenRacks.value.has(r.uid)).map((r) => rackLabel(r, b))
})
const qrShown = computed(() => withQr.value && !fmt.value.flag && fmt.value.h >= QR_MIN_MM)
const pages = computed(() => {
  const f = fmt.value
  if (!f.sheet) return []
  const out = []
  items.value.forEach((it, i) => {
    const s = sheetSlot(f, i + skip.value)
    while (out.length <= s.page) out.push([])
    out[s.page].push({ ...it, x: s.x, y: s.y })
  })
  return out
})
const stripe = (c) => (c ? cableHex(c) || c : '')

// the paper: @page at the label's size (a roll) or A4 (a sheet); only the labels print
let pageStyle = null
function setPage() {
  if (!pageStyle) { pageStyle = document.createElement('style'); document.head.append(pageStyle) }
  const f = fmt.value
  pageStyle.textContent = `@media print { @page { size: ${f.sheet ? 'A4' : `${f.w}mm ${f.h}mm`}; margin: 0 } }`
}
watch(fmt, setPage)
onMounted(() => { document.body.classList.add('labels-page'); setPage() })
onBeforeUnmount(() => { document.body.classList.remove('labels-page'); pageStyle?.remove() })
const print = () => window.print()
</script>

<template>
  <div class="page wide">
    <div class="page-head no-print">
      <h1>Labels</h1>
      <span class="sub">With a QR code: scanning one opens the host, cable or rack in labdex.</span>
      <span class="grow"></span>
      <button class="btn primary" :disabled="!items.length" @click="print"><Printer :size="15" /> Print {{ items.length }} label{{ items.length === 1 ? '' : 's' }}</button>
    </div>

    <div class="layout">
      <aside class="card controls no-print">
        <nav class="tabs" aria-label="What to label">
          <a v-for="[k, l] in KINDS" :key="k" href="#" :class="{ 'router-link-active': what === k }" :aria-current="what === k ? 'page' : undefined"
            @click.prevent="set({ what: k })">{{ l }}</a>
        </nav>

        <template v-if="what === 'hosts'">
          <div class="row">
            <HostPicker all :exclude="chosenHosts" placeholder="Add a host…" style="flex: 1" @pick="(n) => addHosts([n])" />
            <select v-model="zonePick" aria-label="Add every host of a zone"><option value="">+ a whole zone</option>
              <option v-for="z in zones" :key="z" :value="z">{{ z }}</option></select>
          </div>
          <div class="chips"><span v-for="n in chosenHosts" :key="n" class="chip mono">{{ n }}
            <button class="x" :aria-label="`Remove ${n}`" @click="dropHost(n)"><X :size="12" /></button></span></div>
          <a v-if="chosenHosts.length" href="#" class="small" @click.prevent="set({ host: [] })">Clear</a>
        </template>

        <template v-else-if="what === 'cables'">
          <p class="small muted">Two labels per cable, one for each end: which end it is and where the other one goes.</p>
          <label class="small"><input type="checkbox" :checked="cables.length && chosenCables.size === cables.length" @change="allCables"> all {{ cables.length }}</label>
          <div class="pick-list">
            <label v-for="c in cables" :key="c.id" class="small"><input type="checkbox" :checked="chosenCables.has(String(c.id))" @change="toggle('cable', String(c.id))">
              <b class="mono">{{ c.label || '—' }}</b> <span class="muted">{{ c.a_host }} {{ c.a_port }} ⇄ {{ c.b_host }} {{ c.b_port }}</span></label>
          </div>
        </template>

        <template v-else-if="what === 'ports'">
          <p class="small muted">For a switch or patch panel: a label for every cabled port, saying where it goes.</p>
          <div class="row"><HostPicker all placeholder="A switch or patch panel…" style="flex: 1" @pick="(n) => set({ port: n })" /></div>
          <p v-if="portHost" class="small">Ports of <b class="mono">{{ portHost }}</b> <span class="muted">({{ items.length }} cabled)</span></p>
        </template>

        <template v-else>
          <div class="pick-list">
            <label v-for="r in racks" :key="r.uid" class="small"><input type="checkbox" :checked="chosenRacks.has(r.uid)" @change="toggle('rack', r.uid)">
              <b>{{ r.name }}</b> <span class="muted">{{ r.site }}</span></label>
            <p v-if="!racks.length" class="small muted">No racks yet.</p>
          </div>
        </template>

        <hr>
        <label class="small fld">Label<select v-model="fmtId"><option v-for="f in FORMATS" :key="f.id" :value="f.id">{{ f.name }}</option></select></label>
        <label class="small"><input type="checkbox" :checked="withQr" @change="set({ qr: withQr ? '0' : undefined })"> QR code
          <span v-if="withQr && !qrShown" class="faint">(not on labels this small)</span></label>
        <label v-if="fmt.sheet" class="small fld">Start at label<input type="number" min="1" :value="skip + 1" style="width: 70px"
          @change="(e) => set({ skip: String(Math.max(0, (parseInt(e.target.value, 10) || 1) - 1)) || undefined })">
          <span class="faint">for a sheet that's partly used</span></label>
        <p class="small faint">QR codes lead to <span class="mono">{{ base }}</span><template v-if="!state.meta?.public_url">: set the public address
          under Settings › General if your phone reaches labdex by another name</template>.</p>
        <p class="small faint">Print at 100 % (no "fit to page"), on the label size above.</p>
      </aside>

      <section class="preview">
        <p v-if="!items.length" class="muted no-print">Pick what to label on the left.</p>
        <div v-if="items.length" class="print-root" :class="{ sheetmode: !!fmt.sheet }">
          <template v-if="fmt.sheet">
            <div v-for="(p, n) in pages" :key="n" class="paper">
              <div v-for="it in p" :key="it.key" class="lbl" :style="{ left: `${it.x}mm`, top: `${it.y}mm`, width: `${fmt.w}mm`, height: `${fmt.h}mm` }">
                <div class="inner" :style="{ borderLeftColor: stripe(it.colour) || 'transparent' }">
                  <QrCode v-if="qrShown" :text="it.url" class="qr" :style="{ width: `${fmt.h - 3}mm`, height: `${fmt.h - 3}mm` }" />
                  <div class="txt" :style="{ '--t': `${Math.min(fmt.h * 0.22, 5)}mm` }">
                    <b>{{ it.title }}</b><span v-if="it.sub" class="mono">{{ it.sub }}</span><span v-for="l in it.lines" :key="l" class="ln">{{ l }}</span>
                  </div>
                </div>
              </div>
            </div>
          </template>
          <template v-else>
            <div v-for="it in items" :key="it.key" class="lbl roll" :style="{ width: `${fmt.w}mm`, height: `${fmt.h}mm` }">
              <div v-if="fmt.flag" class="flag">
                <div v-for="half in 2" :key="half" class="half" :style="{ borderLeftColor: stripe(it.colour) || 'transparent' }">
                  <b>{{ it.title }}</b><span class="mono">{{ it.sub }}</span>
                </div>
              </div>
              <div v-else class="inner" :style="{ borderLeftColor: stripe(it.colour) || 'transparent' }">
                <QrCode v-if="qrShown" :text="it.url" class="qr" :style="{ width: `${fmt.h - 3}mm`, height: `${fmt.h - 3}mm` }" />
                <div class="txt" :style="{ '--t': `${Math.min(fmt.h * 0.22, 6)}mm` }">
                  <b>{{ it.title }}</b><span v-if="it.sub" class="mono">{{ it.sub }}</span><span v-for="l in it.lines" :key="l" class="ln">{{ l }}</span>
                </div>
              </div>
            </div>
          </template>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.layout { display: grid; grid-template-columns: 340px 1fr; gap: 18px; align-items: start; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
.controls { display: flex; flex-direction: column; gap: 10px; position: sticky; top: 12px; }
.controls .tabs { margin: 0; }
.controls .row { display: flex; gap: 6px; }
.controls p { margin: 0; }
.controls hr { border: 0; border-top: 1px solid var(--line); margin: 4px 0; width: 100%; }
.fld { display: flex; flex-direction: column; gap: 4px; }
.chips { display: flex; flex-wrap: wrap; gap: 5px; }
.chip { display: inline-flex; align-items: center; gap: 3px; padding: 2px 4px 2px 8px; border-radius: 999px; background: var(--accent-faint); font-size: 12px; }
.chip .x { border: 0; background: none; padding: 1px; cursor: pointer; color: inherit; opacity: 0.6; display: inline-flex; }
.pick-list { max-height: 360px; overflow: auto; display: flex; flex-direction: column; gap: 3px; }
.pick-list label { display: flex; gap: 6px; align-items: baseline; }

/* the labels: real sizes in mm, black on white like the paper */
.preview { overflow: auto; }
.print-root { color: #000; display: flex; flex-wrap: wrap; gap: 4mm; }
.print-root.sheetmode { display: block; }
.paper { position: relative; width: 210mm; height: 297mm; background: #fff; box-shadow: 0 1px 4px rgba(0, 0, 0, 0.18); margin-bottom: 6mm; }
.paper .lbl { position: absolute; outline: 0.2mm dashed #c8c8c8; }
.lbl { background: #fff; overflow: hidden; box-sizing: border-box; font-family: var(--sans, sans-serif); }
.lbl.roll { outline: 0.2mm solid #bbb; border-radius: 1.5mm; }
.inner { display: flex; align-items: center; gap: 1.6mm; height: 100%; padding: 1.5mm 2mm 1.5mm 1.5mm; box-sizing: border-box; border-left: 2.2mm solid transparent; }
.qr { flex: none; }
.txt { min-width: 0; display: flex; flex-direction: column; line-height: 1.15; }
.txt b { font-size: var(--t); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.txt .mono { font-size: calc(var(--t) * 0.55); overflow-wrap: anywhere; line-height: 1.1; }
.txt .ln { font-size: calc(var(--t) * 0.55); color: #333; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.flag { display: grid; grid-template-columns: 1fr 1fr; height: 100%; }
.half { display: flex; flex-direction: column; justify-content: center; padding: 0 2mm; border-left: 2mm solid transparent; line-height: 1.1; min-width: 0; }
.half + .half { border-right: 0; }
.half b { font-size: 3.6mm; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.half .mono { font-size: 2.2mm; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>

<style>
/* printing from this page: only the labels, at their real size */
@media print {
  body.labels-page .tbar, body.labels-page .demobar, body.labels-page .no-print { display: none !important; }
  body.labels-page .page { padding: 0 !important; margin: 0 !important; max-width: none !important; }
  body.labels-page .layout { display: block !important; }
  body.labels-page .print-root { display: block !important; gap: 0 !important; }
  body.labels-page .lbl.roll { outline: 0 !important; border-radius: 0 !important; break-after: page; }
  body.labels-page .paper { box-shadow: none !important; margin: 0 !important; break-after: page; }
  body.labels-page .paper .lbl { outline: 0 !important; }
}
</style>
