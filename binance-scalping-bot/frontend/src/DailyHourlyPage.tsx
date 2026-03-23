import { useEffect, useMemo, useState } from 'react'
import './App.css'

type PaperTradeHistoryItem = {
  id: number
  opened_at: string
  pnl?: number | null
  result?: number | null
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

const API_HOST = window.location.hostname === 'localhost' ? '127.0.0.1' : (window.location.hostname || '127.0.0.1')
const API_BASE_CANDIDATES = [`http://${API_HOST}:8005`, `http://${API_HOST}:8000`]
const API_HEAVY_TIMEOUT_MS = 30000
const REFRESH_MS = 15000
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

function buildDailyHourlySummary(items: PaperTradeHistoryItem[]): DailyHourlyTradeSummary[] {
  const cutoffMs = Date.now() - LOOKBACK_DAYS * 24 * 60 * 60 * 1000
  const buckets = new Map<string, DailyHourlyTradeSummary>()

  for (const item of items) {
    if (typeof item.pnl !== 'number') continue

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

async function fetchDailyHourlySummary(): Promise<DailyHourlyTradeSummary[]> {
  let lastError: Error | null = null

  for (const baseUrl of API_BASE_CANDIDATES) {
    try {
      const response = await fetchResponseWithTimeout(
        `${baseUrl}/api/v1/paper-trades/history?limit=${HISTORY_LIMIT}`,
        API_HEAVY_TIMEOUT_MS,
      )

      if (!response.ok) {
        const detail = (await response.text().catch(() => '')).trim()
        throw new Error(detail || `Paper trade history API unavailable (${response.status})`)
      }

      const payload = (await response.json()) as PaperTradeHistoryResponse
      return buildDailyHourlySummary(Array.isArray(payload.items) ? payload.items : [])
    } catch (err) {
      lastError = err instanceof Error ? err : new Error('Khong the tai du lieu daily-hourly.')
    }
  }

  throw lastError ?? new Error('Khong the tai du lieu daily-hourly.')
}

export default function DailyHourlyPage() {
  const [dailyHourlyPnl, setDailyHourlyPnl] = useState<DailyHourlyTradeSummary[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      try {
        const rows = await fetchDailyHourlySummary()
        if (!cancelled) {
          setDailyHourlyPnl(rows)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Khong the tai du lieu daily-hourly.')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    load()
    const timer = window.setInterval(load, REFRESH_MS)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [])

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

  return (
    <div className="container" style={{ padding: '20px' }}>
      <header className="header">
        <h1>Daily Hourly Entry Time PnL</h1>
        <p className="subtitle">Loi nhuan thong ke theo tung gio vao lenh cua 30 ngay gan nhat.</p>
      </header>

      {error && <div className="error-msg">{error}</div>}

      <div className="card">
        <div className="content" style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
          <div>
            <strong>So ngay:</strong> {rows.length}
          </div>
          <div>
            <strong>So o co giao dich:</strong> {dailyHourlyPnl.length}
          </div>
          <div>
            <strong>Nguon du lieu:</strong> history API
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
              </tr>
            </thead>
            <tbody>
              {rows.map(({ date, hours }) => (
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
                </tr>
              ))}
            </tbody>
          </table>

          {!loading && rows.length === 0 && !error && (
            <p style={{ padding: '20px' }}>Chua co du lieu giao dich trong 30 ngay gan nhat.</p>
          )}

          {loading && <p style={{ padding: '20px' }}>Dang tai du lieu daily-hourly...</p>}
        </div>
      </div>
    </div>
  )
}