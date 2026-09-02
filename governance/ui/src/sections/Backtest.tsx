// باك تست رسمي — ذرّات النظام على تيكات تاريخية، دفتر ورقي، بلا أمر للمنصّة.
import { useCallback, useEffect, useState } from 'react'

interface Catalog {
  ok: boolean
  running?: boolean
  data?: { id: string; label: string }[]
  history?: HistoryRow[]
  note?: string
  windows?: Record<string, { first?: string; last?: string; first_ts?: number; last_ts?: number; count?: number }>
  promote?: { live_approved?: boolean; can_promote?: boolean; reason?: string; windows?: boolean; vault_present?: boolean }
}
interface HistoryRow {
  run_id?: string
  source?: string
  ticks?: number
  trades?: number
  net_pnl?: number
  win_rate?: number
  duration_s?: number
  at?: number
}
interface TradeRow {
  id: number
  side: string
  entry_price: number
  exit_price: number
  pnl: number
  net_pnl: number
  reason?: string
  duration_s?: number
  status?: string
}
interface DecisionRow {
  side?: string
  reason?: string
  score?: number
  confidence?: number
  at?: number
}
interface Report {
  ok: boolean
  error?: string | null
  run_id?: string
  source?: string
  symbol?: string
  timeframe?: string
  from_ts?: number
  to_ts?: number
  candles?: number
  ticks?: number
  atoms_loaded?: number
  duration_s?: number
  paper?: {
    capital?: number
    lot?: number
    final_equity?: number
    metrics?: {
      total_trades?: number
      winning_trades?: number
      losing_trades?: number
      win_rate?: number
      net_pnl?: number
      profit_factor?: number | null
      max_drawdown?: number
      return_pct?: number
    }
    sides?: Record<string, number>
    trades?: TradeRow[]
    decisions?: DecisionRow[]
    open?: { side?: string; entry_price?: number } | null
  }
  last?: Record<string, Record<string, unknown>>
  news?: { empty?: boolean; note?: string; news?: { headline?: string; published_at?: number; impact_level?: string }[]; calendar?: { title?: string; scheduled_at?: number; currency?: string; impact_level?: string }[] }
  pnl_file?: { json?: string; csv?: string }
}

const num = (n?: number | null, d = 2) =>
  n == null || !Number.isFinite(n) ? '—' : n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: d })

function chip(on: boolean) {
  return {
    fontSize: 12, padding: '3px 10px', borderRadius: 8, cursor: 'pointer',
    fontFamily: 'inherit', whiteSpace: 'nowrap' as const,
    background: on ? 'var(--accent)' : 'transparent',
    color: on ? '#06121c' : 'var(--dim)',
    border: `1px solid ${on ? 'var(--accent)' : 'var(--glassb)'}`,
  }
}

