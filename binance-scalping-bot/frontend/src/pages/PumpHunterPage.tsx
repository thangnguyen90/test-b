import { useEffect, useMemo, useRef, useState } from 'react'
import './PumpHunterPage.css'
import {
  buildLiquidationHeatmap,
  DEFAULT_LIQUID_MAP_PALETTE,
  samplePaletteColor,
  type LiquidMapHeatmapData,
} from '../lib/liquidationMapModel'

const API_BASE = 'http://127.0.0.1:8000'
const WS_BASE = API_BASE.replace(/^http/, 'ws')
const REFRESH_MS = 20_000
const LIQ_MAP_THRESHOLD = 0.62
const LIQ_MAP_TIMEFRAME = '12h'
const LIVE_TOP_SYMBOLS = 24

type PumpHunterItem = {
  symbol: string
  mark_price: number
  pump_score: number
  effective_score: number
  stage: string
  signal_label: string
  setup_quality: string
  volume_ratio_5m: number
  volume_ratio_15m: number
  volume_z_15m: number
  breakout_pct_20: number
  breakout_pct_55: number
  momentum_pct_3: number
  momentum_pct_12: number
  expansion_ratio: number
  close_in_range: number
  body_pct: number
  ema_stack_gap_pct: number
  above_ema_stack: boolean
  est_liq_target_price: number
  est_liq_target_low: number
  est_liq_target_high: number
  est_liq_distance_pct: number
  est_liq_score: number
  invalidation_price: number
  risk_pct: number
  reward_pct: number
  rr_ratio: number
  swept_recently: boolean
  entry_ready: boolean
  rejection_score: number
  sweep_distance_pct?: number | null
  notes: string[]
  ticker_quote_volume: number
  ticker_change_pct_24h: number
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

type MarketPricesBatchResponse = {
  prices?: Record<string, number>
  timestamp?: string | null
  timestamps?: Record<string, string>
}

type PumpCandle = {
  timestamp: number
  open: number
  high: number
  low: number
  close: number
  volume: number
  ema13: number
  ema25: number
  ema99: number
}

type PumpHunterDetail = PumpHunterItem & {
  candles: PumpCandle[]
}

type PumpConfluenceMetrics = {
  liq_confluence_score: number
  combined_score: number
  liq_peak_intensity: number
  liq_avg_intensity: number
  liq_hotspot_price: number | null
  liq_hotspot_distance_pct: number | null
}

type PumpHunterDisplayItem = PumpHunterItem & PumpConfluenceMetrics
type PumpHunterDetailView = PumpHunterDetail & PumpConfluenceMetrics
type PumpHunterSelectedView = PumpHunterDisplayItem & {
  candles?: PumpCandle[]
}

function formatNumber(value?: number | null, digits = 4): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  return value.toFixed(digits)
}

function formatPct(value?: number | null, digits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`
}

function formatCompact(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  return new Intl.NumberFormat('en-US', {
    notation: 'compact',
    maximumFractionDigits: 2,
  }).format(value)
}

function formatTime(value?: string | null): string {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('vi-VN', {
    dateStyle: 'short',
    timeStyle: 'medium',
    timeZone: 'Asia/Ho_Chi_Minh',
  }).format(date)
}

function applyLivePrice(item: PumpHunterDisplayItem, livePrice?: number): PumpHunterDisplayItem {
  if (typeof livePrice !== 'number' || !Number.isFinite(livePrice) || livePrice <= 0) return item
  const target = item.est_liq_target_price
  const nextDistance = target > 0 ? ((target - livePrice) / livePrice) * 100.0 : item.est_liq_distance_pct
  const nextHotspotDistance = item.liq_hotspot_price && item.liq_hotspot_price > 0
    ? ((item.liq_hotspot_price - livePrice) / livePrice) * 100.0
    : item.liq_hotspot_distance_pct
  return {
    ...item,
    mark_price: livePrice,
    est_liq_distance_pct: Number.isFinite(nextDistance) ? nextDistance : item.est_liq_distance_pct,
    liq_hotspot_distance_pct: nextHotspotDistance != null && Number.isFinite(nextHotspotDistance)
      ? nextHotspotDistance
      : item.liq_hotspot_distance_pct,
  }
}

function getPumpTradePlan(item: PumpHunterDisplayItem | PumpHunterSelectedView) {
  const isPostSweep = item.signal_label === 'ENTER' || item.signal_label === 'SWEEPED' || item.stage === 'POST_SWEEP' || item.stage === 'SWEEPED'
  if (isPostSweep) {
    return {
      side: 'SHORT' as const,
      entryPrice: item.mark_price,
      takeProfit: item.invalidation_price,
      stopLoss: item.est_liq_target_high,
    }
  }
  return {
    side: 'LONG' as const,
    entryPrice: item.mark_price,
    takeProfit: item.est_liq_target_price,
    stopLoss: item.invalidation_price,
  }
}

function buildLinePath(
  candles: PumpCandle[],
  field: keyof Pick<PumpCandle, 'ema13' | 'ema25' | 'ema99'>,
  width: number,
  top: number,
  chartHeight: number,
  minPrice: number,
  maxPrice: number,
): string {
  if (candles.length === 0 || maxPrice <= minPrice) return ''
  const innerWidth = width - 88
  const step = innerWidth / Math.max(1, candles.length - 1)
  return candles.map((candle, index) => {
    const x = 56 + (step * index)
    const ratio = (candle[field] - minPrice) / (maxPrice - minPrice)
    const y = top + chartHeight - (ratio * chartHeight)
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`
  }).join(' ')
}

