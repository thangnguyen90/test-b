import { useEffect, useMemo, useRef, useState } from 'react'
import '../App.css'
import './EntryTouchSignalsPage.css'

const API_BASE = 'http://127.0.0.1:8000'
const WS_BASE = API_BASE.replace(/^http/, 'ws')
const REFRESH_MS = 20_000
const ENTRY_TOUCH_SLIPPAGE = 0.0015
const AUTO_OPEN_COOLDOWN_MS = 30_000
const LIVE_TOP_SYMBOLS = 24
const FALLBACK_ORDER_USDT = 20
const AUTO_ENTRY_MIN_EFFECTIVE_SCORE = 50
const TABLE_PAGE_SIZE = 12
const ENTRY_MAX_15M_RANGE_PCT = 4.8
const ENTRY_MAX_15M_ATR_PCT = 2.8
const ENTRY_MAX_15M_UPPER_WICK_PCT = 2.4

type PumpHunterItem = {
  symbol: string
  mark_price: number
  suggested_entry_price?: number
  pump_score: number
  effective_score: number
  stage: string
  signal_label: string
  est_liq_target_price: number
  est_liq_target_low: number
  est_liq_target_high: number
  est_liq_distance_pct: number
  invalidation_price: number
  rejection_score: number
  volume_ratio_15m: number
  range_pct_15m: number
  atr_pct_15m: number
  upper_wick_pct_15m: number
  ticker_quote_volume: number
  updated_at: string
}

type PumpHunterScanResponse = {
  scanned: number
  count: number
  min_score: number
  max_symbols: number
  items: PumpHunterItem[]
  updated_at: string
  note: string
}

type PaperTrade = {
  id: number
  symbol: string
  side: 'LONG' | 'SHORT'
  entry_type?: string
  signal_win_probability: number
  effective_win_probability: number
  entry_price: number
  take_profit: number
  stop_loss: number
  quantity: number
  leverage: number
  margin_usdt?: number | null
  status: string
  opened_at: string
  closed_at?: string | null
  close_price?: number | null
  close_reason?: string | null
  pnl?: number | null
  pnl_pct?: number | null
  entry_point_score?: number | null
}

type MarketPricesBatchResponse = {
  prices?: Record<string, number>
  timestamp?: string | null
  timestamps?: Record<string, string>
}

type PriceStreamMessage = {
  type: string
  prices?: Record<string, number>
  timestamp?: string | null
  timestamps?: Record<string, string>
}

type PaperMarketOpenRequest = {
  symbol: string
  side: 'LONG' | 'SHORT'
  signal_win_probability: number
  effective_win_probability?: number
  repo_scope?: 'main' | 'candles' | 'auto'
  entry_type?: string
  entry_price?: number
  entry_point_score?: number
  take_profit: number
  stop_loss: number
  order_usdt?: number
}

type PaperManualCloseRequest = {
  force_result?: 0 | 1
}

type PaperTradeStats = {
  order_usdt: number
  margin_usdt: number
  leverage: number
}

type PumpEntrySignal = {
  symbol: string
  side: 'LONG' | 'SHORT'
  signal_label: string
  stage: string
  pump_score: number
  effective_score: number
  mark_price: number
  live_mark_price: number
  entry_price: number
  take_profit_1: number
  take_profit_2: number
  take_profit: number
  stop_loss: number
  can_enter: boolean
  touched: boolean
  blocked_reason: string
  rejection_score: number
  volume_ratio_15m: number
  range_pct_15m: number
  atr_pct_15m: number
  upper_wick_pct_15m: number
  ticker_quote_volume: number
  entry_distance_pct: number
  target_distance_pct: number
  tp1_distance_pct: number
  projected_profit_1_usdt: number
  projected_profit_2_usdt: number
  projected_loss_usdt: number
  updated_at: string
}

function canonicalSymbol(symbol: string): string {
  return symbol.replace(':USDT', '').trim().toUpperCase()
}

function formatVnTimestamp(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('vi-VN', {
    dateStyle: 'short',
    timeStyle: 'medium',
    timeZone: 'Asia/Ho_Chi_Minh',
  }).format(date)
}

function formatCompact(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 2,
  }).format(value)
}

