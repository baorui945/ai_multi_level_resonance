const BASE = ''

export async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(BASE + path)
  if (!r.ok) {
    const text = await r.text()
    throw new Error(`${r.status} ${text.slice(0, 200)}`)
  }
  return r.json() as Promise<T>
}

export async function postJSON<T>(path: string, body?: any): Promise<T> {
  const r = await fetch(BASE + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`)
  return r.json() as Promise<T>
}

export function fileURL(rel: string): string {
  return '/api/download/' + rel.split('/').map(encodeURIComponent).join('/')
}

export async function getScan(universe: string): Promise<any> {
  return getJSON(`/api/scan?universe=${encodeURIComponent(universe)}`)
}

export async function runScan(universe: string, date?: string): Promise<any> {
  return postJSON('/api/scan/run', { universe, date: date || null })
}

export async function getScanJob(id: string): Promise<any> {
  return getJSON(`/api/scan/job/${encodeURIComponent(id)}`)
}

export function downloadCSV(filename: string, headers: string[], rows: any[][]) {
  const esc = (v: any) => {
    const s = v == null ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const text = '\uFEFF' + [headers, ...rows].map(r => r.map(esc).join(',')).join('\n')
  const blob = new Blob([text], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename
  a.click()
  URL.revokeObjectURL(a.href)
}

export function fmt(v: any, digits = 2, suffix = ''): string {
  if (v == null || Number.isNaN(Number(v))) return '—'
  return Number(v).toFixed(digits) + suffix
}

export function fmtPct(v: any, digits = 2): string {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}%`
}

export function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
