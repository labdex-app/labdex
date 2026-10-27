import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import './style.css'
import { applyTheme } from './theme'

applyTheme()

// v-rowlink: a table row (or any element) you click to open something is also reachable
// with Tab and opens with Enter, like a link.
const rowlink = {
  mounted(el) {
    el.tabIndex = 0
    if (!el.getAttribute('role')) el.setAttribute('role', 'link')
    el._rowkey = (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target === el) { e.preventDefault(); el.click() } }
    el.addEventListener('keydown', el._rowkey)
  },
  unmounted(el) { el.removeEventListener('keydown', el._rowkey) },
}

createApp(App).use(router).directive('rowlink', rowlink).mount('#app')
