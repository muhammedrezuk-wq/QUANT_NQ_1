// محرّك الترجيح (580) — ث٣ بورقة ق١٠ §١٨–٢١ (ختم NQ بند 22 حزمة ث):
// • فوق: لوحة الحالة الحيّة من حدث tilt.state عبر البثّ القائم — القيمة الحالية
//   لكل حقل ومساهمته والمستوى النشط وسبب الترجيح (§٢٠–٢١) والمحصلة الخام والمقصوصة.
// • تحت: بطاقة لكل حقل قابل للترجيح — منحناه المخزون (عتبة ← مقدار بدقة ٤ منازل)
//   من /gov/tilt/rules (قراءة فقط من مخزن المحرّك) + محرّر نقاط كامل.
// الحفظ حصرًا عبر بوّابة الأوامر (٩٠١) action=tilt_rule بالتأكيد المزدوج القائم.
// ⛔ اللوحة لا تحسب ترجيحًا ولا تخترع منحنى ولا رقمًا — تعرض ما وصل،
//    والمجهول «مجهول» بخافت لا صفرًا.
import { useEffect, useState } from 'react'
import { useStore } from '../core/store'
import { confirmedCommand } from '../core/commands'
import { TILT_FIELD_AR, TILT_SIDE_AR } from '../core/arabic'

const TILT_FIELDS = Object.keys(TILT_FIELD_AR)
const MAX_POINTS = 12

// ملاحظة الصدق (الحقيقة الحالية للمحرّك) — تُعرض بوضوح فوق البطاقات
const HONESTY_NOTE = 'المحرك يبدأ بلا منحنيات وبحد أقصى 0.0 — لا ترجيح يصدر حتى تُنشئ القواعد ويُضبط الحد'

export interface TiltRule {
  field: string
  side: string
  points: [number, number][] | null // null = points_json فاسد بالمخزن (يُعلن، لا يُخترع [])
  enabled: boolean
  version?: number
  updated_at?: number
  updated_by?: string
}

// ——— أرقام لاتينية بدقة أربع منازل (عرف اللوحة nfmt) — والمجهول null لا صفر ———
const nfmt4 = (value: unknown): string | null =>
  typeof value === 'number' && Number.isFinite(value)
    ? value.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 4, minimumFractionDigits: 4 })
    : null
const signed4 = (value: unknown): string | null => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return (value > 0 ? '+' : '') + nfmt4(value)
}
const Unknown = () => <span className="dim">مجهول</span>
const numOrUnknown = (text: string | null) => (text == null ? <Unknown /> : <b className="num">{text}</b>)

