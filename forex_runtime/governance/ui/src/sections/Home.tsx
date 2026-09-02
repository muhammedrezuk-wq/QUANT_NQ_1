// الرئيسية (850) v8 «حاملة البلاطات» — ختم المالك ٢٠٢٦-٠٨-٢٠.
// أمره الحرفيّ: «حط مجموع كل اللوحات بهي لوحة… وأنا بصغّر وبكبّر بالطول وبالعرض
// وبغيّر المكان» ثمّ «كل موجود عيّفهم — ضيف الناقص بس».
// لذلك: **لا يُعاد بناء أي قسم هنا.** الأقسام تُركَّب كما هي (نفس شيفرتها ونفس
// أرقامها) داخل بلاطات، والمكتوب هنا هو الناقص فقط: الحكم بجملة · جدول الرموز ·
// الحاجبون · صحّة الأسطول · المال · التغذية.
// القوانين من ورقتَي ٩٩ و٩٧: أرقام أجنبية (١) · صفر إنكليزي خام (١/٢ج) ·
// لا زرّ ميّت · لا حقل مخترع — والغياب يُعلَن غيابًا.
import { useEffect, useMemo, useState } from 'react'
import { DecisionDialsCard } from './Settings'
import { useStore } from '../core/store'
import { TileBoard, useLayout, type Layout, type TileDef } from '../components/Tiles'
import { RoomBar } from '../components/RoomBar'
import { EarlyWarningStrip } from '../components/EarlyWarning'

// ── الأقسام تُركَّب كما هي ──
import Network from './Network'
import Charts from './Charts'
import Control from './Control'
import Analysis from './Analysis'
import Market from './Market'
import Risk from './Risk'
import Alerts from './Alerts'
import Monitor from './Monitor'
import Execution from './Execution'
import Atoms from './Atoms'
import Portfolios from './Portfolios'
import Log from './Log'
import Connection from './Connection'
import NQ from './NQ'

interface Account { account_id?: string; balance?: number; equity?: number }
interface Pos { symbol: string; profit?: number | null }
interface Positions { floating_pnl?: number; positions?: Pos[] }
interface Term { trade_allowed?: boolean }
interface Tg { running: boolean; paired: boolean; token: boolean }

const money = (n?: number | null) => (n == null ? '—'
  : n.toLocaleString('ar-EG-u-nu-latn', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))
const int = (n?: number | null) => (n == null ? '—' : n.toLocaleString('ar-EG-u-nu-latn'))

const FILTER_AR: Record<string, [string, number]> = {
  confidence_filter: ['فلتر الثقة', 460], conditions_filter: ['فلتر الشروط', 461],
  timing_filter: ['فلتر التوقيت', 462], position_filter: ['حارس المراكز', 463],
  freshness_filter: ['فلتر الطزاجة', 464], asset_filter: ['التحكّم بالأصول', 468],
}
const filterAr = (id: string) => (FILTER_AR[id] ? `${FILTER_AR[id][0]} ${FILTER_AR[id][1]}` : id)

const SIG: Record<string, [string, string]> = {
  up: ['صاعد', 'var(--green)'], down: ['هابط', 'var(--red)'], sideways: ['عرضي', 'var(--dim)'],
  neutral: ['محايد', 'var(--dim)'], buy: ['شراء', 'var(--green)'], sell: ['بيع', 'var(--red)'],
  none: ['بلا', 'var(--dim)'],
}
const TREND: Record<string, [string, string]> = {
  uptrend: ['صاعد', 'var(--green)'], downtrend: ['هابط', 'var(--red)'],
  ranging: ['عرضي', 'var(--dim)'], neutral: ['محايد', 'var(--dim)'],
  sideways: ['عرضي', 'var(--dim)'], none: ['بلا اتجاه', 'var(--dim)'],
}
const pair = (m: Record<string, [string, string]>, k?: string): [string, string] =>
  (k ? m[k] ?? m[k.toLowerCase()] ?? [k, 'var(--dim)'] : ['لسا', 'var(--dim)'])

function ageOfTick(ts: number, now: number): [string, string] {
  const ms = ts > 1e11 ? ts : ts * 1000
  const s = Math.max(0, (now - ms) / 1000)
  if (s < 3) return ['حيّ', 'var(--green)']
  if (s < 15) return [`${Math.round(s)} ث`, 'var(--green)']
  if (s < 60) return [`${Math.round(s)} ث`, 'var(--amber)']
  return [`${Math.round(s / 60)} د`, 'var(--red)']
}

