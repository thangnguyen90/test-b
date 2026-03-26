import { useEffect, useMemo, useState } from 'react'
import './App.css'

type PaperTradeHistoryItem = {
  id: number
  opened_at: string
  pnl?: number | null
  result?: number | null
  entry_type?: string | null
}

type PaperTradeHistoryResponse = {
  items?: PaperTradeHistoryItem[]
}

type DailyHourlyTradeSummary = {
  trade_date: string
  trade_hour: number
  total_trades: number
  win_trades: number
  loss_trades: number
  win_rate: number
  total_pnl: number
  avg_pnl: number
}

type EntryTypeFilter = 'ALL' | 'LIMIT' | 'ML_CANDLES_BG' | 'ML_CANDLES_TEST' | 'ML_TEST'

const ENTRY_TYPE_OPTIONS: { value: EntryTypeFilter; label: string }[] = [
  { value: 'ALL', label: 'ALL' },
  { value: 'LIMIT', label: 'ML (LIMIT)' },
  { value: 'ML_CANDLES_BG', label: 'ML Candles BG' },
  { value: 'ML_CANDLES_TEST', label: 'ML Candles Test' },
  { value: 'ML_TEST', label: 'ML Test' },
]

const API_HOST = window.location.hostname === 'localhost' ? '127.0.0.1' : (window.location.hostname || '127.0.0.1')
const API_BASE_CANDIDATES = [`http://${API_HOST}:8005`, `http://${API_HOST}:8000`]
const API_HEAVY_TIMEOUT_MS = 30000
const REFRESH_MS = 30000
const HISTORY_LIMIT = 2000
const LOOKBACK_DAYS = 30
const VN_TIME_ZONE = 'Asia/Bangkok'
const dateFormatter = new Intl.DateTimeFormat('sv-SE', {
  timeZone: VN_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})
const hourFormatter = new Intl.DateTimeFormat('en-GB', {
  timeZone: VN_TIME_ZONE,
  hour: '2-digit',
  hour12: false,
})

async function fetchResponseWithTimeout(url: string, timeoutMs: number) {
  const controller = new AbortController()
  const id = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const res = await fetch(url, { signal: controller.signal })
    window.clearTimeout(id)
    return res
  } catch (err) {
    window.clearTimeout(id)
    throw err
  }
}

