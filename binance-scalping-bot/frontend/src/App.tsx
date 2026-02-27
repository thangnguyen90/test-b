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
  symbol: string
  price?: number
  timestamp?: string | null
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
  pnl?: number | null
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
}

const API_BASE = 'http://127.0.0.1:8000'
const SIGNALS_WS_URL = 'ws://127.0.0.1:8000/ws/signals?min_win=0.7&max_symbols=80&interval_sec=12'
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

function buildHeatmap(
  coin: string,
  basePrice: number,
  threshold: number,
  timeframe: string,
  pastSeriesInput: number[],
): HeatmapData {
  const rows = 320
  const cols = 980
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
  const [health, setHealth] = useState<Health | null>(null)
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
  const [paperStats, setPaperStats] = useState<PaperTradeStats | null>(null)
  const [paperOpenTrades, setPaperOpenTrades] = useState<PaperTrade[]>([])
  const [paperHistory, setPaperHistory] = useState<PaperTrade[]>([])

  const [selectedCoin, setSelectedCoin] = useState('BTC/USDT')
  const [searchCoin, setSearchCoin] = useState('')
  const [timeframe, setTimeframe] = useState('12h')
  const [threshold, setThreshold] = useState(0.62)
  const [paletteId, setPaletteId] = useState(PALETTES[0].id)

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

  async function fetchHealth() {
    const response = await fetch(`${API_BASE}/health`)
    if (!response.ok) throw new Error('Health check failed')
    setHealth(await response.json())
  }

  async function fetchSignal(symbol = selectedCoin, markPrice = chartBasePrice) {
    const response = await fetch(
      `${API_BASE}/api/v1/signals/latest?symbol=${encodeURIComponent(symbol)}&mark_price=${markPrice}`,
    )
    if (!response.ok) throw new Error('Signal API failed')
    setSignal(await response.json())
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
    setCoins(data.symbols)
    setSymbolsSource('binance')
    setSymbolsStatus(`Binance symbols loaded: ${data.symbols.length}`)
    if (!data.symbols.includes(selectedCoin)) {
      setSelectedCoin(data.symbols[0])
    }
  }

  async function fetchMarketPriceFallback(symbol = selectedCoin): Promise<number> {
    const response = await fetch(
      `${API_BASE}/api/v1/market/price?symbol=${encodeURIComponent(symbol)}`,
    )
    if (!response.ok) throw new Error(`Cannot fetch market price for ${symbol}`)
    const data = (await response.json()) as MarketPriceResponse
    setMarketPrice(data.price)
    setMarketPriceTime(data.timestamp)
    return data.price
  }

  async function fetchKlines(symbol = selectedCoin) {
    const response = await fetch(
      `${API_BASE}/api/v1/market/klines?symbol=${encodeURIComponent(symbol)}&timeframe=5m&limit=1200`,
    )
    if (!response.ok) throw new Error(`Cannot fetch klines for ${symbol}`)
    const data = (await response.json()) as KlinesResponse
    const closes = (data.candles ?? []).map((c) => c.close).filter((x) => Number.isFinite(x))
    if (closes.length > 10) setKlineSeries(closes)
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
    if (!statsRes.ok || !openRes.ok || !historyRes.ok) {
      throw new Error('Paper trading stats API unavailable')
    }

    const statsPayload = await statsRes.json() as { stats: PaperTradeStats }
    const openPayload = await openRes.json() as { items: PaperTrade[] }
    const historyPayload = await historyRes.json() as { items: PaperTrade[] }
    setPaperStats(statsPayload.stats ?? null)
    setPaperOpenTrades(openPayload.items ?? [])
    setPaperHistory(historyPayload.items ?? [])
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
    const refreshForSymbol = async () => {
      try {
        await Promise.all([
          fetchSignal(selectedCoin, marketPrice ?? chartBasePrice),
          fetchKlines(selectedCoin),
        ])
        setError('')
      } catch (err) {
        // Keep chart running even if one request fails on symbol switch.
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
      fallbackTimer = window.setInterval(() => {
        fetchHighWinSignals().catch(() => {
          // Keep previous values when fallback request fails.
        })
      }, 25000)
    }

    const connect = () => {
      if (!mounted) return
      setSignalsWsStatus('connecting')
      socket = new WebSocket(SIGNALS_WS_URL)

      socket.onopen = () => {
        if (!mounted) return
        setSignalsWsStatus('live')
        stopFallback()
      }

      socket.onmessage = (event) => {
        if (!mounted) return
        try {
          const payload = JSON.parse(event.data) as { type?: string; data?: ScanSignalsResponse }
          if (payload.type !== 'signals_scan' || !payload.data) return
          setHighWinSignals(payload.data.signals ?? [])
          setScannedCount(payload.data.scanned ?? 0)
        } catch {
          // Ignore malformed payload
        }
      }

      socket.onerror = () => {
        if (!mounted) return
        setSignalsWsStatus('fallback')
        startFallback()
      }

      socket.onclose = () => {
        if (!mounted) return
        setSignalsWsStatus('fallback')
        startFallback()
        reconnectTimer = window.setTimeout(connect, 5000)
      }
    }

    fetchHighWinSignals().catch(() => {
      // Initial fallback fetch.
    })
    connect()

    return () => {
      mounted = false
      stopFallback()
      if (reconnectTimer != null) window.clearTimeout(reconnectTimer)
      socket?.close()
    }
  }, [])

  useEffect(() => {
    let socket: WebSocket | null = null
    let reconnectTimer: number | null = null
    let mounted = true

    const connect = () => {
      if (!mounted) return
      setPriceWsStatus('connecting')
      const url = `ws://127.0.0.1:8000/ws/price?symbol=${encodeURIComponent(selectedCoin)}&interval_sec=2`
      socket = new WebSocket(url)

      socket.onopen = () => {
        if (!mounted) return
        setPriceWsStatus('live')
      }

      socket.onmessage = (event) => {
        if (!mounted) return
        try {
          const msg = JSON.parse(event.data) as PriceStreamMessage
          if (msg.type === 'price' && typeof msg.price === 'number') {
            setMarketPrice(msg.price)
            setMarketPriceTime(msg.timestamp ?? null)
            return
          }
          if (msg.type === 'price_error') {
            setPriceWsStatus('fallback')
          }
        } catch {
          // ignore invalid payload
        }
      }

      socket.onerror = () => {
        if (!mounted) return
        setPriceWsStatus('fallback')
      }

      socket.onclose = () => {
        if (!mounted) return
        setPriceWsStatus('fallback')
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
          <button type="button" onClick={() => setShowPaperScreen((v) => !v)}>
            {showPaperScreen ? 'Back To Main Screen' : 'Open Paper Trade Stats'}
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
            <div className="stats-item"><strong>Total PnL:</strong> {paperStats?.total_pnl?.toFixed(6) ?? '0.000000'}</div>
            <div className="stats-item"><strong>Avg PnL:</strong> {paperStats?.avg_pnl?.toFixed(6) ?? '0.000000'}</div>
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
                    <th>Side</th>
                    <th>Entry</th>
                    <th>TP</th>
                    <th>SL</th>
                    <th>Signal%</th>
                    <th>Effective%</th>
                  </tr>
                </thead>
                <tbody>
                  {paperOpenTrades.map((row) => (
                    <tr key={row.id}>
                      <td>{row.id}</td>
                      <td>{row.symbol}</td>
                      <td>{row.side}</td>
                      <td>{row.entry_price}</td>
                      <td>{row.take_profit}</td>
                      <td>{row.stop_loss}</td>
                      <td>{(row.signal_win_probability * 100).toFixed(2)}</td>
                      <td>{(row.effective_win_probability * 100).toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
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
                    <th>Side</th>
                    <th>Status</th>
                    <th>Entry</th>
                    <th>Close</th>
                    <th>PnL</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {paperHistory.map((row) => (
                    <tr key={`${row.id}-${row.status}`}>
                      <td>{row.id}</td>
                      <td>{row.symbol}</td>
                      <td>{row.side}</td>
                      <td>{row.status}</td>
                      <td>{row.entry_price}</td>
                      <td>{row.close_price ?? '-'}</td>
                      <td>{row.pnl ?? '-'}</td>
                      <td>{row.result == null ? '-' : row.result === 1 ? 'WIN' : 'LOSS'}</td>
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
          <h2>{selectedCoin} Liquidation Map</h2>
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
            <p><strong>Symbol:</strong> {signal?.symbol ?? selectedCoin}</p>
            <p><strong>Win Probability:</strong> {signal ? `${(signal.win_probability * 100).toFixed(2)}%` : '-'}</p>
            <p><strong>Entry:</strong> {signal?.predicted_entry_price ?? '-'}</p>
            <p><strong>TP:</strong> {signal?.take_profit ?? '-'}</p>
            <p><strong>SL:</strong> {signal?.stop_loss ?? '-'}</p>
            <button onClick={createDemoPendingOrder} disabled={!signal || isLoadingOrder}>
              {isLoadingOrder ? 'Submitting...' : 'Create Demo Pending Order'}
            </button>
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
                      <td>{order.symbol}</td>
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
                  <th>Entry</th>
                  <th>TP</th>
                  <th>SL</th>
                </tr>
              </thead>
              <tbody>
                {highWinSignals.map((item) => (
                  <tr key={`${item.symbol}-${item.side}`}>
                    <td>
                      <button
                        type="button"
                        className="symbol-jump"
                        onClick={() => openSymbolOnChart(item.symbol, item.predicted_entry_price)}
                        title={`Open ${item.symbol} on liquidation chart`}
                      >
                        {item.symbol}
                      </button>
                    </td>
                    <td>{item.side}</td>
                    <td>{(item.win_probability * 100).toFixed(2)}</td>
                    <td>{item.predicted_entry_price}</td>
                    <td>{item.take_profit}</td>
                    <td>{item.stop_loss}</td>
                  </tr>
                ))}
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
            <p><strong>Updated:</strong> {health?.timestamp ?? '-'}</p>
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
            <p><strong>Price Time:</strong> {marketPriceTime ?? '-'}</p>
            <p><strong>Connection:</strong> {priceWsStatus}</p>
          </div>
        </article>
      </section>

      {error ? <p className="error">{error}</p> : null}
    </main>
  )
}

export default App
