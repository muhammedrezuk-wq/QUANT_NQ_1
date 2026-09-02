// ═══ قسم أسمر — لوحة MEXC البشرية (أمر المالك 2026-08-28) ═══
// المبدأ (ورقة السكالبينج ٣): النظام يقرأ ويقترح — والإنسان هو القرار والتنفيذ.
// شارت شموع حيّ + بطاقة أمر (شراء/بيع · رافعة · حجم) + مفاتيح محفوظة خادميًا.
// الوضع الافتراضي تدريب؛ التنفيذ الحقيقي بتفعيل صريح + تأكيد مزدوج.
import { useEffect, useRef, useState } from 'react'
import { createChart, IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts'

const INTERVALS: [string, string][] = [['Min1', '1د'], ['Min5', '5د'], ['Min15', '15د'], ['Min30', '30د'], ['Min60', '1س'], ['4h', '4س'], ['1d', '1ي']]
const LEVERS = [1, 2, 5, 10, 20, 25, 50, 75, 100]

interface Candle { time: number; open: number; high: number; low: number; close: number; volume: number }
interface Status { configured: boolean; key_masked: string; dry_run: boolean }
interface ManualTradeResult {
  trade_id: string
  symbol: string
  pnl_usd: number
  note: string
  operator: string
  recorded_at: number
  delivery_status: string
  attempts: number
}

const card: React.CSSProperties = { border: '1px solid var(--glassb)', background: 'var(--glass)', borderRadius: 12, padding: 14 }
const btn = (extra?: React.CSSProperties): React.CSSProperties => ({ padding: '7px 14px', borderRadius: 8, border: '1px solid var(--glassb)', background: 'var(--glassb)', color: 'var(--ink)', cursor: 'pointer', fontSize: 13, ...extra })
const input: React.CSSProperties = { width: '100%', padding: '7px 9px', borderRadius: 8, border: '1px solid var(--glassb)', background: 'transparent', color: 'var(--ink)', fontSize: 13, boxSizing: 'border-box' }

export default function Mexc() {
  const [marketOk, setMarketOk] = useState<boolean | null>(null)
  const [universe, setUniverse] = useState<{ core: string[]; outer: string[] }>({ core: [], outer: [] })
  const [symbol, setSymbol] = useState('BTC_USDT')
  const [iv, setIv] = useState('Min5')
  const [ticker, setTicker] = useState<{ last?: string; bid?: string; ask?: string } | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [keys, setKeys] = useState({ api_key: '', secret: '' })
  const [testMsg, setTestMsg] = useState('')
  const [ticket, setTicket] = useState({ side: 'BUY', type: 'MARKET', price: '', vol: '', leverage: 20, openType: 1 })
  const [result, setResult] = useState('')
  const [positions, setPositions] = useState<Record<string, unknown>[]>([])
  const [manualResult, setManualResult] = useState({ trade_id: '', pnl_usd: '', note: '', operator: 'ASMAR' })
  const [manualMessage, setManualMessage] = useState('')
  const [manualHistory, setManualHistory] = useState<ManualTradeResult[]>([])
  const chartEl = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Candlestick'> | null>(null)

  const refreshStatus = () => { fetch('/gov/mexc/status', { cache: 'no-store' }).then(r => r.json()).then(setStatus).catch(() => {}) }
  const refreshManualHistory = () => {
    fetch('/gov/mexc/trade-results?limit=12', { cache: 'no-store' })
      .then(r => r.json()).then((d: { results?: ManualTradeResult[] }) => setManualHistory(d.results || []))
      .catch(() => {})
  }

  useEffect(() => {
    fetch('/gov/market', { cache: 'no-store' }).then(r => r.json())
      .then((m: { market?: string }) => setMarketOk(m.market === 'crypto')).catch(() => setMarketOk(false))
    refreshStatus()
    refreshManualHistory()
    fetch('/gov/mexc/universe', { cache: 'no-store' }).then(r => r.json()).then((u: { core?: string[] }) => {
      setUniverse({ core: u.core || [], outer: u.outer || [] })
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (!chartEl.current) return
    const chart = createChart(chartEl.current, {
      autoSize: true,
      height: 430,
      layout: { background: { color: 'transparent' }, textColor: 'rgba(130,170,155,.9)' },
      grid: { vertLines: { color: 'rgba(120,130,150,.10)' }, horzLines: { color: 'rgba(120,130,150,.10)' } },
      timeScale: { timeVisible: true, secondsVisible: false },
    })
    seriesRef.current = chart.addCandlestickSeries({ upColor: '#26a69a', downColor: '#ef5350', wickUpColor: '#26a69a', wickDownColor: '#ef5350', borderVisible: false })
    chartRef.current = chart
    return () => { chart.remove(); chartRef.current = null; seriesRef.current = null }
  }, [])

  useEffect(() => {
    let alive = true
    const load = () => {
      fetch(`/gov/mexc/klines?symbol=${encodeURIComponent(symbol)}&interval=${encodeURIComponent(iv)}`, { cache: 'no-store' })
        .then(r => r.json())
        .then((d: { candles?: Candle[] }) => {
          if (!alive || !seriesRef.current || !d.candles?.length) return
          seriesRef.current.setData(d.candles.map(c => ({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close })))
        }).catch(() => {})
      fetch(`/gov/mexc/ticker?symbol=${encodeURIComponent(symbol)}`, { cache: 'no-store' })
        .then(r => r.json()).then((t) => { if (alive) setTicker(t) }).catch(() => {})
    }
    load()
    const h = window.setInterval(load, 15000)
    return () => { alive = false; window.clearInterval(h) }
  }, [symbol, iv])

  useEffect(() => {
    if (!status?.configured) return
    const load = () => {
      fetch('/gov/mexc/positions', { cache: 'no-store' }).then(r => r.json())
        .then((d) => { const list = (d?.data as Record<string, unknown>[] | undefined) || []; setPositions(list) })
        .catch(() => {})
    }
    load()
    const h = window.setInterval(load, 30000)
    return () => { window.clearInterval(h) }
  }, [status?.configured])

  const saveKeys = async () => {
    const r = await fetch('/gov/mexc/keys', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(keys) })
    const j = await r.json()
    setTestMsg(j.message || JSON.stringify(j)); setKeys({ api_key: '', secret: '' }); refreshStatus()
  }

  const testConn = async () => {
    setTestMsg('…جارٍ الاختبار')
    const r = await fetch('/gov/mexc/test', { cache: 'no-store' })
    const j = await r.json()
    setTestMsg(j?.data ? `✓ متصل — رصيد USDT: ${JSON.stringify(j.data).slice(0, 160)}` : `✗ ${JSON.stringify(j).slice(0, 200)}`)
  }

  const toggleLive = async () => {
    const enable = !!status?.dry_run
    if (enable && !window.confirm('⚠️ تفعيل التنفيذ الحقيقي على MEXC؟ الأوامر بعدها تصل السوق فعلًا.')) return
    await fetch('/gov/mexc/live', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled: enable }) })
    refreshStatus(); setResult('')
  }

  const submit = async () => {
    const vol = parseFloat(ticket.vol || '0')
    if (!vol || vol <= 0) { setResult('✗ أدخل حجمًا صالحًا'); return }
    const live = !!status && !status.dry_run
    const lbl = `${ticket.side === 'BUY' ? 'شراء' : 'بيع'} ${vol} ${symbol} · ${ticket.type === 'LIMIT' ? 'محدد @ ' + ticket.price : 'سوق'} · رافعة ×${ticket.leverage}${live ? ' · ⚠️ تنفيذ حقيقي' : ' · تدريب'}`
    if (!window.confirm(`تأكيد الأمر؟\n${lbl}`)) return
    if (live && !window.confirm(`تأكيد نهائي — هذا أمر حقيقي بمال حقيقي على MEXC.\n${lbl}`)) return
    const r = await fetch('/gov/mexc/order', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, side: ticket.side, type: ticket.type, vol: ticket.vol, price: ticket.type === 'LIMIT' ? ticket.price : undefined, leverage: ticket.leverage, openType: ticket.openType, live }),
    })
    setResult(JSON.stringify(await r.json(), null, 1))
  }

  const submitManualResult = async () => {
    const pnl = Number(manualResult.pnl_usd)
    if (!manualResult.trade_id.trim()) { setManualMessage('✗ أدخل معرّف صفقة MEXC'); return }
    if (!manualResult.pnl_usd.trim() || !Number.isFinite(pnl)) { setManualMessage('✗ أدخل الربح/الخسارة الصافية رقمًا'); return }
    const payload = {
      trade_id: manualResult.trade_id.trim(), symbol,
      pnl_usd: pnl, note: manualResult.note.trim(), operator: manualResult.operator,
    }
    try {
      setManualMessage('…تحضير التأكيد')
      const first = await fetch('/gov/mexc/trade-result', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      })
      const prepared = await first.json()
      if (!first.ok || prepared.stage !== 'confirm') {
        setManualMessage(`✗ ${prepared.message || prepared.error || 'رُفض الطلب'}`); return
      }
      if (!window.confirm(`تأكيد نهائي لنتيجة الصفقة؟\n\n${prepared.summary}\n\nالقيمة صافية بعد الرسوم. ستدخل حدّ الخسارة اليومي.`)) {
        setManualMessage('أُلغي التسجيل — لم يُنشر شيء'); return
      }
      const second = await fetch('/gov/mexc/trade-result', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, confirm: prepared.token }),
      })
      const delivered = await second.json()
      setManualMessage(`${second.ok ? '✓' : '✗'} ${delivered.message || delivered.error || 'نتيجة غير معروفة'}`)
      if (second.ok) {
        setManualResult(r => ({ ...r, trade_id: '', pnl_usd: '', note: '' }))
        refreshManualHistory()
      }
    } catch {
      setManualMessage('✗ تعذّر الاتصال بخادم الحوكمة — لم يتأكد التسجيل')
    }
  }

  const syms = Array.from(new Set(['BTC_USDT', 'ETH_USDT', 'SOL_USDT', ...universe.core, ...universe.outer]))

  if (marketOk === false) {
    return (
      <div style={{ ...card, display: 'grid', gap: 8, justifyItems: 'center', padding: 40 }}>
        <div style={{ fontSize: 40 }}>🔐</div>
        <div style={{ fontWeight: 700 }}>هذه الصفحة لقسم أسمر (الكريبتو)</div>
        <div style={{ color: 'var(--dim)', fontSize: 13 }}>بدّل إلى لوحة الكريبتو من الزر «فوركس ⇄ كريبتو» بالأعلى ثم افتح تبويب MEXC.</div>
      </div>
    )
  }

  return (
    <div style={{ display: 'grid', gap: 14 }}>
      <div style={{ ...card, display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
        <strong style={{ color: 'var(--accent)' }}>MEXC — تنفيذ بشري (قسم أسمر)</strong>
        <select value={symbol} onChange={e => setSymbol(e.target.value)} style={{ ...input, width: 150 }}>
          {syms.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        {ticker?.last ? <span className="num" style={{ fontSize: 18, fontWeight: 700 }}>{ticker.last}</span> : null}
        {ticker?.bid && ticker?.ask ? <span style={{ color: 'var(--dim)', fontSize: 12 }}>bid {ticker.bid} · ask {ticker.ask}</span> : null}
        <span style={{ flexGrow: 1 }} />
        {INTERVALS.map(([v, l]) => (
          <button key={v} style={btn(iv === v ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {})} onClick={() => setIv(v)}>{l}</button>
        ))}
        <span style={{ fontSize: 11, color: 'var(--dim)' }}>الشموع حيّة من MEXC كل ١٥ث</span>
      </div>

      <section style={{ ...card, border: '1px solid rgba(245,158,11,.35)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', marginBottom: 10 }}>
          <div>
            <strong style={{ color: 'var(--amber)' }}>تسجيل نتيجة صفقة مغلقة</strong>
            <div style={{ color: 'var(--dim)', fontSize: 11, marginTop: 4 }}>يدوي فقط · تأكيدان · معرّف فريد · PnL صافي بعد الرسوم</div>
          </div>
          <span style={{ color: 'var(--amber)', fontSize: 11 }}>يحدّث حدّي الخسارة في الذرّة 2275</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(180px,1.4fr) minmax(140px,1fr) minmax(180px,1.6fr) auto', gap: 8 }}>
          <input style={input} dir="ltr" placeholder="MEXC order/deal ID الفريد" value={manualResult.trade_id}
            onChange={e => setManualResult({ ...manualResult, trade_id: e.target.value })} />
          <input style={input} dir="ltr" type="number" step="0.01" placeholder="PnL USD (+ / −)" value={manualResult.pnl_usd}
            onChange={e => setManualResult({ ...manualResult, pnl_usd: e.target.value })} />
          <input style={input} placeholder="ملاحظة اختيارية" value={manualResult.note}
            onChange={e => setManualResult({ ...manualResult, note: e.target.value })} />
          <button onClick={() => void submitManualResult()} style={btn({ background: 'rgba(180,83,9,.35)', whiteSpace: 'nowrap' })}>راجع ثم سجّل</button>
        </div>
        <div style={{ color: 'var(--dim)', fontSize: 11, marginTop: 8 }}>
          الرمز الحالي: <b dir="ltr" style={{ color: 'var(--text)' }}>{symbol}</b>. الخسارة بإشارة سالبة.
          عند بلوغ −2% أو 3 خسائر متتالية تتوقف التوصيات الجديدة؛ هذا ليس قاطعًا لأوامر MEXC اليدوية.
        </div>
        {manualMessage ? <div style={{ marginTop: 9, fontSize: 12, color: manualMessage.startsWith('✓') ? 'var(--green)' : manualMessage.startsWith('✗') ? 'var(--red)' : 'var(--text)' }}>{manualMessage}</div> : null}
        <div style={{ marginTop: 12, overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead><tr style={{ color: 'var(--dim)', textAlign: 'right' }}>
              <th style={{ padding: 6 }}>المعرّف</th><th>الرمز</th><th>صافي USD</th><th>الحالة</th><th>الوقت</th>
            </tr></thead>
            <tbody>
              {manualHistory.map(row => <tr key={row.trade_id} style={{ borderTop: '1px solid var(--glassb)' }}>
                <td dir="ltr" style={{ padding: 6, fontFamily: 'monospace' }}>{row.trade_id}</td>
                <td dir="ltr">{row.symbol}</td>
                <td dir="ltr" style={{ color: row.pnl_usd < 0 ? 'var(--red)' : row.pnl_usd > 0 ? 'var(--green)' : 'var(--text)' }}>{row.pnl_usd > 0 ? '+' : ''}{row.pnl_usd.toFixed(2)}</td>
                <td style={{ color: row.delivery_status === 'DELIVERED' ? 'var(--green)' : 'var(--amber)' }}>{row.delivery_status}</td>
                <td dir="ltr">{new Date(row.recorded_at * 1000).toLocaleString('ar')}</td>
              </tr>)}
              {!manualHistory.length ? <tr><td colSpan={5} style={{ color: 'var(--dim)', padding: 9 }}>لا نتائج مسجلة بعد.</td></tr> : null}
            </tbody>
          </table>
        </div>
      </section>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: 14, alignItems: 'start' }}>
        <div style={card}>
          <div ref={chartEl} style={{ minHeight: 430 }} />
        </div>

        <div style={{ display: 'grid', gap: 14 }}>
          <div style={card}>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>بطاقة الأمر</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
              <button style={btn(ticket.side === 'BUY' ? { background: '#26a69a', color: '#04150f', fontWeight: 700 } : {})} onClick={() => setTicket(t => ({ ...t, side: 'BUY' }))}>شراء / LONG</button>
              <button style={btn(ticket.side === 'SELL' ? { background: '#ef5350', color: '#150406', fontWeight: 700 } : {})} onClick={() => setTicket(t => ({ ...t, side: 'SELL' }))}>بيع / SHORT</button>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
              <button style={btn(ticket.type === 'MARKET' ? { borderColor: 'var(--accent)' } : {})} onClick={() => setTicket(t => ({ ...t, type: 'MARKET' }))}>سوق</button>
              <button style={btn(ticket.type === 'LIMIT' ? { borderColor: 'var(--accent)' } : {})} onClick={() => setTicket(t => ({ ...t, type: 'LIMIT', price: t.price || String(ticker?.last || '') }))}>محدد</button>
            </div>
            {ticket.type === 'LIMIT' ? (
              <input style={{ ...input, marginBottom: 8 }} placeholder="السعر" value={ticket.price} onChange={e => setTicket(t => ({ ...t, price: e.target.value }))} />
            ) : null}
            <input style={{ ...input, marginBottom: 8 }} placeholder={`الحجم (عقود ${symbol})`} value={ticket.vol} onChange={e => setTicket(t => ({ ...t, vol: e.target.value }))} />
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
              {LEVERS.map(l => (
                <button key={l} style={btn(ticket.leverage === l ? { borderColor: 'var(--accent)', color: 'var(--accent)', fontWeight: 700, padding: '4px 9px' } : { padding: '4px 9px', fontSize: 12 })} onClick={() => setTicket(t => ({ ...t, leverage: l }))}>×{l}</button>
              ))}
            </div>
            <button style={{ ...btn, width: '100%', fontWeight: 700, fontSize: 15, background: ticket.side === 'BUY' ? 'rgba(38,166,154,.25)' : 'rgba(239,83,80,.25)' }} onClick={() => void submit()}>
              {status?.dry_run === false ? '⚠️ تنفيذ حقيقي' : 'تنفيذ (تدريب)'}
            </button>
            {result ? <pre style={{ marginTop: 8, fontSize: 11, color: 'var(--dim)', whiteSpace: 'pre-wrap', maxHeight: 130, overflow: 'auto' }}>{result}</pre> : null}
          </div>

          <div style={card}>
            <div style={{ fontWeight: 700, marginBottom: 8 }}>مفاتيح MEXC {status?.configured ? <span style={{ color: 'var(--green)' }}>· {status.key_masked}</span> : <span style={{ color: 'var(--amber)' }}>· غير مهيّأة</span>}</div>
            <input style={{ ...input, marginBottom: 6 }} placeholder="API Key" value={keys.api_key} onChange={e => setKeys(k => ({ ...k, api_key: e.target.value }))} />
            <input style={{ ...input, marginBottom: 8 }} placeholder="Secret" type="password" value={keys.secret} onChange={e => setKeys(k => ({ ...k, secret: e.target.value }))} />
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              <button style={btn()} onClick={() => void saveKeys()}>حفظ (تدريب)</button>
              <button style={btn()} onClick={() => void testConn()} disabled={!status?.configured}>اختبار الاتصال</button>
              <button style={btn(status?.dry_run === false ? { borderColor: 'var(--red)', color: 'var(--red)' } : {})} onClick={() => void toggleLive()} disabled={!status?.configured}>
                {status?.dry_run === false ? 'إيقاف التنفيذ الحقيقي' : 'تفعيل التنفيذ الحقيقي'}
              </button>
            </div>
            {testMsg ? <div style={{ marginTop: 8, fontSize: 11, color: 'var(--dim)', wordBreak: 'break-all' }}>{testMsg}</div> : null}
            <div style={{ marginTop: 8, fontSize: 10, color: 'var(--dim)' }}>تُحفظ المفاتيح خادميًا في var/mexc_api.json (خارج أي شحن) ولا تُعاد للوحة. القرار لك وحدك — النظام يقترح فقط.</div>
          </div>

          {status?.configured ? (
            <div style={card}>
              <div style={{ fontWeight: 700, marginBottom: 8 }}>المراكز المفتوحة ({positions.length})</div>
              {positions.length === 0
                ? <div style={{ fontSize: 12, color: 'var(--dim)' }}>لا مراكز — أو جرّب بعد اتصال ناجح.</div>
                : positions.map((pos, i) => (
                  <div key={i} style={{ fontSize: 12, borderBottom: '1px solid var(--glassb)', padding: '4px 0' }}>
                    {String(pos.symbol || '?')} · {String(pos.positionType === '1' ? 'LONG' : pos.positionType === '2' ? 'SHORT' : '?')} · vol {String(pos.holdVol ?? '?')} · PnR {String(pos.unRealizedRate ?? pos.unrealizedRate ?? '?')}
                  </div>
                ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  )
}
