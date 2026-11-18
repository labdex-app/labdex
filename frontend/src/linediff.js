// A line diff for short texts (a host's YAML, a file a plan writes): the longest common
// subsequence of lines, so unchanged lines stay put and only the real edits show.
// Texts past MAX lines on both sides fall back to "all removed, all added" to stay quick.
const MAX = 2000

export function diffLines(before = '', after = '') {
  const a = before ? before.replace(/\n$/, '').split('\n') : []
  const b = after ? after.replace(/\n$/, '').split('\n') : []
  if (a.length * b.length > MAX * MAX) {
    return [...a.map((t) => ({ op: '-', text: t })), ...b.map((t) => ({ op: '+', text: t }))]
  }
  // lengths of the common tail from (i, j), one row at a time
  const n = a.length
  const m = b.length
  const L = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1])
    }
  }
  const out = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) { out.push({ op: ' ', text: a[i] }); i++; j++ }
    else if (L[i + 1][j] >= L[i][j + 1]) out.push({ op: '-', text: a[i++] })
    else out.push({ op: '+', text: b[j++] })
  }
  while (i < n) out.push({ op: '-', text: a[i++] })
  while (j < m) out.push({ op: '+', text: b[j++] })
  return out
}

// YAML tokens for colouring: key, string, number, bool, comment. Good enough for the files
// labdex writes; it never has to parse anything a person can't read.
const SCALAR = /^(-?\d+(\.\d+)?|true|false|null|~)$/
export function yamlTokens(line) {
  const out = []
  const m = line.match(/^(\s*(?:-\s+)?)([^\s#:'"][^:#]*?|"[^"]*"|'[^']*')(:)(\s|$)(.*)$/)
  let rest = line
  if (m) {
    out.push({ t: '', s: m[1] }, { t: 'key', s: m[2] }, { t: '', s: m[3] + m[4] })
    rest = m[5]
  } else {
    const d = line.match(/^(\s*-\s+)(.*)$/)
    if (d) { out.push({ t: '', s: d[1] }); rest = d[2] }
  }
  const c = rest.search(/(^|\s)#/)
  const value = c >= 0 ? rest.slice(0, c) : rest
  const comment = c >= 0 ? rest.slice(c) : ''
  if (value) {
    const v = value.trim()
    const kind = !v ? '' : SCALAR.test(v) ? (/^-?\d/.test(v) ? 'num' : 'bool') : /^[[{|>]/.test(v) ? '' : 'str'
    out.push({ t: kind, s: value })
  }
  if (comment) out.push({ t: 'comment', s: comment })
  return out
}
