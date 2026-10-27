import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'
import { readdirSync, readFileSync, writeFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { brotliCompressSync, gzipSync, constants } from 'node:zlib'

const outDir = fileURLToPath(new URL('../backend/labdex/static', import.meta.url))

// Writes .br and .gz next to every text asset; Flask serves them when the browser accepts them.
function precompress() {
  const walk = (d) => readdirSync(d).flatMap((f) => (statSync(join(d, f)).isDirectory() ? walk(join(d, f)) : [join(d, f)]))
  return {
    name: 'labdex-precompress',
    apply: 'build',
    closeBundle() {
      for (const f of walk(outDir)) {
        if (!/\.(js|css|html|svg|json|map)$/.test(f)) continue
        const buf = readFileSync(f)
        if (buf.length < 1024) continue
        writeFileSync(f + '.br', brotliCompressSync(buf, { params: { [constants.BROTLI_PARAM_QUALITY]: 11 } }))
        writeFileSync(f + '.gz', gzipSync(buf, { level: 9 }))
      }
    },
  }
}

// `npm run build` writes straight into the Flask package, which serves it.
// `npm run dev` proxies /api to a Flask dev server on :5000.
export default defineConfig({
  plugins: [vue(), precompress()],
  build: {
    outDir,
    emptyOutDir: true,
    assetsDir: 'assets',
  },
  server: {
    proxy: { '/api': 'http://127.0.0.1:5000', '/healthz': 'http://127.0.0.1:5000' },
  },
})
