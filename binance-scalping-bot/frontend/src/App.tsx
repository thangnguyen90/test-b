import { useEffect, useMemo, useRef, useState } from 'react'
import './App.css'

type Health = {
  status: string
  app_name: string
  environment: string
  timestamp: string
}

type Signal = {
  symbol: string
  side: 'LONG' | 'SHORT'
  win_probability: number
  predicted_entry_price: number
  stop_loss: number
  take_profit: number
}

type Order = {
  id: number
  symbol: string
  side: 'LONG' | 'SHORT'
  quantity: number
  leverage: number
  predicted_entry_price: number
  stop_loss: number
  take_profit: number
  win_probability: number
  status: string
  created_at: string
  expiration_time?: string
}

type PriceStreamMessage = {
  type: string
  symbol?: string
  symbols?: string[]
  price?: number
  prices?: Record<string, number>
  timestamp?: string | null
  timestamps?: Record<string, string>
  source?: string
  error?: string
}

type Palette = {
  id: string
  name: string
  stops: Array<[number, number, number, number]>
}

type HeatmapData = {
  rows: number
  cols: number
  currentCol: number
  values: Float32Array
  priceSeries: number[]
  minPrice: number
  maxPrice: number
}

type HoverPayload = {
  x: number
  y: number
  col: number
  row: number
  price: number
  intensity: number
}

type SymbolListResponse = {
  count: number
  symbols: string[]
}

type MarketPriceResponse = {
  symbol: string
  price: number
  timestamp: string | null
}

type MarketPricesBatchResponse = {
  prices?: Record<string, number>
  timestamp?: string | null
  timestamps?: Record<string, string>
}

type KlineItem = {
  timestamp: number
  open: number
  high: number
  low: number
  close: number
  volume: number
}

type KlinesResponse = {
  symbol: string
  timeframe: string
  count: number
  candles: KlineItem[]
}

type EmaLine = {
  period: number
  color: string
  points: Array<number | null>
}

type ScanSignalItem = {
  symbol: string
  side: 'LONG' | 'SHORT'
  win_probability: number
  predicted_entry_price: number
  stop_loss: number
  take_profit: number
}

type ScanSignalsResponse = {
  min_win: number
  scanned: number
  count: number
  signals: ScanSignalItem[]
  source?: string
  timestamp?: string
}

type PaperTrade = {
  id: number
  symbol: string
  side: 'LONG' | 'SHORT'
  signal_win_probability: number
  effective_win_probability: number
  entry_price: number
  take_profit: number
  stop_loss: number
  quantity: number
  leverage: number
  status: string
  opened_at: string
  closed_at?: string | null
  close_price?: number | null
  close_reason?: string | null
  pnl?: number | null
  pnl_pct?: number | null
  margin_usdt?: number | null
  result?: number | null
}

type PaperTradeStats = {
  total_trades: number
  open_trades: number
  closed_trades: number
  win_trades: number
  loss_trades: number
  win_rate: number
  total_pnl: number
  avg_pnl: number
  total_pnl_pct: number
  avg_pnl_pct: number
  order_usdt: number
  margin_usdt: number
}

type DailyTradeSummary = {
  trade_date: string
  total_trades: number
  win_trades: number
  loss_trades: number
  win_rate: number
  total_pnl: number
  avg_pnl: number
}

type VolatilityItem = {
  symbol: string
  move_pct: number
  abs_move_pct: number
  from_price: number
  to_price: number
  days: number
}

type LiquidationOverviewItem = {
  symbol: string
  mark_price: number
  funding_rate: number
  long_short_ratio: number
  open_interest_notional: number
  est_liq_zone_price: number
  est_liq_zone_value: number
  signal_side?: 'LONG' | 'SHORT' | null
  signal_win_probability?: number | null
  signal_entry_price?: number | null
  signal_take_profit?: number | null
  signal_stop_loss?: number | null
  signal_order_type?: 'LIMIT' | 'MARKET' | null
}

type BtcTrendItem = {
  timeframe: string
  trend: 'BULLISH' | 'BEARISH' | 'SIDEWAYS'
  action: 'LONG' | 'SHORT' | 'WAIT'
  confidence: number
  prob_up: number
  prob_down: number
  technical_score: number
  ml_score: number
  blended_score: number
  rsi: number
  slope_pct: number
}

type BtcTrendResponse = {
  symbol: string
  mark_price: number
  ml_side: 'LONG' | 'SHORT'
  ml_win_probability: number
  items: BtcTrendItem[]
}

type MlStatus = {
  is_loaded: boolean
  model_path: string
  trained_at: string | null
  feature_count: number
  accuracy: number | null
  roc_auc: number | null
  training_in_progress: boolean
  auto_train_enabled?: boolean
  auto_train_running?: boolean
  auto_train_interval_minutes?: number | null
  auto_train_next_run_at?: string | null
  auto_train_last_run_started_at?: string | null
  auto_train_last_run_finished_at?: string | null
  auto_train_last_result?: string | null
  auto_train_last_error?: string | null
  last_train_trigger?: string | null
  last_train_started_at?: string | null
  last_train_finished_at?: string | null
  last_train_duration_sec?: number | null
  last_train_result?: string | null
  last_train_error?: string | null
  train_log_path?: string | null
}

type SortDirection = 'asc' | 'desc'

type PaperMarketOpenRequest = {
  symbol: string
  side: 'LONG' | 'SHORT'
  signal_win_probability: number
  effective_win_probability?: number
  entry_price?: number
  take_profit: number
  stop_loss: number
}

type PaperManualCloseRequest = {
  force_result?: 0 | 1
}

const API_BASE = 'http://127.0.0.1:8000'
const WS_BASE = API_BASE.replace(/^http/, 'ws')
const AUTO_LIQ_MIN_WIN = 0.7
const AUTO_LIQ_MAX_ORDERS_PER_CYCLE = 3
const AUTO_LIQ_OPEN_COOLDOWN_MS = 30 * 60 * 1000
const SIGNAL_RISK_LEVERAGE = 5
const FALLBACK_COINS = [
  'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT', 'ADA/USDT', 'DOGE/USDT',
  'AVAX/USDT', 'DOT/USDT', 'LINK/USDT', 'SUI/USDT', 'TON/USDT', 'NEAR/USDT',
  'TRX/USDT', 'ATOM/USDT', 'MANA/USDT', 'PEOPLE/USDT', 'XLM/USDT', 'NEO/USDT',
]

const TIMEFRAME_FACTOR: Record<string, number> = {
  '1h': 0.8,
  '4h': 1.0,
  '12h': 1.4,
  '24h': 1.8,
}

const TIMEFRAME_RANGE_PCT: Record<string, number> = {
  '1h': 0.07,
  '4h': 0.12,
  '12h': 0.2,
  '24h': 0.3,
}

const VN_TIMEZONE = 'Asia/Ho_Chi_Minh'
const VN_DATETIME_FORMATTER = new Intl.DateTimeFormat('en-GB', {
  timeZone: VN_TIMEZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const PALETTES: Palette[] = [
  {
    id: 'coinglassish',
    name: 'Blue-Yellow',
    stops: [
      [0, 43, 5, 72],
      [0.35, 42, 58, 123],
      [0.58, 76, 165, 175],
      [0.78, 179, 224, 81],
      [1, 246, 244, 86],
    ],
  },
  {
    id: 'icefire',
    name: 'Ice-Fire',
    stops: [
      [0, 12, 24, 62],
      [0.32, 47, 98, 157],
      [0.6, 107, 209, 224],
      [0.8, 255, 146, 126],
      [1, 255, 227, 148],
    ],
  },
]

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value))
}

