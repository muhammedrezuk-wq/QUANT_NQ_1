import { useMemo, useState } from 'react'
import { useStore, type AtomRec } from '../core/store'
import './crypto-dashboard.css'
import { priceText } from '../core/i18n'

type Tab = 'overview' | 'universe' | 'pattern' | 'trigger' | 'senses' | 'health' | 'log'
type AnyMap = Record<string, any>
type MarketTick = { bid?: number; ask?: number; ts?: number }

const SOURCE_IDS = [1001, 1002]
const SOURCE_NAMES: Record<number, string> = { 1001: 'مدير كون الأصول', 1002: 'تغذية السوق' }
const asMap = (value: unknown): AnyMap => value && typeof value === 'object' ? value as AnyMap : {}
const text = (value: unknown, fallback = '—'): string => value === null || value === undefined || value === '' ? fallback : typeof value === 'object' ? fallback : String(value)
const num = (value: unknown, digits = 2): string => { const n = Number(value); return Number.isFinite(n) ? n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: digits }) : '—' }
const colorFor = (value: unknown): string => { const s = String(value ?? '').toLowerCase(); if (s.includes('healthy') || s.includes('active') || s === 'ok' || s === 'running') return 'green'; if (s.includes('await') || s.includes('stale') || s.includes('unknown')) return 'amber'; if (s.includes('fail') || s.includes('error') || s.includes('unhealthy')) return 'red'; return 'grey' }
const labelFor = (name: string): string => ({
  'crypto.decision.card': 'بطاقة القرار', 'crypto.pattern.state': 'النمط', 'crypto.trigger.state': 'محكمة الزناد',
  'crypto.feed.state': 'حالة التغذية', 'crypto.universe.snapshot.state': 'Snapshot الكون',
}[name] ?? name)

function Empty({ children }: { children: string }) { return <div className="crypto-empty">{children}</div> }

function MetricCard({ title, value, tone = '' }: { title: string; value: unknown; tone?: string }) {
  return <div className="crypto-stat"><span>{title}</span><strong className={tone}>{text(value)}</strong></div>
}

function MarketRow({ row }: { row: AnyMap }) {
  return <div className="crypto-market-row"><b>{text(row.symbol)}</b><span>{text(row.asset_class)}</span><span>${num(row.amount24_usd, 0)}</span><span>{num(row.spread_ticks)} ticks</span><span>{num(row.daily_range_pct)}%</span><span className={`crypto-state ${colorFor(row.membership)}`}>{text(row.membership, 'ACTIVE')}</span></div>
}

// ٢٠٢٦-٠٩-٠١: أسماء الحقول كانت لا تطابق ما تنشره الذرّة 2277 إطلاقًا.
// المنشور فعليًّا: entry_price · stop_loss · take_profit · take_profit_2 ·
// take_profit_runner · entry_class · grade · ring · gate_margin · stop_pct.
// والبطاقة كانت تقرأ card.stop / card.take_profit_1 — أسماء غير موجودة —
// فيظهر الوقف والهدف «—» حتى حين تصل بطاقة صحيحة كاملة.
const CLASS_AR: Record<string, string> = {
  '①rejection_at_level': '① ارتداد عن مستوى',
  '②break_retest': '② كسر وإعادة اختبار',
  '③filtered_break': '③ كسر مُرشَّح',
}
const classLabel = (v: unknown): string => {
  const k = String(v ?? '')
  return CLASS_AR[k] ?? (k || 'بلا صنف')
}
/** رقم الصنف للترتيب (①=1 ②=2 ③=3) — الترتيب الموثَّق، لا ترتيب الوصول. */
const classOrder = (v: unknown): number => {
  const k = String(v ?? '')
  const i = '①②③④⑤'.indexOf(k.charAt(0))
  return i < 0 ? 9 : i + 1
}
const dirAr = (v: unknown): string =>
  ({ long: 'شراء', short: 'بيع', buy: 'شراء', sell: 'بيع' } as Record<string, string>)[String(v ?? '').toLowerCase()] ?? text(v)

