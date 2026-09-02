// الشارت — تنفيذ ميتاتريدر لا التحليل.
// التاريخ: CopyRates من الإكسبرت عبر /gov/exec-candles (nq_brain.candles_history).
// الحيّ: تِكّات feed.mt5.tick / brokerMarket فقط. انقطاع النواة لا يمسح الشموع:
// آخر لقطة تُحفظ، والسحب من قاعدة الإكسبرت يستمر طالما الحوكمة تقرأ nq_brain.
import { useEffect, useMemo, useRef, useState } from 'react'
import { createChart, ColorType, type IChartApi, type ISeriesApi, type IPriceLine, type SeriesMarker, type Time } from 'lightweight-charts'
import { useStore } from '../core/store'
import { priceText } from '../core/i18n'

interface Pos {
  ticket: number; symbol: string; side: string; volume: number
  entry_price: number; current_price: number
  stop_loss?: number | null; take_profit?: number | null
  profit?: number | null
}
interface Positions { floating_pnl: number; positions: Pos[] }

const pnlTxt = (n: number) => `${n >= 0 ? '+' : ''}${n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 2 })}`

interface TradeEv {
  event_type: string; ticket: number; side: string; volume: number
  entry_price: number | null; exit_price: number | null
  open_time: number | null; close_time: number | null; reason: string | null
}

const TFS: [number, string][] = [
  [5, '5ث'], [15, '15ث'], [30, '30ث'],
  [60, '1د'], [180, '3د'], [300, '5د'], [900, '15د'], [1800, '30د'], [2700, '45د'],
  [3600, 'ساعة'], [7200, '2س'], [10800, '3س'], [14400, '4س'],
  [86400, 'يوم'], [604800, 'أسبوع'], [2592000, 'شهر'],
]
const EA_WARMUP = 200
const tsec = (ts?: number) => (ts && ts > 1e9 ? (ts > 1e12 ? ts / 1000 : ts) : 0)

interface Candle { time: number; open: number; high: number; low: number; close: number }
interface ExecPayload {
  candles?: Candle[]
  source?: string
  count?: number
  warmup_bars?: number
  last_tick?: { bid: number; ask: number; tick_ms: number } | null
  symbols?: string[]
  ea_db?: boolean
}

let __marketCache: string | null = null
const marketNow = async (): Promise<string> => {
  if (__marketCache) return __marketCache
  try { const r = await fetch('/gov/market', { cache: 'no-store' }); const j = await r.json(); __marketCache = j.market || 'forex' } catch { __marketCache = 'forex' }
  return __marketCache
}
const IV_BY_TF: Record<number, string> = { 60: 'Min1', 300: 'Min5', 900: 'Min15', 1800: 'Min30', 3600: 'Min60', 14400: '4h', 86400: '1d' }

const cacheKey = (sym: string, tf: number) => `nq.execChart.${sym}.${tf}`
function loadCache(sym: string, tf: number): Candle[] {
  try {
    const raw = localStorage.getItem(cacheKey(sym, tf))
    if (!raw) return []
    const cs = JSON.parse(raw) as Candle[]
    return Array.isArray(cs) ? cs.filter((c) => c && c.time > 0 && c.close > 0) : []
  } catch { return [] }
}
function saveCache(sym: string, tf: number, cs: Candle[]) {
  try { localStorage.setItem(cacheKey(sym, tf), JSON.stringify(cs.slice(-400))) } catch { /* حصة التخزين */ }
}