function hashText(text: string): number {
  let hash = 2166136261
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

function mulberry32(seed: number): () => number {
  return () => {
    let t = seed += 0x6D2B79F5
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

function samplePaletteColor(palette: Palette, t: number): [number, number, number] {
  const x = clamp(t, 0, 1)
  let left = palette.stops[0]
  let right = palette.stops[palette.stops.length - 1]

  for (let i = 0; i < palette.stops.length - 1; i += 1) {
    const a = palette.stops[i]
    const b = palette.stops[i + 1]
    if (x >= a[0] && x <= b[0]) {
      left = a
      right = b
      break
    }
  }

  const span = right[0] - left[0] || 1
  const ratio = (x - left[0]) / span
  const r = Math.round(left[1] + (right[1] - left[1]) * ratio)
  const g = Math.round(left[2] + (right[2] - left[2]) * ratio)
  const b = Math.round(left[3] + (right[3] - left[3]) * ratio)
  return [r, g, b]
}

function timeframeToHours(tf: string): number {
  const raw = tf.toLowerCase().trim()
  if (raw.endsWith('h')) return Number(raw.replace('h', '')) || 12
  if (raw.endsWith('d')) return (Number(raw.replace('d', '')) || 1) * 24
  return 12
}

function resampleSeries(source: number[], targetLength: number): number[] {
  if (targetLength <= 0) return []
  if (source.length === 0) return Array.from({ length: targetLength }, () => 0)
  if (source.length === 1) return Array.from({ length: targetLength }, () => source[0])
  if (source.length === targetLength) return source.slice()

  const out = new Array<number>(targetLength)
  for (let i = 0; i < targetLength; i += 1) {
    const t = i / Math.max(1, targetLength - 1)
    const idx = t * (source.length - 1)
    const lo = Math.floor(idx)
    const hi = Math.min(source.length - 1, lo + 1)
    const frac = idx - lo
    out[i] = source[lo] + ((source[hi] - source[lo]) * frac)
  }
  return out
}

function calcEMA(series: number[], period: number): Array<number | null> {
  if (series.length === 0) return []
  const alpha = 2 / (period + 1)
  const out: Array<number | null> = new Array(series.length).fill(null)
  let ema = series[0]
  for (let i = 0; i < series.length; i += 1) {
    if (i === 0) {
      ema = series[0]
    } else {
      ema = (series[i] * alpha) + (ema * (1 - alpha))
    }
    if (i >= period - 1) out[i] = ema
  }
  return out
}

function canonicalSymbol(symbol: string): string {
  return symbol.replace(':USDT', '').trim().toUpperCase()
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

function calcSignalRiskPct(
  side: 'LONG' | 'SHORT',
  entryPrice: number,
  stopLoss: number,
  leverage = SIGNAL_RISK_LEVERAGE,
): number | null {
  if (entryPrice <= 0 || stopLoss <= 0 || leverage <= 0) return null
  const move = side === 'LONG'
    ? (entryPrice - stopLoss) / entryPrice
    : (stopLoss - entryPrice) / entryPrice
  return Math.max(0, move * leverage * 100)
}

function calcMarginUsdt(entryPrice: number, quantity: number, leverage: number): number | null {
  if (entryPrice <= 0 || quantity <= 0 || leverage <= 0) return null
  return (entryPrice * quantity) / leverage
}

function formatVnTimestamp(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value

  const parts = VN_DATETIME_FORMATTER.formatToParts(date)
  const get = (type: Intl.DateTimeFormatPartTypes): string =>
    parts.find((item) => item.type === type)?.value ?? ''

  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')}`
}

function calcUnrealizedPnlPct(trade: PaperTrade, markPrice?: number): number | null {
  if (typeof markPrice !== 'number') return null
  return calcPnlPct(trade.side, trade.entry_price, markPrice, trade.leverage)
}

function buildHeatmap(
  coin: string,
  basePrice: number,
  threshold: number,
  timeframe: string,
  pastSeriesInput: number[],
): HeatmapData {
  const rows = 260
  const cols = 760
  const currentCol = Math.floor(cols * 0.72)
  const series: number[] = []
  const seed = hashText(`${coin}:${threshold}:${timeframe}`)
  const random = mulberry32(seed)
  const tfVol = TIMEFRAME_FACTOR[timeframe] ?? 1
  const tfRangePct = TIMEFRAME_RANGE_PCT[timeframe] ?? 0.2

  const pastFromBinance = pastSeriesInput.length > 32
    ? resampleSeries(pastSeriesInput, currentCol + 1)
    : []
  let price = pastFromBinance.length > 0 ? pastFromBinance[0] : basePrice
  const drift = (random() - 0.5) * basePrice * 0.0015

  for (let x = 0; x <= currentCol; x += 1) {
    if (pastFromBinance.length > 0) {
      price = Math.max(0.0000001, pastFromBinance[x])
    } else {
      const step = (random() - 0.5) * basePrice * 0.0036 * tfVol
      price = Math.max(0.0000001, price + step + drift * 0.025)
    }
    series.push(price)
  }

  for (let x = currentCol + 1; x < cols; x += 1) {
    const step = (random() - 0.5) * basePrice * 0.0012 * tfVol
    price = Math.max(0.0000001, price + step + drift * 0.015)
    series.push(price)
  }

  const minSeries = Math.min(...series)
  const maxSeries = Math.max(...series)
  const bandRange = Math.max(basePrice * tfRangePct, (maxSeries - minSeries) * 1.2)
  const center = basePrice
  const minPrice = Math.max(0.00000001, center - bandRange)
  const maxPrice = center + bandRange
  const values = new Float32Array(rows * cols)

  const bandCount = 72
  const bands = Array.from({ length: bandCount }, () => {
    const startsNearNow = random() < 0.58
    const start = startsNearNow
      ? Math.floor((currentCol * 0.35) + random() * (currentCol * 0.55))
      : Math.floor(random() * (currentCol * 0.9))
    const willExtendFuture = random() < 0.68
    const len = Math.floor((cols * 0.12) + random() * (cols * 0.55))
    const end = willExtendFuture
      ? cols - 1 - Math.floor(random() * Math.max(4, cols * 0.015))
      : Math.min(cols - 1, start + len)
    const centerStart = minPrice + random() * (maxPrice - minPrice)
    const slope = (random() - 0.5) * (maxPrice - minPrice) * 0.02
    const centerEnd = centerStart + slope
    return {
      start,
      end,
      centerStart,
      centerEnd,
      widthPx: 0.8 + random() * 2.2,
      strength: 0.22 + random() * 1.05,
    }
  })

  for (let i = 0; i < values.length; i += 1) {
    values[i] = 0.008 + random() * 0.012
  }

  for (let b = 0; b < bands.length; b += 1) {
    const band = bands[b]
    const span = Math.max(1, band.end - band.start)
    for (let x = band.start; x <= band.end; x += 1) {
      const t = (x - band.start) / span
      const center = band.centerStart + (band.centerEnd - band.centerStart) * t
      const edge = Math.sin(Math.PI * t) ** 0.55
      const progressBoost = (0.86 + ((x / (cols - 1)) * 0.24)) * edge
      const centerY = ((maxPrice - center) / (maxPrice - minPrice)) * (rows - 1)
      const radius = Math.max(2, Math.ceil(band.widthPx * 4))
      const yMin = Math.max(0, Math.floor(centerY - radius))
      const yMax = Math.min(rows - 1, Math.ceil(centerY + radius))

      for (let y = yMin; y <= yMax; y += 1) {
        const dy = Math.abs(y - centerY)
        const localNoise = 0.9 + (random() * 0.2)
        const local = Math.exp(-0.5 * (dy / band.widthPx) ** 2) * band.strength * progressBoost * localNoise
        const futureBoost = x > currentCol ? 1.08 : 1
        values[(y * cols) + x] += local * futureBoost
      }
    }
  }

  for (let x = 0; x < cols; x += 1) {
    const progress = x / (cols - 1)
    const anchor = series[x]
    const anchorY = ((maxPrice - anchor) / (maxPrice - minPrice)) * (rows - 1)
    const width = rows * 0.012
    const yMin = Math.max(0, Math.floor(anchorY - width * 3.2))
    const yMax = Math.min(rows - 1, Math.ceil(anchorY + width * 3.2))

    for (let y = yMin; y <= yMax; y += 1) {
      const d = Math.abs(y - anchorY)
      const nearPrice = Math.exp(-0.5 * (d / width) ** 2)
      values[(y * cols) + x] += nearPrice * (0.12 + 0.15 * progress)
    }
  }

  const smoothed = new Float32Array(values.length)
  for (let x = 0; x < cols; x += 1) {
    for (let y = 0; y < rows; y += 1) {
      const idx = (y * cols) + x
      const up = y > 0 ? values[((y - 1) * cols) + x] : values[idx]
      const mid = values[idx]
      const down = y < rows - 1 ? values[((y + 1) * cols) + x] : values[idx]
      smoothed[idx] = (up * 0.2) + (mid * 0.6) + (down * 0.2)
    }
  }

  let maxIntensity = 0
  for (let i = 0; i < smoothed.length; i += 1) {
    if (smoothed[i] > maxIntensity) maxIntensity = smoothed[i]
  }

  const normalized = new Float32Array(smoothed.length)
  const cutoff = clamp(threshold * 0.52, 0, 0.95)
  for (let i = 0; i < smoothed.length; i += 1) {
    const n = maxIntensity > 0 ? smoothed[i] / maxIntensity : 0
    let out = n < cutoff ? n * 0.2 : n
    out = Math.pow(clamp(out, 0, 1), 0.82)
    normalized[i] = out
  }

  return {
    rows,
    cols,
    currentCol,
    values: normalized,
    priceSeries: series,
    minPrice,
    maxPrice,
  }
}

function LiquidationMapCanvas({
  data,
  palette,
  emaLines,
  onHover,
  onLeave,
}: {
  data: HeatmapData
  palette: Palette
  emaLines: EmaLine[]
  onHover: (payload: HoverPayload) => void
  onLeave: () => void
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const { rows, cols, values, priceSeries, minPrice, maxPrice } = data
    const dpr = window.devicePixelRatio || 1
    canvas.width = Math.floor(cols * dpr)
    canvas.height = Math.floor(rows * dpr)

    const ctx = canvas.getContext('2d')
    if (!ctx) return

    ctx.setTransform(1, 0, 0, 1, 0, 0)
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.scale(dpr, dpr)

    const imageData = ctx.createImageData(cols, rows)
    for (let i = 0; i < values.length; i += 1) {
      const v = clamp(values[i], 0, 1)
      const [r, g, b] = samplePaletteColor(palette, v)
      const o = i * 4
      imageData.data[o] = r
      imageData.data[o + 1] = g
      imageData.data[o + 2] = b
      imageData.data[o + 3] = 255
    }
    ctx.putImageData(imageData, 0, 0)

    const scaleY = (price: number) => ((maxPrice - price) / (maxPrice - minPrice)) * (rows - 1)
    const drawSeries = (
      points: Array<number | null>,
      color: string,
      lineWidth: number,
      dashed = false,
    ) => {
      ctx.beginPath()
      ctx.strokeStyle = color
      ctx.lineWidth = lineWidth
      if (dashed) ctx.setLineDash([3, 3])
      else ctx.setLineDash([])
      let started = false
      for (let x = 0; x < points.length; x += 1) {
        const value = points[x]
        if (value == null) {
          started = false
          continue
        }
        const y = scaleY(value)
        if (!started) {
          ctx.moveTo(x, y)
          started = true
        } else {
          ctx.lineTo(x, y)
        }
      }
      ctx.stroke()
    }

    for (const ema of emaLines) {
      drawSeries(ema.points, ema.color, 1.05, false)
    }

    const pastPriceLine: Array<number | null> = new Array(priceSeries.length).fill(null)
    for (let x = 0; x <= data.currentCol; x += 1) pastPriceLine[x] = priceSeries[x]
    drawSeries(pastPriceLine, '#ff6f7f', 1.15, false)

    ctx.setLineDash([3, 3])
    const futurePriceLine: Array<number | null> = new Array(priceSeries.length).fill(null)
    for (let x = data.currentCol; x < priceSeries.length; x += 1) futurePriceLine[x] = priceSeries[x]
    drawSeries(futurePriceLine, 'rgba(255, 111, 127, 0.75)', 1.0, true)
    ctx.setLineDash([])
    ctx.shadowBlur = 0
  }, [data, palette, emaLines])

  return (
    <canvas
      ref={canvasRef}
      className="liq-canvas"
      onMouseMove={(event) => {
        const canvas = canvasRef.current
        if (!canvas) return
        const rect = canvas.getBoundingClientRect()
        const localX = clamp(event.clientX - rect.left, 0, rect.width - 1)
        const localY = clamp(event.clientY - rect.top, 0, rect.height - 1)
        const col = clamp(Math.floor((localX / rect.width) * data.cols), 0, data.cols - 1)
        const row = clamp(Math.floor((localY / rect.height) * data.rows), 0, data.rows - 1)
        const idx = (row * data.cols) + col
        const intensity = data.values[idx] ?? 0
        const price = data.maxPrice - ((row / (data.rows - 1)) * (data.maxPrice - data.minPrice))
        onHover({
          x: localX,
          y: localY,
          col,
          row,
          intensity,
          price,
        })
      }}
      onMouseLeave={onLeave}
    />
  )
}

function App() {
  const mapSectionRef = useRef<HTMLElement | null>(null)
  const selectedCoinRef = useRef<string>('BTC/USDT')
  const symbolReqIdRef = useRef(0)
  const liqReqRef = useRef(false)
  const btcTrendReqRef = useRef(false)
  const liqAutoOpenedRef = useRef<Record<string, number>>({})
  const [health, setHealth] = useState<Health | null>(null)
  const [mlStatus, setMlStatus] = useState<MlStatus | null>(null)
  const [signal, setSignal] = useState<Signal | null>(null)
  const [pendingOrders, setPendingOrders] = useState<Order[]>([])
  const [error, setError] = useState<string>('')
  const [isLoadingOrder, setIsLoadingOrder] = useState(false)
  const [coins, setCoins] = useState<string[]>(FALLBACK_COINS)
  const [marketPrice, setMarketPrice] = useState<number | null>(null)
  const [marketPriceTime, setMarketPriceTime] = useState<string | null>(null)
  const [hoverInfo, setHoverInfo] = useState<HoverPayload | null>(null)
  const [highWinSignals, setHighWinSignals] = useState<ScanSignalItem[]>([])
  const [scannedCount, setScannedCount] = useState(0)
  const [signalsWsStatus, setSignalsWsStatus] = useState<'connecting' | 'live' | 'fallback'>('connecting')
  const [klineSeries, setKlineSeries] = useState<number[]>([])
  const [emaVisible, setEmaVisible] = useState<Record<number, boolean>>({
    9: true,
    21: true,
    50: false,
    200: false,
  })
  const [symbolsSource, setSymbolsSource] = useState<'binance' | 'fallback'>('fallback')
  const [symbolsStatus, setSymbolsStatus] = useState<string>('Using fallback symbols')
  const [priceWsStatus, setPriceWsStatus] = useState<'connecting' | 'live' | 'fallback'>('connecting')
  const [showPaperScreen, setShowPaperScreen] = useState(false)
  const [showDailyScreen, setShowDailyScreen] = useState(false)
  const [paperStats, setPaperStats] = useState<PaperTradeStats | null>(null)
  const [paperOpenTrades, setPaperOpenTrades] = useState<PaperTrade[]>([])
  const [paperHistory, setPaperHistory] = useState<PaperTrade[]>([])
  const [dailySummary, setDailySummary] = useState<DailyTradeSummary[]>([])
  const [paperLivePrices, setPaperLivePrices] = useState<Record<string, number>>({})
  const [paperLivePriceTime, setPaperLivePriceTime] = useState<Record<string, string>>({})
  const [paperPriceWsStatus, setPaperPriceWsStatus] = useState<'connecting' | 'live' | 'fallback'>('connecting')
  const [isOpeningMarketOrder, setIsOpeningMarketOrder] = useState(false)
  const [closingTradeId, setClosingTradeId] = useState<number | null>(null)
  const [volDays, setVolDays] = useState<1 | 3 | 5 | 7>(1)
  const [topVolatility, setTopVolatility] = useState<VolatilityItem[]>([])
  const [liqOverview, setLiqOverview] = useState<LiquidationOverviewItem[]>([])
  const [liqPage, setLiqPage] = useState(1)
  const [liqPageSize] = useState(30)
  const [liqTotalSymbols, setLiqTotalSymbols] = useState(0)
  const [btcTrend, setBtcTrend] = useState<BtcTrendResponse | null>(null)
  const [autoLiqMarketEnabled, setAutoLiqMarketEnabled] = useState(false)
  const [volSort, setVolSort] = useState<{ key: keyof VolatilityItem; direction: SortDirection }>({
    key: 'abs_move_pct',
    direction: 'desc',
  })
  const [liqSort, setLiqSort] = useState<{ key: keyof LiquidationOverviewItem; direction: SortDirection }>({
    key: 'est_liq_zone_value',
    direction: 'desc',
  })

  const [selectedCoin, setSelectedCoin] = useState('BTC/USDT')
  const [searchCoin, setSearchCoin] = useState('')
  const [timeframe, setTimeframe] = useState('12h')
  const [threshold, setThreshold] = useState(0.62)
  const [paletteId, setPaletteId] = useState(PALETTES[0].id)

  useEffect(() => {
    selectedCoinRef.current = selectedCoin
  }, [selectedCoin])

  const activePalette = useMemo(
    () => PALETTES.find((p) => p.id === paletteId) ?? PALETTES[0],
    [paletteId],
  )

  const displayedCoins = useMemo(() => {
    const q = searchCoin.trim().toLowerCase()
    if (!q) return coins
    return coins.filter((coin) => coin.toLowerCase().includes(q))
  }, [searchCoin, coins])

  const connectionStatus = useMemo(() => {
    if (priceWsStatus === 'live') return 'Live'
    if (priceWsStatus === 'fallback') return 'Degraded'
    return 'Connecting'
  }, [priceWsStatus])

  const chartBasePrice = useMemo(() => {
    return marketPrice ?? signal?.predicted_entry_price ?? 62000
  }, [marketPrice, signal?.predicted_entry_price])

  const heatmapData = useMemo(
    () => buildHeatmap(selectedCoin, chartBasePrice, threshold, timeframe, klineSeries),
    [selectedCoin, chartBasePrice, threshold, timeframe, klineSeries],
  )

  const emaLines = useMemo(() => {
    const config: Array<{ period: number; color: string }> = [
      { period: 9, color: '#ffd166' },
      { period: 21, color: '#7cf7ff' },
      { period: 50, color: '#ff9b73' },
      { period: 200, color: '#f7f7f7' },
    ]
    const pastLength = heatmapData.currentCol + 1
    const src = klineSeries.length > 10
      ? resampleSeries(klineSeries, pastLength)
      : heatmapData.priceSeries.slice(0, pastLength)
    const lines: EmaLine[] = []
    for (const item of config) {
      if (!emaVisible[item.period]) continue
      const emaPast = calcEMA(src, item.period)
      const points: Array<number | null> = new Array(heatmapData.cols).fill(null)
      for (let i = 0; i < emaPast.length; i += 1) points[i] = emaPast[i]
      lines.push({ period: item.period, color: item.color, points })
    }
    return lines
  }, [klineSeries, heatmapData.currentCol, heatmapData.cols, heatmapData.priceSeries, emaVisible])

  const priceTicks = useMemo(() => {
    const ticks: number[] = []
    const total = 8
    for (let i = 0; i < total; i += 1) {
      const ratio = i / (total - 1)
      const value = heatmapData.maxPrice - ((heatmapData.maxPrice - heatmapData.minPrice) * ratio)
      ticks.push(value)
    }
    return ticks
  }, [heatmapData.maxPrice, heatmapData.minPrice])

  const hoverSummary = useMemo(() => {
    if (!hoverInfo) return null
    const current = marketPrice ?? chartBasePrice
    const distancePct = ((hoverInfo.price - current) / current) * 100
    const side = hoverInfo.price >= current ? 'Shorts At Risk' : 'Longs At Risk'
    const liqScore = hoverInfo.intensity * 100
    const estUsd = (18000 + (hoverInfo.intensity * 6200000)) * (1 + (Math.abs(distancePct) * 0.06))
    const lookbackHours = timeframeToHours(timeframe)
    const nowMs = Date.now()
    const pastMs = lookbackHours * 60 * 60 * 1000
    const futureMs = pastMs * 0.6
    let hoveredMs = nowMs
    if (hoverInfo.col <= heatmapData.currentCol) {
      const pastRatio = (heatmapData.currentCol - hoverInfo.col) / Math.max(1, heatmapData.currentCol)
      hoveredMs = nowMs - (pastRatio * pastMs)
    } else {
      const futureRatio = (hoverInfo.col - heatmapData.currentCol) / Math.max(1, heatmapData.cols - 1 - heatmapData.currentCol)
      hoveredMs = nowMs + (futureRatio * futureMs)
    }
    const deltaHours = (hoveredMs - nowMs) / (1000 * 60 * 60)
    return {
      side,
      liqScore,
      estUsd,
      distancePct,
      timeText: new Date(hoveredMs).toLocaleString(),
      deltaHours,
    }
  }, [hoverInfo, marketPrice, chartBasePrice, timeframe, heatmapData.cols, heatmapData.currentCol])

  const sortedVolatility = useMemo(() => {
    const rows = [...topVolatility]
    const { key, direction } = volSort
    rows.sort((a, b) => {
      const av = a[key]
      const bv = b[key]
      if (typeof av === 'number' && typeof bv === 'number') {
        return direction === 'asc' ? av - bv : bv - av
      }
      return direction === 'asc'
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av))
    })
    return rows
  }, [topVolatility, volSort])

  const sortedLiqOverview = useMemo(() => {
    const rows = [...liqOverview]
    const { key, direction } = liqSort
    rows.sort((a, b) => {
      const av = a[key]
      const bv = b[key]
      if (typeof av === 'number' && typeof bv === 'number') {
        return direction === 'asc' ? av - bv : bv - av
      }
      return direction === 'asc'
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av))
    })
    return rows
  }, [liqOverview, liqSort])
  const openTradeKeySet = useMemo(() => {
    const set = new Set<string>()
    for (const row of paperOpenTrades) {
      set.add(`${canonicalSymbol(row.symbol)}:${row.side}`)
    }
    return set
  }, [paperOpenTrades])
  const liqMaxPage = useMemo(
    () => Math.max(1, Math.ceil((liqTotalSymbols || 0) / liqPageSize)),
    [liqTotalSymbols, liqPageSize],
  )

  function toggleVolSort(key: keyof VolatilityItem) {
    setVolSort((prev) => {
      if (prev.key === key) {
        return { key, direction: prev.direction === 'asc' ? 'desc' : 'asc' }
      }
      return { key, direction: 'desc' }
    })
  }

  function toggleLiqSort(key: keyof LiquidationOverviewItem) {
    setLiqSort((prev) => {
      if (prev.key === key) {
        return { key, direction: prev.direction === 'asc' ? 'desc' : 'asc' }
      }
      return { key, direction: 'desc' }
    })
  }

  function getLiqTrend(row: LiquidationOverviewItem): 'LONG' | 'SHORT' {
    if (row.est_liq_zone_price >= row.mark_price) return 'LONG'
    return 'SHORT'
  }

  function openSymbolOnChart(symbol: string, hintPrice?: number) {
    setError('')
    setHoverInfo(null)
    setKlineSeries([])
    if (typeof hintPrice === 'number' && Number.isFinite(hintPrice) && hintPrice > 0) {
      setMarketPrice(hintPrice)
    }
    if (!coins.includes(symbol)) {
      setCoins((prev) => [symbol, ...prev])
    }
    setSelectedCoin(symbol)
    window.requestAnimationFrame(() => {
      mapSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  function canOpenFromLiq(row: LiquidationOverviewItem): boolean {
    if (row.signal_side && openTradeKeySet.has(`${canonicalSymbol(row.symbol)}:${row.signal_side}`)) {
      return false
    }
    return (
      !!row.signal_side
      && typeof row.signal_win_probability === 'number'
      && row.signal_win_probability >= AUTO_LIQ_MIN_WIN
      && typeof row.signal_take_profit === 'number'
      && typeof row.signal_stop_loss === 'number'
    )
  }

  async function openFromLiqRow(row: LiquidationOverviewItem) {
    if (!canOpenFromLiq(row) || !row.signal_side) return
    await openPaperMarketOrder({
      symbol: row.symbol,
      side: row.signal_side,
      signal_win_probability: row.signal_win_probability ?? 0,
      entry_price: row.signal_entry_price ?? row.mark_price,
      take_profit: row.signal_take_profit as number,
      stop_loss: row.signal_stop_loss as number,
    })
  }

  function renderSymbolJump(symbol: string, hintPrice?: number) {
    return (
      <button
        type="button"
        className="symbol-jump"
        onClick={() => openSymbolOnChart(symbol, hintPrice)}
        title={`Open ${symbol} on liquidation chart`}
      >
        {symbol}
      </button>
    )
  }

  function resolveLivePrice(symbol: string): number | undefined {
    if (paperLivePrices[symbol] != null) return paperLivePrices[symbol]
    const key = canonicalSymbol(symbol)
    for (const [k, v] of Object.entries(paperLivePrices)) {
      if (canonicalSymbol(k) === key) return v
    }
    return undefined
  }

  function resolveLiveTime(symbol: string): string | undefined {
    if (paperLivePriceTime[symbol] != null) return paperLivePriceTime[symbol]
    const key = canonicalSymbol(symbol)
    for (const [k, v] of Object.entries(paperLivePriceTime)) {
      if (canonicalSymbol(k) === key) return v
    }
    return undefined
  }

  async function fetchHealth() {
    const response = await fetch(`${API_BASE}/health`)
    if (!response.ok) throw new Error('Health check failed')
    setHealth(await response.json())
  }

  async function fetchMlStatus() {
    const response = await fetch(`${API_BASE}/api/v1/ml/status`)
    if (!response.ok) throw new Error('ML status API failed')
    setMlStatus(await response.json() as MlStatus)
  }

  async function fetchSignal(symbol = selectedCoin, markPrice = chartBasePrice) {
    const response = await fetch(
      `${API_BASE}/api/v1/signals/latest?symbol=${encodeURIComponent(symbol)}&mark_price=${markPrice}`,
    )
    if (!response.ok) throw new Error('Signal API failed')
    const payload = await response.json()
    if (canonicalSymbol(symbol) === canonicalSymbol(selectedCoinRef.current)) {
      setSignal(payload)
    }
  }

  async function fetchPendingOrders() {
    const response = await fetch(`${API_BASE}/api/v1/orders/pending`)
    if (!response.ok) throw new Error('Pending orders API failed')
    setPendingOrders(await response.json())
  }

  async function fetchFuturesSymbols() {
    const response = await fetch(`${API_BASE}/api/v1/market/symbols`)
    if (!response.ok) throw new Error('Cannot fetch Binance Futures symbols')
    const data = (await response.json()) as SymbolListResponse
    if (!Array.isArray(data.symbols) || data.symbols.length === 0) return
    setCoins((prev) => {
      const merged = [...prev, ...data.symbols]
      return Array.from(new Set(merged))
    })
    setSymbolsSource('binance')
    setSymbolsStatus(`Binance symbols loaded: ${data.symbols.length}`)
  }

  async function fetchMarketPriceFallback(symbol = selectedCoin): Promise<number> {
    const response = await fetch(
      `${API_BASE}/api/v1/market/price?symbol=${encodeURIComponent(symbol)}`,
    )
    if (!response.ok) throw new Error(`Cannot fetch market price for ${symbol}`)
    const data = (await response.json()) as MarketPriceResponse
    if (canonicalSymbol(symbol) === canonicalSymbol(selectedCoinRef.current)) {
      setMarketPrice(data.price)
      setMarketPriceTime(data.timestamp)
    }
    return data.price
  }

  async function fetchMarketPricesBatch(symbols: string[]): Promise<Record<string, number>> {
    if (symbols.length === 0) return {}
    const response = await fetch(
      `${API_BASE}/api/v1/market/prices?symbols=${encodeURIComponent(symbols.join(','))}`,
    )
    if (!response.ok) throw new Error('Cannot fetch market prices batch')
    const data = await response.json() as MarketPricesBatchResponse
    const prices = data.prices ?? {}
    if (data.timestamps && Object.keys(data.timestamps).length > 0) {
      setPaperLivePriceTime((prev) => ({ ...prev, ...data.timestamps }))
    } else if (data.timestamp) {
      const fallbackStampMap: Record<string, string> = {}
      for (const key of Object.keys(prices)) fallbackStampMap[key] = data.timestamp
      setPaperLivePriceTime((prev) => ({ ...prev, ...fallbackStampMap }))
    }
    return prices
  }

  async function fetchKlines(symbol = selectedCoin) {
    const response = await fetch(
      `${API_BASE}/api/v1/market/klines?symbol=${encodeURIComponent(symbol)}&timeframe=5m&limit=1200`,
    )
    if (!response.ok) throw new Error(`Cannot fetch klines for ${symbol}`)
    const data = (await response.json()) as KlinesResponse
    const closes = (data.candles ?? []).map((c) => c.close).filter((x) => Number.isFinite(x))
    if (closes.length > 10 && canonicalSymbol(symbol) === canonicalSymbol(selectedCoinRef.current)) {
      setKlineSeries(closes)
    }
  }

  async function fetchHighWinSignals() {
    const response = await fetch(
      `${API_BASE}/api/v1/signals/scan?min_win=0.7&max_symbols=80`,
    )
    if (!response.ok) throw new Error('Cannot scan high-win signals')
    const data = (await response.json()) as ScanSignalsResponse
    setHighWinSignals(data.signals ?? [])
    setScannedCount(data.scanned ?? 0)
    setSignalsWsStatus('fallback')
  }

  async function fetchPaperTradingStats() {
    const [statsRes, openRes, historyRes] = await Promise.all([
      fetch(`${API_BASE}/api/v1/paper-trades/stats`),
      fetch(`${API_BASE}/api/v1/paper-trades/open`),
      fetch(`${API_BASE}/api/v1/paper-trades/history?limit=120`),
    ])
    const errors: string[] = []

    if (statsRes.ok) {
      const statsPayload = await statsRes.json() as { stats: PaperTradeStats }
      setPaperStats(statsPayload.stats ?? null)
    } else {
      errors.push(`stats:${statsRes.status}`)
    }

    if (openRes.ok) {
      const openPayload = await openRes.json() as { items: PaperTrade[] }
      setPaperOpenTrades(openPayload.items ?? [])
    } else {
      errors.push(`open:${openRes.status}`)
    }

    if (historyRes.ok) {
      const historyPayload = await historyRes.json() as { items: PaperTrade[] }
      setPaperHistory(historyPayload.items ?? [])
    } else {
      errors.push(`history:${historyRes.status}`)
    }

    if (errors.length > 0) {
      throw new Error(`Paper trading partial failure (${errors.join(', ')})`)
    }
  }

  async function fetchDailySummary() {
    const response = await fetch(`${API_BASE}/api/v1/paper-trades/daily?days=30`)
    if (!response.ok) throw new Error('Daily summary API unavailable')
    const payload = await response.json() as { items: DailyTradeSummary[] }
    setDailySummary(payload.items ?? [])
  }

  async function fetchTopVolatility(days: 1 | 3 | 5 | 7 = volDays) {
    const response = await fetch(`${API_BASE}/api/v1/analytics/top-volatility?days=${days}&limit=30`)
    if (!response.ok) throw new Error('Cannot fetch top volatility')
    const payload = await response.json() as { items: VolatilityItem[] }
    setTopVolatility(payload.items ?? [])
  }

  async function fetchLiqOverview(page = liqPage) {
    if (liqReqRef.current) return
    liqReqRef.current = true
    try {
      const response = await fetch(
        `${API_BASE}/api/v1/analytics/liquidation-overview?page=${page}&page_size=${liqPageSize}&full_symbols=true`,
      )
      if (!response.ok) throw new Error('Cannot fetch liquidation overview')
      const payload = await response.json() as { items: LiquidationOverviewItem[]; total_symbols?: number; page?: number }
      setLiqOverview(payload.items ?? [])
      setLiqTotalSymbols(payload.total_symbols ?? 0)
      if (payload.page && payload.page !== liqPage) setLiqPage(payload.page)
    } finally {
      liqReqRef.current = false
    }
  }

  async function fetchBtcTrend() {
    if (btcTrendReqRef.current) return
    btcTrendReqRef.current = true
    try {
      const response = await fetch(`${API_BASE}/api/v1/analytics/btc-trend`)
      if (!response.ok) throw new Error('Cannot fetch BTC trend forecast')
      const payload = await response.json() as BtcTrendResponse
      setBtcTrend(payload)
    } finally {
      btcTrendReqRef.current = false
    }
  }

  async function openPaperMarketOrder(input: PaperMarketOpenRequest) {
    setIsOpeningMarketOrder(true)
    let timeoutId: number | null = null
    try {
      const controller = new AbortController()
      timeoutId = window.setTimeout(() => controller.abort(), 12000)
      const response = await fetch(`${API_BASE}/api/v1/paper-trades/market-open`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
        signal: controller.signal,
      })
      if (response.status === 409) {
        // Duplicate open trade for same symbol/side; treat as idempotent success.
        fetchPaperTradingStats().catch(() => {
          // no-op
        })
        return
      }
      if (!response.ok) {
        const text = await response.text()
        throw new Error(`Market open failed: ${text}`)
      }
      // Do not block the button waiting for all stats endpoints.
      fetchPaperTradingStats().catch(() => {
        // Keep UI responsive even if stats refresh fails once.
      })
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') {
        throw new Error('Market open request timeout (12s)')
      }
      throw err
    } finally {
      if (timeoutId != null) window.clearTimeout(timeoutId)
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
        throw new Error(`Close trade failed: ${text}`)
      }
      await fetchPaperTradingStats()
    } finally {
      setClosingTradeId(null)
    }
  }

  async function createDemoPendingOrder() {
    if (!signal) return

    setIsLoadingOrder(true)
    try {
      const response = await fetch(`${API_BASE}/api/v1/orders/pending`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: signal.symbol,
          side: signal.side,
          quantity: 0.01,
          leverage: 5,
          predicted_entry_price: signal.predicted_entry_price,
          stop_loss: signal.stop_loss,
          take_profit: signal.take_profit,
          win_probability: signal.win_probability,
        }),
      })

      if (!response.ok) throw new Error('Create pending order failed')
      await fetchPendingOrders()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setIsLoadingOrder(false)
    }
  }

  useEffect(() => {
    const bootstrap = async () => {
      try {
        setError('')
        await fetchMarketPriceFallback(selectedCoin).catch(() => {
          // Initial fallback until price websocket is connected.
        })
        await Promise.all([
          fetchHealth(),
          fetchMlStatus(),
          fetchPendingOrders(),
          fetchSignal(selectedCoin, chartBasePrice),
          fetchKlines(selectedCoin),
        ])
        await fetchFuturesSymbols().catch(() => {
          setSymbolsSource('fallback')
          setSymbolsStatus('Cannot reach Binance symbols endpoint, using fallback list')
        })
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unknown error')
      }
    }

    bootstrap()
    return () => undefined
  }, [])

  useEffect(() => {
    fetchMlStatus().catch(() => {
      // Keep previous ML status when endpoint is temporarily unavailable.
    })
    const timer = window.setInterval(() => {
      fetchMlStatus().catch(() => {
        // Keep previous ML status on transient errors.
      })
    }, 5000)
    return () => {
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    const timer = window.setInterval(() => {
      fetchFuturesSymbols().catch(() => {
        setSymbolsSource('fallback')
        setSymbolsStatus('Cannot reach Binance symbols endpoint, using fallback list')
      })
    }, 60000)

    return () => {
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    const reqId = ++symbolReqIdRef.current
    const refreshForSymbol = async () => {
      try {
        const symbol = selectedCoin
        await Promise.all([
          fetchSignal(symbol, marketPrice ?? chartBasePrice),
          fetchKlines(symbol),
        ])
        if (reqId !== symbolReqIdRef.current) return
        setError('')
      } catch (err) {
        // Keep chart running even if one request fails on symbol switch.
        if (reqId !== symbolReqIdRef.current) return
        setError(err instanceof Error ? err.message : 'Unknown error')
      }
    }
    refreshForSymbol()
  }, [selectedCoin])

  useEffect(() => {
    if (!showPaperScreen) return

    fetchPaperTradingStats().catch((err) => {
      setError(err instanceof Error ? err.message : 'Unknown error')
    })

    const timer = window.setInterval(() => {
      fetchPaperTradingStats().catch(() => {
        // Keep previous panel data when one refresh fails.
      })
    }, 8000)

    return () => {
      window.clearInterval(timer)
    }
  }, [showPaperScreen])

  useEffect(() => {
    if (!showDailyScreen) return

    fetchDailySummary().catch((err) => {
      setError(err instanceof Error ? err.message : 'Unknown error')
    })

    const timer = window.setInterval(() => {
      fetchDailySummary().catch(() => {
        // Keep previous daily summary on transient failures.
      })
    }, 12000)

    return () => {
      window.clearInterval(timer)
    }
  }, [showDailyScreen])

  useEffect(() => {
    if (!showPaperScreen || paperOpenTrades.length === 0) return

    let mounted = true
    const symbols = Array.from(new Set(paperOpenTrades.map((row) => row.symbol)))
    let ws: WebSocket | null = null
    let reconnectTimer: number | null = null
    let fallbackTimer: number | null = null

    const stopFallback = () => {
      if (fallbackTimer != null) {
        window.clearInterval(fallbackTimer)
        fallbackTimer = null
      }
    }

    const startFallback = () => {
      if (fallbackTimer != null) return
      setPaperPriceWsStatus('fallback')
      fallbackTimer = window.setInterval(() => {
        fetchMarketPricesBatch(symbols)
          .then((prices) => {
            if (!mounted) return
            setPaperLivePrices((prev) => ({ ...prev, ...prices }))
          })
          .catch(() => {
            // Keep last prices on transient failures.
          })
      }, 1000)
    }

    const connect = () => {
      if (!mounted) return
      setPaperPriceWsStatus('connecting')
      const wsUrl = `${WS_BASE}/ws/prices?symbols=${encodeURIComponent(symbols.join(','))}&interval_sec=1`
      ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        if (!mounted) return
        setPaperPriceWsStatus('live')
        stopFallback()
      }
      ws.onmessage = (event) => {
        if (!mounted) return
        try {
          const payload = JSON.parse(event.data) as PriceStreamMessage
          if (payload.type === 'prices_error') {
            startFallback()
            return
          }
          if (payload.type !== 'prices' || !payload.prices) return
          setPaperLivePrices((prev) => ({ ...prev, ...payload.prices }))
          if (payload.timestamps && Object.keys(payload.timestamps).length > 0) {
            setPaperLivePriceTime((prev) => ({ ...prev, ...payload.timestamps! }))
          } else if (payload.timestamp) {
            const stamp = payload.timestamp
            const updates: Record<string, string> = {}
            for (const key of Object.keys(payload.prices)) updates[key] = stamp
            setPaperLivePriceTime((prev) => ({ ...prev, ...updates }))
          }
        } catch {
          // Ignore malformed payload.
        }
      }
      ws.onerror = () => {
        if (!mounted) return
        startFallback()
      }
      ws.onclose = () => {
        if (!mounted) return
        startFallback()
        reconnectTimer = window.setTimeout(connect, 4000)
      }
    }

    fetchMarketPricesBatch(symbols)
      .then((prices) => {
        if (!mounted) return
        setPaperLivePrices((prev) => ({ ...prev, ...prices }))
      })
      .catch(() => {
        // Ignore initial failures.
      })
    connect()

    return () => {
      mounted = false
      if (reconnectTimer != null) window.clearTimeout(reconnectTimer)
      stopFallback()
      ws?.close()
    }
  }, [showPaperScreen, paperOpenTrades])

  useEffect(() => {
    fetchTopVolatility(volDays).catch(() => {
      // Keep previous volatility table on request failure.
    })
  }, [volDays])

  useEffect(() => {
    fetchBtcTrend().catch(() => {
      // Keep previous BTC trend block on request failure.
    })

    const timer = window.setInterval(() => {
      fetchBtcTrend().catch(() => {
        // Keep previous BTC trend block on periodic refresh failure.
      })
    }, 15000)

    return () => {
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    fetchLiqOverview(liqPage).catch(() => {
      // Keep previous liquidation overview table on request failure.
    })

    const timer = window.setInterval(() => {
      fetchLiqOverview(liqPage).catch(() => {
        // Keep previous liquidation overview on periodic refresh failure.
      })
    }, 45000)

    return () => {
      window.clearInterval(timer)
    }
  }, [liqPage, liqPageSize])

  useEffect(() => {
    if (!autoLiqMarketEnabled) return
    if (isOpeningMarketOrder) return

    const now = Date.now()
    let opened = 0

    const run = async () => {
      for (const row of sortedLiqOverview) {
        if (opened >= AUTO_LIQ_MAX_ORDERS_PER_CYCLE) break
        if (!canOpenFromLiq(row)) continue

        const key = `${row.symbol}:${row.signal_side}`
        const lastOpened = liqAutoOpenedRef.current[key] ?? 0
        if ((now - lastOpened) < AUTO_LIQ_OPEN_COOLDOWN_MS) continue

        liqAutoOpenedRef.current[key] = now
        try {
          await openFromLiqRow(row)
          opened += 1
        } catch {
          // Ignore duplicate/temporary failures in auto mode.
        }
      }
    }

    run().catch(() => {
      // Do not block UI if auto-open cycle fails.
    })
  }, [autoLiqMarketEnabled, sortedLiqOverview, isOpeningMarketOrder])

  useEffect(() => {
    setSignalsWsStatus('fallback')
    fetchHighWinSignals().catch(() => {
      // Initial fetch.
    })
    const timer = window.setInterval(() => {
      fetchHighWinSignals().catch(() => {
        // Keep previous values when request fails.
      })
    }, 12000)
    return () => {
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    let socket: WebSocket | null = null
    let reconnectTimer: number | null = null
    let fallbackTimer: number | null = null
    let mounted = true

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
        fetchMarketPriceFallback(selectedCoinRef.current).catch(() => {
          // Keep last market price if fallback request fails.
        })
      }, 1000)
    }

    const connect = () => {
      if (!mounted) return
      setPriceWsStatus('connecting')
      const url = `${WS_BASE}/ws/price?symbol=${encodeURIComponent(selectedCoin)}&interval_sec=1`
      socket = new WebSocket(url)

      socket.onopen = () => {
        if (!mounted) return
        setPriceWsStatus('live')
        stopFallback()
      }

      socket.onmessage = (event) => {
        if (!mounted) return
        try {
          const msg = JSON.parse(event.data) as PriceStreamMessage
          if (msg.type === 'price' && typeof msg.price === 'number') {
            if (canonicalSymbol(msg.symbol ?? selectedCoin) !== canonicalSymbol(selectedCoinRef.current)) {
              return
            }
            setMarketPrice(msg.price)
            setMarketPriceTime(msg.timestamp ?? null)
            return
          }
          if (msg.type === 'price_error') {
            startFallback()
          }
        } catch {
          // ignore invalid payload
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

    fetchMarketPriceFallback(selectedCoin).catch(() => {
      // websocket should recover if REST fallback also fails.
    })
    connect()

    return () => {
      mounted = false
      if (reconnectTimer != null) window.clearTimeout(reconnectTimer)
      stopFallback()
      socket?.close()
    }
  }, [selectedCoin])

  return (
    <main className="app-shell">
      <section className="hero">
        <p className="eyebrow">Liquidation Monitor</p>
        <h1>Liquidation Map + ML Signal</h1>
        <p className="subtext">
          A liquidation heatmap styled dashboard with coin selection, threshold control, and realtime overlay.
        </p>
        <div className="hero-actions">
          <button
            type="button"
            onClick={() => {
              setShowPaperScreen((v) => !v)
              setShowDailyScreen(false)
            }}
          >
            {showPaperScreen ? 'Back To Main Screen' : 'Open Paper Trade Stats'}
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => {
              setShowDailyScreen((v) => !v)
              setShowPaperScreen(false)
            }}
          >
            {showDailyScreen ? 'Back To Main Screen' : 'Open Daily Win/Loss'}
          </button>
        </div>
      </section>

      {showPaperScreen ? (
        <section className="card">
          <header className="card-header">
            <h2>Paper Trade Statistics (MySQL)</h2>
            <span className="badge neutral">Realtime</span>
          </header>
          <div className="stats-grid">
            <div className="stats-item"><strong>Total:</strong> {paperStats?.total_trades ?? 0}</div>
            <div className="stats-item"><strong>Open:</strong> {paperStats?.open_trades ?? 0}</div>
            <div className="stats-item"><strong>Closed:</strong> {paperStats?.closed_trades ?? 0}</div>
            <div className="stats-item"><strong>Win:</strong> {paperStats?.win_trades ?? 0}</div>
            <div className="stats-item"><strong>Loss:</strong> {paperStats?.loss_trades ?? 0}</div>
            <div className="stats-item"><strong>Win Rate:</strong> {paperStats ? `${(paperStats.win_rate * 100).toFixed(2)}%` : '0%'}</div>
            <div className="stats-item"><strong>Order Value:</strong> {paperStats && typeof paperStats.order_usdt === 'number' ? `${paperStats.order_usdt.toFixed(2)} USDT` : '-'}</div>
            <div className="stats-item"><strong>Margin:</strong> {paperStats && typeof paperStats.margin_usdt === 'number' ? `${paperStats.margin_usdt.toFixed(2)} USDT` : '-'}</div>
            <div className="stats-item"><strong>Total PnL:</strong> {paperStats ? `${paperStats.total_pnl.toFixed(4)} USDT` : '0.0000 USDT'}</div>
            <div className="stats-item"><strong>Avg PnL:</strong> {paperStats ? `${paperStats.avg_pnl.toFixed(4)} USDT` : '0.0000 USDT'}</div>
            <div className="stats-item"><strong>Total PnL%:</strong> {paperStats && typeof paperStats.total_pnl_pct === 'number' ? `${paperStats.total_pnl_pct.toFixed(2)}%` : '0.00%'}</div>
            <div className="stats-item"><strong>Avg PnL%:</strong> {paperStats && typeof paperStats.avg_pnl_pct === 'number' ? `${paperStats.avg_pnl_pct.toFixed(2)}%` : '0.00%'}</div>
          </div>

          <h3 className="section-title">Open Paper Trades</h3>
          <div className="content table-wrap">
            {paperOpenTrades.length === 0 ? (
              <p>No open paper trades.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Symbol</th>
                    <th>uPnL (USDT)</th>
                    <th>uPnL% (Margin)</th>
                    <th>Side</th>
                    <th>Entry</th>
                    <th>Mark (WS)</th>
                    <th>Mark TS</th>
                    <th>Margin (USDT)</th>
                    <th>TP</th>
                    <th>SL</th>
                    <th>Signal%</th>
                    <th>Effective%</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {paperOpenTrades.map((row) => {
                    const mark = resolveLivePrice(row.symbol)
                    const markTs = resolveLiveTime(row.symbol)
                    const upnlPct = calcUnrealizedPnlPct(row, mark)
                    const marginUsdt = typeof row.margin_usdt === 'number'
                      ? row.margin_usdt
                      : calcMarginUsdt(row.entry_price, row.quantity, row.leverage)
                    const upnlUsdt = (typeof upnlPct === 'number' && typeof marginUsdt === 'number')
                      ? (marginUsdt * upnlPct / 100)
                      : null
                    return (
                    <tr key={row.id}>
                      <td>{row.id}</td>
                      <td>{renderSymbolJump(row.symbol, row.entry_price)}</td>
                      <td>
                        {typeof upnlUsdt === 'number' ? (
                          <span className={upnlUsdt >= 0 ? 'pnl-pos' : 'pnl-neg'}>
                            {`${upnlUsdt >= 0 ? '+' : ''}${upnlUsdt.toFixed(4)}`}
                          </span>
                        ) : '-'}
                      </td>
                      <td>
                        {typeof upnlPct === 'number' ? (
                          <span className={upnlPct >= 0 ? 'pnl-pos' : 'pnl-neg'}>
                            {`${upnlPct >= 0 ? '+' : ''}${upnlPct.toFixed(2)}%`}
                          </span>
                        ) : '-'}
                      </td>
                      <td>{row.side}</td>
                      <td>{row.entry_price}</td>
                      <td>{typeof mark === 'number' ? mark : '-'}</td>
                      <td>{formatVnTimestamp(markTs)}</td>
                      <td>{typeof marginUsdt === 'number' ? marginUsdt.toFixed(2) : '-'}</td>
                      <td>{row.take_profit}</td>
                      <td>{row.stop_loss}</td>
                      <td>{(row.signal_win_probability * 100).toFixed(2)}</td>
                      <td>{(row.effective_win_probability * 100).toFixed(2)}</td>
                      <td>
                        <button
                          type="button"
                          className="btn-inline btn-secondary"
                          disabled={closingTradeId === row.id}
                          onClick={() => {
                            closePaperTrade(row.id, { force_result: 0 }).catch((err) => {
                              setError(err instanceof Error ? err.message : 'Unknown error')
                            })
                          }}
                        >
                          {closingTradeId === row.id ? 'Closing...' : 'Close Wrong'}
                        </button>
                      </td>
                    </tr>
                  )})}
                </tbody>
              </table>
            )}
            <p className="symbols-text">Open trade WS status: {paperPriceWsStatus}</p>
          </div>

          <h3 className="section-title">Closed Trade History</h3>
          <div className="content table-wrap">
            {paperHistory.length === 0 ? (
              <p>No closed paper trades yet.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Symbol</th>
                    <th>PnL (USDT)</th>
                    <th>PnL% (Margin)</th>
                    <th>Side</th>
                    <th>Status</th>
                    <th>Entry</th>
                    <th>Close</th>
                    <th>Close Reason</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {paperHistory.map((row) => {
                    const closePnlPct = typeof row.pnl_pct === 'number'
                      ? row.pnl_pct
                      : (
                        typeof row.close_price === 'number'
                          ? calcPnlPct(row.side, row.entry_price, row.close_price, row.leverage)
                          : null
                      )
                    return (
                      <tr key={`${row.id}-${row.status}`}>
                        <td>{row.id}</td>
                        <td>{renderSymbolJump(row.symbol, row.entry_price)}</td>
                        <td>
                          {typeof row.pnl === 'number' ? (
                            <span className={row.pnl >= 0 ? 'pnl-pos' : 'pnl-neg'}>
                              {`${row.pnl >= 0 ? '+' : ''}${row.pnl.toFixed(4)}`}
                            </span>
                          ) : '-'}
                        </td>
                        <td>
                          {typeof closePnlPct === 'number' ? (
                            <span className={closePnlPct >= 0 ? 'pnl-pos' : 'pnl-neg'}>
                              {`${closePnlPct >= 0 ? '+' : ''}${closePnlPct.toFixed(2)}%`}
                            </span>
                          ) : '-'}
                        </td>
                        <td>{row.side}</td>
                        <td>{row.status}</td>
                        <td>{row.entry_price}</td>
                        <td>{row.close_price ?? '-'}</td>
                        <td>{row.close_reason ?? '-'}</td>
                        <td>{row.result == null ? '-' : row.result === 1 ? 'WIN' : 'LOSS'}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
        </section>
      ) : null}

      {showDailyScreen ? (
        <section className="card">
          <header className="card-header">
            <h2>Daily Win/Loss Summary (VN Time)</h2>
            <span className="badge neutral">Last 30 Days</span>
          </header>
          <div className="content table-wrap">
            {dailySummary.length === 0 ? (
              <p>No closed trades in selected period.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Total</th>
                    <th>Win</th>
                    <th>Loss</th>
                    <th>Win Rate</th>
                    <th>Total PnL</th>
                    <th>Avg PnL</th>
                  </tr>
                </thead>
                <tbody>
                  {dailySummary.map((row) => (
                    <tr key={row.trade_date}>
                      <td>{row.trade_date}</td>
                      <td>{row.total_trades}</td>
                      <td>{row.win_trades}</td>
                      <td>{row.loss_trades}</td>
                      <td>{(row.win_rate * 100).toFixed(2)}%</td>
                      <td>{row.total_pnl.toFixed(6)}</td>
                      <td>{row.avg_pnl.toFixed(6)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      ) : null}

      <section className="card liq-card" ref={mapSectionRef}>
        <div className="search-row">
          <input
            value={searchCoin}
            onChange={(e) => setSearchCoin(e.target.value)}
            className="search-input"
            placeholder="Search coin"
          />
          <select value={selectedCoin} onChange={(e) => setSelectedCoin(e.target.value)} className="select-control">
            {displayedCoins.map((coin) => (
              <option key={coin} value={coin}>{coin}</option>
            ))}
          </select>
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} className="select-control">
            <option value="1h">1h</option>
            <option value="4h">4h</option>
            <option value="12h">12h</option>
            <option value="24h">24h</option>
          </select>
        </div>
        <div className="symbols-meta">
          <span className={`badge ${symbolsSource === 'binance' ? 'success' : 'warn'}`}>
            {symbolsSource === 'binance' ? 'Binance Symbols' : 'Fallback Symbols'}
          </span>
          <span className="symbols-text">{symbolsStatus}</span>
        </div>

        <div className="coin-chip-row">
          {displayedCoins.slice(0, 8).map((coin) => (
            <button
              key={coin}
              className={`coin-chip ${selectedCoin === coin ? 'coin-chip-active' : ''}`}
              onClick={() => setSelectedCoin(coin)}
              type="button"
            >
              {coin}
            </button>
          ))}
        </div>

        <div className="control-row">
          <div className="palette-group">
            {PALETTES.map((palette) => (
              <button
                key={palette.id}
                type="button"
                onClick={() => setPaletteId(palette.id)}
                className={`palette-btn ${paletteId === palette.id ? 'palette-btn-active' : ''}`}
                title={palette.name}
              >
                <span
                  className="palette-swatch"
                  style={{
                    background: `linear-gradient(90deg, rgb(${palette.stops[0][1]} ${palette.stops[0][2]} ${palette.stops[0][3]}), rgb(${palette.stops[palette.stops.length - 1][1]} ${palette.stops[palette.stops.length - 1][2]} ${palette.stops[palette.stops.length - 1][3]}))`,
                  }}
                />
              </button>
            ))}
          </div>

          <div className="threshold-group">
            <label htmlFor="threshold">Liquidity Threshold = {threshold.toFixed(2)}</label>
            <input
              id="threshold"
              type="range"
              min={0.2}
              max={0.95}
              step={0.01}
              value={threshold}
              onChange={(e) => setThreshold(Number(e.target.value))}
            />
          </div>
        </div>
        <div className="ema-row">
          {[9, 21, 50, 200].map((period) => (
            <label key={period} className="ema-toggle">
              <input
                type="checkbox"
                checked={!!emaVisible[period]}
                onChange={(e) => {
                  const checked = e.target.checked
                  setEmaVisible((prev) => ({ ...prev, [period]: checked }))
                }}
              />
              <span>EMA {period}</span>
            </label>
          ))}
        </div>

        <div className="map-header">
          <h2>
            {selectedCoin} Liquidation Map
            {' | Price: '}
            {marketPrice != null ? marketPrice.toFixed(marketPrice >= 100 ? 2 : 6) : '...'}
            {' | WS: '}
            {priceWsStatus}
            {' | TS: '}
            {formatVnTimestamp(marketPriceTime)}
          </h2>
          <span className={`badge ${connectionStatus === 'Live' ? 'success' : 'warn'}`}>{connectionStatus}</span>
        </div>

        <div className="map-stage">
          <div className="map-canvas-wrap">
            <LiquidationMapCanvas
              data={heatmapData}
              palette={activePalette}
              emaLines={emaLines}
              onHover={(payload) => setHoverInfo(payload)}
              onLeave={() => setHoverInfo(null)}
            />
            <div
              className="now-marker"
              style={{ left: `${(heatmapData.currentCol / Math.max(1, heatmapData.cols - 1)) * 100}%` }}
            >
              NOW
            </div>
            {hoverInfo ? (
              <>
                <div className="crosshair-v" style={{ left: `${hoverInfo.x}px` }} />
                <div className="crosshair-h" style={{ top: `${hoverInfo.y}px` }} />
              </>
            ) : null}
          </div>
          <div className="price-axis">
            {priceTicks.map((price, idx) => (
              <span key={`${price}-${idx}`}>{price.toFixed(price >= 100 ? 2 : 6)}</span>
            ))}
          </div>
        </div>
        {hoverInfo && hoverSummary ? (
          <div className="hover-panel">
            <p><strong>Hovered Price:</strong> {hoverInfo.price.toFixed(hoverInfo.price >= 100 ? 2 : 6)}</p>
            <p><strong>Distance:</strong> {hoverSummary.distancePct.toFixed(2)}%</p>
            <p><strong>Zone:</strong> {hoverSummary.side}</p>
            <p><strong>Liquidity Score:</strong> {hoverSummary.liqScore.toFixed(1)}</p>
            <p><strong>Est. Liquidation:</strong> ${Math.round(hoverSummary.estUsd).toLocaleString()}</p>
            <p><strong>Time Slice:</strong> {hoverSummary.timeText}</p>
            <p><strong>Offset:</strong> {hoverSummary.deltaHours >= 0 ? '+' : ''}{hoverSummary.deltaHours.toFixed(1)}h</p>
          </div>
        ) : (
          <div className="hover-panel muted">
            <p>Hover on heatmap to inspect price zone and liquidation estimate.</p>
          </div>
        )}
      </section>

      <section className="grid two-col">
        <article className="card">
          <header className="card-header">
            <h2>Latest ML Signal</h2>
            <span className="badge neutral">{signal?.side ?? 'N/A'}</span>
          </header>
          <div className="content">
            <p>
              <strong>Symbol:</strong>{' '}
              {renderSymbolJump(signal?.symbol ?? selectedCoin, signal?.predicted_entry_price)}
            </p>
            <p><strong>Win Probability:</strong> {signal ? `${(signal.win_probability * 100).toFixed(2)}%` : '-'}</p>
            <p>
              <strong>Signal Risk%:</strong>{' '}
              {signal ? (
                <span className="pnl-neg">
                  {(calcSignalRiskPct(
                    signal.side,
                    signal.predicted_entry_price,
                    signal.stop_loss,
                  ) ?? 0).toFixed(2)}%
                </span>
              ) : '-'}
            </p>
            <p><strong>Entry:</strong> {signal?.predicted_entry_price ?? '-'}</p>
            <p><strong>TP:</strong> {signal?.take_profit ?? '-'}</p>
            <p><strong>SL:</strong> {signal?.stop_loss ?? '-'}</p>
            <div className="action-row">
              <button onClick={createDemoPendingOrder} disabled={!signal || isLoadingOrder}>
                {isLoadingOrder ? 'Submitting...' : 'Create Demo Pending Order'}
              </button>
              <button
                className="btn-secondary"
                onClick={() => {
                  if (!signal) return
                  openPaperMarketOrder({
                    symbol: signal.symbol,
                    side: signal.side,
                    signal_win_probability: signal.win_probability,
                    entry_price: signal.predicted_entry_price,
                    take_profit: signal.take_profit,
                    stop_loss: signal.stop_loss,
                  }).catch((err) => {
                    setError(err instanceof Error ? err.message : 'Unknown error')
                  })
                }}
                disabled={
                  !signal
                  || isOpeningMarketOrder
                  || openTradeKeySet.has(`${canonicalSymbol(signal.symbol)}:${signal.side}`)
                }
              >
                {isOpeningMarketOrder ? 'Opening...' : 'Open Paper Market Now'}
              </button>
            </div>
          </div>
        </article>

        <article className="card">
          <header className="card-header">
            <h2>Pending Orders</h2>
            <span className="badge neutral">{pendingOrders.length}</span>
          </header>
          <div className="content table-wrap">
            {pendingOrders.length === 0 ? (
              <p>No pending orders yet.</p>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Pair</th>
                    <th>Side</th>
                    <th>Entry</th>
                    <th>TP</th>
                    <th>SL</th>
                  </tr>
                </thead>
                <tbody>
                  {pendingOrders.map((order) => (
                    <tr key={order.id}>
                      <td>{order.id}</td>
                      <td>{renderSymbolJump(order.symbol, order.predicted_entry_price)}</td>
                      <td>{order.side}</td>
                      <td>{order.predicted_entry_price}</td>
                      <td>{order.take_profit}</td>
                      <td>{order.stop_loss}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </article>
      </section>

      <section className="card">
        <header className="card-header">
          <h2>High Win Signals (&gt; 70%)</h2>
          <div className="scan-actions">
            <span className="badge neutral">Scanned: {scannedCount}</span>
            <span className={`badge ${signalsWsStatus === 'live' ? 'success' : signalsWsStatus === 'connecting' ? 'warn' : 'neutral'}`}>
              {signalsWsStatus === 'live' ? 'WS Live' : signalsWsStatus === 'connecting' ? 'WS Connecting' : 'REST Fallback'}
            </span>
          </div>
        </header>
        <div className="content table-wrap">
          {highWinSignals.length === 0 ? (
            <p>No coin currently above 70% win probability.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Win%</th>
                  <th>Risk%</th>
                  <th>Entry</th>
                  <th>TP</th>
                  <th>SL</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {highWinSignals.map((item) => (
                  <tr key={`${item.symbol}-${item.side}`}>
                    <td>
                      {renderSymbolJump(item.symbol, item.predicted_entry_price)}
                    </td>
                    <td>{item.side}</td>
                    <td>{(item.win_probability * 100).toFixed(2)}</td>
                    <td>
                      <span className="pnl-neg">
                        {(calcSignalRiskPct(
                          item.side,
                          item.predicted_entry_price,
                          item.stop_loss,
                        ) ?? 0).toFixed(2)}%
                      </span>
                    </td>
                    <td>{item.predicted_entry_price}</td>
                    <td>{item.take_profit}</td>
                    <td>{item.stop_loss}</td>
                    <td>
                      <button
                        type="button"
                        className="btn-inline"
                        disabled={
                          isOpeningMarketOrder
                          || openTradeKeySet.has(`${canonicalSymbol(item.symbol)}:${item.side}`)
                        }
                        onClick={() => {
                          openPaperMarketOrder({
                            symbol: item.symbol,
                            side: item.side,
                            signal_win_probability: item.win_probability,
                            entry_price: item.predicted_entry_price,
                            take_profit: item.take_profit,
                            stop_loss: item.stop_loss,
                          }).catch((err) => {
                            setError(err instanceof Error ? err.message : 'Unknown error')
                          })
                        }}
                      >
                        {isOpeningMarketOrder ? 'Opening...' : 'Market Open'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="card">
        <header className="card-header">
          <h2>Top Volatility Coins</h2>
          <div className="tab-row">
            {[1, 3, 5, 7].map((d) => (
              <button
                key={d}
                type="button"
                className={`tab-btn ${volDays === d ? 'tab-btn-active' : ''}`}
                onClick={() => setVolDays(d as 1 | 3 | 5 | 7)}
              >
                {d}D
              </button>
            ))}
          </div>
        </header>
        <div className="content table-wrap">
          {topVolatility.length === 0 ? (
            <p>No volatility data available.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleVolSort('symbol')}>Symbol</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleVolSort('move_pct')}>Move%</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleVolSort('abs_move_pct')}>Abs Move%</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleVolSort('from_price')}>From</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleVolSort('to_price')}>To</button></th>
                </tr>
              </thead>
              <tbody>
                {sortedVolatility.map((row) => (
                  <tr key={`${row.symbol}-${row.days}`}>
                    <td>{renderSymbolJump(row.symbol, row.to_price)}</td>
                    <td>{row.move_pct.toFixed(2)}</td>
                    <td>{row.abs_move_pct.toFixed(2)}</td>
                    <td>{row.from_price}</td>
                    <td>{row.to_price}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="card">
        <header className="card-header">
          <h2>BTC Trend Forecast (15m / 1h / 4h / 1d)</h2>
          <span className="badge neutral">
            {btcTrend ? `${btcTrend.symbol} ${btcTrend.mark_price.toFixed(2)}` : 'Loading'}
          </span>
        </header>
        <div className="content table-wrap">
          {btcTrend == null || btcTrend.items.length === 0 ? (
            <p>No BTC trend data available.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>TF</th>
                  <th>Trend</th>
                  <th>Action</th>
                  <th>Confidence</th>
                  <th>Prob Up</th>
                  <th>Prob Down</th>
                  <th>RSI</th>
                  <th>Slope%</th>
                  <th>Tech Score</th>
                  <th>ML Score</th>
                  <th>Blend</th>
                </tr>
              </thead>
              <tbody>
                {btcTrend.items.map((row) => (
                  <tr key={row.timeframe} className={row.action === 'LONG' ? 'row-long' : row.action === 'SHORT' ? 'row-short' : ''}>
                    <td>{row.timeframe}</td>
                    <td>{row.trend}</td>
                    <td>
                      <span className={row.action === 'LONG' ? 'pill-long' : row.action === 'SHORT' ? 'pill-short' : ''}>
                        {row.action}
                      </span>
                    </td>
                    <td>{(row.confidence * 100).toFixed(1)}%</td>
                    <td>{(row.prob_up * 100).toFixed(1)}%</td>
                    <td>{(row.prob_down * 100).toFixed(1)}%</td>
                    <td>{row.rsi.toFixed(2)}</td>
                    <td>{row.slope_pct.toFixed(3)}</td>
                    <td>{row.technical_score.toFixed(3)}</td>
                    <td>{row.ml_score.toFixed(3)}</td>
                    <td>{row.blended_score.toFixed(3)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="card">
        <header className="card-header">
          <h2>Liquidation Zone + Long/Short + Funding</h2>
          <div className="scan-actions">
            <span className="badge neutral">Page {liqPage}/{liqMaxPage}</span>
            <span className="badge neutral">Symbols: {liqTotalSymbols}</span>
            <span className="badge neutral">Estimated Zone</span>
            <button
              type="button"
              className="btn-inline btn-secondary"
              disabled={liqPage <= 1}
              onClick={() => setLiqPage((p) => Math.max(1, p - 1))}
            >
              Prev
            </button>
            <button
              type="button"
              className="btn-inline btn-secondary"
              disabled={liqPage >= liqMaxPage}
              onClick={() => setLiqPage((p) => Math.min(liqMaxPage, p + 1))}
            >
              Next
            </button>
            <button
              type="button"
              className={`btn-inline ${autoLiqMarketEnabled ? '' : 'btn-secondary'}`}
              onClick={() => setAutoLiqMarketEnabled((v) => !v)}
              title="Auto open market orders from this table"
            >
              {autoLiqMarketEnabled ? 'Auto Market ON' : 'Auto Market OFF'}
            </button>
          </div>
        </header>
        <div className="content table-wrap">
          {liqOverview.length === 0 ? (
            <p>No liquidation overview data available.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleLiqSort('symbol')}>Symbol</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleLiqSort('mark_price')}>Mark</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleLiqSort('est_liq_zone_price')}>Est Liq Zone Price</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleLiqSort('est_liq_zone_value')}>Est Liq Zone Value</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleLiqSort('long_short_ratio')}>L/S Ratio</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleLiqSort('funding_rate')}>Funding Rate</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleLiqSort('open_interest_notional')}>OI Notional</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleLiqSort('signal_win_probability')}>Win%</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleLiqSort('signal_entry_price')}>Entry</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleLiqSort('signal_take_profit')}>TP</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleLiqSort('signal_stop_loss')}>SL</button></th>
                  <th><button type="button" className="th-sort-btn" onClick={() => toggleLiqSort('signal_order_type')}>Signal Type</button></th>
                  <th>Trend</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {sortedLiqOverview.map((row) => {
                  const trend = getLiqTrend(row)
                  return (
                  <tr key={row.symbol} className={trend === 'LONG' ? 'row-long' : 'row-short'}>
                    <td>{renderSymbolJump(row.symbol, row.mark_price)}</td>
                    <td>{row.mark_price.toFixed(row.mark_price >= 100 ? 2 : 6)}</td>
                    <td>{row.est_liq_zone_price.toFixed(row.est_liq_zone_price >= 100 ? 2 : 6)}</td>
                    <td>{Math.round(row.est_liq_zone_value).toLocaleString()}</td>
                    <td>{row.long_short_ratio.toFixed(3)}</td>
                    <td>{(row.funding_rate * 100).toFixed(4)}%</td>
                    <td>{Math.round(row.open_interest_notional).toLocaleString()}</td>
                    <td>{typeof row.signal_win_probability === 'number' ? `${(row.signal_win_probability * 100).toFixed(2)}%` : '-'}</td>
                    <td>{typeof row.signal_entry_price === 'number' ? row.signal_entry_price.toFixed(row.signal_entry_price >= 100 ? 2 : 6) : '-'}</td>
                    <td>{typeof row.signal_take_profit === 'number' ? row.signal_take_profit.toFixed(row.signal_take_profit >= 100 ? 2 : 6) : '-'}</td>
                    <td>{typeof row.signal_stop_loss === 'number' ? row.signal_stop_loss.toFixed(row.signal_stop_loss >= 100 ? 2 : 6) : '-'}</td>
                    <td>{row.signal_order_type ?? '-'}</td>
                    <td><span className={trend === 'LONG' ? 'pill-long' : 'pill-short'}>{trend}</span></td>
                    <td>
                      <button
                        type="button"
                        className="btn-inline"
                        disabled={isOpeningMarketOrder || !canOpenFromLiq(row)}
                        onClick={() => {
                          openFromLiqRow(row).catch((err) => {
                            setError(err instanceof Error ? err.message : 'Unknown error')
                          })
                        }}
                      >
                        {isOpeningMarketOrder ? 'Opening...' : 'Market Open'}
                      </button>
                    </td>
                  </tr>
                )})}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section className="grid two-col">
        <article className="card">
          <header className="card-header">
            <h2>System Health</h2>
            <span className={`badge ${health?.status === 'ok' ? 'success' : 'warn'}`}>
              {health?.status ?? 'unknown'}
            </span>
          </header>
          <div className="content">
            <p><strong>App:</strong> {health?.app_name ?? '-'}</p>
            <p><strong>Env:</strong> {health?.environment ?? '-'}</p>
            <p><strong>Updated:</strong> {formatVnTimestamp(health?.timestamp)}</p>
          </div>
        </article>

        <article className="card">
          <header className="card-header">
            <h2>Market Price</h2>
            <span className={`badge ${priceWsStatus === 'live' ? 'success' : priceWsStatus === 'connecting' ? 'warn' : 'neutral'}`}>
              {priceWsStatus === 'live' ? 'WS Live' : priceWsStatus === 'connecting' ? 'WS Connecting' : 'REST Fallback'}
            </span>
          </header>
          <div className="content">
            <p><strong>Symbol:</strong> {selectedCoin}</p>
            <p><strong>Real Price (Binance):</strong> {marketPrice ?? '-'}</p>
            <p><strong>Price Time:</strong> {formatVnTimestamp(marketPriceTime)}</p>
            <p><strong>Connection:</strong> {priceWsStatus}</p>
          </div>
        </article>
      </section>

      <section className="card">
        <header className="card-header">
          <h2>ML Training Monitor</h2>
          <span className={`badge ${mlStatus?.training_in_progress ? 'warn' : 'success'}`}>
            {mlStatus?.training_in_progress ? 'Training...' : 'Idle'}
          </span>
        </header>
        <div className="content">
          <p><strong>Model Loaded:</strong> {mlStatus?.is_loaded ? 'Yes' : 'No'}</p>
          <p><strong>Last Trained At:</strong> {formatVnTimestamp(mlStatus?.trained_at ?? null)}</p>
          <p><strong>Last Train Trigger:</strong> {mlStatus?.last_train_trigger ?? '-'}</p>
          <p><strong>Train Start (VN):</strong> {formatVnTimestamp(mlStatus?.last_train_started_at ?? null)}</p>
          <p><strong>Train End (VN):</strong> {formatVnTimestamp(mlStatus?.last_train_finished_at ?? null)}</p>
          <p><strong>Duration:</strong> {typeof mlStatus?.last_train_duration_sec === 'number' ? `${mlStatus.last_train_duration_sec.toFixed(2)}s` : '-'}</p>
          <p><strong>Result:</strong> {mlStatus?.last_train_result ?? '-'}</p>
          <p><strong>Error:</strong> {mlStatus?.last_train_error ?? '-'}</p>
          <p><strong>Auto Train:</strong> {mlStatus?.auto_train_enabled ? 'ON' : 'OFF'} ({mlStatus?.auto_train_interval_minutes ?? '-'}m)</p>
          <p><strong>Next Auto Run (VN):</strong> {formatVnTimestamp(mlStatus?.auto_train_next_run_at ?? null)}</p>
          <p><strong>Train Log:</strong> <code>{mlStatus?.train_log_path ?? '-'}</code></p>
        </div>
      </section>

      {error ? <p className="error">{error}</p> : null}
    </main>
  )
}

export default App