function formatPnl(value: number): string {
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}`
}

function matchEntryType(itemEntryType: string | null | undefined, filter: EntryTypeFilter): boolean {
  if (filter === 'ALL') return true
  const normalized = (itemEntryType ?? 'LIMIT').trim().toUpperCase()
  if (filter === 'LIMIT') return normalized === 'LIMIT'
  if (filter === 'ML_CANDLES_BG') return normalized === 'ML_CANDLES_BG'
  if (filter === 'ML_CANDLES_TEST') return normalized === 'ML_CANDLES_TEST'
  if (filter === 'ML_TEST') return normalized === 'ML_TEST'
  return normalized === filter
}

function buildDailyHourlySummary(
  items: PaperTradeHistoryItem[],
  entryTypeFilter: EntryTypeFilter,
): DailyHourlyTradeSummary[] {
  const cutoffMs = Date.now() - LOOKBACK_DAYS * 24 * 60 * 60 * 1000
  const buckets = new Map<string, DailyHourlyTradeSummary>()

  for (const item of items) {
    if (typeof item.pnl !== 'number') continue
    if (!matchEntryType(item.entry_type, entryTypeFilter)) continue

    const openedAtMs = Date.parse(item.opened_at)
    if (!Number.isFinite(openedAtMs) || openedAtMs < cutoffMs) continue

    const openedAt = new Date(openedAtMs)
    const tradeDate = dateFormatter.format(openedAt)
    const tradeHour = Number.parseInt(hourFormatter.format(openedAt), 10)
    if (!Number.isFinite(tradeHour)) continue

    const bucketKey = `${tradeDate}-${tradeHour}`
    const current = buckets.get(bucketKey) ?? {
      trade_date: tradeDate,
      trade_hour: tradeHour,
      total_trades: 0,
      win_trades: 0,
      loss_trades: 0,
      win_rate: 0,
      total_pnl: 0,
      avg_pnl: 0,
    }

    current.total_trades += 1
    current.total_pnl += item.pnl

    if (item.result === 1 || item.pnl > 0) {
      current.win_trades += 1
    } else {
      current.loss_trades += 1
    }

    current.win_rate = current.total_trades > 0 ? current.win_trades / current.total_trades : 0
    current.avg_pnl = current.total_trades > 0 ? current.total_pnl / current.total_trades : 0
    buckets.set(bucketKey, current)
  }

  return Array.from(buckets.values()).sort((a, b) => {
    if (a.trade_date !== b.trade_date) return b.trade_date.localeCompare(a.trade_date)
    return a.trade_hour - b.trade_hour
  })
}

async function fetchHistoryItems(repoScope: string): Promise<PaperTradeHistoryItem[]> {
  let lastError: Error | null = null
  for (const baseUrl of API_BASE_CANDIDATES) {
    try {
      const response = await fetchResponseWithTimeout(
        `${baseUrl}/api/v1/paper-trades/history?limit=${HISTORY_LIMIT}&repo_scope=${repoScope}`,
        API_HEAVY_TIMEOUT_MS,
      )
      if (!response.ok) {
        const detail = (await response.text().catch(() => '')).trim()
        throw new Error(detail || `History API unavailable (${response.status})`)
      }
      const payload = (await response.json()) as PaperTradeHistoryResponse
      return Array.isArray(payload.items) ? payload.items : []
    } catch (err) {
      lastError = err instanceof Error ? err : new Error('Cannot load history.')
    }
  }
  throw lastError ?? new Error('Cannot load history.')
}

async function fetchAllHistoryItems(): Promise<PaperTradeHistoryItem[]> {
  const [mainItems, candlesItems] = await Promise.all([
    fetchHistoryItems('main').catch(() => [] as PaperTradeHistoryItem[]),
    fetchHistoryItems('candles').catch(() => [] as PaperTradeHistoryItem[]),
  ])
  // Dedupe by id (main takes priority)
  const seen = new Set<number>()
  const merged: PaperTradeHistoryItem[] = []
  for (const item of [...mainItems, ...candlesItems]) {
    if (!seen.has(item.id)) {
      seen.add(item.id)
      merged.push(item)
    }
  }
  return merged
}

export default function DailyHourlyPage() {
  const [rawItems, setRawItems] = useState<PaperTradeHistoryItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [entryTypeFilter, setEntryTypeFilter] = useState<EntryTypeFilter>('ALL')

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      try {
        const items = await fetchAllHistoryItems()
        if (!cancelled) {
          setRawItems(items)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Khong the tai du lieu.')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    setLoading(true)
    load()
    const timer = window.setInterval(load, REFRESH_MS)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

  // Count trades per entry type for badge display
  const entryTypeCounts = useMemo(() => {
    const counts: Record<EntryTypeFilter, number> = { ALL: 0, LIMIT: 0, ML_CANDLES_BG: 0, ML_CANDLES_TEST: 0, ML_TEST: 0 }
    const cutoffMs = Date.now() - LOOKBACK_DAYS * 24 * 60 * 60 * 1000
    for (const item of rawItems) {
      if (typeof item.pnl !== 'number') continue
      const openedAtMs = Date.parse(item.opened_at)
      if (!Number.isFinite(openedAtMs) || openedAtMs < cutoffMs) continue
      counts.ALL += 1
      const normalized = (item.entry_type ?? 'LIMIT').trim().toUpperCase()
      if (normalized === 'LIMIT') counts.LIMIT += 1
      else if (normalized === 'ML_CANDLES_BG') counts.ML_CANDLES_BG += 1
      else if (normalized === 'ML_CANDLES_TEST') counts.ML_CANDLES_TEST += 1
      else if (normalized === 'ML_TEST') counts.ML_TEST += 1
    }
    return counts
  }, [rawItems])

  const dailyHourlyPnl = useMemo(
    () => buildDailyHourlySummary(rawItems, entryTypeFilter),
    [rawItems, entryTypeFilter],
  )

  // Compute hourly aggregation across all days for the summary row
  const hourlyAgg = useMemo(() => {
    const agg = new Map<number, { wins: number; losses: number; totalPnl: number; count: number }>()
    for (const item of dailyHourlyPnl) {
      const current = agg.get(item.trade_hour) ?? { wins: 0, losses: 0, totalPnl: 0, count: 0 }
      current.wins += item.win_trades
      current.losses += item.loss_trades
      current.totalPnl += item.total_pnl
      current.count += item.total_trades
      agg.set(item.trade_hour, current)
    }
    return agg
  }, [dailyHourlyPnl])

  const rows = useMemo(() => {
    const byDate = new Map<string, Map<number, DailyHourlyTradeSummary>>()

    for (const item of dailyHourlyPnl) {
      if (!byDate.has(item.trade_date)) {
        byDate.set(item.trade_date, new Map<number, DailyHourlyTradeSummary>())
      }
      byDate.get(item.trade_date)!.set(item.trade_hour, item)
    }

    return Array.from(byDate.entries())
      .sort((a, b) => b[0].localeCompare(a[0]))
      .map(([date, hours]) => ({ date, hours }))
  }, [dailyHourlyPnl])

  const totalTrades = dailyHourlyPnl.reduce((s, r) => s + r.total_trades, 0)
  const totalPnl = dailyHourlyPnl.reduce((s, r) => s + r.total_pnl, 0)
  const totalWins = dailyHourlyPnl.reduce((s, r) => s + r.win_trades, 0)
  const overallWinRate = totalTrades > 0 ? ((totalWins / totalTrades) * 100).toFixed(1) : '-'

  return (
    <div className="container" style={{ padding: '20px' }}>
      <header className="header">
        <h1>Daily Hourly Entry Time PnL</h1>
        <p className="subtitle">Loi nhuan thong ke theo tung gio vao lenh cua {LOOKBACK_DAYS} ngay gan nhat.</p>
      </header>

      {error && <div className="error-msg">{error}</div>}

      <div className="card">
        <div className="content" style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', alignItems: 'center' }}>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
            <strong>Entry Type:</strong>
            {ENTRY_TYPE_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={`tab-btn ${entryTypeFilter === opt.value ? 'tab-btn-active' : ''}`}
                onClick={() => setEntryTypeFilter(opt.value)}
              >
                {opt.label} ({entryTypeCounts[opt.value]})
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
            <div><strong>Days:</strong> {rows.length}</div>
            <div><strong>Trades:</strong> {totalTrades}</div>
            <div><strong>Win Rate:</strong> {overallWinRate}%</div>
            <div className={totalPnl > 0 ? 'pnl-pos' : totalPnl < 0 ? 'pnl-neg' : ''}>
              <strong>Total PnL:</strong> {formatPnl(totalPnl)}
            </div>
          </div>
        </div>

        <div className="content table-wrap" style={{ overflowX: 'auto', paddingBottom: '20px' }}>
          <table style={{ minWidth: '1200px', width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={{ position: 'sticky', left: 0, background: '#f8f3e7', zIndex: 1, padding: '10px', textAlign: 'left', borderBottom: '1px solid #d6c6a5' }}>Date</th>
                {Array.from({ length: 24 }).map((_, i) => (
                  <th key={i} style={{ padding: '10px', textAlign: 'center', borderBottom: '1px solid #d6c6a5' }}>
                    {String(i).padStart(2, '0')}h
                  </th>
                ))}
                <th style={{ padding: '10px', textAlign: 'center', borderBottom: '1px solid #d6c6a5', fontWeight: 700 }}>Day Total</th>
              </tr>
            </thead>
            <tbody>
              {/* Summary row */}
              <tr style={{ borderBottom: '2px solid #d6c6a5', background: '#f0ead2' }}>
                <td style={{ position: 'sticky', left: 0, background: '#f0ead2', zIndex: 1, padding: '10px', fontWeight: 700 }}>
                  TOTAL
                </td>
                {Array.from({ length: 24 }).map((_, hour) => {
                  const agg = hourlyAgg.get(hour)
                  if (!agg || agg.count === 0) {
                    return (
                      <td key={hour} style={{ padding: '10px', textAlign: 'center', color: '#9f8b67' }}>
                        -
                      </td>
                    )
                  }
                  const winRate = agg.count > 0 ? ((agg.wins / agg.count) * 100).toFixed(1) : '0.0'
                  const pnlClass = agg.totalPnl > 0 ? 'pnl-pos' : agg.totalPnl < 0 ? 'pnl-neg' : ''
                  return (
                    <td
                      key={hour}
                      className={pnlClass}
                      title={`Trades: ${agg.count} | W: ${agg.wins} | L: ${agg.losses} | WR: ${winRate}%`}
                      style={{ padding: '10px', textAlign: 'center', fontWeight: 700, fontSize: '0.9em' }}
                    >
                      {formatPnl(agg.totalPnl)}
                    </td>
                  )
                })}
                <td
                  className={totalPnl > 0 ? 'pnl-pos' : totalPnl < 0 ? 'pnl-neg' : ''}
                  style={{ padding: '10px', textAlign: 'center', fontWeight: 700, fontSize: '0.9em' }}
                >
                  {formatPnl(totalPnl)}
                </td>
              </tr>
              {rows.map(({ date, hours }) => {
                const dayTotal = Array.from(hours.values()).reduce((s, c) => s + c.total_pnl, 0)
                return (
                  <tr key={date} style={{ borderBottom: '1px solid #efe4cb' }}>
                    <td style={{ position: 'sticky', left: 0, background: '#f8f3e7', zIndex: 1, padding: '10px', fontWeight: 700 }}>
                      {date}
                    </td>
                    {Array.from({ length: 24 }).map((_, hour) => {
                      const cell = hours.get(hour)
                      if (!cell) {
                        return (
                          <td key={hour} style={{ padding: '10px', textAlign: 'center', color: '#9f8b67' }}>
                            -
                          </td>
                        )
                      }

                      const pnlClass = cell.total_pnl > 0 ? 'pnl-pos' : cell.total_pnl < 0 ? 'pnl-neg' : ''
                      return (
                        <td
                          key={hour}
                          className={pnlClass}
                          title={`Trades: ${cell.total_trades} | W: ${cell.win_trades} | L: ${cell.loss_trades} | WR: ${(cell.win_rate * 100).toFixed(1)}% | Avg: ${formatPnl(cell.avg_pnl)}`}
                          style={{ padding: '10px', textAlign: 'center', fontWeight: 600, fontSize: '0.9em' }}
                        >
                          {formatPnl(cell.total_pnl)}
                        </td>
                      )
                    })}
                    <td
                      className={dayTotal > 0 ? 'pnl-pos' : dayTotal < 0 ? 'pnl-neg' : ''}
                      style={{ padding: '10px', textAlign: 'center', fontWeight: 700, fontSize: '0.9em' }}
                    >
                      {formatPnl(dayTotal)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          {!loading && rows.length === 0 && !error && (
            <p style={{ padding: '20px' }}>Chua co du lieu giao dich trong {LOOKBACK_DAYS} ngay gan nhat.</p>
          )}

          {loading && <p style={{ padding: '20px' }}>Dang tai du lieu daily-hourly...</p>}
        </div>
      </div>
    </div>
  )
}