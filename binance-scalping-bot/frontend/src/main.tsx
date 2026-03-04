import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

function patchPerformanceMeasure(): void {
  if (typeof window === 'undefined' || !window.performance) return

  const perf = window.performance as Performance & { __safeMeasurePatched?: boolean }
  if (perf.__safeMeasurePatched) return

  const proto = Object.getPrototypeOf(perf) as Performance
  const originalMeasure = proto.measure
  if (typeof originalMeasure !== 'function') return

  const safeMeasure: Performance['measure'] = function (
    this: Performance,
    ...args: Parameters<Performance['measure']>
  ): ReturnType<Performance['measure']> {
    try {
      return originalMeasure.apply(this, args as unknown as [name: string, startOrMeasureOptions?: string | PerformanceMeasureOptions, endMark?: string])
    } catch (err) {
      if (err instanceof DOMException && err.name === 'DataCloneError') {
        const [name, startOrOptions, endMark] = args
        if (startOrOptions && typeof startOrOptions === 'object' && !Array.isArray(startOrOptions)) {
          const { detail: _ignoredDetail, ...rest } = startOrOptions as PerformanceMeasureOptions & { detail?: unknown }
          try {
            if (typeof endMark === 'string') {
              return originalMeasure.call(this, name, rest, endMark)
            }
            return originalMeasure.call(this, name, rest)
          } catch {
            return undefined as unknown as ReturnType<Performance['measure']>
          }
        }
        return undefined as unknown as ReturnType<Performance['measure']>
      }
      throw err
    }
  }

  try {
    Object.defineProperty(proto, 'measure', {
      configurable: true,
      writable: true,
      value: safeMeasure,
    })
    perf.__safeMeasurePatched = true
  } catch {
    // Ignore if browser disallows redefining Performance API methods.
  }
}

patchPerformanceMeasure()

createRoot(document.getElementById('root')!).render(
  <App />,
)
