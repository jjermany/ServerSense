const defaults: RequestInit = { credentials: 'include', cache: 'no-store', headers: { 'Content-Type': 'application/json' } }

export const API_REQUEST_TIMEOUT_MS = 15_000

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message)
    this.name = 'ApiError'
  }
}

export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const controller = new AbortController()
  const callerSignal = options.signal
  const abortFromCaller = () => controller.abort(callerSignal?.reason)
  if (callerSignal?.aborted) abortFromCaller()
  else callerSignal?.addEventListener('abort', abortFromCaller, { once: true })
  const timeout = window.setTimeout(() => controller.abort(), API_REQUEST_TIMEOUT_MS)

  try {
    const response = await fetch(path, {
      ...defaults,
      ...options,
      headers: { ...defaults.headers, ...options.headers },
      signal: controller.signal,
    })
    if (!response.ok) {
      let detail = `Request failed (${response.status})`
      try { detail = (await response.json()).detail ?? detail } catch { /* response was not JSON */ }
      throw new ApiError(detail, response.status)
    }
    if (response.status === 204) return undefined as T
    return response.json() as Promise<T>
  } catch (reason) {
    if (controller.signal.aborted && !callerSignal?.aborted) {
      throw new Error('Server did not respond in time. Please try again.', { cause: reason })
    }
    throw reason
  } finally {
    window.clearTimeout(timeout)
    callerSignal?.removeEventListener('abort', abortFromCaller)
  }
}

export const formatBytes = (value: number, digits = 1) => {
  if (!Number.isFinite(value)) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  const unit = Math.min(Math.floor(Math.log(Math.max(value, 1)) / Math.log(1000)), units.length - 1)
  return `${(value / 1000 ** unit).toFixed(digits)} ${units[unit]}`
}

export const formatRate = (value: number | null) =>
  value == null ? 'Learning' : `${formatBytes(value)}/s`