function toFallbackDisplayItem(item: PumpHunterItem): PumpHunterDisplayItem {
  return {
    ...item,
    liq_confluence_score: 0,
    combined_score: item.effective_score ?? item.pump_score,
    liq_peak_intensity: 0,
    liq_avg_intensity: 0,
    liq_hotspot_price: null,
    liq_hotspot_distance_pct: null,
  }
}

function PumpChart({ detail }: { detail: PumpHunterDetailView }) {
  const candles = detail.candles ?? []
  const width = 920
  const priceTop = 24
  const priceHeight = 300
  const volumeTop = 344
  const volumeHeight = 82
  const fullHeight = 444

  const priceBounds = useMemo(() => {
    const prices = candles.flatMap((candle) => [candle.low, candle.high, candle.ema13, candle.ema25, candle.ema99])
    prices.push(detail.est_liq_target_low, detail.est_liq_target_high, detail.invalidation_price, detail.mark_price)
    const safe = prices.filter((value) => Number.isFinite(value) && value > 0)
    const min = Math.min(...safe)
    const max = Math.max(...safe)
    const padding = (max - min) * 0.08 || detail.mark_price * 0.03 || 1
    return {
      minPrice: Math.max(0, min - padding),
      maxPrice: max + padding,
      maxVolume: Math.max(1, ...candles.map((candle) => candle.volume)),
    }
  }, [candles, detail.est_liq_target_high, detail.est_liq_target_low, detail.invalidation_price, detail.mark_price])

  const step = (width - 88) / Math.max(1, candles.length)
  const candleWidth = Math.max(4, Math.min(10, step * 0.62))

  const yForPrice = (value: number): number => {
    if (priceBounds.maxPrice <= priceBounds.minPrice) return priceTop + priceHeight / 2
    const ratio = (value - priceBounds.minPrice) / (priceBounds.maxPrice - priceBounds.minPrice)
    return priceTop + priceHeight - (ratio * priceHeight)
  }

  const zoneY = yForPrice(detail.est_liq_target_high)
  const zoneHeight = Math.max(6, yForPrice(detail.est_liq_target_low) - zoneY)
  const currentY = yForPrice(detail.mark_price)
  const invalidationY = yForPrice(detail.invalidation_price)

  const ema13Path = buildLinePath(candles, 'ema13', width, priceTop, priceHeight, priceBounds.minPrice, priceBounds.maxPrice)
  const ema25Path = buildLinePath(candles, 'ema25', width, priceTop, priceHeight, priceBounds.minPrice, priceBounds.maxPrice)
  const ema99Path = buildLinePath(candles, 'ema99', width, priceTop, priceHeight, priceBounds.minPrice, priceBounds.maxPrice)

  return (
    <svg viewBox={`0 0 ${width} ${fullHeight}`} className="pump-chart-svg" role="img" aria-label={`${detail.symbol} pump chart`}>
      <defs>
        <linearGradient id="pump-chart-bg" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#10151a" />
          <stop offset="100%" stopColor="#161f26" />
        </linearGradient>
      </defs>

      <rect x="0" y="0" width={width} height={fullHeight} rx="26" fill="url(#pump-chart-bg)" />
      <rect x="0" y={volumeTop - 10} width={width} height="1" fill="rgba(152, 176, 196, 0.18)" />
      <rect x="0" y={zoneY} width={width} height={zoneHeight} fill="rgba(255, 117, 87, 0.12)" />

      {[0.2, 0.4, 0.6, 0.8].map((ratio) => (
        <line
          key={ratio}
          x1="42"
          x2={width - 18}
          y1={priceTop + (priceHeight * ratio)}
          y2={priceTop + (priceHeight * ratio)}
          stroke="rgba(152, 176, 196, 0.12)"
        />
      ))}

      <path d={ema99Path} fill="none" stroke="#6f8be8" strokeWidth="2" strokeOpacity="0.85" />
      <path d={ema25Path} fill="none" stroke="#ff6d8a" strokeWidth="2.2" strokeOpacity="0.92" />
      <path d={ema13Path} fill="none" stroke="#ffcb66" strokeWidth="2.2" strokeOpacity="0.9" />

      {candles.map((candle, index) => {
        const x = 56 + (step * index)
        const wickY1 = yForPrice(candle.high)
        const wickY2 = yForPrice(candle.low)
        const openY = yForPrice(candle.open)
        const closeY = yForPrice(candle.close)
        const bodyY = Math.min(openY, closeY)
        const bodyHeight = Math.max(2, Math.abs(closeY - openY))
        const isUp = candle.close >= candle.open
        const volumeHeightPx = (candle.volume / priceBounds.maxVolume) * volumeHeight
        return (
          <g key={candle.timestamp}>
            <line x1={x} x2={x} y1={wickY1} y2={wickY2} stroke={isUp ? '#5ee4a2' : '#ff7f84'} strokeWidth="1.7" />
            <rect
              x={x - (candleWidth / 2)}
              y={bodyY}
              width={candleWidth}
              height={bodyHeight}
              rx="2"
              fill={isUp ? '#29d68a' : '#ff646f'}
            />
            <rect
              x={x - (Math.max(3, candleWidth - 2) / 2)}
              y={volumeTop + volumeHeight - volumeHeightPx}
              width={Math.max(3, candleWidth - 2)}
              height={Math.max(2, volumeHeightPx)}
              rx="2"
              fill={isUp ? 'rgba(41, 214, 138, 0.78)' : 'rgba(255, 100, 111, 0.78)'}
            />
          </g>
        )
      })}

      <line x1="26" x2={width - 18} y1={currentY} y2={currentY} stroke="#8cd7ff" strokeDasharray="6 6" strokeWidth="1.4" />
      <line x1="26" x2={width - 18} y1={invalidationY} y2={invalidationY} stroke="#ffd166" strokeDasharray="5 5" strokeWidth="1.2" />
      <line x1="26" x2={width - 18} y1={yForPrice(detail.est_liq_target_price)} y2={yForPrice(detail.est_liq_target_price)} stroke="#ff7b72" strokeDasharray="2 5" strokeWidth="1.5" />

      <text x={width - 16} y={currentY - 6} textAnchor="end" className="pump-chart-label">Now {formatNumber(detail.mark_price, 4)}</text>
      <text x={width - 16} y={zoneY - 8} textAnchor="end" className="pump-chart-label">Kill zone {formatNumber(detail.est_liq_target_low, 4)} - {formatNumber(detail.est_liq_target_high, 4)}</text>
      <text x={width - 16} y={invalidationY - 6} textAnchor="end" className="pump-chart-label">Invalidation {formatNumber(detail.invalidation_price, 4)}</text>
      <text x="24" y="18" className="pump-chart-title">{detail.symbol} 15m</text>
    </svg>
  )
}