// ٢٠٢٦-٠٩-٠١ (حكم المالك: «في قسم بشرح ليش الصفقة كانت لتتاخذ وما تاخذت…
// هي ما هي مسجّلة خالص مع إنها موثّقة»). القسم كان موجودًا شكلًا: تبويب
// «محكمة الزناد» يرمي `JSON.stringify(value)` خامًا على الشاشة — وهذا يخالف
// قاعدة اللوحة نفسها («المالك لا يقرأ إنجليزي/خام — فلا حالة نيّئة تظهر»).
// الحمولة الحقيقيّة من الذرّة 2273:
//   { symbol, long:{verdict, votes:{aggressor,walls,oi,premium,lens}},
//     short:{…}, senses_seen:[…] }
// و«ليش ما دخلت» = الحكم + أي حاسّة صوّتت وأيّها صمتت. تُعرض كما هي.
const SENSE_AR: Record<string, string> = {
  aggressor: 'المهاجم', walls: 'الجدران', oi: 'المراكز المفتوحة',
  premium: 'العلاوة', lens: 'العدسة',
}
const VERDICT_AR: Record<string, [string, string]> = {
  PASS: ['عبرت', 'up'], ENTER: ['عبرت', 'up'],
  ABSTAIN: ['امتنعت', 'muted'], VETO: ['فيتو', 'down'], BLOCK: ['محجوبة', 'down'],
}

function TriggerSide({ label, side }: { label: string; side: AnyMap }) {
  const votes = asMap(side.votes)
  const names = Object.keys(votes)
  const yes = names.filter((k) => Number(votes[k]) > 0)
  const no = names.filter((k) => !(Number(votes[k]) > 0))
  const [vAr, vCls] = VERDICT_AR[String(side.verdict ?? '').toUpperCase()] ?? [text(side.verdict), 'muted']
  return <div className="crypto-trigger-side">
    <div className="crypto-trigger-head"><b>{label}</b><span className={`crypto-verdict ${vCls}`}>{vAr}</span></div>
    <div className="crypto-votes">
      {names.map((k) => <span key={k} className={`crypto-vote ${Number(votes[k]) > 0 ? 'up' : 'off'}`}>
        {SENSE_AR[k] ?? k}
      </span>)}
    </div>
    <small>{yes.length ? `صوّتت: ${yes.map((k) => SENSE_AR[k] ?? k).join(' · ')}` : 'لم تصوّت أي حاسّة'}
      {no.length ? ` — صامتة: ${no.map((k) => SENSE_AR[k] ?? k).join(' · ')}` : ''}</small>
  </div>
}

function TriggerCourt({ row }: { row: AnyMap }) {
  const seen = Array.isArray(row.senses_seen) ? (row.senses_seen as unknown[]).map(String) : []
  return <article className="crypto-trigger">
    <header><b>{text(row.symbol)}</b><span>حواسّ واصلة: {seen.length ? seen.map((k) => SENSE_AR[k] ?? k).join(' · ') : 'لا شيء'}</span></header>
    <div className="crypto-trigger-sides">
      <TriggerSide label="شراء" side={asMap(row.long)} />
      <TriggerSide label="بيع" side={asMap(row.short)} />
    </div>
  </article>
}

