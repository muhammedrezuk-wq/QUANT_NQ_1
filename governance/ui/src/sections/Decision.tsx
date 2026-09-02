// القرار (855) — القرار النهائي لكل رمز، بسلسلته الكاملة مقاسة من الكود الحيّ:
// 451 تجميع ← 452 تقييم ← 453 درجة ← 455/456/457 أهلية ← 458 حسم ← 454 حواجز ← 466 موافقة ← 467 بوابة.
// حزمة ج (ختم 22 بند 29): ج١ بطاقة الرمز بالثمانية الواصلة للقرار، ج٢ صفحة القرار
// الكاملة (بطاقة 12 حقلًا + الحواجز + الأهلية الثلاث + رحلة القرار + سجل 707).
// كل رقم من حدث حقيقي مصدره symbolStreams (محرّك حزمة ج، core/engine.ts) — لا حساب
// بالواجهة؛ المجهول «مجهول» لا صفر (م٥)، و«451 لا ينشر ratio» يُقال صراحة لا يُخترع.
import { useEffect, useMemo, useState } from 'react'
import { useStore } from '../core/store'
import { arabicVisible, fieldAr } from '../core/arabic'
import { SectionAtomsHealth, SectionConfigTable } from '../components/SectionAtoms'
import { blockedByAr } from './Execution'
import TiltEngine from './TiltEngine'
import { DecisionDialsCard, DECISION_DIAL_NAMES } from './Settings'

type Rec = Record<string, unknown>

const SIG: Record<string, { t: string; a: string; c: string }> = {
  buy: { t: 'شراء', a: '▲', c: 'green' }, sell: { t: 'بيع', a: '▼', c: 'red' },
  wait: { t: 'انتظار', a: '⏸', c: 'grey' }, none: { t: 'لا قرار', a: '·', c: 'grey' },
}
const sigOf = (s?: unknown) => SIG[String(s ?? '')] ?? { t: 'غير معروف', a: '·', c: 'grey' }

const num = (n: unknown, dp = 2): string =>
  typeof n === 'number' && Number.isFinite(n)
    ? n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: dp, minimumFractionDigits: dp })
    : '—'
