export type LiquidMapPalette = {
  id: string
  name: string
  stops: Array<[number, number, number, number]>
}

export type LiquidMapHeatmapData = {
  rows: number
  cols: number
  currentCol: number
  values: Float32Array
  priceSeries: number[]
  minPrice: number
  maxPrice: number
}

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

export const DEFAULT_LIQUID_MAP_PALETTE: LiquidMapPalette = {
  id: 'coinglassish',
  name: 'Blue-Yellow',
  stops: [
    [0, 43, 5, 72],
    [0.35, 42, 58, 123],
    [0.58, 76, 165, 175],
    [0.78, 179, 224, 81],
    [1, 246, 244, 86],
  ],
}

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

export function samplePaletteColor(palette: LiquidMapPalette, t: number): [number, number, number] {
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

export function buildLiquidationHeatmap(
  coin: string,
  basePrice: number,
  threshold: number,
  timeframe: string,
  pastSeriesInput: number[],
): LiquidMapHeatmapData {
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
      const centerPrice = band.centerStart + (band.centerEnd - band.centerStart) * t
      const edge = Math.sin(Math.PI * t) ** 0.55
      const progressBoost = (0.86 + ((x / (cols - 1)) * 0.24)) * edge
      const centerY = ((maxPrice - centerPrice) / (maxPrice - minPrice)) * (rows - 1)
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

export function calcLiquidMapConfluence(
  heatmap: LiquidMapHeatmapData,
  zoneLow: number,
  zoneHigh: number,
): {
  peakIntensity: number
  avgIntensity: number
  confluenceScore: number
  hotspotPrice: number | null
  hotspotDistancePct: number | null
} {
  if (zoneLow <= 0 || zoneHigh <= 0 || heatmap.maxPrice <= heatmap.minPrice) {
    return {
      peakIntensity: 0,
      avgIntensity: 0,
      confluenceScore: 0,
      hotspotPrice: null,
      hotspotDistancePct: null,
    }
  }

  const low = Math.min(zoneLow, zoneHigh)
  const high = Math.max(zoneLow, zoneHigh)
  const priceSpan = heatmap.maxPrice - heatmap.minPrice
  const zoneTopRow = Math.max(0, Math.floor(((heatmap.maxPrice - high) / priceSpan) * (heatmap.rows - 1)))
  const zoneBottomRow = Math.min(heatmap.rows - 1, Math.ceil(((heatmap.maxPrice - low) / priceSpan) * (heatmap.rows - 1)))

  let peak = 0
  let sum = 0
  let count = 0
  let bestRow = -1
  let bestCol = -1

  for (let col = heatmap.currentCol; col < heatmap.cols; col += 1) {
    for (let row = zoneTopRow; row <= zoneBottomRow; row += 1) {
      const intensity = heatmap.values[(row * heatmap.cols) + col] ?? 0
      sum += intensity
      count += 1
      if (intensity > peak) {
        peak = intensity
        bestRow = row
        bestCol = col
      }
    }
  }

  const avg = count > 0 ? sum / count : 0
  const rawScore = (peak * 0.72) + (avg * 0.28)
  const confluenceScore = clamp(rawScore * 100, 0, 100)

  let hotspotPrice: number | null = null
  let hotspotDistancePct: number | null = null
  if (bestRow >= 0 && bestCol >= 0) {
    hotspotPrice = heatmap.maxPrice - ((bestRow / (heatmap.rows - 1)) * priceSpan)
    const futureCols = Math.max(1, heatmap.cols - 1 - heatmap.currentCol)
    hotspotDistancePct = ((bestCol - heatmap.currentCol) / futureCols) * 100
  }

  return {
    peakIntensity: peak,
    avgIntensity: avg,
    confluenceScore,
    hotspotPrice,
    hotspotDistancePct,
  }
}
