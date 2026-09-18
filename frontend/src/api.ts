export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = 'ApiError' }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, init)
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = await response.json() as { detail?: unknown; message?: string }
      if (typeof body.detail === 'string') message = body.detail
      else if (body.detail) message = JSON.stringify(body.detail)
      else if (body.message) message = body.message
    } catch { /* Keep the HTTP status when a proxy returns HTML. */ }
    throw new ApiError(message, response.status)
  }
  return response.json() as Promise<T>
}

export const post = (body: unknown): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export const errorMessage = (error: unknown): string => error instanceof Error ? error.message : 'Something went wrong. Please try again.'
export const humanize = (text: string): string => text.replaceAll('_', ' ')
export function formatValue(value: number | null | undefined, unit?: string): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—'
  const number = new Intl.NumberFormat('en-AU', { maximumFractionDigits: 3 }).format(value)
  return unit === 'percent' ? `${number}%` : unit === 'AUD_million' ? `${number} A$m` : number
}