function clock(epoch?: number) {
  if (!epoch) return '—'
  const ms = epoch > 10_000_000_000 ? epoch : epoch * 1000
  return new Date(ms).toLocaleString('ar-EG-u-nu-latn', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function Backtest() {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [source, setSource] = useState('okx')
  const [candles, setCandles] = useState(80)
  const [capital, setCapital] = useState(10000)
  const [lot, setLot] = useState(0.01)
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [report, setReport] = useState<Report | null>(null)

  const loadCatalog = useCallback(() => {
    fetch('/gov/backtest/catalog', { cache: 'no-store' })
      .then((r) => r.json() as Promise<Catalog>)
      .then((d) => setCatalog(d))
      .catch(() => setCatalog({ ok: false }))
  }, [])

  useEffect(() => { loadCatalog() }, [loadCatalog])

  const run = async () => {
    setBusy(true); setNote(null)
    try {
      const r = await fetch('/gov/backtest/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          source, max_candles: candles, capital, lot,
          from_date: fromDate || undefined,
          to_date: toDate || undefined,
        }),
      })
      const j = await r.json() as Report
      if (!j.ok) {
        setNote({ ok: false, text: j.error ?? 'تعذّر التشغيل' })
        setReport(null)
      } else {
        setReport(j)
        const m = j.paper?.metrics
        const trades = m?.total_trades ?? 0
        setNote({
          ok: true,
          text: trades === 0
            ? `خلصت — ما في صفقة. قرارات النظام تحت (${num(j.duration_s, 1)} ث)`
            : `خلصت — ${num(trades, 0)} صفقة · صافي ${num(m?.net_pnl)} (${num(j.duration_s, 1)} ث)`,
        })
        loadCatalog()
      }
    } catch {
      setNote({ ok: false, text: 'خطأ اتصال — تأكّد أن خادم الحوكمة شغّال' })
    }
    setBusy(false)
  }

  const m = report?.paper?.metrics
  const trades = report?.paper?.trades ?? []
  const decisions = report?.paper?.decisions ?? []
  const sides = report?.paper?.sides ?? {}
  const last = report?.last ?? {}

  return (
    <div className="section" style={{ display: 'flex', flexDirection: 'column', gap: 10, minHeight: 0 }}>
      <div className="scard">
        <div className="st" style={{ fontWeight: 700, fontSize: 16 }}>باك تست رسمي — معزول عن التداول</div>
        <div className="ss dim" style={{ marginTop: 4 }}>
          نفس محرّك الذرّات (تحليل → بنية → استراتيجية → قرار) على تيكات تاريخية.
          القرار buy/sell بتنفّذ بدفتر ورقي — PnL وصفقات متل التداول، بلا أمر للمنصّة.
          عتبة عدّلتها بالمختبر بتنطبق هون (overlay). ٩٠١ و٥٧٦ و٦٠١ ما بينحملوا.
          هالصفحة باك تست ورقي — مو اعتماد تداول حي.
        </div>
      </div>

      <div className="scard" style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
        <span className="st" style={{ fontSize: 12 }}>البيانات</span>
        {(catalog?.data ?? []).map((d) => (
          <button key={d.id} style={chip(d.id === source)} onClick={() => setSource(d.id)}>{d.label}</button>
        ))}
        <label className="dim" style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          شموع المصدر
          <input className="cfginput num" type="number" min={20} max={400} step={10}
            style={{ width: 72, margin: 0, padding: '4px 7px' }}
            value={candles} onChange={(e) => setCandles(Number(e.target.value))} />
        </label>
        <label className="dim" style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          رأس مال
          <input className="cfginput num" type="number" min={100} step={100}
            style={{ width: 90, margin: 0, padding: '4px 7px' }}
            value={capital} onChange={(e) => setCapital(Number(e.target.value))} />
        </label>
        <label className="dim" style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          لوت
          <input className="cfginput num" type="number" min={0.01} step={0.01}
            style={{ width: 72, margin: 0, padding: '4px 7px' }}
            value={lot} onChange={(e) => setLot(Number(e.target.value))} />
        </label>
        <label className="dim" style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          من
          <input className="cfginput" type="date" style={{ margin: 0, padding: '4px 7px' }}
            value={fromDate} onChange={(e) => setFromDate(e.target.value)} />
        </label>
        <label className="dim" style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          إلى
          <input className="cfginput" type="date" style={{ margin: 0, padding: '4px 7px' }}
            value={toDate} onChange={(e) => setToDate(e.target.value)} />
        </label>
        <span className="dim" style={{ fontSize: 11 }}>
          {catalog?.windows?.[source]?.first ?? ''} → {catalog?.windows?.[source]?.last ?? ''}
        </span>
      </div>

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        <button className="btn start" disabled={busy} onClick={() => void run()}>
          {busy || catalog?.running ? 'عم تشغّل المسار الكامل…' : 'شغّل الباك تست'}
        </button>
        {note ? <span style={{ fontSize: 13, color: note.ok ? 'var(--green)' : 'var(--amber)' }}>{note.text}</span> : null}
      </div>

      {report ? (
        <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))' }}>
          <div className="scard"><div className="st">الرمز</div><div className="sv" style={{ fontSize: 16 }}>{report.symbol}</div><div className="ss">{clock(report.from_ts)} → {clock(report.to_ts)}</div></div>
          <div className="scard"><div className="st">تيكات / شموع ١٠٣</div><div className="sv num">{num(report.ticks, 0)} / {num(report.candles, 0)}</div><div className="ss">{num(report.duration_s, 1)} ث · {num(report.atoms_loaded, 0)} ذرّة</div></div>
          <div className="scard"><div className="st">صفقات</div><div className="sv num">{num(m?.total_trades, 0)}</div><div className="ss">ربح {num(m?.winning_trades, 0)} · خسارة {num(m?.losing_trades, 0)}</div></div>
          <div className="scard"><div className="st">صافي PnL</div><div className={`sv num ${(m?.net_pnl ?? 0) >= 0 ? 'green' : 'amber'}`}>{num(m?.net_pnl)}</div><div className="ss">عائد {num((m?.return_pct ?? 0) * 100, 2)}٪</div></div>
          <div className="scard"><div className="st">نسبة الفوز</div><div className="sv num">{num((m?.win_rate ?? 0) * 100, 1)}٪</div><div className="ss">أقصى تراجع {num(m?.max_drawdown)}</div></div>
          <div className="scard"><div className="st">قرارات</div><div className="sv" style={{ fontSize: 15 }}>ش {num(sides.buy, 0)} · ب {num(sides.sell, 0)} · انتظر {num(sides.wait, 0)}</div><div className="ss">رأس مال {num(report.paper?.capital, 0)}</div></div>
        </div>
      ) : null}

      {report?.error ? <div className="scard" style={{ color: 'var(--amber)' }}>خطأ الجولة: {report.error}</div> : null}

      {trades.length ? (
        <div className="scard" style={{ overflow: 'hidden', padding: 0 }}>
          <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--glassb)' }}>
            <div className="st">الصفقات الورقية</div>
            <div className="ss dim">ملء فوري على سعر التيك وقت القرار — مو أمر سي تريدر.</div>
          </div>
          <div style={{ overflowX: 'auto', maxHeight: 280 }}>
            <table className="tbl" style={{ width: '100%', fontSize: 12.5, borderCollapse: 'collapse' }}>
              <thead><tr className="dim" style={{ textAlign: 'right' }}>
                <th style={{ padding: '6px 10px' }}>#</th>
                <th style={{ padding: '6px 10px' }}>جهة</th>
                <th style={{ padding: '6px 10px' }}>دخول</th>
                <th style={{ padding: '6px 10px' }}>خروج</th>
                <th style={{ padding: '6px 10px' }}>PnL</th>
                <th style={{ padding: '6px 10px' }}>سبب</th>
              </tr></thead>
              <tbody>
                {trades.map((t) => (
                  <tr key={t.id} style={{ borderTop: '1px solid var(--line)' }}>
                    <td className="num" style={{ padding: '6px 10px' }}>{t.id}</td>
                    <td style={{ padding: '6px 10px' }}>{t.side}</td>
                    <td className="num" style={{ padding: '6px 10px' }}>{num(t.entry_price, 2)}</td>
                    <td className="num" style={{ padding: '6px 10px' }}>{num(t.exit_price, 2)}</td>
                    <td className={`num ${t.net_pnl >= 0 ? 'green' : 'amber'}`} style={{ padding: '6px 10px' }}>{num(t.net_pnl)}</td>
                    <td style={{ padding: '6px 10px', fontSize: 11 }}>{t.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : report ? (
        <div className="scard">
          <div className="st">ما في صفقة</div>
          <div className="ss dim" style={{ marginTop: 4 }}>
            النظام ما طلع buy/sell جاهز على هالنافذة — غالباً قرار انتظار أو قسم مو جاهز.
            عاير بالمختبر (عتبة ثقة/عمق) وارجع شغّل. آخر قرار تحت.
          </div>
        </div>
      ) : null}

      {decisions.length ? (
        <div className="scard" style={{ overflow: 'hidden', padding: 0 }}>
          <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--glassb)' }}>
            <div className="st">آخر قرارات ٤٥٨</div>
          </div>
          <div style={{ overflowX: 'auto', maxHeight: 220 }}>
            <table className="tbl" style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead><tr className="dim" style={{ textAlign: 'right' }}>
                <th style={{ padding: '5px 10px' }}>جهة</th>
                <th style={{ padding: '5px 10px' }}>سبب</th>
                <th style={{ padding: '5px 10px' }}>درجة</th>
                <th style={{ padding: '5px 10px' }}>ثقة</th>
              </tr></thead>
              <tbody>
                {decisions.slice().reverse().slice(0, 25).map((d, i) => (
                  <tr key={i} style={{ borderTop: '1px solid var(--line)' }}>
                    <td style={{ padding: '5px 10px' }}>{d.side}</td>
                    <td style={{ padding: '5px 10px' }}>{d.reason ?? '—'}</td>
                    <td className="num" style={{ padding: '5px 10px' }}>{d.score == null ? '—' : num(Number(d.score))}</td>
                    <td className="num" style={{ padding: '5px 10px' }}>{d.confidence == null ? '—' : num(Number(d.confidence))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {report?.news ? (
        <div className="scard">
          <div className="st">أخبار النافذة</div>
          <div className="ss dim" style={{ marginTop: 4 }}>{report.news.note}</div>
          {(report.news.calendar ?? []).slice(0, 12).map((c, i) => (
            <div key={`c${i}`} style={{ fontSize: 12.5, marginTop: 4 }}>
              <span className="num dim">{clock(c.scheduled_at)}</span> · {c.currency} · {c.impact_level} · {c.title}
            </div>
          ))}
          {(report.news.news ?? []).slice(0, 12).map((n, i) => (
            <div key={`n${i}`} style={{ fontSize: 12.5, marginTop: 4 }}>
              <span className="num dim">{clock(n.published_at)}</span> · {n.impact_level} · {n.headline}
            </div>
          ))}
        </div>
      ) : null}

      {Object.keys(last).length ? (
        <div className="scard">
          <div className="st">آخر حمولة من المسار</div>
          <pre style={{ margin: '8px 0 0', fontSize: 11, direction: 'ltr', textAlign: 'left', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {JSON.stringify(last, null, 2)}
          </pre>
        </div>
      ) : null}

      {(catalog?.history?.length ?? 0) > 0 ? (
        <div className="scard">
          <div className="st">آخر الجولات</div>
          <div style={{ display: 'grid', gap: 4, marginTop: 6, fontSize: 12.5 }}>
            {(catalog?.history ?? []).slice().reverse().map((h) => (
              <div key={h.run_id} style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <span className="num dim">{clock(h.at)}</span>
                <span>{h.source}</span>
                <span className="num">صفقات {num(h.trades, 0)}</span>
                <span className="num">PnL {num(h.net_pnl)}</span>
                <span className="num dim">{num(h.duration_s, 1)} ث</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}