const timeText = (v: unknown): string => {
  const n = typeof v === 'number' ? v : undefined
  if (!n) return 'لم يصل'
  const ms = n > 10_000_000_000 ? n : n * 1000
  return new Date(ms).toLocaleString('ar-EG-u-nu-latn', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}
const fmtAny = (v: unknown): string => {
  if (v == null) return '—'
  if (typeof v === 'number') return num(v)
  if (typeof v === 'boolean') return v ? 'نعم' : 'لا'
  const s = String(v)
  return SIG[s] ? SIG[s].t : arabicVisible(s)
}

// ——— ج١ — الثماني الواصلة للقرار (451 تجميع + 453 درجة) ———
const AGG_STATE: Record<string, { text: string; cls: string }> = {
  READY: { text: 'جاهزة للقرار', cls: 'green' },
  ANALYZING: { text: 'قيد التحليل', cls: 'amber' },
  NOT_READY: { text: 'غير جاهزة', cls: 'grey' },
  // بختم NQ بند 26: «متقادم» أحمر أينما ظهر
  STALE: { text: 'متقادمة', cls: 'red' },
}
const aggStateOf = (s?: unknown) => AGG_STATE[String(s ?? '')] ?? { text: arabicVisible(s, 'حالة غير مترجمة'), cls: 'grey' }

const SOURCE_LABEL: Record<string, string> = {
  '150': 'التحليل', '166': 'دمج التحليل', '200': 'البنية', '250': 'السيولة',
  '300': 'الإحصاء', '350': 'الاحتمالات', '400': 'الاستراتيجيات', '401': 'إشارة الدخول',
}
const SCORE_WARN_AR: Record<string, string> = {
  NO_ELIGIBLE_EVIDENCE: 'لا دليل مؤهّل بعد',
  INCOMPLETE_DECISION_CYCLE: 'دورة القرار غير مكتملة',
  LOW_PARTICIPATION: 'مشاركة الأدلة تحت الحد الأدنى — حُيِّد الاتجاه',
}

function EightSummaryCard({ symbol }: { symbol: string }) {
  const agg = useStore((s) => s.symbolStreams['decision.aggregated.state']?.[symbol]) as Rec | undefined
  const scored = useStore((s) => s.symbolStreams['decision.scored.state']?.[symbol]) as Rec | undefined
  if (!agg && !scored) {
    return <div className="ss dim">لم تصل بعد دورة تجميع (451) ولا درجة (453) لهذا الرمز.</div>
  }
  const depthUnknown = Array.isArray(agg?.depth_unknown_fields) ? (agg!.depth_unknown_fields as unknown[]).map(String) : []
  const depthVal = (field: string) => {
    const v = agg?.[field]
    if (depthUnknown.includes(field) || typeof v !== 'number') return <span className="dim">مجهول</span>
    return <b className="num">{num(v, 1)}</b>
  }
  const st = aggStateOf(agg?.aggregate_state)
  const evidence = Array.isArray(agg?.evidence) ? (agg!.evidence as Rec[]) : []
  const dirV = scored?.direction_value
  const strV = scored?.strength_value
  const confV = scored?.confidence_value
  const weightV = agg?.active_weight
  const warn = Array.isArray(scored?.warnings) ? (scored!.warnings as unknown[]).map(String) : []
  const dirColor = typeof dirV === 'number' ? (dirV > 5 ? 'var(--green)' : dirV < -5 ? 'var(--red)' : undefined) : undefined

  return (
    <div style={{ border: '1px solid var(--glassb)', borderRadius: 10, padding: '9px 12px', display: 'flex', flexDirection: 'column', gap: 7 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13 }}>الثماني الواصلة للقرار (451 + 453)</b>
        <span className={`pill ${st.cls}`} style={{ marginInlineStart: 'auto' }}>{st.text}</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', fontSize: 13, alignItems: 'center' }}>
        <span><span className="dim">{fieldAr('direction')}</span> {typeof dirV === 'number'
          ? <b className="num" style={{ color: dirColor }}>{dirV > 0 ? '+' : ''}{num(dirV)}</b>
          : <span className="dim">مجهول</span>}</span>
        <span><span className="dim">{fieldAr('strength')}</span> {typeof strV === 'number' ? <b className="num">{num(strV)}</b> : <span className="dim">مجهول</span>}</span>
        <span><span className="dim">{fieldAr('confidence')}</span> {typeof confV === 'number' ? <b className="num">{num(confV)}</b> : <span className="dim">مجهول</span>}</span>
        <span><span className="dim">{fieldAr('current_depth')}</span> {depthVal('current_depth')} <span className="dim">من</span> {depthVal('required_depth')}</span>
        <span><span className="dim">{fieldAr('weight')}</span> {typeof weightV === 'number' ? <b className="num">{num(weightV, 0)}</b> : <span className="dim">مجهول</span>}</span>
        <span title="451 لا ينشر حقل ratio بحمولته الحالية — لا تُحسب بالواجهة">
          <span className="dim">{fieldAr('ratio')}</span> <span className="dim">مجهول — لا يُنشر</span>
        </span>
      </div>
      {warn.length ? <div className="ss dim">تنبيه الدرجة: {warn.map((w) => SCORE_WARN_AR[w] ?? arabicVisible(w)).join(' · ')}</div> : null}
      {evidence.length ? (
        <div className="ss dim">
          سطر مصادر: {evidence.map((e, i) => (
            <span key={i}>{i > 0 ? ' · ' : ''}{SOURCE_LABEL[String(e.source ?? '')] ?? arabicVisible(e.source, 'مصدر غير معروف')}
              {' '}({e.weight_known ? num((e.weight_effect as number | undefined) ?? (e.weight as number | undefined), 0) : '—'})</span>
          ))}
        </div>
      ) : null}
    </div>
  )
}

// ——— ج٢.١ — بطاقة القرار (الهوية الست + الجانب + الموافقة + الثماني + وقت البوابة) ———
function idField(key: string, value: unknown) {
  return (
    <span key={key}><span className="dim">{fieldAr(key)}</span>{' '}
      <b className="num" style={{ fontSize: 12.5 }}>{value == null || value === '' ? '—' : String(value)}</b></span>
  )
}

function DecisionHeaderCard({ symbol }: { symbol: string }) {
  const agg = useStore((s) => s.symbolStreams['decision.aggregated.state']?.[symbol]) as Rec | undefined
  const approved = useStore((s) => s.symbolStreams['decision.approved.state']?.[symbol]) as Rec | undefined
  const resolved = useStore((s) => s.symbolStreams['decision.resolved.state']?.[symbol]) as Rec | undefined
  const gatePassed = useStore((s) => s.symbolStreams['decision.gate.passed']?.[symbol]) as Rec | undefined
  const gateBlocked = useStore((s) => s.symbolStreams['decision.gate.blocked']?.[symbol]) as Rec | undefined
  const gateRecorded = useStore((s) => s.symbolStreams['decision.gate.recorded']?.[symbol]) as Rec | undefined

  const identity = agg ?? approved ?? resolved ?? {}
  const side = (approved?.decision_side ?? resolved?.decision_side) as string | undefined
  const sig = sigOf(side)
  const isApproved = approved?.approved as boolean | undefined
  const gate = gatePassed ?? gateBlocked ?? gateRecorded
  const gateLabel = gatePassed ? 'مرّت البوّابة ✓' : gateBlocked ? 'حجبتها البوّابة' : gateRecorded ? 'سُجّلت انتظارًا' : 'لم تصل البوّابة بعد'
  const gateColor = gatePassed ? 'green' : gateBlocked ? 'red' : gateRecorded ? 'amber' : 'grey'

  return (
    <div className="scard" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700, fontSize: 14 }}>{symbol}</span>
        <span className={`pill ${sig.c}`} style={{ fontSize: 12 }}>{sig.a} {sig.t}</span>
        <span className={`pill ${isApproved === true ? 'green' : isApproved === false ? 'red' : 'grey'}`}>
          {isApproved === true ? 'مُعتمَد ✓' : isApproved === false ? 'مرفوض' : 'بلا قرار موافقة بعد'}
        </span>
        <span className={`pill ${gateColor}`} style={{ marginInlineStart: 'auto' }}>{gateLabel}</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px 12px', fontSize: 11.5 }}>
        {idField('account_id', identity.account_id)}
        {idField('broker', identity.broker)}
        {idField('timeframe', identity.timeframe)}
        {idField('period_start', identity.period_start)}
        {idField('decision_id', identity.decision_id ?? gate?.decision_id)}
        {idField('gate_request_id', gate?.gate_request_id)}
      </div>
      <div className="ss dim">وقت البوابة: {timeText(gate?.gated_at)}</div>
      <EightSummaryCard symbol={symbol} />
    </div>
  )
}