export function ChartPanel({ symbol, tf, tfLabel }: { symbol: string; tf: number; tfLabel?: string }) {
  const boxRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const linesRef = useRef<IPriceLine[]>([])
  const [last, setLast] = useState<number | null>(null)
  const [nc, setNc] = useState(0)
  const [hist, setHist] = useState<'load' | 'ok' | 'empty' | 'held'>('load')
  const posState = useStore((s) => s.streams['platform.positions.state']) as Positions | undefined

  useEffect(() => {
    const box = boxRef.current
    if (!box) return
    const chart = createChart(box, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: '#8a97ad', fontFamily: 'IBM Plex Sans Arabic, sans-serif', attributionLogo: false },
      watermark: { visible: true, text: 'تنفيذ MT5', color: 'rgba(159,184,220,0.10)', fontSize: 22, horzAlign: 'center', vertAlign: 'bottom' },
      grid: { vertLines: { color: 'rgba(159,184,220,0.06)' }, horzLines: { color: 'rgba(159,184,220,0.06)' } },
      rightPriceScale: { borderColor: 'rgba(159,184,220,0.12)' },
      timeScale: { borderColor: 'rgba(159,184,220,0.12)', timeVisible: true, secondsVisible: tf < 60, rightOffset: 4 },
      crosshair: { mode: 0 },
    })
    const series = chart.addCandlestickSeries({
      upColor: '#34d399', downColor: '#fb7185', borderVisible: false, wickUpColor: '#34d399', wickDownColor: '#fb7185',
    })
    chartRef.current = chart; seriesRef.current = series

    let disposed = false
    const cleanup2: Array<() => void> = []
    let cur: Candle | null = null
    let lastTime = 0
    let fitted = false

    const paint = (cs: Candle[], mode: 'ok' | 'held') => {
      if (!cs.length) return
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      series.setData(cs as any)
      cur = { ...cs[cs.length - 1] }; lastTime = cur.time
      setNc(cs.length); setHist(mode); setLast(cur.close)
      if (!fitted) { chart.timeScale().fitContent(); fitted = true }
    }

    const applyTick = (bid: number, ask: number, ts?: number) => {
      const price = (bid + ask) / 2
      const stamp = tsec(ts)
      let bucket = stamp > 0 ? Math.floor(stamp / tf) * tf : (cur ? cur.time : 0)
      if (!bucket) return
      if (bucket < lastTime) bucket = lastTime
      if (!cur || bucket > cur.time) {
        cur = { time: bucket, open: price, high: price, low: price, close: price }
        lastTime = bucket
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        series.update(cur as any); setNc((n) => n + 1)
      } else {
        cur.high = Math.max(cur.high, price); cur.low = Math.min(cur.low, price); cur.close = price
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        series.update({ ...cur } as any)
      }
      setLast(price)
    }

    const cached = loadCache(symbol, tf)
    if (cached.length) paint(cached, 'held')

    const ingest = (d: ExecPayload, fromPoll: boolean) => {
      const cs = d.candles ?? []
      if (cs.length) {
        paint(cs, 'ok')
        saveCache(symbol, tf, cs)
      } else if (!fromPoll && !cached.length) {
        setHist('empty')
      }
      const tick = d.last_tick
      if (tick && tick.bid > 0 && tick.ask >= tick.bid) {
        applyTick(tick.bid, tick.ask, tick.tick_ms)
      }
    }

    void marketNow().then((mkt) => {
      if (disposed) return
      if (mkt === 'crypto') {
        return fetch(`/gov/mexc/klines?symbol=${encodeURIComponent(symbol)}&interval=${IV_BY_TF[tf] || 'Min5'}`, { cache: 'no-store' })
          .then((r) => r.json())
          .then((d: { candles?: Candle[] }) => {
            if (disposed) return
            const cs = d.candles ?? []
            if (cs.length) paint(cs, 'ok')
            else if (!cached.length) setHist('empty')
            const poll = () => fetch(`/gov/mexc/ticker?symbol=${encodeURIComponent(symbol)}`, { cache: 'no-store' })
              .then((r) => r.json())
              .then((t: { bid?: string; ask?: string }) => {
                if (disposed || t.bid == null || t.ask == null) return
                applyTick(parseFloat(t.bid), parseFloat(t.ask))
              }).catch(() => {})
            poll(); const th = setInterval(poll, 15000)
            cleanup2.push(() => clearInterval(th))
          })
      }
      const url = `/gov/exec-candles?symbol=${encodeURIComponent(symbol)}&tf=${tf}&limit=${EA_WARMUP}`
      const pull = (fromPoll: boolean) => fetch(url, { cache: 'no-store' })
        .then((r) => r.json())
        .then((d: ExecPayload) => { if (!disposed) ingest(d, fromPoll) })
        .catch(() => {
          if (disposed) return
          if (cur) setHist('held')
          else if (!cached.length) setHist('empty')
        })
      void pull(false)
      const th = setInterval(() => { void pull(true) }, 2500)
      cleanup2.push(() => clearInterval(th))
      const t0 = useStore.getState().brokerMarket[symbol]
      if (t0) applyTick(t0.bid, t0.ask, t0.ts)
    })

    const unsub = useStore.subscribe((state, prev) => {
      const b = state.brokerMarket[symbol]
      if (b && b !== prev.brokerMarket[symbol]) applyTick(b.bid, b.ask, b.ts)
    })

    return () => { disposed = true; unsub(); chart.remove(); chartRef.current = null; seriesRef.current = null; linesRef.current = []; cleanup2.forEach((f) => f()) }
  }, [symbol, tf])

  const lastOrder = useStore((s) =>
    s.execOrders.find((o) => o.symbol === symbol && o.kind !== 'rejected'))
  useEffect(() => {
    const s = seriesRef.current
    if (!s) return
    linesRef.current.forEach((l) => s.removePriceLine(l)); linesRef.current = []
    const add = (price: number | null | undefined, color: string, style: number, title: string) => {
      if (price == null || !(price > 0)) return
      linesRef.current.push(s.createPriceLine({
        price, color, lineWidth: style === 0 ? 1 : 2, lineStyle: style,
        axisLabelVisible: true, title,
      }))
    }
    for (const p of posState?.positions?.filter((x) => x.symbol === symbol) ?? []) {
      const pr = p.profit
      add(p.entry_price, p.side === 'BUY' ? '#34d399' : '#fb7185', 2,
        `${p.side === 'BUY' ? 'شراء' : 'بيع'} ${p.volume}${pr != null ? ` · ${pnlTxt(pr)}` : ''}`)
      add(p.stop_loss, '#fb7185', 0, 'وقف')
      add(p.take_profit, '#34d399', 0, 'هدف')
    }
    if (lastOrder) {
      add(lastOrder.stop_loss, '#fb7185', 3, 'وقف الأمر')
      add(lastOrder.take_profit, '#34d399', 3, 'هدف الأمر')
    }
  }, [posState, lastOrder, symbol, tf, nc])

  useEffect(() => {
    let stop = false
    const load = () =>
      fetch(`/gov/trades?symbol=${encodeURIComponent(symbol)}&limit=60`)
        .then((r) => r.json())
        .then((d: { trades?: TradeEv[] }) => {
          const s = seriesRef.current
          if (stop || !s || !d.trades) return
          const snap = (t: number) => (Math.floor(t / tf) * tf) as Time
          const marks: SeriesMarker<Time>[] = []
          for (const t of d.trades) {
            if (t.open_time) {
              marks.push({
                time: snap(t.open_time), position: t.side === 'BUY' ? 'belowBar' : 'aboveBar',
                shape: t.side === 'BUY' ? 'arrowUp' : 'arrowDown',
                color: t.side === 'BUY' ? '#34d399' : '#fb7185',
                text: `${t.side === 'BUY' ? 'شراء' : 'بيع'} ${t.volume}`,
              })
            }
            if (t.close_time) {
              marks.push({
                time: snap(t.close_time), position: 'aboveBar', shape: 'circle',
                color: '#9fb8dc', text: `خروج${t.exit_price != null ? ' ' + t.exit_price : ''}`,
              })
            }
          }
          marks.sort((a, b) => (a.time as number) - (b.time as number))
          try { s.setMarkers(marks) } catch { /* شمعة العلامة برّا المدى المعروض */ }
        })
        .catch(() => { /* الخادم القديم بلا المنفذ — بعد إعادة الفتح */ })
    load()
    const t = setInterval(load, 15000)
    return () => { stop = true; clearInterval(t) }
  }, [symbol, tf, nc > 0 ? 1 : 0])

  const mine = posState?.positions?.filter((p) => p.symbol === symbol) ?? []
  const openHere = mine.length
  const pnlHere = mine.reduce((s2, p) => s2 + (p.profit ?? 0), 0)
  const histLabel =
    hist === 'load' ? 'جارٍ جلب شموع MT5…'
    : hist === 'held' ? `${nc} شمعة · آخر لقطة الإكسبرت`
    : hist === 'empty' ? 'يبني من تِكّات MT5…'
    : `${nc} شمعة · تنفيذ MT5`

  return (
    <div className="chartpanel">
      <div className="chhead">
        <span className="chsym">{symbol}</span>
        {tfLabel ? <span style={{ color: 'var(--accent)', fontWeight: 700, fontSize: 13 }}>{tfLabel}</span> : null}
        <span className="num chlast">{priceText(last)}</span>
        {openHere > 0 ? <span className="chtrade">● {openHere} صفقة</span> : null}
        {openHere > 0 ? (
          <span className="num" style={{ color: pnlHere >= 0 ? 'var(--green)' : 'var(--red)', fontWeight: 700 }}>
            {pnlTxt(pnlHere)}
          </span>
        ) : null}
        <span className="dim chtf" style={{ marginInlineStart: 'auto' }}>{histLabel}</span>
      </div>
      <div className="chbox" ref={boxRef} />
    </div>
  )
}

