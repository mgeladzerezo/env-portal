import type { EnvironmentStatus } from '../api/client'

export function formatUsd(amount: number | null | undefined, digits = 2): string {
  if (amount === null || amount === undefined) return 'n/a'
  return `$${amount.toFixed(digits)}`
}

export function formatTimeLeft(expiresAt: string, now: Date = new Date()): string {
  const minutes = Math.floor((new Date(expiresAt).getTime() - now.getTime()) / 60_000)
  if (minutes <= 0) return 'expired'
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return `${hours}h ${minutes % 60}m`
  return `${Math.floor(hours / 24)}d ${hours % 24}h`
}

export const STATUS_LABELS: Record<EnvironmentStatus, string> = {
  requested: 'Requested',
  planning: 'Planning',
  blocked_by_policy: 'Blocked by policy',
  applying: 'Applying',
  ready: 'Ready',
  failed: 'Failed',
  destroying: 'Destroying',
  destroyed: 'Destroyed',
}

export function formatOutput(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}

// Hourly prices are fractions of a cent; two decimals would round most of them to $0.00.
export const HOURLY_DIGITS = 4
