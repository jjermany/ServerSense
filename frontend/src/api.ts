const defaults: RequestInit = { credentials: 'include', headers: { 'Content-Type': 'application/json' } }

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, { ...defaults, ...options, headers: { ...defaults.headers, ...options.headers } })
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try { detail = (await response.json()).detail ?? detail } catch { /* response was not JSON */ }
    throw new Error(detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const formatBytes = (value: number, digits = 1) => {
  if (!Number.isFinite(value)) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  const unit = Math.min(Math.floor(Math.log(Math.max(value, 1)) / Math.log(1000)), units.length - 1)
  return `${(value / 1000 ** unit).toFixed(digits)} ${units[unit]}`
}

export const formatRate = (value: number | null) =>
  value == null ? 'Learning' : `${formatBytes(value)}/s`