function LiquidMapPanel({
  detail,
  heatmap,
}: {
  detail: PumpHunterDetailView
  heatmap: LiquidMapHeatmapData
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const { rows, cols, values } = heatmap
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
      const [r, g, b] = samplePaletteColor(DEFAULT_LIQUID_MAP_PALETTE, values[i] ?? 0)
      const offset = i * 4
      imageData.data[offset] = r
      imageData.data[offset + 1] = g
      imageData.data[offset + 2] = b
      imageData.data[offset + 3] = 255
    }
    ctx.putImageData(imageData, 0, 0)

    const scaleY = (price: number) => ((heatmap.maxPrice - price) / (heatmap.maxPrice - heatmap.minPrice)) * (rows - 1)
    const currentY = scaleY(detail.mark_price)
    const zoneTop = scaleY(detail.est_liq_target_high)
    const zoneBottom = scaleY(detail.est_liq_target_low)

    ctx.strokeStyle = 'rgba(255, 255, 255, 0.95)'
    ctx.setLineDash([4, 4])
    ctx.beginPath()
    ctx.moveTo(0, currentY)
    ctx.lineTo(cols, currentY)
    ctx.stroke()

    ctx.setLineDash([])
    ctx.fillStyle = 'rgba(255, 123, 114, 0.18)'
    ctx.fillRect(0, zoneTop, cols, Math.max(4, zoneBottom - zoneTop))

    const points = heatmap.priceSeries
    ctx.beginPath()
    ctx.strokeStyle = '#ff707f'
    ctx.lineWidth = 1.2
    for (let x = 0; x < points.length; x += 1) {
      const y = scaleY(points[x])
      if (x === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    }
    ctx.stroke()
  }, [detail, heatmap])

  return (
    <div className="pump-liquid-map-card">
      <div className="pump-liquid-map-header">
        <div>
          <h3>Đồng Thuận Liquid Map</h3>
          <p>Dùng cùng logic với liquid map đang có: ngưỡng {LIQ_MAP_THRESHOLD} | khung {LIQ_MAP_TIMEFRAME}</p>
        </div>
        <div className="pump-liquid-score-box">
          <span>Đồng thuận</span>
          <strong>{detail.liq_confluence_score.toFixed(1)}</strong>
        </div>
      </div>
      <canvas ref={canvasRef} className="pump-liquid-map-canvas" />
      <div className="pump-liquid-map-meta">
        <span>Đỉnh {detail.liq_peak_intensity.toFixed(3)}</span>
        <span>TB {detail.liq_avg_intensity.toFixed(3)}</span>
        <span>Hotspot {detail.liq_hotspot_price ? formatNumber(detail.liq_hotspot_price, 4) : '-'}</span>
        <span>Khoảng cách tương lai {detail.liq_hotspot_distance_pct != null ? `${detail.liq_hotspot_distance_pct.toFixed(1)}%` : '-'}</span>
      </div>
    </div>
  )
}