function Line({ k, v, c }: { k: string; v: string; c?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 11.5, lineHeight: 1.6, minWidth: 0, padding: '0 9px' }}>
      <span className="dim" style={{ flex: 'none' }}>{k}</span>
      <span className="num" style={{ color: c, fontWeight: 600, textAlign: 'end', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v}</span>
    </div>
  )
}

const DEFAULT_LAYOUT: Layout = {
  verdict: { x: 0, y: 0, w: 12, h: 1, on: true },
  symbols: { x: 0, y: 1, w: 7, h: 4, on: true },
  blockers: { x: 7, y: 1, w: 5, h: 2, on: true },
  money: { x: 7, y: 3, w: 5, h: 2, on: true },
  charts: { x: 0, y: 5, w: 7, h: 7, on: true },
  risk: { x: 7, y: 5, w: 5, h: 4, on: true },
  alerts: { x: 7, y: 9, w: 5, h: 3, on: true },
  fleet: { x: 0, y: 12, w: 4, h: 3, on: false },
  feed: { x: 4, y: 12, w: 4, h: 3, on: false },
  network: { x: 0, y: 12, w: 8, h: 6, on: false },
  analysis: { x: 0, y: 12, w: 12, h: 6, on: false },
  market: { x: 0, y: 12, w: 6, h: 5, on: false },
  execution: { x: 0, y: 12, w: 12, h: 6, on: false },
  control: { x: 0, y: 12, w: 12, h: 6, on: false },
  atoms: { x: 0, y: 12, w: 12, h: 6, on: false },
  portfolios: { x: 0, y: 12, w: 6, h: 4, on: false },
  log: { x: 0, y: 12, w: 6, h: 5, on: false },
  monitor: { x: 0, y: 12, w: 6, h: 5, on: false },
  connection: { x: 0, y: 12, w: 6, h: 4, on: false },
  nq: { x: 0, y: 12, w: 4, h: 5, on: false },
  dials: { x: 0, y: 12, w: 6, h: 5, on: false },
}