// ——— ج٢.٢ — جدول الحواجز (454) ———
const BARRIER_REASON_AR: Record<string, string> = {
  DECISION_SIDE_UNKNOWN: 'الجانب غير معروف من 458',
  DECISION_SIDE_WAIT: 'الجانب انتظار — لا اتجاه للتنفيذ',
  SCORE_UNKNOWN: 'الدرجة غير معروفة',
  SCORE_BELOW_MIN: 'الدرجة تحت العتبة الدنيا',
  FILTER_VERDICT_MISSING: 'حكم الفلتر غائب',
  FILTER_VERDICT_STALE: 'حكم الفلتر متقادم',
  FILTER_CYCLE_MISMATCH: 'حكم الفلتر من دورة مختلفة',
  FILTER_FAILED: 'الفلتر رفض',
  CALENDAR_UNKNOWN: 'التقويم الاقتصادي مجهول',
  CALENDAR_EVENT_WINDOW: 'داخل نافذة حدث اقتصادي',
  NEWS_WINDOW_BLOCK: 'داخل نافذة خبر مؤثّر',
  MARKET_QUALITY_UNKNOWN: 'جودة السوق مجهولة',
  MARKET_QUALITY_INVALID: 'جودة السوق ساقطة',
  FEED_NOT_ACTIVE: 'التغذية غير نشطة',
}