export default function PumpHunterPage() {
  const [scan, setScan] = useState<PumpHunterScanResponse | null>(null)
  const [detail, setDetail] = useState<PumpHunterSelectedView | null>(null)
  const [selectedSymbol, setSelectedSymbol] = useState<string>('')
  const [minScore, setMinScore] = useState(58)
  const [maxSymbols, setMaxSymbols] = useState(35)
  const [limit, setLimit] = useState(18)
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [loadingScan, setLoadingScan] = useState(false)
  const [error, setError] = useState('')
  const [livePrices, setLivePrices] = useState<Record<string, number>>({})
  const [livePriceTime, setLivePriceTime] = useState<Record<string, string>>({})
  const [priceWsStatus, setPriceWsStatus] = useState<'connecting' | 'live' | 'fallback'>('fallback')

  const enhancedItems = useMemo(() => {
    const base = (scan?.items ?? []).map((item) => applyLivePrice(toFallbackDisplayItem(item), livePrices[item.symbol]))
    base.sort((a, b) => {
      if (b.combined_score !== a.combined_score) return b.combined_score - a.combined_score
      return b.pump_score - a.pump_score
    })
    return base
  }, [scan, livePrices])

  const liveSymbols = useMemo(
    () => (scan?.items ?? []).slice(0, Math.min(LIVE_TOP_SYMBOLS, Math.max(limit, 1))).map((item) => item.symbol),
    [scan, limit],
  )
  const liveSymbolsKey = useMemo(() => liveSymbols.join(','), [liveSymbols])

  const fetchScan = async () => {
    try {
      setLoadingScan(true)
      setError('')
      const params = new URLSearchParams({
        max_symbols: String(maxSymbols),
        min_score: String(minScore),
        limit: String(limit),
      })
      const response = await fetch(`${API_BASE}/api/v1/analytics/pump-hunter?${params.toString()}`)
      if (!response.ok) {
        throw new Error(`Pump hunter scan failed (${response.status})`)
      }
      const payload = await response.json() as PumpHunterScanResponse
      setScan(payload)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unknown error')
    } finally {
      setLoadingScan(false)
    }
  }

  useEffect(() => {
    fetchScan().catch(() => undefined)
  }, [minScore, maxSymbols, limit])

  useEffect(() => {
    if (!autoRefresh) return () => undefined
    const timer = window.setInterval(() => {
      fetchScan().catch(() => undefined)
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
      const response = await fetch(
        `${API_BASE}/api/v1/market/prices?symbols=${encodeURIComponent(liveSymbols.join(','))}`,
      )
      if (!response.ok) throw new Error(`Cannot fetch pump hunter live prices (${response.status})`)
      const data = await response.json() as MarketPricesBatchResponse
      if (!mounted) return
      setLivePrices((prev) => ({ ...prev, ...(data.prices ?? {}) }))
      if (data.timestamps && Object.keys(data.timestamps).length > 0) {
        setLivePriceTime((prev) => ({ ...prev, ...data.timestamps! }))
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
        fetchBatchFallback().catch(() => {
          // keep last known live prices
        })
      }, 2000)
    }

    const connect = () => {
      if (!mounted) return
      setPriceWsStatus('connecting')
      const wsUrl = `${WS_BASE}/ws/prices?symbols=${encodeURIComponent(liveSymbols.join(','))}&interval_sec=1`
      socket = new WebSocket(wsUrl)

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
          // ignore malformed websocket payload
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

    fetchBatchFallback().catch(() => {
      // websocket may still connect
    })
    connect()

    return () => {
      mounted = false
      if (reconnectTimer != null) window.clearTimeout(reconnectTimer)
      stopFallback()
      socket?.close()
    }
  }, [liveSymbolsKey])

  useEffect(() => {
    const first = enhancedItems[0]?.symbol ?? scan?.items?.[0]?.symbol ?? ''
    if (!selectedSymbol && first) {
      setSelectedSymbol(first)
      return
    }
    if (selectedSymbol && enhancedItems.some((item) => item.symbol === selectedSymbol)) return
    if (first) setSelectedSymbol(first)
  }, [enhancedItems, scan, selectedSymbol])

  useEffect(() => {
    if (!selectedSymbol) return () => undefined
    const selected = enhancedItems.find((item) => item.symbol === selectedSymbol)
      ?? scan?.items.find((item) => item.symbol === selectedSymbol)
    if (selected) {
      setDetail(toFallbackDisplayItem(selected))
    }
    return () => undefined
  }, [enhancedItems, scan, selectedSymbol])

  const summary = useMemo(() => {
    const items = enhancedItems.length > 0 ? enhancedItems : (scan?.items ?? []).map((item) => toFallbackDisplayItem(item))
    if (items.length === 0) {
      return {
        avgScore: 0,
        avgVol: 0,
        avgCombined: 0,
        avgConfluence: 0,
        nearSweep: 0,
      }
    }
    const avgScore = items.reduce((sum, item) => sum + item.pump_score, 0) / items.length
    const avgVol = items.reduce((sum, item) => sum + item.volume_ratio_15m, 0) / items.length
    const avgCombined = items.reduce((sum, item) => sum + item.combined_score, 0) / items.length
    const avgConfluence = items.reduce((sum, item) => sum + item.liq_confluence_score, 0) / items.length
    const nearSweep = items.filter((item) => item.stage === 'NEAR_SWEEP').length
    return { avgScore, avgVol, avgCombined, avgConfluence, nearSweep }
  }, [enhancedItems, scan])

  const detailHeatmap = useMemo(() => {
    if (!detail || !detail.candles || detail.candles.length === 0) return null
    const closeSeries = detail.candles.map((candle) => candle.close)
    return buildLiquidationHeatmap(
      detail.symbol,
      detail.mark_price,
      LIQ_MAP_THRESHOLD,
      LIQ_MAP_TIMEFRAME,
      closeSeries,
    )
  }, [detail])

  return (
    <main className="pump-page-shell">
      <section className="pump-hero">
        <div>
          <p className="pump-eyebrow">Pump Hunter</p>
          <h1>Bộ quét coin đang manh nha bơm và đồng thuận với liquid map</h1>
          <p className="pump-subtext">
            Trang này đã ghép thêm liquid map đang có để coin chỉ lên top khi vừa có volume breakout, vừa có vùng thanh lý phía trên trùng với target pump.
          </p>
        </div>
        <div className="pump-hero-actions">
          <button type="button" onClick={() => { fetchScan().catch(() => undefined) }}>
            {loadingScan ? 'Đang quét...' : 'Quét lại ngay'}
          </button>
          <button
            type="button"
            className="pump-btn-secondary"
            onClick={() => {
              window.location.search = ''
            }}
          >
            Về dashboard cũ
          </button>
        </div>
      </section>

      <section className="pump-toolbar">
        <label className="pump-field">
          <span>Điểm tối thiểu</span>
          <input type="number" min="0" max="100" value={minScore} onChange={(event) => setMinScore(Number(event.target.value) || 0)} />
        </label>
        <label className="pump-field">
          <span>Quét tối đa</span>
          <input type="number" min="0" max="500" value={maxSymbols} onChange={(event) => setMaxSymbols(Number(event.target.value) || 0)} />
        </label>
        <label className="pump-field">
          <span>Lấy top</span>
          <input type="number" min="1" max="100" value={limit} onChange={(event) => setLimit(Number(event.target.value) || 18)} />
        </label>
        <div className="pump-toggle pump-inline-actions">
          <span>Chế độ nhanh</span>
          <div className="pump-inline-buttons">
            <button type="button" className="pump-btn-secondary" onClick={() => setMaxSymbols(0)}>MAX</button>
            <button type="button" className="pump-btn-secondary" onClick={() => setMaxSymbols(80)}>80</button>
          </div>
        </div>
        <label className="pump-toggle">
          <input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} />
          <span>Tự làm mới 20 giây</span>
        </label>
        <div className="pump-update-box">
          <strong>Cập nhật</strong>
          <span>{formatTime(scan?.updated_at)}</span>
          <span className={`pump-ws-chip pump-ws-${priceWsStatus}`}>
            {priceWsStatus === 'live' ? 'Giá WS live' : priceWsStatus === 'connecting' ? 'WS đang nối' : 'REST fallback'}
          </span>
        </div>
      </section>

      {scan?.note ? (
        <section className={`pump-note-banner ${scan.note.toLowerCase().includes('khong lay duoc') || scan.note.toLowerCase().includes('tam thoi') ? 'pump-note-warn' : ''}`}>
          {scan.note}
        </section>
      ) : null}

      <section className="pump-summary-grid">
        <article className="pump-summary-card">
          <span>Đã quét</span>
          <strong>{scan?.scanned ?? 0}</strong>
        </article>
        <article className="pump-summary-card">
          <span>Chế độ quét</span>
          <strong>{(scan?.max_symbols ?? maxSymbols) === 0 ? 'MAX' : scan?.max_symbols ?? maxSymbols}</strong>
        </article>
        <article className="pump-summary-card">
          <span>Số kèo đạt</span>
          <strong>{scan?.count ?? 0}</strong>
        </article>
        <article className="pump-summary-card">
          <span>Điểm pump TB</span>
          <strong>{summary.avgScore.toFixed(1)}</strong>
        </article>
        <article className="pump-summary-card">
          <span>Điểm liq map TB</span>
          <strong>{summary.avgConfluence.toFixed(1)}</strong>
        </article>
        <article className="pump-summary-card">
          <span>Điểm tổng hợp TB</span>
          <strong>{summary.avgCombined.toFixed(1)}</strong>
        </article>
        <article className="pump-summary-card">
          <span>Vol spike 15m TB</span>
          <strong>x{summary.avgVol.toFixed(2)}</strong>
        </article>
        <article className="pump-summary-card">
          <span>Sắp quét</span>
          <strong>{summary.nearSweep}</strong>
        </article>
      </section>

      <section className="pump-content-grid">
        <div className="pump-list-panel">
          <div className="pump-panel-header">
            <div>
              <h2>Danh sách ứng viên</h2>
              <p>{scan?.note ?? 'Đang nạp bộ quét...'}</p>
            </div>
            <span className="pump-enhance-note">Đang hiển thị nhanh theo scan để tránh lỗi detail</span>
          </div>

          <div className="pump-table-wrap">
            <table className="pump-table">
              <thead>
                <tr>
                  <th>Mã</th>
                  <th>Loại tín hiệu</th>
                  <th>Side</th>
                  <th>Tổng hợp</th>
                  <th>Pump</th>
                  <th>Liq Map</th>
                  <th>Giai đoạn</th>
                  <th>Entry</th>
                  <th>TP</th>
                  <th>Vol 15m</th>
                  <th>Vùng quét</th>
                  <th>Khoảng cách</th>
                </tr>
              </thead>
              <tbody>
                {enhancedItems.map((item) => (
                  (() => {
                    const tradePlan = getPumpTradePlan(item)
                    return (
                  <tr
                    key={item.symbol}
                    className={selectedSymbol === item.symbol ? 'pump-row-active' : ''}
                    onClick={() => setSelectedSymbol(item.symbol)}
                  >
                    <td>
                      <div className="pump-symbol-cell">
                        <strong>{item.symbol}</strong>
                        <span className={`pump-grade pump-grade-${item.signal_label.toLowerCase()}`}>{item.signal_label}</span>
                      </div>
                    </td>
                    <td>
                      <span className={`pump-grade pump-grade-${item.signal_label.toLowerCase()}`}>
                        {item.signal_label}
                      </span>
                    </td>
                    <td>
                      <span className={tradePlan.side === 'LONG' ? 'pump-side-long' : 'pump-side-short'}>
                        {tradePlan.side}
                      </span>
                    </td>
                    <td>{item.combined_score.toFixed(1)}</td>
                    <td>{item.pump_score.toFixed(1)}</td>
                    <td>{item.liq_confluence_score.toFixed(1)}</td>
                    <td><span className="pump-stage-pill">{item.stage}</span></td>
                    <td>{formatNumber(tradePlan.entryPrice, 4)}</td>
                    <td>{formatNumber(tradePlan.takeProfit, 4)}</td>
                    <td>x{item.volume_ratio_15m.toFixed(2)}</td>
                    <td>{formatNumber(item.est_liq_target_price, 4)}</td>
                    <td>
                      <div className="pump-distance-cell">
                        <span>{formatPct(item.est_liq_distance_pct)}</span>
                        <small>{formatNumber(item.mark_price, 4)}</small>
                      </div>
                    </td>
                  </tr>
                    )
                  })()
                ))}
              </tbody>
            </table>
            {!loadingScan && enhancedItems.length === 0 ? (
              <div className="pump-empty">Chưa có coin đạt bộ lọc hiện tại.</div>
            ) : null}
          </div>
        </div>

        <div className="pump-detail-panel">
          <div className="pump-panel-header">
            <div>
              <h2>{detail?.symbol ?? (selectedSymbol || 'Chọn coin')}</h2>
              <p>
                {detail
                  ? `${detail.signal_label} | Tổng hợp ${detail.combined_score.toFixed(1)} | Pump ${detail.pump_score.toFixed(1)} | Liq map ${detail.liq_confluence_score.toFixed(1)}`
                  : 'Chọn một coin để xem chart và độ đồng thuận liquid map'}
              </p>
            </div>
          </div>

          {detail ? (
            <>
              {(() => {
                const tradePlan = getPumpTradePlan(detail)
                return (
              <div className="pump-kpi-grid">
                <article className="pump-kpi-card">
                  <span>Giá live</span>
                  <strong>{formatNumber(detail.mark_price, 4)}</strong>
                  <em>{formatTime(livePriceTime[detail.symbol])}</em>
                </article>
                <article className="pump-kpi-card">
                  <span>Hướng lệnh</span>
                  <strong>{tradePlan.side}</strong>
                </article>
                <article className="pump-kpi-card">
                  <span>Tín hiệu</span>
                  <strong>{detail.signal_label}</strong>
                </article>
                <article className="pump-kpi-card">
                  <span>Điểm tổng hợp</span>
                  <strong>{detail.combined_score.toFixed(1)}</strong>
                </article>
                <article className="pump-kpi-card">
                  <span>Điểm pump</span>
                  <strong>{detail.pump_score.toFixed(1)}</strong>
                </article>
                <article className="pump-kpi-card">
                  <span>Điểm liq map</span>
                  <strong>{detail.liq_confluence_score.toFixed(1)}</strong>
                </article>
                <article className="pump-kpi-card">
                  <span>Mục tiêu quét</span>
                  <strong>{formatNumber(detail.est_liq_target_price, 4)}</strong>
                </article>
                <article className="pump-kpi-card">
                  <span>Entry đề xuất</span>
                  <strong>{formatNumber(tradePlan.entryPrice, 4)}</strong>
                </article>
                <article className="pump-kpi-card">
                  <span>TP đề xuất</span>
                  <strong>{formatNumber(tradePlan.takeProfit, 4)}</strong>
                </article>
                <article className="pump-kpi-card">
                  <span>SL đề xuất</span>
                  <strong>{formatNumber(tradePlan.stopLoss, 4)}</strong>
                </article>
                <article className="pump-kpi-card">
                  <span>Khoảng cách mục tiêu</span>
                  <strong>{formatPct(detail.est_liq_distance_pct)}</strong>
                </article>
                <article className="pump-kpi-card">
                  <span>Quote Vol 24h</span>
                  <strong>{formatCompact(detail.ticker_quote_volume)}</strong>
                </article>
                <article className="pump-kpi-card">
                  <span>Điểm từ chối giá</span>
                  <strong>{detail.rejection_score.toFixed(1)}</strong>
                </article>
              </div>
                )
              })()}

              {detail.candles && detail.candles.length > 0 ? (
                <>
                  <div className="pump-chart-card">
                    <PumpChart detail={detail as PumpHunterDetailView} />
                  </div>

                  {detailHeatmap ? <div className="pump-chart-card"><LiquidMapPanel detail={detail as PumpHunterDetailView} heatmap={detailHeatmap} /></div> : null}
                </>
              ) : (
                <div className="pump-chart-card">
                  <div className="pump-inline-placeholder">
                    Đang hiển thị nhanh từ kết quả scan. Chart chi tiết và liquid map chi tiết tạm thời tắt để tránh request detail lỗi.
                  </div>
                </div>
              )}

              <div className="pump-metrics-grid">
                <article className="pump-metric-block">
                  <h3>Động lực</h3>
                  <div className="pump-metric-row"><span>Tỷ lệ volume 15m</span><strong>x{detail.volume_ratio_15m.toFixed(2)}</strong></div>
                  <div className="pump-metric-row"><span>Tỷ lệ volume 5m</span><strong>x{detail.volume_ratio_5m.toFixed(2)}</strong></div>
                  <div className="pump-metric-row"><span>Volume z 15m</span><strong>{detail.volume_z_15m.toFixed(2)}</strong></div>
                  <div className="pump-metric-row"><span>Động lượng 3 nến</span><strong>{formatPct(detail.momentum_pct_3)}</strong></div>
                  <div className="pump-metric-row"><span>Động lượng 12 nến</span><strong>{formatPct(detail.momentum_pct_12)}</strong></div>
                </article>

                <article className="pump-metric-block">
                  <h3>Đồng thuận liq map</h3>
                  <div className="pump-metric-row"><span>Cường độ đỉnh</span><strong>{detail.liq_peak_intensity.toFixed(3)}</strong></div>
                  <div className="pump-metric-row"><span>Cường độ trung bình</span><strong>{detail.liq_avg_intensity.toFixed(3)}</strong></div>
                  <div className="pump-metric-row"><span>Giá hotspot</span><strong>{detail.liq_hotspot_price ? formatNumber(detail.liq_hotspot_price, 4) : '-'}</strong></div>
                  <div className="pump-metric-row"><span>Khoảng cách hotspot tương lai</span><strong>{detail.liq_hotspot_distance_pct != null ? `${detail.liq_hotspot_distance_pct.toFixed(1)}%` : '-'}</strong></div>
                  <div className="pump-metric-row"><span>Tỷ lệ lời/lỗ</span><strong>{detail.rr_ratio.toFixed(2)}</strong></div>
                  <div className="pump-metric-row"><span>Khoảng cách sweep</span><strong>{detail.sweep_distance_pct != null ? `${detail.sweep_distance_pct.toFixed(2)}%` : '-'}</strong></div>
                </article>
              </div>

              <article className="pump-notes-card">
                <h3>Model ghi nhận</h3>
                <div className="pump-note-list">
                  {detail.notes.map((note) => (
                    <span key={note} className="pump-note-chip">{note}</span>
                  ))}
                  {detail.liq_confluence_score >= 55 ? <span className="pump-note-chip">Liquid map đồng thuận với vùng quét</span> : null}
                  {detail.liq_hotspot_price != null ? (
                    <span className="pump-note-chip">Hotspot giá {formatNumber(detail.liq_hotspot_price, 4)}</span>
                  ) : null}
                </div>
              </article>
            </>
          ) : (
            <div className="pump-placeholder">
              Chọn một dòng bên trái để xem dữ liệu.
            </div>
          )}
        </div>
      </section>

      {error ? <p className="pump-error">{error}</p> : null}
    </main>
  )
}