function formatPct(value?: number | null, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`
}

function calcPnlPct(
  side: 'LONG' | 'SHORT',
  entryPrice: number,
  closePrice: number,
  leverage: number,
): number | null {
  if (!Number.isFinite(closePrice) || closePrice <= 0) return null
  if (entryPrice <= 0 || leverage <= 0) return null
  const movePct = side === 'LONG'
    ? (closePrice - entryPrice) / entryPrice
    : (entryPrice - closePrice) / entryPrice
  return movePct * leverage * 100
}

function calcMarginUsdt(entryPrice: number, quantity: number, leverage: number): number | null {
  if (entryPrice <= 0 || quantity <= 0 || leverage <= 0) return null
  return (entryPrice * quantity) / leverage
}

function isEntryTouched(entryPrice: number, markPrice?: number): boolean {
  if (!Number.isFinite(entryPrice) || entryPrice <= 0) return false
  if (!Number.isFinite(markPrice) || (markPrice as number) <= 0) return false
  const upper = entryPrice * (1 + ENTRY_TOUCH_SLIPPAGE)
  const lower = entryPrice * (1 - ENTRY_TOUCH_SLIPPAGE)
  return (markPrice as number) >= lower && (markPrice as number) <= upper
}

function calcProjectedPnlUsdt(
  side: 'LONG' | 'SHORT',
  entryPrice: number,
  exitPrice: number,
  orderUsdt: number,
): number {
  if (!Number.isFinite(entryPrice) || entryPrice <= 0) return 0
  if (!Number.isFinite(exitPrice) || exitPrice <= 0) return 0
  const movePct = side === 'LONG'
    ? ((exitPrice - entryPrice) / entryPrice)
    : ((entryPrice - exitPrice) / entryPrice)
  return orderUsdt * movePct
}

function deriveTp1(
  side: 'LONG' | 'SHORT',
  entryPrice: number,
  takeProfit2: number,
): number {
  if (!Number.isFinite(entryPrice) || entryPrice <= 0) return takeProfit2
  if (!Number.isFinite(takeProfit2) || takeProfit2 <= 0) return takeProfit2
  const move = takeProfit2 - entryPrice
  return side === 'LONG'
    ? entryPrice + (move * 0.5)
    : entryPrice + (move * 0.5)
}

function derivePostSweepShortEntry(item: Pick<PumpHunterItem, 'mark_price' | 'suggested_entry_price' | 'invalidation_price' | 'est_liq_target_low' | 'est_liq_target_high'>, liveMark: number): number {
  if (typeof item.suggested_entry_price === 'number' && item.suggested_entry_price > 0) {
    return item.suggested_entry_price
  }
  const currentPrice = liveMark > 0 ? liveMark : item.mark_price
  if (!Number.isFinite(currentPrice) || currentPrice <= 0) return item.mark_price
  const invalidationPrice = Number.isFinite(item.invalidation_price) && item.invalidation_price > 0
    ? item.invalidation_price
    : currentPrice
  const zoneLow = Number.isFinite(item.est_liq_target_low) && item.est_liq_target_low > 0
    ? item.est_liq_target_low
    : 0
  const zoneHigh = Number.isFinite(item.est_liq_target_high) && item.est_liq_target_high > 0
    ? item.est_liq_target_high
    : 0
  const baseEntry = Math.max(currentPrice, invalidationPrice)
  let reboundCap = zoneLow > baseEntry ? zoneLow : 0
  if (reboundCap <= baseEntry && zoneHigh > baseEntry) {
    reboundCap = zoneHigh * 0.985
  }
  let entry = reboundCap > baseEntry
    ? baseEntry + ((reboundCap - baseEntry) * 0.55)
    : baseEntry * 1.0015
  if (zoneHigh > 0) {
    entry = Math.min(entry, zoneHigh * 0.9975)
  }
  return Math.max(baseEntry, entry)
}

function isTooVolatile15m(item: Pick<PumpHunterItem, 'range_pct_15m' | 'atr_pct_15m' | 'upper_wick_pct_15m'>): boolean {
  return (
    item.range_pct_15m >= ENTRY_MAX_15M_RANGE_PCT
    || item.atr_pct_15m >= ENTRY_MAX_15M_ATR_PCT
    || item.upper_wick_pct_15m >= ENTRY_MAX_15M_UPPER_WICK_PCT
  )
}

function toPumpEntrySignal(item: PumpHunterItem, orderUsdt: number, livePrice?: number): PumpEntrySignal {
  const liveMark = typeof livePrice === 'number' && Number.isFinite(livePrice) && livePrice > 0
    ? livePrice
    : item.mark_price
  const isPostSweep = item.signal_label === 'ENTER' || item.signal_label === 'SWEEPED' || item.stage === 'POST_SWEEP' || item.stage === 'SWEEPED'
  const side: 'LONG' | 'SHORT' = isPostSweep ? 'SHORT' : 'LONG'
  const entryPrice = isPostSweep
    ? derivePostSweepShortEntry(item, liveMark)
    : liveMark
  const takeProfit = isPostSweep ? item.invalidation_price : item.est_liq_target_price
  const takeProfit1 = deriveTp1(side, entryPrice, takeProfit)
  const stopLoss = isPostSweep ? item.est_liq_target_high : item.invalidation_price
  const touched = isEntryTouched(entryPrice, liveMark)
  const passesScoreGate = item.effective_score > AUTO_ENTRY_MIN_EFFECTIVE_SCORE
  const blockedBy15mVolatility = isTooVolatile15m(item)
  const geometryOk = side === 'SHORT'
    ? takeProfit < entryPrice && stopLoss > entryPrice
    : takeProfit > entryPrice && stopLoss < entryPrice
  const canEnter = item.signal_label === 'ENTER' && passesScoreGate && !blockedBy15mVolatility && geometryOk && touched
  let blockedReason = '-'
  if (blockedBy15mVolatility) {
    blockedReason = `Loai do bien dong 15m cao (${item.range_pct_15m.toFixed(1)}% range)`
  } else if (!passesScoreGate) {
    blockedReason = `Điểm phải > ${AUTO_ENTRY_MIN_EFFECTIVE_SCORE}`
  } else if (!geometryOk) {
    blockedReason = side === 'SHORT'
      ? 'Entry/TP chua hop le, tranh short ngay sau nen sap'
      : 'Entry/TP chua hop le'
  } else if (!touched && side === 'SHORT') {
    blockedReason = 'Cho gia hoi cham entry roi moi short'
  } else if (!touched) {
    blockedReason = 'Cho gia cham entry'
  } else if (item.signal_label === 'SWEEPED') {
    blockedReason = 'Đã sweep nhưng chưa xác nhận entry'
  } else if (item.signal_label !== 'ENTER') {
    blockedReason = 'Chưa đủ xác nhận entry'
  }
  const entryDistancePct = entryPrice > 0 ? ((entryPrice - liveMark) / liveMark) * 100.0 : 0.0
  const tp1DistancePct = takeProfit1 > 0 ? ((takeProfit1 - liveMark) / liveMark) * 100.0 : 0.0
  const targetDistancePct = takeProfit > 0 ? ((takeProfit - liveMark) / liveMark) * 100.0 : 0.0
  const projectedProfit1Usdt = calcProjectedPnlUsdt(side, entryPrice, takeProfit1, orderUsdt)
  const projectedProfit2Usdt = calcProjectedPnlUsdt(side, entryPrice, takeProfit, orderUsdt)
  const projectedLossUsdt = calcProjectedPnlUsdt(side, entryPrice, stopLoss, orderUsdt)
  return {
    symbol: item.symbol,
    side,
    signal_label: item.signal_label,
    stage: item.stage,
    pump_score: item.pump_score,
    effective_score: item.effective_score,
    mark_price: item.mark_price,
    live_mark_price: liveMark,
    entry_price: entryPrice,
    take_profit_1: takeProfit1,
    take_profit_2: takeProfit,
    take_profit: takeProfit,
    stop_loss: stopLoss,
    can_enter: canEnter,
    touched,
    blocked_reason: blockedReason,
    rejection_score: item.rejection_score,
    volume_ratio_15m: item.volume_ratio_15m,
    range_pct_15m: item.range_pct_15m,
    atr_pct_15m: item.atr_pct_15m,
    upper_wick_pct_15m: item.upper_wick_pct_15m,
    ticker_quote_volume: item.ticker_quote_volume,
    entry_distance_pct: entryDistancePct,
    target_distance_pct: targetDistancePct,
    tp1_distance_pct: tp1DistancePct,
    projected_profit_1_usdt: projectedProfit1Usdt,
    projected_profit_2_usdt: projectedProfit2Usdt,
    projected_loss_usdt: projectedLossUsdt,
    updated_at: item.updated_at,
  }
}

export default function EntryTouchSignalsPage() {
  const autoOpenedRef = useRef<Record<string, number>>({})
  const [scan, setScan] = useState<PumpHunterScanResponse | null>(null)
  const [openTrades, setOpenTrades] = useState<PaperTrade[]>([])
  const [historyTrades, setHistoryTrades] = useState<PaperTrade[]>([])
  const [paperStats, setPaperStats] = useState<PaperTradeStats | null>(null)
  const [livePrices, setLivePrices] = useState<Record<string, number>>({})
  const [livePriceTime, setLivePriceTime] = useState<Record<string, string>>({})
  const [minScore, setMinScore] = useState(40)
  const [maxSymbols, setMaxSymbols] = useState(80)
  const [limit, setLimit] = useState(30)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [isOpeningMarketOrder, setIsOpeningMarketOrder] = useState(false)
  const [closingTradeId, setClosingTradeId] = useState<number | null>(null)
  const [activeTab, setActiveTab] = useState<'signals' | 'profit' | 'history'>('signals')
  const [signalsPage, setSignalsPage] = useState(1)
  const [profitPage, setProfitPage] = useState(1)
  const [historyPage, setHistoryPage] = useState(1)
  const [error, setError] = useState('')
  const [priceWsStatus, setPriceWsStatus] = useState<'connecting' | 'live' | 'fallback'>('fallback')
  const orderUsdtPerTrade = paperStats && typeof paperStats.order_usdt === 'number' && paperStats.order_usdt > 0
    ? paperStats.order_usdt
    : FALLBACK_ORDER_USDT

  const openTradeKeySet = useMemo(() => {
    const set = new Set<string>()
    for (const row of openTrades) {
      set.add(`${canonicalSymbol(row.symbol)}:${row.side}`)
    }
    return set
  }, [openTrades])

  const pumpEntryOpenTrades = useMemo(
    () => openTrades.filter((row) => String(row.entry_type ?? '').trim().toUpperCase() === 'PUMP_ENTRY_TOUCH'),
    [openTrades],
  )

  const pumpEntryClosedTrades = useMemo(
    () => historyTrades.filter((row) => String(row.entry_type ?? '').trim().toUpperCase() === 'PUMP_ENTRY_TOUCH'),
    [historyTrades],
  )

  const liveSymbols = useMemo(() => {
    const set = new Set<string>()
    for (const item of (scan?.items ?? []).slice(0, Math.min(LIVE_TOP_SYMBOLS, Math.max(limit, 1)))) {
      set.add(item.symbol)
    }
    for (const row of pumpEntryOpenTrades) set.add(row.symbol)
    return Array.from(set)
  }, [scan, limit, pumpEntryOpenTrades])
  const liveSymbolsKey = useMemo(() => liveSymbols.join(','), [liveSymbols])

  const signals = useMemo(() => {
    const rows = (scan?.items ?? [])
      .filter((item) => item.signal_label === 'ENTER' || item.signal_label === 'SWEEPED' || item.signal_label === 'ARMING')
      .filter((item) => !isTooVolatile15m(item))
      .map((item) => toPumpEntrySignal(item, orderUsdtPerTrade, livePrices[item.symbol]))
    rows.sort((a, b) => {
      if (a.can_enter !== b.can_enter) return a.can_enter ? -1 : 1
      if (a.touched !== b.touched) return a.touched ? -1 : 1
      return b.effective_score - a.effective_score
    })
    return rows
  }, [scan, livePrices, orderUsdtPerTrade])

  const summary = useMemo(() => {
    const touched = signals.filter((item) => item.touched).length
    const ready = signals.filter((item) => item.can_enter).length
    const floatingPnlUsdt = pumpEntryOpenTrades.reduce((sum, row) => {
      const mark = livePrices[row.symbol]
      const upnlPct = typeof mark === 'number' ? calcPnlPct(row.side, row.entry_price, mark, row.leverage) : null
      const marginUsdt = typeof row.margin_usdt === 'number'
        ? row.margin_usdt
        : calcMarginUsdt(row.entry_price, row.quantity, row.leverage)
      if (typeof upnlPct !== 'number' || typeof marginUsdt !== 'number') return sum
      return sum + ((marginUsdt * upnlPct) / 100)
    }, 0)
    return {
      touched,
      ready,
      open: pumpEntryOpenTrades.length,
      closed: pumpEntryClosedTrades.length,
      hits: signals.length,
      enter: signals.filter((item) => item.signal_label === 'ENTER').length,
      floatingPnlUsdt,
      closedPnlUsdt: pumpEntryClosedTrades.reduce((sum, row) => sum + (typeof row.pnl === 'number' ? row.pnl : 0), 0),
    }
  }, [signals, pumpEntryOpenTrades, pumpEntryClosedTrades, livePrices])

  async function fetchSignals() {
    const params = new URLSearchParams({
      max_symbols: String(maxSymbols),
      min_score: String(minScore),
      limit: String(limit),
    })
    const response = await fetch(`${API_BASE}/api/v1/analytics/pump-hunter?${params.toString()}`)
    if (!response.ok) throw new Error(`Không thể quét pump hunter (${response.status})`)
    const payload = await response.json() as PumpHunterScanResponse
    setScan(payload)
    setLastUpdated(payload.updated_at ?? new Date().toISOString())
  }

  async function fetchOpenTrades() {
    const response = await fetch(`${API_BASE}/api/v1/paper-trades/open?repo_scope=main`)
    if (!response.ok) throw new Error(`Không thể lấy lệnh mở (${response.status})`)
    const payload = await response.json() as { items: PaperTrade[] }
    setOpenTrades(payload.items ?? [])
  }

  async function fetchHistoryTrades() {
    const response = await fetch(`${API_BASE}/api/v1/paper-trades/history?limit=200&repo_scope=main`)
    if (!response.ok) throw new Error(`Không thể lấy lịch sử lệnh (${response.status})`)
    const payload = await response.json() as { items: PaperTrade[] }
    setHistoryTrades(payload.items ?? [])
  }

  async function fetchPaperStats() {
    const response = await fetch(`${API_BASE}/api/v1/paper-trades/stats?repo_scope=main`)
    if (!response.ok) throw new Error(`Không thể lấy cấu hình paper trade (${response.status})`)
    const payload = await response.json() as { stats?: PaperTradeStats }
    setPaperStats(payload.stats ?? null)
  }

  async function refreshAll() {
    setIsLoading(true)
    setError('')
    try {
      await Promise.all([fetchSignals(), fetchOpenTrades(), fetchHistoryTrades(), fetchPaperStats()])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Lỗi không xác định')
    } finally {
      setIsLoading(false)
    }
  }

  async function openPaperMarketOrder(input: PaperMarketOpenRequest) {
    setIsOpeningMarketOrder(true)
    try {
      const response = await fetch(`${API_BASE}/api/v1/paper-trades/market-open`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      })
      if (response.status === 409) return
      if (!response.ok) {
        const text = await response.text()
        throw new Error(`Mở lệnh thất bại: ${text}`)
      }
    } finally {
      setIsOpeningMarketOrder(false)
    }
  }

  async function closePaperTrade(tradeId: number, payload: PaperManualCloseRequest = {}) {
    setClosingTradeId(tradeId)
    try {
      const response = await fetch(`${API_BASE}/api/v1/paper-trades/close/${tradeId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!response.ok) {
        const text = await response.text()
        throw new Error(`Đóng lệnh thất bại: ${text}`)
      }
      await refreshAll()
    } finally {
      setClosingTradeId(null)
    }
  }

  useEffect(() => {
    refreshAll().catch(() => undefined)
  }, [minScore, maxSymbols, limit])

  useEffect(() => {
    if (!autoRefresh) return () => undefined
    const timer = window.setInterval(() => {
      refreshAll().catch(() => undefined)
    }, REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [autoRefresh, minScore, maxSymbols, limit])

  useEffect(() => {
    if (liveSymbols.length === 0) {
      setPriceWsStatus('fallback')
      return () => undefined
    }

    let socket: WebSocket | null = null
    let reconnectTimer: number | null = null
    let fallbackTimer: number | null = null
    let mounted = true

    const fetchBatchFallback = async () => {
      const response = await fetch(`${API_BASE}/api/v1/market/prices?symbols=${encodeURIComponent(liveSymbols.join(','))}`)
      if (!response.ok) throw new Error(`Không thể lấy giá batch (${response.status})`)
      const data = await response.json() as MarketPricesBatchResponse
      if (!mounted) return
      setLivePrices((prev) => ({ ...prev, ...(data.prices ?? {}) }))
      if (data.timestamps && Object.keys(data.timestamps).length > 0) {
        setLivePriceTime((prev) => ({ ...prev, ...data.timestamps }))
      } else if (data.timestamp && data.prices) {
        const updates: Record<string, string> = {}
        for (const key of Object.keys(data.prices)) updates[key] = data.timestamp
        setLivePriceTime((prev) => ({ ...prev, ...updates }))
      }
    }

    const stopFallback = () => {
      if (fallbackTimer != null) {
        window.clearInterval(fallbackTimer)
        fallbackTimer = null
      }
    }

    const startFallback = () => {
      if (fallbackTimer != null) return
      setPriceWsStatus('fallback')
      fallbackTimer = window.setInterval(() => {
        fetchBatchFallback().catch(() => undefined)
      }, 2000)
    }

    const connect = () => {
      if (!mounted) return
      setPriceWsStatus('connecting')
      socket = new WebSocket(`${WS_BASE}/ws/prices?symbols=${encodeURIComponent(liveSymbols.join(','))}&interval_sec=1`)

      socket.onopen = () => {
        if (!mounted) return
        setPriceWsStatus('live')
        stopFallback()
      }

      socket.onmessage = (event) => {
        if (!mounted) return
        try {
          const payload = JSON.parse(event.data) as PriceStreamMessage
          if (payload.type !== 'prices' || !payload.prices) return
          setPriceWsStatus('live')
          stopFallback()
          setLivePrices((prev) => ({ ...prev, ...payload.prices! }))
          if (payload.timestamps && Object.keys(payload.timestamps).length > 0) {
            setLivePriceTime((prev) => ({ ...prev, ...payload.timestamps! }))
          } else if (payload.timestamp) {
            const updates: Record<string, string> = {}
            for (const key of Object.keys(payload.prices)) updates[key] = payload.timestamp
            setLivePriceTime((prev) => ({ ...prev, ...updates }))
          }
        } catch {
          // ignore malformed payload
        }
      }

      socket.onerror = () => {
        if (!mounted) return
        startFallback()
      }

      socket.onclose = () => {
        if (!mounted) return
        startFallback()
        reconnectTimer = window.setTimeout(connect, 4000)
      }
    }

    fetchBatchFallback().catch(() => undefined)
    connect()

    return () => {
      mounted = false
      if (reconnectTimer != null) window.clearTimeout(reconnectTimer)
      stopFallback()
      socket?.close()
    }
  }, [liveSymbolsKey])

  useEffect(() => {
    if (isOpeningMarketOrder) return
    const now = Date.now()
    const run = async () => {
      for (const item of signals) {
        if (!item.can_enter || !item.touched) continue
        const key = `${canonicalSymbol(item.symbol)}:${item.side}`
        if (openTradeKeySet.has(key)) continue
        const lastOpened = autoOpenedRef.current[key] ?? 0
        if ((now - lastOpened) < AUTO_OPEN_COOLDOWN_MS) continue

        autoOpenedRef.current[key] = now
        try {
          await openPaperMarketOrder({
            symbol: item.symbol,
            side: item.side,
            signal_win_probability: Math.min(0.99, Math.max(0.01, item.pump_score / 100)),
            effective_win_probability: Math.min(0.99, Math.max(0.01, item.effective_score / 100)),
            repo_scope: 'main',
            entry_type: 'PUMP_ENTRY_TOUCH',
            entry_price: item.entry_price,
            entry_point_score: item.effective_score,
            take_profit: item.take_profit,
            stop_loss: item.stop_loss,
            order_usdt: orderUsdtPerTrade,
          })
          await refreshAll()
          break
        } catch {
          // ignore transient failures
        }
      }
    }
    run().catch(() => undefined)
  }, [signals, openTradeKeySet, isOpeningMarketOrder])

  const profitRows = useMemo(() => {
    return pumpEntryOpenTrades.map((row) => {
      const mark = livePrices[row.symbol]
      const upnlPct = typeof mark === 'number' ? calcPnlPct(row.side, row.entry_price, mark, row.leverage) : null
      const marginUsdt = typeof row.margin_usdt === 'number'
        ? row.margin_usdt
        : calcMarginUsdt(row.entry_price, row.quantity, row.leverage)
      const upnlUsdt = (typeof upnlPct === 'number' && typeof marginUsdt === 'number')
        ? (marginUsdt * upnlPct / 100)
        : null
      const tp1 = deriveTp1(row.side, row.entry_price, row.take_profit)
      const tp1Pct = calcPnlPct(row.side, row.entry_price, tp1, row.leverage)
      const tpPct = calcPnlPct(row.side, row.entry_price, row.take_profit, row.leverage)
      return {
        ...row,
        mark,
        upnlPct,
        upnlUsdt,
        marginUsdt,
        tp1,
        tp1Pct,
        tpPct,
      }
    }).sort((a, b) => {
      const aVal = typeof a.upnlUsdt === 'number' ? a.upnlUsdt : Number.NEGATIVE_INFINITY
      const bVal = typeof b.upnlUsdt === 'number' ? b.upnlUsdt : Number.NEGATIVE_INFINITY
      return bVal - aVal
    })
  }, [pumpEntryOpenTrades, livePrices])

  const signalTotalPages = Math.max(1, Math.ceil(signals.length / TABLE_PAGE_SIZE))
  const profitTotalPages = Math.max(1, Math.ceil(profitRows.length / TABLE_PAGE_SIZE))
  const historyTotalPages = Math.max(1, Math.ceil(pumpEntryClosedTrades.length / TABLE_PAGE_SIZE))

  const pagedSignals = useMemo(() => {
    const start = (signalsPage - 1) * TABLE_PAGE_SIZE
    return signals.slice(start, start + TABLE_PAGE_SIZE)
  }, [signals, signalsPage])

  const pagedProfitRows = useMemo(() => {
    const start = (profitPage - 1) * TABLE_PAGE_SIZE
    return profitRows.slice(start, start + TABLE_PAGE_SIZE)
  }, [profitRows, profitPage])

  const pagedHistoryRows = useMemo(() => {
    const start = (historyPage - 1) * TABLE_PAGE_SIZE
    return pumpEntryClosedTrades.slice(start, start + TABLE_PAGE_SIZE)
  }, [pumpEntryClosedTrades, historyPage])

  useEffect(() => {
    setSignalsPage((prev) => Math.min(prev, signalTotalPages))
  }, [signalTotalPages])

  useEffect(() => {
    setProfitPage((prev) => Math.min(prev, profitTotalPages))
  }, [profitTotalPages])

  useEffect(() => {
    setHistoryPage((prev) => Math.min(prev, historyTotalPages))
  }, [historyTotalPages])

  function renderPagination(currentPage: number, totalPages: number, setPage: (value: number) => void) {
    if (totalPages <= 1) return null
    return (
      <div className="entry-touch-pagination">
        <button
          type="button"
          className="btn-inline"
          disabled={currentPage <= 1}
          onClick={() => setPage(Math.max(1, currentPage - 1))}
        >
          Trang trước
        </button>
        <span className="entry-touch-pagination-meta">Trang {currentPage}/{totalPages}</span>
        <button
          type="button"
          className="btn-inline"
          disabled={currentPage >= totalPages}
          onClick={() => setPage(Math.min(totalPages, currentPage + 1))}
        >
          Trang sau
        </button>
      </div>
    )
  }

  return (
    <main className="app-shell app-shell-wide entry-touch-page">
      <section className="hero">
        <p className="eyebrow">Pump Entry Touch</p>
        <h1>Vào Lệnh Riêng Cho Model Pump Hunter</h1>
        <p className="subtext">
          Màn này dùng trực tiếp tín hiệu từ Pump Hunter, không còn đi qua model cũ. Chỉ những tín hiệu của model mới mới xuất hiện ở đây.
        </p>
        <div className="hero-actions entry-touch-actions">
          <button type="button" onClick={() => { refreshAll().catch(() => undefined) }}>
            {isLoading ? 'Đang quét...' : 'Quét lại ngay'}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              window.location.search = '?view=pump-hunter'
            }}
          >
            Về Pump Hunter
          </button>
        </div>
      </section>

      <section className="grid two-col entry-touch-top">
        <article className="card">
          <header className="card-header">
            <h2>Cài Đặt</h2>
            <span className="badge success">Auto Entry Luôn Bật</span>
          </header>
          <div className="daily-filter-row">
            <label className="entry-touch-field">
              <span>Điểm tối thiểu</span>
              <input
                className="search-input"
                type="number"
                min="0"
                max="100"
                value={minScore}
                onChange={(event) => setMinScore(Number(event.target.value) || 0)}
              />
            </label>
            <label className="entry-touch-field">
              <span>Quét tối đa</span>
              <input
                className="search-input"
                type="number"
                min="0"
                max="500"
                value={maxSymbols}
                onChange={(event) => setMaxSymbols(Number(event.target.value) || 0)}
              />
            </label>
            <label className="entry-touch-field">
              <span>Lấy top</span>
              <input
                className="search-input"
                type="number"
                min="1"
                max="100"
                value={limit}
                onChange={(event) => setLimit(Number(event.target.value) || 30)}
              />
            </label>
            <label className="entry-touch-toggle">
              <input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />
              <span>Tự làm mới 20 giây</span>
            </label>
          </div>
          <p className="subtext">
            Cập nhật lần cuối: {formatVnTimestamp(lastUpdated)} | {priceWsStatus === 'live' ? 'Giá WS live' : priceWsStatus === 'connecting' ? 'WS đang nối' : 'REST fallback'} | Auto paper entry khi model mới xác nhận ENTER
          </p>
          <p className="subtext">Chỉ auto vào lệnh khi `Điểm &gt; 50`, tín hiệu là `ENTER`, và giá đã chạm entry.</p>
          <p className="subtext">Tự loại coin có biên độ 15m quá gắt: range ≥ {ENTRY_MAX_15M_RANGE_PCT}%, ATR ≥ {ENTRY_MAX_15M_ATR_PCT}%, hoặc râu trên ≥ {ENTRY_MAX_15M_UPPER_WICK_PCT}%.</p>
          <p className="subtext">Mỗi lệnh auto dùng {orderUsdtPerTrade} USDT vốn vào lệnh, lấy từ env `PAPER_TRADE_ORDER_USDT` của backend.</p>
          {scan?.note ? <p className="subtext">{scan.note}</p> : null}
        </article>

        <article className="card">
          <header className="card-header">
            <h2>Tổng Quan</h2>
            <span className="badge neutral">Scanned: {scan?.scanned ?? 0}</span>
          </header>
          <div className="stats-grid">
            <div className="stats-item"><strong>Tín hiệu:</strong> {summary.hits}</div>
            <div className="stats-item"><strong>ENTER:</strong> {summary.enter}</div>
            <div className="stats-item"><strong>Đã chạm entry:</strong> {summary.touched}</div>
            <div className="stats-item"><strong>Vào được:</strong> {summary.ready}</div>
            <div className="stats-item"><strong>Lệnh mở:</strong> {summary.open}</div>
            <div className="stats-item"><strong>Lệnh đã đóng:</strong> {summary.closed}</div>
            <div className="stats-item"><strong>uPnL mở:</strong> {`${summary.floatingPnlUsdt >= 0 ? '+' : ''}${summary.floatingPnlUsdt.toFixed(2)} USDT`}</div>
            <div className="stats-item"><strong>PnL đã chốt:</strong> {`${summary.closedPnlUsdt >= 0 ? '+' : ''}${summary.closedPnlUsdt.toFixed(2)} USDT`}</div>
          </div>
        </article>
      </section>

      <section className="card">
        <header className="card-header">
          <h2>Điều Khiển</h2>
          <div className="entry-touch-tab-row">
            <button
              type="button"
              className={activeTab === 'signals' ? 'entry-touch-tab active' : 'entry-touch-tab'}
              onClick={() => setActiveTab('signals')}
            >
              Tín hiệu vào lệnh
            </button>
            <button
              type="button"
              className={activeTab === 'profit' ? 'entry-touch-tab active' : 'entry-touch-tab'}
              onClick={() => setActiveTab('profit')}
            >
              Quản lý lợi nhuận
            </button>
            <button
              type="button"
              className={activeTab === 'history' ? 'entry-touch-tab active' : 'entry-touch-tab'}
              onClick={() => setActiveTab('history')}
            >
              Lịch sử đã đóng
            </button>
          </div>
        </header>
      </section>

      {activeTab === 'signals' ? (
      <section className="card">
        <header className="card-header">
          <h2>Tín Hiệu Entry Từ Pump Hunter</h2>
          <span className="badge neutral">{signals.length}</span>
        </header>
        <div className="content table-wrap">
          {signals.length === 0 ? (
            <p>Chưa có tín hiệu của model mới đạt bộ lọc hiện tại.</p>
          ) : (
            <table className="entry-touch-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Signal</th>
                  <th>Giai đoạn</th>
                  <th>Side</th>
                  <th>Điểm</th>
                  <th>Entry</th>
                  <th>Mark live</th>
                  <th>Chạm Entry</th>
                  <th>Can Enter</th>
                  <th>Blocked</th>
                  <th>TP1</th>
                  <th>Lời TP1</th>
                  <th>TP2</th>
                  <th>Lời TP2</th>
                  <th>SL</th>
                  <th>Lỗ dự kiến</th>
                  <th>Vol 15m</th>
                  <th>Quote Vol</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {pagedSignals.map((item) => {
                  const isOpen = openTradeKeySet.has(`${canonicalSymbol(item.symbol)}:${item.side}`)
                  return (
                    <tr key={item.symbol}>
                      <td><strong>{item.symbol}</strong></td>
                      <td><span className={`badge ${item.signal_label === 'ENTER' ? 'success' : item.signal_label === 'SWEEPED' ? 'warn' : 'neutral'}`}>{item.signal_label}</span></td>
                      <td>{item.stage}</td>
                      <td><span className={item.side === 'LONG' ? 'pill-long' : 'pill-short'}>{item.side}</span></td>
                      
                      <td>{item.effective_score.toFixed(1)}</td>
                      <td>
                        <div className="entry-touch-price-stack">
                          <span>{item.entry_price.toFixed(4)}</span>
                          <span className="entry-touch-meta">{formatPct(item.entry_distance_pct)}</span>
                        </div>
                      </td>
                      <td>
                        <div className="entry-touch-price-stack">
                          <span>{item.live_mark_price.toFixed(4)}</span>
                          <span className="entry-touch-meta">{formatVnTimestamp(livePriceTime[item.symbol])}</span>
                        </div>
                      </td>
                      <td>
                        <span className={`badge ${item.touched ? 'success' : 'warn'}`}>
                          {item.touched ? 'ĐÃ CHẠM' : 'CHỜ CHẠM'}
                        </span>
                      </td>
                      <td>
                        <span className={`badge ${item.can_enter ? 'success' : 'neutral'}`}>
                          {item.can_enter ? 'READY' : 'BLOCK'}
                        </span>
                      </td>
                      <td>{item.blocked_reason}</td>
                      <td>
                        <div className="entry-touch-price-stack">
                          <span>{item.take_profit_1.toFixed(4)}</span>
                          <span className="entry-touch-meta">{formatPct(item.tp1_distance_pct)}</span>
                        </div>
                      </td>
                      <td className={item.projected_profit_1_usdt >= 0 ? 'pnl-pos' : 'pnl-neg'}>{`${item.projected_profit_1_usdt >= 0 ? '+' : ''}${item.projected_profit_1_usdt.toFixed(2)} USDT`}</td>
                      <td>
                        <div className="entry-touch-price-stack">
                          <span>{item.take_profit_2.toFixed(4)}</span>
                          <span className="entry-touch-meta">{formatPct(item.target_distance_pct)}</span>
                        </div>
                      </td>
                      <td className={item.projected_profit_2_usdt >= 0 ? 'pnl-pos' : 'pnl-neg'}>{`${item.projected_profit_2_usdt >= 0 ? '+' : ''}${item.projected_profit_2_usdt.toFixed(2)} USDT`}</td>
                      <td>{item.stop_loss.toFixed(4)}</td>
                      <td className={item.projected_loss_usdt >= 0 ? 'pnl-pos' : 'pnl-neg'}>{`${item.projected_loss_usdt >= 0 ? '+' : ''}${item.projected_loss_usdt.toFixed(2)} USDT`}</td>
                      <td>x{item.volume_ratio_15m.toFixed(2)}</td>
                      <td>{formatCompact(item.ticker_quote_volume)}</td>
                      <td>
                        <button
                          type="button"
                          className="btn-inline"
                          disabled
                        >
                          {isOpen ? 'Đã auto mở' : 'Dành cho lệnh thực tế'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
          {renderPagination(signalsPage, signalTotalPages, setSignalsPage)}
        </div>
      </section>
      ) : null}

      {activeTab === 'history' ? (
      <section className="card">
        <header className="card-header">
          <h2>Lịch Sử Lệnh Đã Đóng</h2>
          <span className="badge neutral">{pumpEntryClosedTrades.length}</span>
        </header>
        <div className="content table-wrap">
          <p className="entry-touch-open-note">
            Chỉ hiển thị lịch sử các lệnh của model mới với `entry_type = PUMP_ENTRY_TOUCH`.
          </p>
          {pumpEntryClosedTrades.length === 0 ? (
            <p>Chưa có lệnh đóng nào từ màn này.</p>
          ) : (
            <table className="entry-touch-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Point vào</th>
                  <th>Entry</th>
                  <th>Close</th>
                  <th>PnL USDT</th>
                  <th>PnL %</th>
                  <th>Lý do đóng</th>
                  <th>Mở lúc</th>
                  <th>Đóng lúc</th>
                </tr>
              </thead>
              <tbody>
                {pagedHistoryRows.map((row) => (
                  <tr key={row.id} className={typeof row.pnl === 'number' ? (row.pnl > 0 ? 'row-profit' : row.pnl < 0 ? 'row-loss' : '') : ''}>
                    <td>{row.id}</td>
                    <td><strong>{row.symbol}</strong></td>
                    <td><span className={row.side === 'LONG' ? 'pill-long' : 'pill-short'}>{row.side}</span></td>
                    <td>{typeof row.entry_point_score === 'number' ? row.entry_point_score.toFixed(1) : '-'}</td>
                    <td>{row.entry_price}</td>
                    <td>{typeof row.close_price === 'number' ? row.close_price : '-'}</td>
                    <td className={typeof row.pnl === 'number' ? (row.pnl >= 0 ? 'pnl-pos' : 'pnl-neg') : ''}>{typeof row.pnl === 'number' ? `${row.pnl >= 0 ? '+' : ''}${row.pnl.toFixed(2)}` : '-'}</td>
                    <td className={typeof row.pnl_pct === 'number' ? (row.pnl_pct >= 0 ? 'pnl-pos' : 'pnl-neg') : ''}>{typeof row.pnl_pct === 'number' ? formatPct(row.pnl_pct) : '-'}</td>
                    <td>{row.close_reason ?? '-'}</td>
                    <td>{formatVnTimestamp(row.opened_at)}</td>
                    <td>{formatVnTimestamp(row.closed_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {renderPagination(historyPage, historyTotalPages, setHistoryPage)}
        </div>
      </section>
      ) : null}

      {activeTab === 'profit' ? (
      <section className="card">
        <header className="card-header">
          <h2>Quản Lý Lợi Nhuận Lệnh Đang Mở</h2>
          <span className="badge neutral">{pumpEntryOpenTrades.length}</span>
        </header>
        <div className="content table-wrap">
          <p className="entry-touch-open-note">
            Phần này chỉ hiện các lệnh mở từ model mới với `entry_type = PUMP_ENTRY_TOUCH`, kèm PnL live để chốt lời hoặc đóng tay khi cần.
          </p>
          <p className="entry-touch-open-note">
            Mặc định hiện tại cho lệnh mới là {orderUsdtPerTrade} USDT từ env backend. Nếu vẫn thấy mức khác thì đó là lệnh cũ đã được mở trước khi đổi cấu hình.
          </p>
          {pumpEntryOpenTrades.length === 0 ? (
            <p>Chưa có lệnh mở nào từ màn này.</p>
          ) : (
            <table className="entry-touch-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Point vào</th>
                  <th>Entry</th>
                  <th>Mark live</th>
                  <th>uPnL USDT</th>
                  <th>uPnL %</th>
                  <th>Vốn lệnh</th>
                  <th>Margin</th>
                  <th>TP1</th>
                  <th>TP1 %</th>
                  <th>TP</th>
                  <th>TP %</th>
                  <th>SL</th>
                  <th>Opened</th>
                  <th>Hành động</th>
                </tr>
              </thead>
              <tbody>
                {pagedProfitRows.map((row) => (
                  <tr key={row.id} className={typeof row.upnlUsdt === 'number' ? (row.upnlUsdt > 0 ? 'row-profit' : row.upnlUsdt < 0 ? 'row-loss' : '') : ''}>
                    <td>{row.id}</td>
                    <td><strong>{row.symbol}</strong></td>
                    <td><span className={row.side === 'LONG' ? 'pill-long' : 'pill-short'}>{row.side}</span></td>
                    <td>{typeof row.entry_point_score === 'number' ? row.entry_point_score.toFixed(1) : '-'}</td>
                    <td>{row.entry_price}</td>
                    <td>
                      <div className="entry-touch-price-stack">
                        <span>{typeof row.mark === 'number' ? row.mark.toFixed(4) : '-'}</span>
                        <span className="entry-touch-meta">{formatVnTimestamp(livePriceTime[row.symbol])}</span>
                      </div>
                    </td>
                    <td className={typeof row.upnlUsdt === 'number' ? (row.upnlUsdt >= 0 ? 'pnl-pos' : 'pnl-neg') : ''}>{typeof row.upnlUsdt === 'number' ? `${row.upnlUsdt >= 0 ? '+' : ''}${row.upnlUsdt.toFixed(2)}` : '-'}</td>
                    <td className={typeof row.upnlPct === 'number' ? (row.upnlPct >= 0 ? 'pnl-pos' : 'pnl-neg') : ''}>{typeof row.upnlPct === 'number' ? formatPct(row.upnlPct) : '-'}</td>
                    <td>{`${(row.entry_price * row.quantity).toFixed(2)} USDT`}</td>
                    <td>{typeof row.marginUsdt === 'number' ? `${row.marginUsdt.toFixed(2)} (${row.leverage}x)` : `${row.leverage}x`}</td>
                    <td>{row.tp1.toFixed(4)}</td>
                    <td>{typeof row.tp1Pct === 'number' ? formatPct(row.tp1Pct) : '-'}</td>
                    <td>{row.take_profit}</td>
                    <td>{typeof row.tpPct === 'number' ? formatPct(row.tpPct) : '-'}</td>
                    <td>{row.stop_loss}</td>
                    <td>{formatVnTimestamp(row.opened_at)}</td>
                    <td>
                      <div className="entry-touch-manage-actions">
                        <button
                          type="button"
                          className="btn-inline"
                          disabled={closingTradeId === row.id}
                          onClick={() => {
                            closePaperTrade(row.id).catch((err) => {
                              setError(err instanceof Error ? err.message : 'Lỗi không xác định')
                            })
                          }}
                        >
                          {closingTradeId === row.id ? 'Đang đóng...' : 'Đóng lệnh'}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {renderPagination(profitPage, profitTotalPages, setProfitPage)}
        </div>
      </section>
      ) : null}

      {error ? <p className="error">{error}</p> : null}
    </main>
  )
}