export default function Home({ onGo }: { onGo: (id: string) => void }) {
  const atoms = useStore((s) => s.atoms)
  const acc = useStore((s) => s.streams['platform.account.state']) as Account | undefined
  const posState = useStore((s) => s.streams['platform.positions.state']) as Positions | undefined
  const term = useStore((s) => s.streams['platform.terminal_state']) as Term | undefined
  const risk = useStore((s) => s.risk)
  const gate = useStore((s) => s.gate)
  const execution = useStore((s) => s.execution)
  const market = useStore((s) => s.market)
  const analysis = useStore((s) => s.analysis)
  const structure = useStore((s) => s.structure)
  const strategy = useStore((s) => s.strategy)
  const decision = useStore((s) => s.decision)
  const conn = useStore((s) => s.conn)
  const flows = useStore((s) => s.flows)

  const dialState = useStore((s) => s.streams['dial.profile.state']) as
    { profiles?: Array<{ dial?: number; hedge_target?: number }> } | undefined
  const profile = dialState?.profiles?.[0]
  const dialPct = typeof profile?.dial === 'number' ? profile.dial : null
  const hedge = typeof profile?.hedge_target === 'number' ? profile.hedge_target : null

  const [tg, setTg] = useState<Tg | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const { layout, save, reset } = useLayout(DEFAULT_LAYOUT)

  useEffect(() => {
    let alive = true
    const ask = () => fetch('/gov/telegram').then((r) => r.json())
      .then((j: Tg) => { if (alive) setTg(j) }).catch(() => { if (alive) setTg(null) })
    ask()
    const id = window.setInterval(ask, 5000)
    return () => { alive = false; window.clearInterval(id) }
  }, [])
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])

  const fleet = useMemo(() => {
    const sick: Array<{ id: number; name: string; state: string }> = []
    let running = 0
    for (const a of Object.values(atoms)) {
      if (a.state === 'running') running++
      const hs = a.health?.state
      if (hs && hs !== 'healthy') sick.push({ id: a.id, name: a.name_ar ?? a.name, state: hs })
    }
    sick.sort((x, y) => x.id - y.id)
    return { total: Object.keys(atoms).length, running, sick }
  }, [atoms])

  const blockers = useMemo(() => {
    const msg = atoms[454]?.health?.message ?? ''
    const g = (k: string) => {
      const m = new RegExp(`${k}=([^\\s]+)`).exec(msg)
      return m && m[1] !== '-' ? m[1].split(',').filter(Boolean) : []
    }
    const n = (k: string) => { const m = new RegExp(`${k}=(\\d+)`).exec(msg); return m ? Number(m[1]) : null }
    return { fail: g('fail'), miss: g('miss'), seen: n('seen'), passed: n('passed') }
  }, [atoms])

  const symbols = useMemo(() => Object.keys(market).sort(), [market])
  const posBySym = useMemo(() => {
    const m: Record<string, { n: number; pnl: number }> = {}
    for (const p of posState?.positions ?? []) {
      const e = (m[p.symbol] ??= { n: 0, pnl: 0 }); e.n++; e.pnl += p.profit ?? 0
    }
    return m
  }, [posState])

  const analysisOf = (s: string) => analysis[s]
    ?? analysis[Object.keys(analysis).find((k) => k.endsWith(`::${s}`)) ?? '']

  const votes = (recs: Record<string, { results?: Record<string, { signal?: string }> }>, s: string) => {
    let b = 0, k = 0
    for (const u of Object.values(recs[s]?.results ?? {})) {
      if (u.signal === 'buy') b++; else if (u.signal === 'sell') k++
    }
    return b > k ? [`شراء ${int(b)}`, 'var(--green)'] as const
      : k > b ? [`بيع ${int(k)}`, 'var(--red)'] as const
        : ['حياد', 'var(--dim)'] as const
  }

  const feedAge = (...names: string[]) => {
    let best = Infinity
    for (const n of names) { const t = flows[n]; if (t) best = Math.min(best, (performance.now() - t) / 1000) }
    return best
  }
  const feedTxt = (age: number): [string, string] => {
    if (conn !== 'live') return ['لا نعرف', 'var(--dim)']
    if (age === Infinity) return ['صامت', 'var(--red)']
    if (age < 10) return ['حيّ', 'var(--green)']
    if (age < 60) return [`${Math.round(age)} ث`, 'var(--amber)']
    return [`${Math.round(age / 60)} د`, 'var(--red)']
  }

  const pnl = posState?.floating_pnl ?? null
  const kill = risk?.kill_switch_state === true
  const gateOpen = gate?.status === 'LIVE'
  const halted = execution?.halted === true || gate?.status === 'HALTED' || gate?.status === 'PARTIAL_HALT'

  const verdict: [string, string, string?] =
    conn !== 'live' ? ['⛔ النواة مقطوعة — ما في قراءة حيّة', 'var(--red)', 'شغّل «غرفة القيادة» أو افحص الاتصال']
      : kill ? ['🛑 التداول موقوف — قاطع الأمان مُفعَّل', 'var(--red)', 'التصفير بيدك من «المخاطر»']
        : halted ? ['🛑 إيقاف طارئ فعّال — كل الأوامر مقفولة', 'var(--red)', 'رفعه بيدك من «تحكّم»']
          : !gateOpen ? ['🔒 يحلّل ولا يفتح صفقات — البوّابة مقفولة بقرارك', 'var(--amber)', 'فتحها من «التنفيذ» أو «تحكّم»']
            : fleet.sick.length ? [`⚠️ البوّابة مفتوحة — و${int(fleet.sick.length)} ذرّة مش سليمة`, 'var(--amber)', 'أسماؤها ببلاطة «صحّة الأسطول»']
              : ['🟢 النظام شغّال — الأمر الصالح بيوصل للمنصّة فعليًّا', 'var(--green)']

  const th: React.CSSProperties = { padding: '3px 8px', textAlign: 'start', fontWeight: 500, borderBottom: '1px solid var(--glassb)', whiteSpace: 'nowrap' }
  const td: React.CSSProperties = { padding: '4px 8px', borderBottom: '1px solid var(--glassb)', whiteSpace: 'nowrap' }

  const defs: TileDef[] = [
    {
      id: 'verdict', title: 'الحكم بلمحة', go: 'control',
      render: () => (
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '4px 10px', minWidth: 0 }}>
          <span style={{ fontSize: 14, fontWeight: 800, color: verdict[1], whiteSpace: 'nowrap' }}>{verdict[0]}</span>
          {verdict[2] ? <span className="dim" style={{ fontSize: 11.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{verdict[2]}</span> : null}
        </div>
      ),
    },
    {
      id: 'symbols', title: 'الرموز — سعر · عمر · تحليل · ثقة · بنية · استراتيجيات · قرار · مركز', go: 'market',
      render: () => (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, tableLayout: 'fixed' }}>
          <thead>
            <tr className="dim" style={{ fontSize: 10.5 }}>
              <th style={{ ...th, width: '13%' }}>الرمز</th><th style={{ ...th, width: '14%' }}>السعر</th>
              <th style={{ ...th, width: '9%' }}>العمر</th><th style={{ ...th, width: '12%' }}>التحليل</th>
              <th style={{ ...th, width: '11%' }}>الثقة</th><th style={{ ...th, width: '11%' }}>البنية</th>
              <th style={{ ...th, width: '14%' }}>الاستراتيجيات</th><th style={{ ...th, width: '10%' }}>القرار</th>
              <th style={{ ...th, width: '10%' }}>المركز</th>
            </tr>
          </thead>
          <tbody>
            {symbols.map((sym) => {
              const a = analysisOf(sym)
              const sig = pair(SIG, (a as { signal?: string } | undefined)?.signal ?? undefined)
              const tr = pair(TREND, structure[sym]?.structure?.trend)
              const st = votes(strategy, sym)
              const dc = votes(decision, sym)
              const p = posBySym[sym]
              const age = ageOfTick(market[sym].ts, now)
              const cf = a?.confidence
              return (
                <tr key={sym}>
                  <td style={{ ...td, fontWeight: 700 }}>{sym}</td>
                  <td className="num" style={td}>{money(market[sym].bid)}</td>
                  <td className="num" style={{ ...td, color: age[1], fontSize: 11 }}>{age[0]}</td>
                  <td style={{ ...td, color: sig[1] }}>{sig[0]}</td>
                  <td className="num" style={{ ...td, color: cf == null ? 'var(--dim)' : cf >= 60 ? 'var(--green)' : cf > 0 ? 'var(--amber)' : 'var(--dim)' }}>
                    {cf == null ? '—' : `${Math.round(cf)}%`}
                  </td>
                  <td style={{ ...td, color: tr[1] }}>{tr[0]}</td>
                  <td style={{ ...td, color: st[1] }}>{st[0]}</td>
                  <td style={{ ...td, color: dc[1] }}>{dc[0]}</td>
                  <td className="num" style={{ ...td, color: p ? (p.pnl >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--dim)', fontWeight: p ? 700 : 400 }}>
                    {p ? `${int(p.n)} · ${money(p.pnl)}` : '—'}
                  </td>
                </tr>
              )
            })}
            {symbols.length === 0 ? <tr><td style={td} colSpan={9} className="dim">بانتظار أوّل سعر من النواة…</td></tr> : null}
          </tbody>
        </table>
      ),
    },
    {
      id: 'blockers', title: 'مين واقف بوجه الصفقة', go: 'execution',
      render: () => (
        <div style={{ padding: '5px 9px' }}>
          {blockers.fail.length === 0 && blockers.miss.length === 0 ? (
            <span className="dim" style={{ fontSize: 11.5 }}>
              {atoms[454] ? 'ما في حاجب بآخر دورة' : 'فلتر القرار (454) ما وصلت حالته'}
            </span>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3 }}>
              {blockers.fail.map((f) => (
                <span key={`f${f}`} style={{ fontSize: 10.5, padding: '1px 6px', borderRadius: 6, border: '1px solid var(--red)', color: 'var(--red)', whiteSpace: 'nowrap' }}>✗ {filterAr(f)}</span>
              ))}
              {blockers.miss.map((f) => (
                <span key={`m${f}`} style={{ fontSize: 10.5, padding: '1px 6px', borderRadius: 6, border: '1px solid var(--amber)', color: 'var(--amber)', whiteSpace: 'nowrap' }}>⃠ {filterAr(f)} — ما وصل حكمه</span>
              ))}
            </div>
          )}
          {blockers.seen != null ? <div className="dim num" style={{ fontSize: 10.5, marginTop: 4 }}>دورات {int(blockers.seen)} · مرقت {int(blockers.passed)}</div> : null}
        </div>
      ),
    },
    {
      id: 'money', title: 'المال', go: 'portfolios',
      render: () => (<div style={{ paddingTop: 4 }}>
        <Line k="حساب التنفيذ" v={(acc as { account_id?: string } | undefined)?.account_id ?? 'ما وصل'} />
        <Line k="القيمة / الرصيد" v={acc ? `${money(acc.equity)} · ${money(acc.balance)}` : '—'} />
        <Line k="الربح العائم" v={pnl == null ? '—' : `${pnl >= 0 ? '+' : ''}${money(pnl)}`} c={(pnl ?? 0) >= 0 ? 'var(--green)' : 'var(--red)'} />
        <Line k="صفقات مفتوحة" v={int(posState?.positions?.length ?? null)} />
        <Line k="التعرّض / التحوّط" v={`${dialPct == null ? '—' : `${Math.round(dialPct)}%`} · ${hedge == null ? '—' : `${Math.round(hedge * 100)}%`}`} />
      </div>),
    },
    {
      id: 'fleet', title: 'صحّة الأسطول', go: 'atoms',
      render: () => (<div style={{ paddingTop: 4 }}>
        <Line k="مسجّلة / شغّالة" v={`${int(fleet.total)} · ${int(fleet.running)}`} />
        <Line k="مش سليمة" v={int(fleet.sick.length)} c={fleet.sick.length ? 'var(--red)' : 'var(--green)'} />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 3, padding: '4px 9px' }}>
          {fleet.sick.map((s) => (
            <span key={s.id} className="num" title={`${s.name} — ${s.state}`}
              style={{ fontSize: 10.5, padding: '1px 5px', borderRadius: 5, border: '1px solid var(--glassb)', color: s.state === 'unhealthy' ? 'var(--red)' : 'var(--amber)' }}>{s.id}</span>
          ))}
        </div>
      </div>),
    },
    {
      id: 'feed', title: 'السوق والاتصال', go: 'market',
      render: () => (<div style={{ paddingTop: 4 }}>
        <Line k="الاتصال بالنواة" v={conn === 'live' ? 'حيّ' : conn === 'down' ? 'مقطوع' : 'جارٍ'} c={conn === 'live' ? 'var(--green)' : 'var(--red)'} />
        {(() => { const f = feedTxt(feedAge('feed.mt5.tick', 'platform.terminal_state', 'platform.account.state')); return <Line k="المنصّة" v={f[0]} c={f[1]} /> })()}
        {(() => { const f = feedTxt(feedAge('feed.ctrader.tick')); return <Line k="سي‑تريدر" v={f[0]} c={f[1]} /> })()}
        <Line k="تلغرام" v={!tg ? 'لا نعرف' : tg.running && tg.paired ? 'حيّ' : tg.running ? 'بلا اقتران' : tg.token ? 'متوقّف' : 'غير مفعّل'}
          c={tg?.running && tg?.paired ? 'var(--green)' : 'var(--amber)'} />
        <Line k="التداول عند المنصّة" v={term == null ? '—' : term.trade_allowed ? 'مسموح' : 'موقوف'} c={term?.trade_allowed ? 'var(--green)' : 'var(--amber)'} />
        <Line k="رموز تبثّ الآن" v={int(symbols.length)} />
      </div>),
    },

    { id: 'control', title: 'تحكّم', go: 'control', render: () => <Control /> },
    // ── الأقسام كما هي، بلا إعادة بناء ──
    { id: 'charts', title: 'الشارت', go: 'charts', render: () => <Charts /> },
    { id: 'risk', title: 'المخاطر', go: 'risk', render: () => <Risk /> },
    { id: 'alerts', title: 'التنبيهات', go: 'alerts', render: () => <Alerts /> },
    { id: 'network', title: 'الشبكة', go: 'network', render: () => <Network /> },
    { id: 'analysis', title: 'التحليل', go: 'analysis', render: () => <Analysis /> },
    { id: 'market', title: 'السوق', go: 'market', render: () => <Market /> },
    { id: 'execution', title: 'التنفيذ', go: 'execution', render: () => <Execution /> },
    { id: 'atoms', title: 'الذرات', go: 'atoms', render: () => <Atoms /> },
    { id: 'portfolios', title: 'المحافظ', go: 'portfolios', render: () => <Portfolios /> },
    { id: 'log', title: 'السجل', go: 'log', render: () => <Log /> },
    { id: 'monitor', title: 'المراقبة', go: 'monitor', render: () => <Monitor /> },
    { id: 'connection', title: 'الاتصال', go: 'connection', render: () => <Connection /> },
    { id: 'nq', title: 'ذرّة NQ', go: 'nq', render: () => <NQ /> },
    { id: 'dials', title: 'عيارات القرار', go: 'settings', render: () => <DecisionDialsCard /> },
  ]

  return (
    <div className="section" style={{ height: '100%', width: '100%', minWidth: 0, overflow: 'hidden' }}>
      {/* إقفال 150 مرحلة ٤/٥: الإنذار المبكّر + شريط القرار العلويّ — الروم الحيّ */}
      <EarlyWarningStrip />
      <RoomBar />
      <TileBoard defs={defs} layout={layout} save={save} reset={reset} onGo={onGo} />
    </div>
  )
}