function DecisionCard({ card }: { card: AnyMap }) {
  const dir = String(card.direction ?? card.side ?? '').toLowerCase()
  const side = dir === 'long' || dir === 'buy' ? 'up' : dir === 'short' || dir === 'sell' ? 'down' : ''
  return <div className={`crypto-decision-card ${side}`}>
    <div className="crypto-decision-head">
      <b>{text(card.symbol)}</b>
      <span className={`crypto-side ${side}`}>{dirAr(card.direction ?? card.side)}</span>
      <span className="crypto-class">{classLabel(card.entry_class)}</span>
    </div>
    <div className="crypto-decision-grid">
      <div><span>دخول</span><b>{priceText(card.entry_price ?? card.entry)}</b></div>
      <div><span>وقف</span><b className="down">{priceText(card.stop_loss ?? card.stop ?? card.stop_price)}</b></div>
      <div><span>هدف 1</span><b className="up">{priceText(card.take_profit ?? card.target1 ?? card.take_profit_1)}</b></div>
      <div><span>هدف 2</span><b className="up">{priceText(card.take_profit_2 ?? card.target2)}</b></div>
      <div><span>عدّاء</span><b>{card.take_profit_runner == null ? 'لا يوجد' : priceText(card.take_profit_runner)}</b></div>
      <div><span>إلغاء</span><b>{priceText(card.cancel_level)}</b></div>
      <div><span>الوقف %</span><b>{num(card.stop_pct, 2)}%</b></div>
      <div><span>هامش البوّابة</span><b>{num(card.gate_margin, 3)}</b></div>
      <div><span>الحلقة</span><b>{text(card.ring)}</b></div>
      <div><span>الدرجة</span><b>{text(card.grade, 'بلا درجة')}</b></div>
      <div><span>أقصى مخاطرة</span><b>{num(card.max_risk_usd, 2)}$</b></div>
      <div><span>الترتيب بين المنافسات</span><b>{num(card.competing_rank, 0)} / {num(card.competing_count, 0)}</b></div>
    </div>
    <small>مصدر الهدف: {text(card.take_profit_source)} · وقف زمنيّ بعد {num(card.time_stop_candles, 0)} شمعات · بيانات عرض فقط — لا تنفيذ آلي</small>
  </div>
}