export default function Charts() {
  const brokerMarket = useStore((s) => s.brokerMarket)
  const pos = useStore((s) => s.streams['platform.positions.state']) as Positions | undefined
  const [tf, setTf] = useState(300)
  const [shown, setShown] = useState<string[] | null>(null)
  const [mode, setMode] = useState<'symbols' | 'frames'>('symbols')
  const [focusSym, setFocusSym] = useState<string | null>(null)
  const [frames, setFrames] = useState<number[]>([60, 300, 900, 3600])
  const [eaSymbols, setEaSymbols] = useState<string[]>([])

  useEffect(() => {
    let stop = false
    const load = () => fetch('/gov/exec-candles?limit=1', { cache: 'no-store' })
      .then((r) => r.json())
      .then((d: ExecPayload) => { if (!stop && d.symbols) setEaSymbols(d.symbols) })
      .catch(() => {})
    load()
    const t = setInterval(load, 4000)
    return () => { stop = true; clearInterval(t) }
  }, [])

  const symbols = useMemo(() => {
    const set = new Set<string>()
    for (const p of pos?.positions ?? []) set.add(p.symbol)
    for (const k of Object.keys(brokerMarket)) set.add(k)
    for (const s of eaSymbols) set.add(s)
    return Array.from(set)
  }, [brokerMarket, pos, eaSymbols])

  const active = shown ?? symbols.slice(0, 4)
  const toggle = (sym: string) => {
    const cur = shown ?? symbols.slice(0, 4)
    setShown(cur.includes(sym) ? cur.filter((s) => s !== sym) : [...cur, sym])
  }
  const toggleFrame = (v: number) =>
    setFrames(frames.includes(v) ? frames.filter((f) => f !== v) : [...frames, v].sort((a, b) => a - b))
  const tfLabel = (v: number) => TFS.find(([x]) => x === v)?.[1] ?? String(v)

  if (!symbols.length) return <div className="section"><div className="empty">بانتظار شموع الإكسبرت (MT5)… القطع لا يمسح آخر شارت إن وُجد.</div></div>
  const fSym = focusSym ?? symbols[0]

  return (
    <div className="section chartsec">
      <div className="chbar">
        <div className="chtfs">
          <button className={mode === 'symbols' ? 'on' : ''} onClick={() => setMode('symbols')}>عدة رموز</button>
          <button className={mode === 'frames' ? 'on' : ''} onClick={() => setMode('frames')}>رمز × فريمات</button>
        </div>
        <div className="chtfs">
          {TFS.map(([v, l]) => (
            <button
              key={v}
              className={(mode === 'symbols' ? tf === v : frames.includes(v)) ? 'on' : ''}
              onClick={() => (mode === 'symbols' ? setTf(v) : toggleFrame(v))}
            >{l}</button>
          ))}
        </div>
        <div className="chsyms">
          {symbols.map((s) => (
            <button
              key={s}
              className={(mode === 'symbols' ? active.includes(s) : fSym === s) ? 'on' : ''}
              onClick={() => (mode === 'symbols' ? toggle(s) : setFocusSym(s))}
            >{s}</button>
          ))}
        </div>
        {pos && (pos.positions?.length ?? 0) > 0 ? (
          <span className="num" style={{ marginInlineStart: 'auto', fontWeight: 700, fontSize: 15, color: (pos.floating_pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)' }}>
            المجموع الكامل: {pnlTxt(pos.floating_pnl ?? 0)}
          </span>
        ) : null}
      </div>
      {mode === 'symbols' ? (
        <div className="chgrid" data-n={active.length}>
          {active.map((s) => <ChartPanel key={`${s}-${tf}`} symbol={s} tf={tf} />)}
          {active.length === 0 ? <div className="empty">اختر رمزًا من فوق.</div> : null}
        </div>
      ) : (
        <div className="chgrid" data-n={frames.length}>
          {frames.map((f) => <ChartPanel key={`${fSym}-${f}`} symbol={fSym} tf={f} tfLabel={tfLabel(f)} />)}
          {frames.length === 0 ? <div className="empty">اختر فريمًا واحدًا عالأقل من فوق.</div> : null}
        </div>
      )}
    </div>
  )
}