function BarriersTable({ symbol }: { symbol: string }) {
  const filtered = useStore((s) => s.symbolStreams['decision.filtered.state']?.[symbol]) as Rec | undefined
  const approved = useStore((s) => s.symbolStreams['decision.approved.state']?.[symbol]) as Rec | undefined
  const rows = (Array.isArray(filtered?.barriers) ? filtered!.barriers
    : Array.isArray(approved?.barriers) ? approved!.barriers : []) as Rec[]
  if (!filtered && !approved) return null
  return (
    <div className="scard" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '9px 12px', borderBottom: '1px solid var(--glassb)' }}>
        <div className="st">جدول الحواجز — فلتر القرار (454)</div>
      </div>
      {rows.length === 0 ? (
        <div className="ss dim" style={{ padding: '9px 12px' }}>لا حواجز مسجّلة — آخر قرار مرّ الفلتر بلا حجب.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead><tr className="dim" style={{ textAlign: 'right' }}>
              <th style={{ padding: 6 }}>الحاجز</th><th style={{ padding: 6 }}>القيمة</th><th style={{ padding: 6 }}>العتبة</th>
              <th style={{ padding: 6 }}>السبب</th><th style={{ padding: 6 }}>وقت القياس</th>
            </tr></thead>
            <tbody>
              {rows.map((b, i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--glassb)' }}>
                  <td style={{ padding: 6, color: 'var(--red)', fontWeight: 600, whiteSpace: 'nowrap' }}>{blockedByAr(String(b.name ?? ''))}</td>
                  <td style={{ padding: 6 }} className="num">{fmtAny(b.value)}</td>
                  <td style={{ padding: 6 }} className="num">{fmtAny(b.threshold)}</td>
                  <td style={{ padding: 6 }}>{BARRIER_REASON_AR[String(b.reason ?? '')] ?? arabicVisible(b.reason, 'سبب غير مترجَم')}</td>
                  <td style={{ padding: 6, fontSize: 11.5 }} className="dim">{timeText(b.measured_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ——— ج٢.٣ — بطاقات الأهلية الثلاث (455 شراء · 456 بيع · 457 انتظار) ———
const CHECK_FIELD_AR: Record<string, string> = {
  direction: 'الاتجاه', direction_buy: 'اتجاه الشراء', direction_sell: 'اتجاه البيع',
  strength: 'القوّة', confidence: 'الثقة', current_depth: 'العمق الحالي', state: 'الحالة',
}
const ELIG_REASON_AR: Record<string, string> = {
  DIRECTION_BELOW_THRESHOLD: 'الاتجاه تحت العتبة',
  DIRECTION_ABOVE_THRESHOLD: 'الاتجاه فوق العتبة (جهة البيع)',
  DIRECTION_INSUFFICIENT: 'الاتجاه غير كافٍ لأي جهة',
  STRENGTH_BELOW_THRESHOLD: 'القوّة تحت العتبة',
  CONFIDENCE_BELOW_THRESHOLD: 'الثقة تحت العتبة',
  DEPTH_BELOW_THRESHOLD: 'العمق الحالي تحت العتبة',
  STATE_NOT_READY: 'الحالة ليست جاهزة',
  NOT_STARTED: 'لم تُشغَّل',
  NO_INPUT_YET: 'لم يصلها مدخل بعد',
  BUY_SIDE_ELIGIBLE: 'جهة الشراء مؤهّلة — لا داعي للانتظار',
  SELL_SIDE_ELIGIBLE: 'جهة البيع مؤهّلة — لا داعي للانتظار',
  BOTH_SIDES_ELIGIBLE: 'الجهتان مؤهّلتان — الحسم لذرّة 458',
}
const eligReasonAr = (r?: unknown): string => {
  const s = String(r ?? '')
  if (!s) return '—'
  if (s.startsWith('FIELD_UNKNOWN:')) return `${fieldAr(s.split(':')[1] ?? '')} مجهول`
  return ELIG_REASON_AR[s] ?? arabicVisible(s, 'سبب غير مترجَم')
}

function EligibilityMiniCard({ symbol, event, title, kind }: { symbol: string; event: string; title: string; kind: 'buy' | 'sell' | 'wait' }) {
  const data = useStore((s) => s.symbolStreams[event]?.[symbol]) as Rec | undefined
  const [open, setOpen] = useState(false)
  if (!data) {
    return <div className="scard"><div className="st">{title}</div><div className="ss dim">لم يصل حدثها بعد لهذا الرمز</div></div>
  }
  const status = String(data.status ?? '')
  const text = kind === 'wait' ? (status === 'eligible' ? 'ينتظر ⏸' : 'لا ينتظر') : (status === 'eligible' ? 'مؤهّل ✅' : 'غير مؤهّل ❌')
  const cls = kind === 'wait' ? (status === 'eligible' ? 'amber' : 'green') : (status === 'eligible' ? 'green' : 'red')
  const checks = Array.isArray(data.checks) ? (data.checks as Rec[]) : []
  return (
    <div className="scard" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div className="st" style={{ margin: 0 }}>{title}</div>
        <span className={`pill ${cls}`} style={{ marginInlineStart: 'auto' }}>{text}</span>
      </div>
      <div className="ss">{eligReasonAr(data.reason)}</div>
      {checks.length ? (
        <div>
          <button className="btn" style={{ fontSize: 11.5 }} onClick={() => setOpen(!open)}>
            {open ? '▴ خبّي الفحوص' : `▾ الفحوص (${checks.length})`}
          </button>
          {open ? (
            <div style={{ overflowX: 'auto', marginTop: 6 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                <thead><tr className="dim" style={{ textAlign: 'right' }}>
                  <th style={{ padding: 4 }}>الفحص</th><th style={{ padding: 4 }}>القيمة</th><th style={{ padding: 4 }}>العتبة</th><th style={{ padding: 4 }}>النتيجة</th>
                </tr></thead>
                <tbody>
                  {checks.map((c, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--glassb)' }}>
                      <td style={{ padding: 4 }}>{CHECK_FIELD_AR[String(c.name ?? '')] ?? fieldAr(String(c.name ?? ''))}</td>
                      <td style={{ padding: 4 }} className="num">{fmtAny(c.value)}</td>
                      <td style={{ padding: 4 }} className="num">{fmtAny(c.threshold)}</td>
                      <td style={{ padding: 4 }}>{c.passed === true ? <span className="green">نجح</span> : <span className="red">فشل</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

// ——— ج٢.٤ — رحلة القرار (450 السجل الموحّد + أحداث السلسلة) ———
const JOURNEY_STAGES: Array<{ events: string[]; label: string }> = [
  { events: ['decision.aggregated.state'], label: 'تجميع (451)' },
  { events: ['decision.evaluated.state'], label: 'تقييم (452)' },
  { events: ['decision.scored.state'], label: 'درجة (453)' },
  { events: ['decision.eligibility.buy.state', 'decision.eligibility.sell.state', 'decision.wait.state'], label: 'أهلية (455/456/457)' },
  { events: ['decision.resolved.state'], label: 'حسم (458)' },
  { events: ['decision.filtered.state'], label: 'حواجز (454)' },
  { events: ['decision.approved.state'], label: 'موافقة (466)' },
  { events: ['decision.gate.passed', 'decision.gate.blocked', 'decision.gate.recorded'], label: 'بوابة (467)' },
]

function JourneyTimeline({ symbol }: { symbol: string }) {
  const symbolStreams = useStore((s) => s.symbolStreams)
  const record = useStore((s) => s.symbolStreams['decision.cycle.record']?.[symbol]) as Rec | undefined
  return (
    <div className="scard">
      <div className="st">رحلة القرار — تجميع←تقييم←درجة←أهلية←حسم←حواجز←موافقة←بوابة</div>
      <div className="ss dim">
        {record?.decision_id ? `معرّف القرار: ${String(record.decision_id)}` : 'معرّف القرار لم يصل بعد بسجلّ 450 لهذا الرمز'}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginTop: 8 }}>
        {JOURNEY_STAGES.map((stage, i) => {
          const hit = stage.events.some((e) => symbolStreams[e]?.[symbol] != null)
          return (
            <span key={stage.label} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {i > 0 ? <span className="dim">←</span> : null}
              <span className={`pill ${hit ? 'green' : 'grey'}`}>{stage.label}</span>
            </span>
          )
        })}
      </div>
      {record ? (
        <div className="ss dim" style={{ marginTop: 8 }}>
          الحسم النهائي بسجلّ 450: {record.final_decision ? sigOf(record.final_decision).t : '—'}
          {record.final_reason ? ` — ${arabicVisible(record.final_reason, String(record.final_reason))}` : ''}
          {Array.isArray(record.missing_identity) && (record.missing_identity as unknown[]).length
            ? ` · هوية ناقصة: ${(record.missing_identity as unknown[]).map((f) => fieldAr(String(f))).join('، ')}` : ''}
        </div>
      ) : (
        <div className="ss dim" style={{ marginTop: 8 }}>لم يصل سجلّ 450 الموحّد لهذا الرمز بعد — المراحل أعلاه من أحداثها المباشرة.</div>
      )}
    </div>
  )
}

// ——— ج٢.٥ — سجل مخزن دورة حياة التنفيذ (707) — قراءة REST مباشرة، لا بثّ ———
interface DecisionRow {
  id: number; stage?: string; request_id?: string; account_id?: string; symbol?: string
  direction?: string; approved?: number | null; reason?: string; confidence?: number
  strategy_id?: string; model_id?: string; volume?: number; stop_loss?: number
  take_profit?: number; decided_at?: number; decision_id?: string; gate_request_id?: string
}
interface DecisionsPayload { available: boolean; decisions: DecisionRow[]; has_link_columns: boolean }

function DecisionsLedger() {
  const [data, setData] = useState<DecisionsPayload | null>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    const c = new AbortController()
    fetch('/gov/decisions?limit=30', { signal: c.signal })
      .then((r) => r.json() as Promise<DecisionsPayload>)
      .then(setData)
      .catch(() => setErr('تعذّر جلب سجلّ 707 — تأكّد أن المنفذ /gov/decisions حيّ (منفذ جديد يحتاج إعادة تشغيل خادم الحوكمة)'))
    return () => c.abort()
  }, [])

  return (
    <div className="scard" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '9px 12px', borderBottom: '1px solid var(--glassb)' }}>
        <div className="st">سجل مخزن دورة حياة التنفيذ (707)</div>
        <div className="ss dim">آخر القرارات/الأوامر كما كتبها 707 — قراءة فقط. عمودا الربط (معرّف القرار/طلب البوابة) يظهران فقط إذا أثبتتهما القاعدة الحيّة فعليًّا.</div>
      </div>
      {err ? <div className="ss" style={{ padding: '9px 12px', color: 'var(--amber)' }}>{err}</div> : null}
      {!data ? <div className="ss dim" style={{ padding: '9px 12px' }}>جارٍ الجلب…</div>
        : !data.available ? <div className="empty">قاعدة 707 غير متاحة من الخادم الآن (المسار غائب، أو المنفذ الجديد /gov/decisions لم يُحمَّل حيًّا بعد — يحتاج إعادة تشغيل).</div>
        : data.decisions.length === 0 ? <div className="empty">القاعدة فاضية — صفر صفّ حتى الآن.</div>
          : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
                <thead><tr className="dim" style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                  <th style={{ padding: 6 }}>الوقت</th><th style={{ padding: 6 }}>الرمز</th><th style={{ padding: 6 }}>المرحلة</th>
                  <th style={{ padding: 6 }}>الاتجاه</th><th style={{ padding: 6 }}>معتمد</th><th style={{ padding: 6 }}>السبب</th>
                  <th style={{ padding: 6 }}>الحجم</th>
                  {data.has_link_columns ? <><th style={{ padding: 6 }}>معرّف القرار</th><th style={{ padding: 6 }}>معرّف طلب البوابة</th></> : null}
                </tr></thead>
                <tbody>
                  {data.decisions.map((d) => (
                    <tr key={d.id} style={{ borderBottom: '1px solid var(--glassb)' }}>
                      <td style={{ padding: 6, whiteSpace: 'nowrap' }} className="dim">{timeText(d.decided_at)}</td>
                      <td style={{ padding: 6, fontWeight: 600 }}>{d.symbol ?? '—'}</td>
                      <td style={{ padding: 6 }}>{d.stage ? arabicVisible(d.stage, d.stage) : '—'}</td>
                      <td style={{ padding: 6 }}>{d.direction ? sigOf(d.direction).t : '—'}</td>
                      <td style={{ padding: 6 }}>{d.approved == null ? '—' : d.approved ? <span className="green">نعم</span> : <span className="red">لا</span>}</td>
                      <td style={{ padding: 6 }}>{d.reason ? arabicVisible(d.reason, d.reason) : '—'}</td>
                      <td style={{ padding: 6 }} className="num">{fmtAny(d.volume)}</td>
                      {data.has_link_columns ? (<>
                        <td style={{ padding: 6, fontSize: 11 }} className="dim">{d.decision_id ?? '—'}</td>
                        <td style={{ padding: 6, fontSize: 11 }} className="dim">{d.gate_request_id ?? '—'}</td>
                      </>) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
    </div>
  )
}

// ——— الصفحة ———
export default function Decision() {
  const decision = useStore((s) => s.decision)
  const symbolStreams = useStore((s) => s.symbolStreams)
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})

  const syms = useMemo(() => {
    const set = new Set<string>(Object.keys(decision))
    for (const bucket of Object.values(symbolStreams)) for (const sym of Object.keys(bucket)) set.add(sym)
    return Array.from(set).sort()
  }, [decision, symbolStreams])

  if (syms.length === 0) {
    // بند ١٠ (ورقة ٩٩): لا صفحة فاضية وذرّات القسم حيّة — حالتها الفعلية بدل الفراغ.
    return (
      <div className="section chartsec">
        <div className="empty">بانتظار أوّل دورة قرار من النواة… (تحتاج إشارات استراتيجيات مكتملة)</div>
        <TiltEngine />
        <SectionAtomsHealth from={450} to={500} title="ذرّات قسم القرار — حالتها الحيّة الآن"
          note="ما وصلت دورة قرار بعد — هاي حالة ذرّات القسم نفسها من النواة، مو تخمين." />
      </div>
    )
  }

  return (
    <div className="section chartsec" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <DecisionDialsCard onlyNames={DECISION_DIAL_NAMES} includeExtras={false} />
      {syms.map((sym) => (
        <div key={sym} className="scard" style={{ display: 'flex', flexDirection: 'column', gap: 8, background: 'transparent', border: 'none', padding: 0 }}>
          <DecisionHeaderCard symbol={sym} />
          <button className="btn" style={{ fontSize: 12, alignSelf: 'flex-start' }}
            onClick={() => setExpanded((e) => ({ ...e, [sym]: !e[sym] }))}>
            {expanded[sym] ? '▴ خبّي تفاصيل القرار' : '▾ تفاصيل القرار الكاملة — الحواجز، الأهلية الثلاث، رحلة القرار'}
          </button>
          {expanded[sym] ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <BarriersTable symbol={sym} />
              <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))' }}>
                <EligibilityMiniCard symbol={sym} event="decision.eligibility.buy.state" title="أهلية الشراء (455)" kind="buy" />
                <EligibilityMiniCard symbol={sym} event="decision.eligibility.sell.state" title="أهلية البيع (456)" kind="sell" />
                <EligibilityMiniCard symbol={sym} event="decision.wait.state" title="الانتظار (457)" kind="wait" />
              </div>
              <JourneyTimeline symbol={sym} />
            </div>
          ) : null}
        </div>
      ))}

      <DecisionsLedger />
      <TiltEngine />
      <SectionConfigTable from={450} to={500} title="معاملات ذرّات القرار (450-499) — ضبط جماعي (تجميع 451 · تقييم 452 · درجة 453 · أهلية 455/456/457 · تعارض 458)" />
    </div>
  )
}