export default function CryptoDashboard() {
  const [tab, setTab] = useState<Tab>('overview')
  const [overrideSymbol, setOverrideSymbol] = useState('')
  const [controlMessage, setControlMessage] = useState('')
  const conn = useStore((s) => s.conn)
  const atoms = useStore((s) => s.atoms)
  const streams = useStore((s) => s.streams)
  const symbolStreams = useStore((s) => s.symbolStreams) as Record<string, Record<string, unknown>>
  const market = useStore((s) => s.market) as Record<string, MarketTick>
  const events = useStore((s) => s.events)
  const universe = asMap(streams['crypto.universe.snapshot.state'])
  const membership = asMap(streams['crypto.universe.membership.state'])
  const feed = asMap(streams['crypto.feed.state'])
  const coreRows = Array.isArray(universe.core) ? universe.core as AnyMap[] : []
  const outerRows = Array.isArray(universe.outer) ? universe.outer as AnyMap[] : []
  const rejected = Array.isArray(universe.rejected) ? universe.rejected as AnyMap[] : []
  const symbols = useMemo(() => Array.from(new Set([...Object.keys(market), ...coreRows.map((r) => String(r.symbol ?? '')), ...outerRows.map((r) => String(r.symbol ?? ''))].filter(Boolean))).sort(), [market, coreRows, outerRows])
  const senseRows = useMemo(() => Object.entries(streams).filter(([name]) => name.startsWith('sense.') || name.startsWith('micro.')).sort(([a], [b]) => a.localeCompare(b)), [streams])
  const patternRows = useMemo(() => Object.entries(streams).filter(([name]) => /pattern|classification|decision\.type/i.test(name)), [streams])
  const triggerRows = useMemo(() => {
    const byName = symbolStreams['crypto.decision.trigger_court.state'] ?? {}
    return Object.values(byName).map(asMap).filter((r) => r.symbol)
      .sort((a, b) => String(a.symbol).localeCompare(String(b.symbol)))
  }, [symbolStreams])
  // ٢٠٢٦-٠٩-٠١: كان `.find(...)` — بطاقة **واحدة** حتى لو وصلت عشر بطاقات.
  // وأسوأ: النمط `/decision\.card|decision\.state|crypto\.card/` لا يطابق
  // اسم الحدث الحيّ `crypto.decision.signal_card.state` أصلًا، فلم تكن تظهر
  // بطاقة قطّ. الآن تُقرأ من الخريطة الرمزيّة (بطاقة لكل عملة) وتُرتَّب
  // بترتيب الصنف الموثَّق ①②③ ثم بالرمز.
  const signalCards = useMemo(() => {
    const byName = symbolStreams['crypto.decision.signal_card.state'] ?? {}
    return Object.values(byName).map(asMap).filter((c) => c.symbol)
      .sort((a, b) => (classOrder(a.entry_class) - classOrder(b.entry_class))
        || String(a.symbol).localeCompare(String(b.symbol)))
  }, [symbolStreams])
  const connectionLabel = conn === 'live' ? 'متصل حيًا' : conn === 'connecting' ? 'جارِ الاتصال' : 'الاتصال مقطوع'
  const tabs: [Tab, string][] = [['overview', 'نظرة عامة'], ['universe', 'كون الأصول'], ['pattern', 'النمط'], ['trigger', 'محكمة الزناد'], ['senses', 'الحواس'], ['health', 'الصحة'], ['log', 'السجل']]
  const sendOverride = async (decision: 'ALLOW' | 'DENY' | 'NEUTRAL') => {
    const symbol = overrideSymbol.trim().toUpperCase()
    if (!/^[A-Z0-9]+_[A-Z0-9]+$/.test(symbol)) { setControlMessage('أدخل رمزًا بصيغة BASE_QUOTE مثل BTC_USDT'); return }
    if (!window.confirm(`${decision} للعضوية فقط: ${symbol}؟`)) return
    const response = await fetch('/gov/universe/override', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbol, decision, operator: 'dashboard' }),
    }).catch(() => null)
    setControlMessage(response?.ok ? `تم إرسال ${decision} للكون: ${symbol}` : 'تعذّر إرسال أمر العضوية')
  }
  const requestScan = async () => {
    const response = await fetch('/gov/universe/scan', { method: 'POST' }).catch(() => null)
    setControlMessage(response?.ok ? 'تم طلب إعادة مسح الكون' : 'تعذّر طلب إعادة المسح')
  }

  return <section className="crypto-root" dir="rtl">
    <header className="crypto-hero"><div><div className="crypto-kicker">UNIFIED.LOCAL · CRYPTO</div><h1>لوحة الكريبتو</h1><p>Futures USDT فقط · بطاقة ومراقبة · التنفيذ اليدوي على MEXC</p></div><div className="crypto-hero-actions"><span className={`crypto-pill ${colorFor(conn)}`}>{connectionLabel}</span><span className="crypto-pill blue">NO AUTO EXECUTION</span><button className="crypto-refresh" onClick={() => window.location.reload()}>تحديث</button></div></header>
    <nav className="crypto-tabs" aria-label="أقسام لوحة الكريبتو">{tabs.map(([id, label]) => <button key={id} className={tab === id ? 'active' : ''} onClick={() => setTab(id)}>{label}</button>)}</nav>

    {tab === 'overview' && <>
      <div className="crypto-stat-grid"><MetricCard title="حالة التغذية" value={connectionLabel} tone={colorFor(conn)} /><MetricCard title="إصدار الكون" value={universe.universe_version} /><MetricCard title="Core" value={coreRows.length} /><MetricCard title="Outer" value={outerRows.length} /></div>
      <div className="crypto-panel"><div className="crypto-panel-head"><h2>البطاقات الحيّة</h2><span>بطاقة لكل عملة فور وصولها — مرتّبة بترتيب الصنف ①②③</span></div>{signalCards.length ? <div className="crypto-cards">{signalCards.map((c) => <DecisionCard key={String(c.symbol)} card={c} />)}</div> : <Empty>لا توجد بطاقة قرار فعلية الآن. لا توجد بطاقة مختلَقة.</Empty>}</div>
      <div className="crypto-panel"><div className="crypto-panel-head"><h2>حالة الكون والتغذية</h2><span>{text(universe.status, universe.universe_version ? 'ACTIVE' : 'بانتظار أول مسح')}</span></div><div className="crypto-health-grid"><div><span>المصدر</span><b>{text(universe.source, 'mexc.contract.ticker.all')}</b></div><div><span>الرموز المرئية</span><b>{symbols.length}</b></div><div><span>Ticks منشورة</span><b>{num(feed.published_ticks, 0)}</b></div><div><span>آخر تغذية</span><b>{feed.last_feed_at ? new Date(Number(feed.last_feed_at) * 1000).toLocaleString('ar') : '—'}</b></div></div></div>
      <div className="crypto-panel"><div className="crypto-panel-head"><h2>الرموز الفعلية</h2><span>من membership فقط — بلا قائمة ثابتة</span></div>{symbols.length ? <div className="crypto-symbol-grid">{symbols.map((symbol) => { const t = market[symbol] ?? {}; return <article className="crypto-symbol" key={symbol}><div className="crypto-symbol-title"><b>{symbol}</b><span className="crypto-live-dot" /></div><div className="crypto-price-row"><span>Bid</span><strong>{num(t.bid, 8)}</strong></div><div className="crypto-price-row"><span>Ask</span><strong>{num(t.ask, 8)}</strong></div></article> })}</div> : <Empty>لا توجد رموز مؤهلة بعد.</Empty>}</div>
    </>}

    {tab === 'universe' && <div className="crypto-panel"><div className="crypto-panel-head"><h2>كون الأصول</h2><span>ALLOW / DENY / CLEAR OVERRIDE تخص العضوية فقط</span></div><div className="crypto-health-grid"><div><span>آخر مسح</span><b>{universe.scan_finished_at ? new Date(Number(universe.scan_finished_at) * 1000).toLocaleString('ar') : '—'}</b></div><div><span>Universe Version</span><b>{text(universe.universe_version)}</b></div><div><span>Core</span><b>{coreRows.length}</b></div><div><span>Outer</span><b>{outerRows.length}</b></div></div><h3 className="crypto-subtitle">Core (~12)</h3><div className="crypto-market-table"><div className="crypto-market-row crypto-table-head"><b>الرمز</b><span>الأصل</span><span>سيولة/24س</span><span>سبريد</span><span>مدى %</span><span>الحالة</span></div>{coreRows.map((r) => <MarketRow key={String(r.symbol)} row={r} />)}</div>{!coreRows.length && <Empty>لا توجد Core مؤهلة.</Empty>}<h3 className="crypto-subtitle">Outer (~15)</h3><div className="crypto-market-table">{outerRows.map((r) => <MarketRow key={String(r.symbol)} row={r} />)}</div>{!outerRows.length && <Empty>لا توجد Outer مؤهلة.</Empty>}<h3 className="crypto-subtitle">مرفوض اليوم</h3><div className="crypto-rejected-list">{rejected.map((r, i) => <article className="crypto-rejected" key={`${r.symbol}-${i}`}><b>{text(r.symbol)}</b><span>{Array.isArray(r.reasons) ? r.reasons.join(' · ') : text(r.reasons)}</span></article>)}</div>{!rejected.length && <Empty>لا يوجد رفض في آخر Snapshot.</Empty>}<div className="crypto-controls"><input value={overrideSymbol} onChange={(event) => setOverrideSymbol(event.target.value)} placeholder="BTC_USDT" aria-label="رمز العضوية" /><button onClick={() => void sendOverride('ALLOW')}>ALLOW</button><button onClick={() => void sendOverride('DENY')}>DENY</button><button onClick={() => void sendOverride('NEUTRAL')}>CLEAR OVERRIDE</button><button onClick={() => void requestScan()}>تحديث الكون</button></div>{controlMessage && <div className="crypto-control-message">{controlMessage}</div>}<div className="crypto-policy">الأزرار المسموحة: ALLOW · DENY · CLEAR OVERRIDE للعضوية فقط. ممنوع شراء أو بيع أو تنفيذ أو تعديل قرار.</div></div>}

    {tab === 'pattern' && <div className="crypto-panel"><div className="crypto-panel-head"><h2>النمط — ذرّة 454</h2><span>① ② ③ · عرض ما وصل فقط</span></div>{patternRows.length ? <div className="crypto-list">{patternRows.map(([name, value]) => <div className="crypto-list-row" key={name}><b>{labelFor(name)}</b><span>{JSON.stringify(value)}</span></div>)}</div> : <Empty>لا يوجد حدث نمط فعلي في Phase A. لا يتم اختراع تصنيف.</Empty>}</div>}

    {tab === 'trigger' && <div className="crypto-panel"><div className="crypto-panel-head"><h2>محكمة الزناد — ذرّة 455</h2><span>لماذا لم تدخل الصفقة — الحكم وأي حاسّة صوّتت وأيّها صمتت · فيتو AND · لا تنفيذ من اللوحة</span></div>{triggerRows.length ? <div className="crypto-trigger-grid">{triggerRows.map((r) => <TriggerCourt key={String(r.symbol)} row={r} />)}</div> : <Empty>لا يوجد قرار زناد فعلي الآن. لا يوجد «عبرت» مختلَق.</Empty>}<div className="crypto-policy">المسموح: تحديث أو تأكيد تنبيه. الممنوع دائمًا: شراء، بيع، تنفيذ آلي، ومفتاح تداول.</div></div>}

    {tab === 'senses' && <div className="crypto-panel"><div className="crypto-panel-head"><h2>الحواس — 151–267</h2><span>طبقة عرض مستقبلية، لا تُشغّل من Phase A</span></div>{senseRows.length ? <div className="crypto-sense-grid">{senseRows.map(([name, value]) => <article className="crypto-sense" key={name}><b>{name}</b><span className={`crypto-state ${colorFor(mapOf(value).state)}`}>{text(mapOf(value).state, 'وصلت')}</span><p>{JSON.stringify(value)}</p></article>)}</div> : <Empty>لا حواس مشغّلة في هذه النسخة. هذا مقصود: Phase A = كون وتغذية فقط.</Empty>}</div>}

    {tab === 'health' && <div className="crypto-panel"><div className="crypto-panel-head"><h2>الصحة</h2><span>كل الذرات المكتشفة في مسار الكريبتو</span></div><div className="crypto-health-grid">{Object.values(atoms).sort((a: AtomRec, b: AtomRec) => a.id - b.id).map((atom: AtomRec) => <div key={atom.id}><span>{atom.id} · {atom.name_ar ?? atom.name}</span><b className={`crypto-state ${colorFor(atom.health?.state ?? atom.state)}`}>{atom.state} · {atom.health?.message ?? '—'}</b></div>)}</div></div>}

    {tab === 'log' && <div className="crypto-panel"><div className="crypto-panel-head"><h2>السجل</h2><span>الأحداث الفعلية فقط</span></div>{events.length ? <div className="crypto-event-list">{events.map((event) => <div className="crypto-event" key={event.id}><time>{new Date(event.ts).toLocaleTimeString('ar')}</time><b>{event.name}</b><span>{event.detail ?? '—'} {event.n > 1 ? `×${event.n}` : ''}</span></div>)}</div> : <Empty>السجل فارغ حاليًا.</Empty>}</div>}

    <footer className="crypto-footer">البطاقة معلومة، لا أمر. التنفيذ اليدوي فقط على MEXC. الزر الوحيد الذي يغيّر المسار هو تبديل فوركس ⇄ كريبتو داخل نفس التطبيق.</footer>
  </section>
}
