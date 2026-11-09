export function formatDateTime(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}

export function formatAmount(value?: number | null): string {
  if (value === null || value === undefined) return '-'
  return `¥${value.toFixed(2)}`
}

export function formatBytes(value?: number | null): string {
  if (value === null || value === undefined) return '-'
  if (value < 1024) return `${value} B`
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${(value / 1024 / 1024).toFixed(1)} MB`
}

export function versionLabel(version: { label?: string | null; version_number: number }): string {
  return version.label ?? `v${version.version_number}`
}

export function matchesKeyword(keyword: string, values: Array<string | number | null | undefined>): boolean {
  const query = keyword.trim().toLowerCase()
  if (!query) return true
  return values.some((value) => String(value ?? '').toLowerCase().includes(query))
}

export function uniqueOptions(values: Array<string | null | undefined>): string[] {
  return [...new Set(values.filter((value): value is string => Boolean(value)))].sort()
}

/** One status→tone map so the same domain status never gets a different colour per page. */
const statusTone: Record<string, string> = {
  open: 'orange',
  resolved: 'green',
  closed: 'default',
  pending: 'orange',
  approved: 'green',
  rejected: 'red',
  cancelled: 'default',
  accepted: 'green',
  submitted: 'blue',
  failed: 'red',
  active: 'green',
  inactive: 'default',
  draft: 'default',
  processing: 'blue',
  ready: 'cyan',
  published: 'green',
  superseded: 'default',
  archived: 'default',
}

export function statusColor(status?: string | null): string {
  return status ? (statusTone[status] ?? 'default') : 'default'
}