const timeText = (value?: number) => {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null
  const millis = value > 10_000_000_000 ? value : value * 1000
  return new Date(millis).toLocaleString('ar-EG-u-nu-latn', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

// ——— قراءة متسامحة لحمولة tilt.state: نلتقط المفاتيح المعقولة ولا نخترع غائبًا ———
const asObj = (value: unknown): Record<string, unknown> | undefined =>
  value != null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : undefined
const pickNum = (obj: Record<string, unknown> | undefined, keys: string[]): number | undefined => {
  for (const key of keys) {
    const v = obj?.[key]
    if (typeof v === 'number' && Number.isFinite(v)) return v
  }
  return undefined
}
const fieldEntry = (payload: Record<string, unknown> | undefined, field: string): Record<string, unknown> | undefined => {
  for (const container of [payload?.fields, payload?.contributions]) {
    const entry = asObj(asObj(container)?.[field])
    if (entry) return entry
  }
  return asObj(payload?.[field])
}

// §٢١ — سبب الترجيح جملة مبنية من الأرقام الواصلة نفسها (ترجمة لا حساب):
// «الثقة الحالية 82.0000 · المستوى النشط بين 80 و85 · الترجيح +0.10»
function reasonText(label: string, value?: number, lo?: number, hi?: number, tilt?: number): string {
  const valuePart = `${label} الحالية ${nfmt4(value) ?? 'مجهولة'}`
  const levelPart = lo != null && hi != null
    ? `المستوى النشط بين ${nfmt4(lo)} و${nfmt4(hi)}`
    : lo != null ? `المستوى النشط ${nfmt4(lo)}` : 'المستوى النشط مجهول'
  const tiltPart = `الترجيح ${signed4(tilt) ?? 'مجهول'}`
  return `${valuePart} · ${levelPart} · ${tiltPart}`
}

// ——— لوحة الحالة الحيّة (§٢٠–٢١): من حدث tilt.state كما وصل — بلا أي حساب ———
function LiveStatePanel() {
  const live = useStore((s) => s.streams['tilt.state'])
  const payload = asObj(live)

  if (payload == null) {
    return (
      <div className="scard">
        <div className="st" style={{ fontWeight: 700 }}>الحالة الحيّة — ما وصل حدث بعد</div>
        <div className="ss dim">
          المحرّك (580) ينشر «حالة محرّك الترجيح» لحظة ما يشتغل ويقرأ قرارًا معتمدًا —
          حتى ذلك كل القيم مجهولة، ولا تُعرض أصفارًا مكانها.
        </div>
      </div>
    )
  }

  const symbol = typeof payload.symbol === 'string' && payload.symbol ? payload.symbol : null
  const raw = pickNum(payload, ['total_raw', 'raw_total', 'raw'])
  const clamped = pickNum(payload, ['total_clamped', 'clamped_total', 'clamped', 'total'])
  const cap = pickNum(payload, ['cap', 'max_tilt', 'tilt_cap', 'limit'])

  return (
    <div className="scard">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
        <div className="st" style={{ fontWeight: 700 }}>الحالة الحيّة{symbol ? ` — ${symbol}` : ''}</div>
        <span className="ss num" style={{ marginTop: 0, marginInlineStart: 'auto' }}>
          المحصلة الخام: {numOrUnknown(signed4(raw))} · المقصوصة: {numOrUnknown(signed4(clamped))}
          {cap != null ? <> · الحد الأقصى: <b className="num">{nfmt4(cap)}</b></> : null}
        </span>
      </div>
      <div style={{ overflowX: 'auto', marginTop: 8 }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr className="dim" style={{ textAlign: 'right' }}>
              <th style={{ padding: '5px 8px' }}>الحقل</th>
              <th style={{ padding: '5px 8px' }}>القيمة الحالية</th>
              <th style={{ padding: '5px 8px' }}>المستوى النشط</th>
              <th style={{ padding: '5px 8px' }}>المساهمة</th>
              <th style={{ padding: '5px 8px' }}>سبب الترجيح</th>
            </tr>
          </thead>
          <tbody>
            {TILT_FIELDS.map((field) => {
              const entry = fieldEntry(payload, field)
              const value = pickNum(entry, ['value', 'current', 'current_value'])
              const tilt = pickNum(entry, ['tilt', 'contribution', 'amount'])
              const lo = pickNum(entry, ['active_threshold', 'active_level', 'threshold', 'level'])
              const hi = pickNum(entry, ['next_threshold', 'upper_threshold', 'next_level'])
              const tiltClass = tilt == null ? 'grey' : tilt > 0 ? 'green' : tilt < 0 ? 'red' : 'grey'
              return (
                <tr key={field} style={{ borderTop: '1px solid var(--glassb)' }}>
                  <td style={{ padding: '7px 8px', fontWeight: 700, whiteSpace: 'nowrap' }}>{TILT_FIELD_AR[field]}</td>
                  <td style={{ padding: '7px 8px' }}>{numOrUnknown(nfmt4(value))}</td>
                  <td style={{ padding: '7px 8px' }}>
                    {lo == null ? <Unknown />
                      : <span className="num">{hi == null ? nfmt4(lo) : `بين ${nfmt4(lo)} و${nfmt4(hi)}`}</span>}
                  </td>
                  <td style={{ padding: '7px 8px' }}>
                    {tilt == null ? <Unknown /> : <span className={`pill ${tiltClass} num`}>{signed4(tilt)}</span>}
                  </td>
                  <td className="dim num" style={{ padding: '7px 8px', fontSize: 12.5 }}>
                    {reasonText(TILT_FIELD_AR[field], value, lo, hi, tilt)}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ——— محرّر منحنى حقل واحد: نقاط [عتبة ← مقدار] + جهة + تفعيل — الحفظ عبر ٩٠١ ———
interface DraftPoint { t: string; a: string }
const draftFrom = (rule?: TiltRule): DraftPoint[] =>
  (rule?.points ?? []).map(([t, a]) => ({ t: String(t), a: String(a) }))

function FieldCard({ field, rules }: { field: string; rules: TiltRule[] }) {
  const [side, setSide] = useState('up')
  const [points, setPoints] = useState<DraftPoint[]>(() => draftFrom(rules.find((r) => r.side === 'up')))
  const [enabled, setEnabled] = useState(() => rules.find((r) => r.side === 'up')?.enabled ?? true)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)

  const existing = rules.find((r) => r.side === side)
  // تبديل الجهة أو وصول نسخة أحدث من المخزن يعيد ملء المسودّة من الواقع
  useEffect(() => {
    const rule = rules.find((r) => r.side === side)
    setPoints(draftFrom(rule))
    setEnabled(rule?.enabled ?? true)
  }, [side, rules.map((r) => `${r.side}:${r.version}`).join('|')]) // eslint-disable-line react-hooks/exhaustive-deps

  const sortDraft = (draft: DraftPoint[]): DraftPoint[] =>
    draft.slice().sort((x, y) => Number(x.t) - Number(y.t))

  const save = async () => {
    const sorted = sortDraft(points) // ترتيب تلقائي بالعتبة قبل الإرسال
    const numeric: [number, number][] = []
    for (const p of sorted) {
      const t = Number(p.t), a = Number(p.a)
      if (p.t.trim() === '' || p.a.trim() === '' || !Number.isFinite(t) || !Number.isFinite(a)) {
        setNote({ ok: false, text: 'كل عتبة وكل مقدار يجب أن يكون رقمًا صالحًا' }); return
      }
      numeric.push([t, a])
    }
    if (numeric.some(([t], i) => i > 0 && t <= numeric[i - 1][0])) {
      setNote({ ok: false, text: 'عتبتان متساويتان — العتبات يجب أن تكون تصاعدية تمامًا' }); return
    }
    setPoints(sorted)
    setBusy(true); setNote(null)
    const r = await confirmedCommand('tilt_rule', { field, side, points: numeric, enabled })
    setBusy(false)
    setNote(r.ok
      ? { ok: true, text: r.message ?? 'أُرسل — بوّابة الأوامر (٩٠١) تنشره خلال ثانية والمحرّك (580) يطبّقه بمخزنه' }
      : { ok: false, text: r.message ?? 'تعذّر الإرسال' })
  }

  const cell = { padding: '4px 8px' } as const
  return (
    <div className="scard" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <div className="st" style={{ fontWeight: 700 }}>{TILT_FIELD_AR[field]}</div>
        <span className={`pill ${existing ? (existing.enabled ? 'green' : 'grey') : 'grey'}`} style={{ marginInlineStart: 'auto' }}>
          {existing
            ? existing.enabled ? `مفعّلة ✓${existing.version != null ? ` v${existing.version}` : ''}` : 'معطّلة'
            : 'لا منحنى لهذه الجهة بعد'}
        </span>
      </div>

      {/* المنحنيات المخزونة لهذا الحقل (كل الجهات) — كما وصلت من مخزن المحرّك */}
      {rules.length === 0
        ? <div className="ss dim">لا منحنى مخزونًا لهذا الحقل — المحرّك لا يرجّح به شيئًا الآن.</div>
        : rules.map((rule) => (
          <div key={rule.side} style={{ fontSize: 12.5 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
              <b>{TILT_SIDE_AR[rule.side] ?? rule.side}</b>
              <span className={`pill ${rule.enabled ? 'green' : 'grey'}`} style={{ fontSize: 11.5 }}>{rule.enabled ? 'مفعّلة' : 'معطّلة'}</span>
              {rule.updated_by ? <span className="dim num" style={{ fontSize: 11.5 }}>
                {rule.version != null ? `v${rule.version} · ` : ''}{rule.updated_by}{timeText(rule.updated_at) ? ` · ${timeText(rule.updated_at)}` : ''}
              </span> : null}
            </div>
            {rule.points == null
              ? <div className="dim">نقاط المنحنى بالمخزن غير مقروءة — تُعلن مجهولة ولا تُعرض قائمة فارغة مكانها.</div>
              : rule.points.length === 0
                ? <div className="dim">منحنى بلا نقاط (مُسح).</div>
                : <table className="num" style={{ borderCollapse: 'collapse', marginTop: 3 }}>
                    <thead><tr className="dim"><th style={cell}>العتبة</th><th style={cell}>←</th><th style={cell}>المقدار</th></tr></thead>
                    <tbody>
                      {rule.points.map(([t, a], i) => (
                        <tr key={i} style={{ borderTop: '1px solid var(--glassb)' }}>
                          <td style={cell}>{nfmt4(t) ?? <Unknown />}</td>
                          <td style={{ ...cell, opacity: .5 }}>←</td>
                          <td style={cell}>{signed4(a) ?? <Unknown />}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>}
          </div>
        ))}

      {/* المحرّر: إضافة/تعديل/حذف/ترتيب تلقائي/تفعيل — الحفظ بالتأكيد المزدوج */}
      <div style={{ borderTop: '1px solid var(--glassb)', paddingTop: 8, display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <label className="ss" style={{ marginTop: 0, display: 'flex', gap: 6, alignItems: 'center' }}>
            الجهة
            <select className="cfginput" style={{ margin: 0, padding: '4px 8px', fontSize: 13, width: 'auto' }}
              value={side} onChange={(e) => { setNote(null); setSide(e.target.value) }}>
              {Object.entries(TILT_SIDE_AR).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
          <label className="ss" style={{ marginTop: 0, display: 'flex', gap: 6, alignItems: 'center', cursor: 'pointer' }}>
            <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            القاعدة مفعّلة
          </label>
        </div>

        {points.map((p, i) => (
          <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
            <input className="cfginput num" style={{ margin: 0, padding: '5px 7px', fontSize: 13, width: 110 }}
              type="number" inputMode="decimal" step="0.0001" placeholder="العتبة" value={p.t}
              onChange={(e) => setPoints(points.map((q, j) => (j === i ? { ...q, t: e.target.value } : q)))} />
            <span className="dim">←</span>
            <input className="cfginput num" style={{ margin: 0, padding: '5px 7px', fontSize: 13, width: 110 }}
              type="number" inputMode="decimal" step="0.0001" placeholder="المقدار" value={p.a}
              onChange={(e) => setPoints(points.map((q, j) => (j === i ? { ...q, a: e.target.value } : q)))} />
            <button className="btn stop" style={{ padding: '4px 10px' }} title="حذف النقطة"
              onClick={() => setPoints(points.filter((_, j) => j !== i))}>حذف</button>
          </div>
        ))}

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="btn" disabled={points.length >= MAX_POINTS}
            onClick={() => setPoints([...points, { t: '', a: '' }])}>
            إضافة نقطة{points.length >= MAX_POINTS ? ' (بلغت الحد 12)' : ''}
          </button>
          <button className="btn" disabled={points.length < 2} title="ترتيب النقاط تصاعديًّا بالعتبة"
            onClick={() => setPoints(sortDraft(points))}>ترتيب بالعتبة</button>
          <button className="btn" disabled={busy} onClick={save}>
            {busy ? 'جارٍ الإرسال…' : 'حفظ عبر بوّابة الأوامر'}
          </button>
        </div>
        {note ? <div style={{ fontSize: 12.5, color: note.ok ? 'var(--green)' : 'var(--amber)' }}>{note.text}</div> : null}
      </div>
    </div>
  )
}

// ——— القسم كامل: ترويسة بملاحظة الصدق + الحالة الحيّة فوق + بطاقات الحقول ———
export default function TiltEngine() {
  const [data, setData] = useState<{ available?: boolean; rules?: TiltRule[] } | null>(null)
  const [err, setErr] = useState('')
  // الحدث الحي tilt.rules.state يصل عبر المحرّك إلى streams تلقائيًّا؛
  // كل نشرة (لحظة تطبيق 580 قاعدة المالك) تعيد الجلب من المخزن.
  const liveRules = useStore((s) => s.streams['tilt.rules.state'])
  const [refresh, setRefresh] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    fetch('/gov/tilt/rules', { signal: controller.signal })
      .then((r) => r.json())
      .then((d: { available?: boolean; rules?: TiltRule[] }) => { setData(d); setErr('') })
      .catch(() => setErr('تعذّر جلب قواعد الترجيح — تأكّد أن خادم الحوكمة شغّال'))
    return () => controller.abort()
  }, [liveRules, refresh])

  const rules = data?.rules ?? []
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div className="scard">
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap' }}>
          <div className="st" style={{ fontWeight: 700 }}>محرك الترجيح</div>
          <button className="btn" style={{ marginInlineStart: 'auto', padding: '4px 12px' }}
            onClick={() => setRefresh((n) => n + 1)}>تحديث</button>
        </div>
        <div className="ss dim">
          580 يقرأ مخرجات القرار المعتمد الثمانية ويرجّح الهدف بمنحنيات نقاط (عتبة ← مقدار) لكل حقل —
          صعودًا وهبوطًا وبالقيمة المطلقة، بلا شرائح ولا قفز (ق١٠). الحالة حاجز والوزن عامل — لا منحنى لهما.
          كل حفظ يمرّ ببوّابة الأوامر (٩٠١) بتأكيد بخطوتين.
        </div>
        <div style={{ marginTop: 7, fontSize: 13, color: 'var(--amber)', fontWeight: 600 }}>{HONESTY_NOTE}</div>
        {data?.available === false
          ? <div className="ss dim">مخزن قواعد الترجيح غير متاح بعد — يتكوّن مع أول تشغيل للمحرّك (580)؛ يمكنك إنشاء القواعد الآن وتُطبَّق لحظة ما يشتغل.</div>
          : null}
        {err ? <div style={{ marginTop: 6, fontSize: 13, color: 'var(--amber)' }}>{err}</div> : null}
      </div>

      <LiveStatePanel />

      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(330px, 1fr))' }}>
        {TILT_FIELDS.map((field) => (
          <FieldCard key={field} field={field} rules={rules.filter((r) => r.field === field)} />
        ))}
      </div>
    </div>
  )
}
