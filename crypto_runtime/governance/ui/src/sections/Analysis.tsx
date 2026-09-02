import AnalystsPanel from './AnalystsPanel'
// التحليل الحي المستقل: العرض من النواة، واللوحة تضبط العمق والعيار والوزن فقط.
// بند 2ب (دفتر 97، مواصفة المالك الحرفيّة): «حفظ إعدادات الكل» دفعة واحدة ثم
// «تحديث» يتحقّق بقراءة مستقلّة من الخادم (لا ثقة بتقرير المنفّذ) ثم «تمّ الضبط».
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AnalysisSettingsCard } from './Settings'
import { AccountsPair } from '../components/AccountsBar'
import { useStore, type AnalyzerContribution, type AnalysisState, type PathCard, type SectionFusionState } from '../core/store'
import { ANALYZER_AR, analyzerLabel } from '../core/analyzerLabels'
import { arabicVisible, fieldAr } from '../core/arabic'
import { confirmedCommand, confirmedCommandMany } from '../core/commands'
// قارئ العيارات الحاكم يُعاد استعماله كما هو (والحفظ عبر بوّابة الأوامر نفسها)
import { useDecisionDials, type Dial } from './Settings'

const ANALYZERS = Object.keys(ANALYZER_AR).filter((id) => id !== 'fusion')
const DEFAULT_WEIGHTS: Record<string, number> = {
  trend: 15, momentum: 10, volatility: 8, volume: 8, spread: 7,
  candle: 8, gap: 5, session: 5, time: 4, velocity: 7,
  acceleration: 6, volume_quality: 6, noise: 5, correlation: 3,
  relative_strength: 3,
}

interface Setting {
  required_depth: number
  confidence_threshold: number
  // ختم المالك ٢٠٢٦-٠٨-٢١ — ثلاث عتبات نزلت من الكود المحفور إلى المعايرة
  strength_threshold: number
  stale_after_s: number
  direction_neutral_band: number
  weight: number
  revision?: number
}

// حقول العتبات بترتيب عرضها وباسمها العربي وشرح ما تفعله (ملحق اللوحات §١٩:
// ممنوع إعداد بلا معنى واضح). المدى واحد لكلٍّ: 0–100، يفرضه المخزن والبوّابة.
const DIAL_FIELDS: { key: keyof Setting; label: string; hint: string }[] = [
  { key: 'required_depth', label: 'العمق المطلوب', hint: 'كم يلزم أن يجمع قبل أن يُسمح له بالكلام. تحته: قيد التحليل.' },
  { key: 'confidence_threshold', label: 'عتبة الثقة', hint: 'نضج الدليل المطلوب. تحتها: غير جاهز.' },
  { key: 'strength_threshold', label: 'عتبة القوّة', hint: 'أدنى قوّة تستحقّ الجاهزية. صفر = لا حجب بالقوّة.' },
  { key: 'stale_after_s', label: 'مهلة الطزاجة (ثانية)', hint: 'بعدها تُعلَن النتيجة متقادمة.' },
  { key: 'direction_neutral_band', label: 'المنطقة الحيادية', hint: 'أقلّ درجة تُسمّى اتجاهًا. تحتها: عرضي.' },
]

// مفتاح سرعة التحليل الساري (العام المعتمد) — لعرض «السارية» بجانب مهلة الطزاجة.
// المخزَّن هنا هو الأساس عند نقطة التطابق 50؛ والسارية = الأساس × (50/السرعة)
// محصورة [0.5ث، الأساس×5] — نفس معادلة المحرك حرفيًّا (هندسة ١٧ §4).
// ذاكرة قصيرة مشتركة بين الصفوف: جلبة واحدة لكل نافذة ثانيتين.
let speedCache: { at: number; value: number } | null = null
async function fetchAnalysisSpeed(): Promise<number> {
  if (speedCache && Date.now() - speedCache.at < 2000) return speedCache.value
  try {
    const d = await (await fetch('/gov/parameters')).json() as { parameters?: { name: string; scope: string; status: string; value: number }[] }
    const row = (d.parameters ?? []).find((p) => p.name === 'ANALYSIS_SPEED' && p.scope === 'global' && p.status === 'APPROVED')
    speedCache = { at: Date.now(), value: row ? row.value : 50 }
  } catch { speedCache = { at: Date.now(), value: speedCache?.value ?? 50 } }
  return speedCache.value
}
const effectiveStale = (base: number, speed: number) =>
  Math.max(0.5, Math.min(base * 5, base * (50 / Math.max(1, speed))))

type SettingsMap = Record<string, Setting>

// صفٌّ لكل (مسار، محلّل) — المفتاح `fast:trend` / `slow:trend`. المساران
// مستقلّان بحكم ورقة المالك (اجوبة §٢٦)، فلا يشتركان بصفٍّ ولا بمرجع.
//: دقّة النظام المعلَنة — المخزن يقرّب عند الحفظ، والأوراق تكتب `55.0000`.
const round4 = (value?: number | null) =>
  value == null || !Number.isFinite(value) ? 0 : Math.round(value * 10000) / 10000

// الافتراضات = القيم النافذة بالمحرّك حرفيًّا (shared/live_analysis.DIAL_DEFAULTS)
const defaults = (): SettingsMap => Object.fromEntries(
  ANALYSIS_PATHS.flatMap(({ id: path }) => ANALYZERS.map((id) => [
    rowKey(path, id),
    {
      required_depth: 60, confidence_threshold: 60, strength_threshold: 0,
      stale_after_s: 5, direction_neutral_band: 5,
      weight: round4(DEFAULT_WEIGHTS[id] ?? 0), revision: 0,
    },
  ])),
)

const STATES: Record<string, { text: string; cls: string }> = {
  ANALYZING: { text: 'قيد التحليل', cls: 'amber' },
  NOT_READY: { text: 'غير جاهز', cls: 'grey' },
  DECISION_READY: { text: 'جاهز للقرار', cls: 'green' },
  VALID: { text: 'صالح', cls: 'green' },
  // بختم NQ (بند 26): «المتقادم» أحمر بالمواصفة أينما عُرض.
  STALE: { text: 'قديم', cls: 'red' },
  INVALID: { text: 'غير صالح', cls: 'red' },
  ERROR: { text: 'خطأ', cls: 'red' },
}

const SIGNALS: Record<string, { text: string; arrow: string; cls: string }> = {
  up: { text: 'صاعد', arrow: '▲', cls: 'green' },
  down: { text: 'هابط', arrow: '▼', cls: 'red' },
  sideways: { text: 'عرضي', arrow: '▬', cls: 'grey' },
}

const nfmt = (value?: number | null, digits = 1) =>
  value == null || !Number.isFinite(value) ? '—' : value.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: digits, minimumFractionDigits: digits })
const percent = (value?: number | null) => value == null ? '—' : `${nfmt(value)}%`
const timeText = (value?: number) => {
  if (!value) return 'لم يصل'
  const millis = value > 10_000_000_000 ? value : value * 1000
  return new Date(millis).toLocaleString('ar-EG-u-nu-latn', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

// ——— وزن المسار نفسه — مستوى القسم لا مستوى المحلّل (اجوبة §٢٢) ———
// مستويان منفصلان من الأوزان: وزن المحلّل داخل مساره (جدول الأسفل)، ووزن
// المسار داخل مجمّع القسم (هنا). كان الثاني غائبًا عن هذه الصفحة كلّيًّا رغم
// أنّه العيار الذي يقرّر كم يزن كل مسار بالنتيجة النهائية.
// المصدر والتعديل بالمكوّن الحاكم نفسه (`DialRow` ببوّابة الأوامر وتأكيد
// مزدوج) — لا نسخة ثانية من منطق الحفظ.
const PATH_DIALS = ['ANALYSIS_FAST_WEIGHT', 'ANALYSIS_SLOW_WEIGHT',
  'ANALYSIS_FAST_REQUIRED_DEPTH', 'ANALYSIS_SLOW_REQUIRED_DEPTH']

const PATH_DIAL_AR: Record<string, string> = {
  ANALYSIS_FAST_WEIGHT: 'وزن السريع',
  ANALYSIS_SLOW_WEIGHT: 'وزن البطيء',
  ANALYSIS_FAST_REQUIRED_DEPTH: 'عمق السريع',
  ANALYSIS_SLOW_REQUIRED_DEPTH: 'عمق البطيء',
}

// خانة واحدة مرصوصة لكل عيار. المكوّن الحاكم `DialRow` بطاقة كاملة بارتفاع
// شاشة لكل عيار — أربعة منها تبتلع الصفحة. المعروض هنا مختصر، **ومنطق الحفظ
// هو نفسه**: `confirmedCommand('decision_setting')` ببوّابة الأوامر وتأكيد
// مزدوج — لا طريق ثانٍ للكتابة.
function PathDialCell({ dial }: { dial: Dial }) {
  const [draft, setDraft] = useState(() => dial.value.toFixed(2))
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  useEffect(() => { setDraft(dial.value.toFixed(2)) }, [dial.value, dial.version, dial.status])
  const [lo, hi] = dial.bounds
  const save = async () => {
    const n = Number(draft)
    if (draft.trim() === '' || !Number.isFinite(n)) { setNote({ ok: false, text: 'رقم غير صالح' }); return }
    if (n < lo || n > hi) { setNote({ ok: false, text: `خارج ${lo}–${hi}` }); return }
    setBusy(true); setNote(null)
    const r = await confirmedCommand('decision_setting', { name: dial.name, value: Math.round(n * 100) / 100 })
    setBusy(false)
    setNote(r.ok ? { ok: true, text: 'أُرسل' } : { ok: false, text: r.message ?? 'تعذّر' })
  }
  const dirty = Number(draft) !== Number(dial.value.toFixed(2))
  return (
    <div style={{ display: 'grid', gap: 3 }} title={dial.where}>
      <span className="dim" style={{ fontSize: 11.5 }}>
        {PATH_DIAL_AR[dial.name] ?? dial.name}
        {dial.status === 'APPROVED' ? null : <span style={{ color: 'var(--amber)' }}> · غير معتمد</span>}
      </span>
      <div style={{ display: 'flex', gap: 5 }}>
        <input className="cfginput" style={{ margin: 0, padding: '5px 7px', fontSize: 13, width: '100%' }}
          type="number" min={lo} max={hi} step="any"
          value={draft} onChange={(e) => setDraft(e.target.value)} />
        <button className="btn" style={{ padding: '4px 10px', fontSize: 12.5 }}
          disabled={busy || !dirty} onClick={() => void save()}>{busy ? '…' : 'حفظ'}</button>
      </div>
      {note ? <span style={{ fontSize: 11.5, color: note.ok ? 'var(--green)' : 'var(--amber)' }}>{note.text}</span> : null}
    </div>
  )
}

const OWNER_KEYS: { name: string; label: string; hint: string; merged?: boolean }[] = [
  { name: 'MASTER_KEY', label: 'مدموج', hint: 'يحرّك السرعة والأفق والحدود مع بعض. 50 = محايد.', merged: true },
  { name: 'ANALYSIS_SPEED', label: 'سرعة', hint: 'قدّيش التحليل سريع. أعلى = نوافذ أقصر.' },
  { name: 'TRADING_HORIZON', label: 'أفق', hint: 'أعلى = سكالب أضيق. أدنى = سوينغ أوسع.' },
  { name: 'QUALITY_BAR', label: 'حدود', hint: 'أعلى = أشد قبولًا. أدنى = أسهل.' },
]

function OwnerKeyKnob({ dial, label, hint, merged }: { dial: Dial; label: string; hint: string; merged?: boolean }) {
  const [draft, setDraft] = useState(() => String(Math.round(dial.value * 100) / 100))
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  useEffect(() => { setDraft(String(Math.round(dial.value * 100) / 100)) }, [dial.value, dial.version, dial.status])
  const [lo, hi] = dial.bounds
  const n = Number(draft)
  const dirty = Number.isFinite(n) && Math.abs(n - dial.value) > 0.001
  const save = async () => {
    if (draft.trim() === '' || !Number.isFinite(n)) { setNote({ ok: false, text: 'رقم غير صالح' }); return }
    if (n < lo || n > hi) { setNote({ ok: false, text: `من ${lo} إلى ${hi}` }); return }
    setBusy(true); setNote(null)
    const r = await confirmedCommand('decision_setting', { name: dial.name, value: Math.round(n * 100) / 100 })
    setBusy(false)
    setNote(r.ok ? { ok: true, text: 'أُرسل' } : { ok: false, text: r.message ?? 'تعذّر' })
  }
  return (
    <div className={`owner-key${merged ? ' merged' : ''}`} title={hint}>
      <div className="owner-key-head">
        <b>{label}</b>
        <em className="num">{Number.isFinite(n) ? n.toFixed(0) : '—'}</em>
      </div>
      <input type="range" min={lo} max={hi} step={1} value={Number.isFinite(n) ? n : 50}
        onChange={(e) => setDraft(e.target.value)} />
      <div className="owner-key-row">
        <input className="cfginput num" type="number" min={lo} max={hi} step={0.01}
          value={draft} onChange={(e) => setDraft(e.target.value)} />
        <button className="btn" disabled={busy || !dirty} onClick={() => void save()}>{busy ? '…' : 'حفظ'}</button>
      </div>
      {dial.status !== 'APPROVED' ? <span className="dim" style={{ fontSize: 11 }}>غير معتمد — 50 نقطة التطابق</span> : null}
      {note ? <span style={{ fontSize: 11.5, color: note.ok ? 'var(--green)' : 'var(--amber)' }}>{note.text}</span> : null}
    </div>
  )
}

function OwnerKeysBar() {
  const { dials, err } = useDecisionDials(OWNER_KEYS.map((k) => k.name))
  return (
    <div className="scard owner-keys">
      <div className="st" style={{ fontWeight: 700 }}>مفاتيح التحليل — الثلاثة + المدموج</div>
      <div className="ss dim" style={{ marginBottom: 8 }}>
        المدموج يحرّك الثلاثة مع بعض. بعد حفظه بتقدر تلفّ أي واحد لحالو. 50 = سلوك اليوم. ما بيفتح صفقة.
      </div>
      {err ? <div className="ss" style={{ color: 'var(--amber)' }}>{err}</div> : null}
      {dials == null ? <div className="ss dim">جارٍ جلب المفاتيح…</div>
        : (
          <div className="owner-keys-grid">
            {OWNER_KEYS.map((k) => {
              const dial = (dials ?? []).find((d) => d.name === k.name)
              if (!dial) return (
                <div key={k.name} className="owner-key dim">{k.label} — ما وصل من الخادم</div>
              )
              return <OwnerKeyKnob key={k.name} dial={dial} label={k.label} hint={k.hint} merged={k.merged} />
            })}
          </div>
        )}
    </div>
  )
}

function PathWeightsCard() {
  const { dials, err } = useDecisionDials()
  const rows = PATH_DIALS
    .map((name) => (dials ?? []).find((d) => d.name === name))
    .filter((d): d is Dial => !!d)
  const fast = rows.find((d) => d.name === 'ANALYSIS_FAST_WEIGHT')?.value
  const slow = rows.find((d) => d.name === 'ANALYSIS_SLOW_WEIGHT')?.value
  const total = fast != null && slow != null ? fast + slow : null
  const balanced = total != null && Math.abs(total - 100) < 0.01
  return (
    <div className="scard">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
        <div className="st">وزن المسارين — مستوى القسم</div>
        <div className="ss dim" style={{ fontSize: 12 }}>
          حصّة كل مسار في نتيجة القسم — غير أوزان المحلّلين داخله. تعديل أحدهما يضبط الآخر تلقائيًّا.
        </div>
        {total != null ? (
          <span className={`pill ${balanced ? 'green' : 'amber'}`} style={{ marginInlineStart: 'auto' }}>
            المجموع {nfmt(total, 2)}{balanced ? '' : ' ⚠️'}
          </span>
        ) : null}
      </div>
      {err ? <div className="ss" style={{ color: 'var(--amber)' }}>{err}</div> : null}
      {dials == null ? <div className="ss dim">جارٍ القراءة…</div>
        : rows.length === 0 ? <div className="ss dim">لم تصل عيارات المسارين من الخادم.</div>
          : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(178px, 1fr))', gap: 10 }}>
              {rows.map((d) => <PathDialCell key={d.name} dial={d} />)}
            </div>
          )}
    </div>
  )
}

// ختم المالك ٢٠٢٦-٠٨-٢١ — المساران صفّان لا صفّ: ورقة المالك (اجوبة §٢ و§٢٦)
// تنصّ أن نفس الـ15 محلّلًا لهم رؤيتان — تِكّات وشموع — **بإعدادات مستقلّة**:
// «تغيير إعداد في السريع لا يغيّر إعداد البطيء». والجدول كان يعرض صفًّا واحدًا
// ويرسل أمره بلا `path`، فكل تعديل كان يهبط على المسار السريع وحده والبطيء
// لا طريق له من اللوحة أصلًا. المخزن والبوّابة يحملان بُعد المسار من قبل.
export const ANALYSIS_PATHS = [
  { id: 'fast' as const, label: 'سريع', source: 'تِكّات' },
  { id: 'slow' as const, label: 'بطيء', source: 'شموع' },
]
export type AnalysisPath = 'fast' | 'slow'
export const rowKey = (path: AnalysisPath, id: string) => `${path}:${id}`

function AnalyzerRow({ id, path, contribution, setting, accountId, broker, symbol, onSaved, onDraft }: {
  id: string
  path: AnalysisPath
  contribution?: AnalyzerContribution
  setting: Setting
  accountId: string
  broker: string
  symbol: string
  onSaved: (key: string, value: Setting) => void
  onDraft: (key: string, value: Setting) => void
}) {
  const [draft, setDraftRaw] = useState(setting)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  // السارية الآن لمهلة الطزاجة: تُجلب مرة (بذاكرة مشتركة) وتتجدد مع كل اعتماد عيار حي.
  const dialsLive = useStore((s) => s.streams['decision.settings.state'])
  const [speedNow, setSpeedNow] = useState<number | null>(null)
  useEffect(() => {
    let on = true
    fetchAnalysisSpeed().then((v) => { if (on) setSpeedNow(v) })
    return () => { on = false }
  }, [dialsLive])
  const key = rowKey(path, id)
  const pathInfo = ANALYSIS_PATHS.find((p) => p.id === path)!
  // كل تعديل يُبلَّغ للأب — «حفظ إعدادات الكل» يعرف الصفوف المعدَّلة (بند 2ب)
  const setDraft = (value: Setting) => { setDraftRaw(value); onDraft(key, value) }
  useEffect(() => { setDraftRaw(setting); onDraft(key, setting) },
    [setting.required_depth, setting.confidence_threshold, setting.strength_threshold,
      setting.stale_after_s, setting.direction_neutral_band, setting.weight, setting.revision]) // eslint-disable-line react-hooks/exhaustive-deps

  const state = STATES[contribution?.analysis_state ?? contribution?.state ?? ''] ?? { text: 'لم يصل', cls: 'grey' }
  const direction = contribution?.direction ?? contribution?.score
  const directionClass = direction == null ? 'grey' : direction > 5 ? 'green' : direction < -5 ? 'red' : 'grey'
  const save = async () => {
    if (!accountId.trim() || !symbol.trim()) { setNotice('أدخل الحساب والأصل أولًا'); return }
    const values = [...DIAL_FIELDS.map((f) => draft[f.key] as number), draft.weight]
    if (values.some((v) => !Number.isFinite(v) || v < 0 || v > 100)) {
      setNotice('كل قيمة يجب أن تكون بين صفر ومئة'); return
    }
    setBusy(true); setNotice('')
    // §30 — مفتاح المعايرة حساب+وسيط+رمز+محلّل: الوسيط المحلول يُرسل صراحة
    // حين يُعرف (كان غيابه يترك الحلّ لخريطة النواة وحدها — جذر بند 2 المحتمل)
    const result = await confirmedCommand('analysis_setting', {
      account_id: accountId.trim(), symbol: symbol.trim().toUpperCase(), analyzer_id: id,
      ...(broker ? { broker } : {}),
      // المسار جزء من مفتاح المعايرة — بدونه كان كل تعديل يهبط على «سريع»
      path,
      settings: {
        required_depth: draft.required_depth,
        confidence_threshold: draft.confidence_threshold,
        strength_threshold: draft.strength_threshold,
        stale_after_s: draft.stale_after_s,
        direction_neutral_band: draft.direction_neutral_band,
        weight: draft.weight,
      },
    })
    setBusy(false)
    if (result.ok) { setNotice('أُرسل التعديل وسُجّل للتنفيذ'); onSaved(key, draft) }
    else setNotice(result.message ?? 'تعذّر إرسال التعديل')
  }

  // دقّة أربع منازل — هي دقّة المخزن نفسه (`round(value, 4)` عند الحفظ) ودقّة
  // أوراق المالك (`55.0000`). بدونها كانت خانة الوزن تعرض 15.19047619047619
  // فيُقصّ الرقم داخل الخانة ويقرأ المالك ذيلًا لا معنى له.
  const input = (key: keyof Setting, label: string, hint?: string) => (
    <label style={{ display: 'grid', gap: 3, minWidth: 104 }} title={hint}>
      <span className="dim" style={{ fontSize: 11 }}>{label}</span>
      <input className="cfginput" style={{ margin: 0, padding: '5px 7px', fontSize: 13, width: '100%' }}
        type="number" min="0" max="100" step="any"
        value={draft[key] as number} onChange={(e) => setDraft({ ...draft, [key]: Number(e.target.value) })} />
      {key === 'stale_after_s' && speedNow != null && Number.isFinite(draft.stale_after_s) ? (
        <span style={{ fontSize: 10.5, color: 'var(--cyan, #57c7de)' }}>
          السارية مع مفتاح السرعة ({nfmt(speedNow)}): {nfmt(effectiveStale(draft.stale_after_s, speedNow))} ث
        </span>
      ) : null}
    </label>
  )

  return (
    <tr style={{ borderBottom: path === 'slow' ? '1px solid var(--glassb)' : '1px dotted var(--glassb)' }}>
      <td style={{ padding: '9px 8px', whiteSpace: 'nowrap' }}>
        {/* اسم المحلّل مرّة واحدة للصفّين، والمسار مُعلَن بمصدره على كل صفّ */}
        {path === 'fast' ? <div style={{ fontWeight: 700 }}>{analyzerLabel(id)}</div> : null}
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: path === 'fast' ? 3 : 0 }}>
          <span className={`pill ${path === 'fast' ? 'cyan' : 'grey'}`} style={{ padding: '1px 9px', fontSize: 12 }}>{pathInfo.label}</span>
          <span className="dim" style={{ fontSize: 11.5 }}>{pathInfo.source}</span>
        </div>
      </td>
      <td style={{ padding: '9px 8px' }}><span className={`pill ${directionClass}`}>{direction == null ? '—' : `${direction > 0 ? '+' : ''}${nfmt(direction)}`}</span></td>
      {/* §20 — القوّة والحصّة حقلان مستقلّان في البطاقة؛ والمجهول
          يُعرض «غير معروف» ولا يُعرض صفرًا. */}
      <td style={{ padding: '9px 8px' }}>{contribution?.strength == null ? <span className="dim">غير معروف</span> : nfmt(contribution.strength)}</td>
      <td style={{ padding: '9px 8px' }}>{percent(contribution?.confidence)}</td>
      <td style={{ padding: '9px 8px', minWidth: 150 }}>
        <div>{percent(contribution?.current_depth)} <span className="dim">من {percent(contribution?.required_depth ?? setting.required_depth)}</span></div>
        <div style={{ height: 4, background: 'var(--glassb)', borderRadius: 4, marginTop: 5 }}>
          <div style={{ width: `${Math.max(0, Math.min(100, contribution?.current_depth ?? 0))}%`, height: '100%', background: 'var(--cyan)', borderRadius: 4 }} />
        </div>
      </td>
      {/* شبكة ثابتة ثلاثية بدل التفافٍ حرّ: الخانات الخمس كانت تتراصّ عموديًّا
          داخل خليّة ضيّقة فيصير الصفّ الواحد بارتفاع شاشة. */}
      <td style={{ padding: '8px', minWidth: 340 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(96px, 1fr))', gap: 6 }}>
          {DIAL_FIELDS.map((f) => <span key={String(f.key)}>{input(f.key, f.label, f.hint)}</span>)}
        </div>
      </td>
      <td style={{ padding: '8px' }}>{input('weight', 'الوزن', 'حصّة المحلّل داخل مجمّع مساره — تعديله يُعاد توزيعه على النظائر ليبقى المجموع 100')}</td>
      <td style={{ padding: '9px 8px' }}>{contribution?.ratio == null ? <span className="dim">غير معروف</span> : nfmt(contribution.ratio)}</td>
      <td style={{ padding: '9px 8px', whiteSpace: 'nowrap' }}>
        <span className={`pill ${state.cls}`}>{state.text}</span>
        {contribution?.included ? <div className="green" style={{ fontSize: 12.5, marginTop: 3 }}>الوزن داخل الدمج</div> : <div className="dim" style={{ fontSize: 12.5, marginTop: 3 }}>الوزن مستبعد</div>}
      </td>
      <td style={{ padding: '9px 8px', whiteSpace: 'nowrap', fontSize: 12 }}>{timeText(contribution?.timestamp)}</td>
      <td style={{ padding: '8px', minWidth: 130 }}>
        <button className="btn" disabled={busy} onClick={save}>{busy ? 'جارٍ الإرسال…' : 'حفظ الإعداد'}</button>
        {notice ? <div className={notice.startsWith('أُرسل') ? 'green' : 'amber'} style={{ fontSize: 12, marginTop: 3 }}>{notice}</div> : null}
      </td>
    </tr>
  )
}

// §18/§19 — صفوف الأقسام. المصدر: `section_contract` ⇒ الحدث ⇒ المخزن.
// ⛔ لا تحسب هذه الواجهة اتجاهًا ولا قوّةً ولا ثقةً ولا عمقًا ولا وزنًا —
//    تعرض ما وصل، و«غير معروف» حيث لا قيمة. ولا صفر مكان مجهول.
const SECTION_LABELS: Record<string, string> = {
  '150': 'التحليل', '200': 'البنية', '250': 'السيولة',
  '300': 'الإحصاء', '350': 'الاحتمالات', '400': 'الاستراتيجيات',
  '450': 'القرار', '451': 'تجميع القرار',
}

// بند 3 (دفتر 97): حالة القسم وسبب الحجب كانا يطلعان إنكليزي خام
// (STALE · REQUIRED_UNITS). المفردات من shared/section_live.py نفسه — لا اختراع.
const SECTION_STATE: Record<string, { text: string; cls: string }> = {
  READY: { text: 'جاهز', cls: 'green' },
  DECISION_READY: { text: 'جاهز للقرار', cls: 'green' },
  ANALYZING: { text: 'قيد التحليل', cls: 'amber' },
  NOT_READY: { text: 'غير جاهز', cls: 'grey' },
  // بختم NQ (بند 26): «متقادم» أحمر بالمواصفة — موحَّد مع بطاقة المسارين.
  STALE: { text: 'متقادم — وقفت تغذيته', cls: 'red' },
  INVALID: { text: 'غير صالح', cls: 'red' },
  ERROR: { text: 'خطأ', cls: 'red' },
  DORMANT: { text: 'خامل', cls: 'grey' },
}
const BLOCK_REASON: Record<string, string> = {
  REQUIRED_UNITS: 'وحدات إلزامية ساكتة',
  STALE: 'التغذية متقادمة',
  DEPTH: 'العمق لم يكتمل',
  IDENTITY: 'هوية النطاق ناقصة',
  CONFIDENCE: 'بلا ثقة بعد',
}

function cell(value: unknown) {
  return value == null || typeof value !== 'number'
    ? <span className="dim">غير معروف</span>
    : <>{nfmt(value)}</>
}

// ——— بند أ١١ (ختم NQ بند 22) — بطاقة القسم من ١٦٦ v2.3.1 ———
// المصدر: analysis.raw.completed بحقل timeframe="section" — الثماني المدموجة
// (section_contract) + بطاقتا المسارين {fast, slow} كاملتين أو null للغائب.
// ⛔ لا حساب باللوحة: القيمة 0.0 مع ورود الاسم في unknown_fields = مجهول
//    وتُعرض «مجهول» بخافت — لا صفرًا. والمسار الغائب يُعلَن وزنه صراحةً.
const PATH_STATE: Record<string, { text: string; cls: string }> = {
  READY: { text: 'جاهز', cls: 'green' },
  ANALYZING: { text: 'قيد التحليل', cls: 'amber' },
  NOT_READY: { text: 'غير جاهز', cls: 'grey' },
  STALE: { text: 'متقادم', cls: 'red' },
}
const pathState = (raw?: string) =>
  PATH_STATE[raw ?? ''] ?? { text: arabicVisible(raw, 'حالة غير مترجمة'), cls: 'grey' }

type EightCard = { unknown_fields?: string[] } & Record<string, unknown>

const isUnknownField = (card: EightCard, field: string): boolean =>
  Array.isArray(card.unknown_fields) && card.unknown_fields.includes(field)

const numOf = (card: EightCard, field: string): number | undefined => {
  const value = card[field]
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

/** قيمة من الثماني: «مجهول» بخافت إن ورد اسمها في unknown_fields أو غابت. */
function eightVal(card: EightCard, field: string, digits = 1) {
  const value = numOf(card, field)
  if (isUnknownField(card, field) || value == null) return <span className="dim">مجهول</span>
  return <b className="num">{nfmt(value, digits)}</b>
}

/** الاتجاه (±100) بنمط حبّة المحلّلات نفسها: أخضر فوق +5، أحمر تحت -5. */
function dirVal(card: EightCard) {
  const value = numOf(card, 'direction')
  if (isUnknownField(card, 'direction') || value == null) return <span className="dim">مجهول</span>
  const cls = value > 5 ? 'green' : value < -5 ? 'red' : 'grey'
  return <span className={`pill ${cls}`} style={{ padding: '1px 10px' }}>{value > 0 ? '+' : ''}{nfmt(value)}</span>
}

function PathChip({ id, card, declaredWeight }: {
  id: 'fast' | 'slow'
  card?: PathCard | null
  declaredWeight?: number
}) {
  const source = id === 'fast' ? 'من التكّات' : 'من الشموع'
  if (!card) {
    // بند 2 — الغائب يُعرض صراحةً بوزنه المعلَن (path_missing_weight)، لا يُخفى
    return (
      <div style={{ border: '1px dashed var(--glassb)', borderRadius: 10, padding: '9px 12px', display: 'flex', flexDirection: 'column', gap: 7 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontWeight: 700 }}>{fieldAr(id)}</span>
          <span className="dim" style={{ fontSize: 11.5 }}>{source}</span>
          <span className="pill grey" style={{ marginInlineStart: 'auto' }}>غائب</span>
        </div>
        <div style={{ color: 'var(--amber)', fontSize: 12.5 }}>
          {fieldAr(id)} غائب — وزنه {nfmt(declaredWeight, 0)} معلَن
        </div>
      </div>
    )
  }
  const st = pathState(card.state)
  const c = card as unknown as EightCard
  return (
    <div style={{ border: '1px solid var(--glassb)', borderRadius: 10, padding: '9px 12px', display: 'flex', flexDirection: 'column', gap: 7, background: 'var(--glass)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700 }}>{fieldAr(id)}</span>
        <span className="dim" style={{ fontSize: 11.5 }}>{source}</span>
        <span className={`pill ${st.cls}`} style={{ marginInlineStart: 'auto' }}>{st.text}</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', fontSize: 13, alignItems: 'center' }}>
        <span><span className="dim">{fieldAr('direction')}</span> {dirVal(c)}</span>
        <span><span className="dim">{fieldAr('strength')}</span> {eightVal(c, 'strength')}</span>
        <span><span className="dim">{fieldAr('confidence')}</span> {eightVal(c, 'confidence')}</span>
        <span>
          <span className="dim">{fieldAr('current_depth')}</span> {eightVal(c, 'current_depth')}
          <span className="dim"> من {nfmt(numOf(c, 'required_depth'), 0)}</span>
        </span>
        <span><span className="dim">{fieldAr('weight')}</span> <b className="num">{nfmt(numOf(c, 'weight'), 0)}</b></span>
      </div>
    </div>
  )
}

function FusionCard({ body }: { body: SectionFusionState }) {
  const eight = (body.section_contract ?? {}) as unknown as EightCard
  const st = pathState(typeof eight.state === 'string' ? eight.state : undefined)
  const weights = body.path_weights ?? {}
  return (
    <div style={{ border: '1px solid var(--glassb)', borderRadius: 12, padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 9, background: 'var(--glass)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span style={{ fontWeight: 700, fontSize: 16 }}>{body.symbol}</span>
        <span className="dim" style={{ fontSize: 12.5 }}>
          {body.account_id || 'بلا حساب'}{body.broker ? ` · ${body.broker}` : ''}
        </span>
        <span className={`pill ${st.cls}`} style={{ marginInlineStart: 'auto' }}>{st.text}</span>
      </div>
      {/* الثماني المدموجة (section_contract) — كما وصلت، بلا أي حساب */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 16px', fontSize: 13.5, alignItems: 'center' }}>
        <span><span className="dim">{fieldAr('direction')}</span> {dirVal(eight)}</span>
        <span><span className="dim">{fieldAr('strength')}</span> {eightVal(eight, 'strength')}</span>
        <span><span className="dim">{fieldAr('confidence')}</span> {eightVal(eight, 'confidence')}</span>
        <span>
          <span className="dim">{fieldAr('current_depth')}</span> {eightVal(eight, 'current_depth')}
          <span className="dim"> من {nfmt(numOf(eight, 'required_depth'), 0)}</span>
        </span>
        <span><span className="dim">{fieldAr('weight')}</span> {eightVal(eight, 'weight', 0)}</span>
        <span><span className="dim">{fieldAr('ratio')}</span> {eightVal(eight, 'ratio')}</span>
      </div>
      {/* المساران جنبًا إلى جنب — وزن كل مسار معلَن من path_weights */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))', gap: 9 }}>
        <PathChip id="fast" card={body.paths?.fast} declaredWeight={weights.fast ?? body.path_missing_weight} />
        <PathChip id="slow" card={body.paths?.slow} declaredWeight={weights.slow ?? body.path_missing_weight} />
      </div>
      <div className="dim" style={{ fontSize: 12 }}>آخر تحديث {timeText(body.timestamp)}</div>
    </div>
  )
}

function SectionPathsCards() {
  const fusion = useStore((s) => s.sectionFusion)
  const rows = Object.entries(fusion).sort(([a], [b]) => a.localeCompare(b))
  return (
    <div className="scard" style={{ overflow: 'hidden', padding: 0 }}>
      <div style={{ padding: '11px 14px', borderBottom: '1px solid var(--glassb)' }}>
        <div className="st">بطاقة قسم التحليل (150) — دمج المسارين</div>
        <div className="ss dim">
          الثماني المدموجة لكل رمز + المسار السريع (تكّات) والبطيء (شموع) جنبًا إلى جنب.
          «مجهول» يعني ما وصلت قيمة — لا يُعرض صفر مكانها، والمسار الغائب يُعلَن وزنه ولا يُعاد توزيعه خفيةً.
        </div>
      </div>
      <div style={{ padding: 12 }}>
        {rows.length === 0
          ? <div className="dim" style={{ fontSize: 13.5 }}>لم يصل جسم القسم المدموج من النواة بعد.</div>
          : (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: 9 }}>
              {rows.map(([key, body]) => <FusionCard key={key} body={body} />)}
            </div>
          )}
      </div>
    </div>
  )
}

function Summary({ value }: { value?: AnalysisState }) {
  const signal = SIGNALS[value?.signal ?? ''] ?? { text: 'غير جاهز', arrow: '·', cls: 'grey' }
  return (
    <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
      <div className="scard"><div className="st">الاتجاه المدمج</div><div className={`sv ${signal.cls}`}>{signal.arrow} {signal.text}</div><div className="ss">{value?.score == null ? 'لا توجد نتيجة جاهزة' : `${nfmt(value.score)} من مئة`}</div></div>
      <div className="scard"><div className="st">الثقة المدمجة</div><div className="sv num">{percent(value?.confidence)}</div><div className="ss">ليست احتمال ربح</div></div>
      <div className="scard"><div className="st">الوزن النشط</div><div className="sv num green">{nfmt(value?.active_weight)}</div><div className="ss">المتاح {nfmt(value?.available_weight)}</div></div>
      <div className="scard"><div className="st">الوزن الغائب</div><div className={`sv num ${(value?.missing_weight ?? 0) > 0 ? 'amber' : 'green'}`}>{nfmt(value?.missing_weight)}</div><div className="ss">لا يُعاد توزيعه خفيةً</div></div>
      <div className="scard"><div className="st">آخر تحليل حي</div><div className="sv" style={{ fontSize: 16 }}>{timeText(value?.timestamp)}</div><div className="ss">يتغير مع كل تكة صالحة</div></div>
    </div>
  )
}

export default function Analysis() {
  const analysis = useStore((s) => s.analysis)
  const scopes = useMemo(() => Object.entries(analysis).sort(([a], [b]) => a.localeCompare(b, 'ar')), [analysis])
  const [accountId, setAccountId] = useState('')
  const [symbol, setSymbol] = useState('')
  const [settings, setSettings] = useState<SettingsMap>(defaults)

  useEffect(() => {
    if (!accountId && !symbol && scopes[0]) {
      setAccountId(scopes[0][1].account_id ?? '')
      setSymbol(scopes[0][1].symbol ?? '')
    }
  }, [scopes, accountId, symbol])

  // بند 2ب — حالة الدفعة: مسودّات كل الصفوف، الوسيط المحلول، ما أُرسل وينتظر تحقّقًا
  const [drafts, setDrafts] = useState<SettingsMap>({})
  const [broker, setBroker] = useState('')
  const [brokers, setBrokers] = useState<string[]>([])
  // بند ٤ (ورقة ٩٩) — الجذر المقيس 2026-08-19: 37 أمر analysis_setting نفّذتها 901
  // كلّها (DONE بجدول الأوامر) وجدول analysis_settings بقي صفر صف — لأنّ الحمولة
  // كانت بلا وسيط، وshared/live_analysis يرفض النطاق المجهول الوسيط بصمت (لا تخمين).
  // وكانت حلقة موت: اللوحة تتعلّم الوسيط من قاعدة الإعدادات، والقاعدة ما بتنكتب
  // إلا بأمر معه وسيط. الكسر: الوسيط يُحلّ من بثّ بطاقات الأقسام الحي نفسه
  // (المفتاح حساب::وسيط::رمز::قسم — ينشره section_live من النواة، مو تخمين لوحة).
  const sectionCards = useStore((s) => s.sectionCards)
  const liveBroker = useMemo(() => {
    const acc = accountId.trim()
    const sym = symbol.trim().toUpperCase()
    if (!acc || !sym) return ''
    for (const key of Object.keys(sectionCards)) {
      const parts = key.split('::')
      if (parts[0] === acc && parts[2] === sym && parts[1]) return parts[1]
    }
    return ''
  }, [sectionCards, accountId, symbol])
  useEffect(() => {
    if (!broker && liveBroker) setBroker(liveBroker)
  }, [broker, liveBroker])
  const [bulkNote, setBulkNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [busyAll, setBusyAll] = useState(false)
  const pendingRef = useRef<SettingsMap | null>(null)

  // قراءة الإعدادات من الخادم — تُستعمل عند تغيّر النطاق وعند زرّ «تحديث» (تحقّق مستقلّ)
  const loadSettings = useCallback(async (signal?: AbortSignal): Promise<SettingsMap | null> => {
    if (!accountId.trim() || !symbol.trim()) { setSettings(defaults()); return null }
    try {
      const q = `account_id=${encodeURIComponent(accountId.trim())}&symbol=${encodeURIComponent(symbol.trim())}`
        + (broker ? `&broker=${encodeURIComponent(broker)}` : '')
      const response = await fetch(`/gov/analysis/settings?${q}`, { signal })
      const body = (await response.json()) as {
        settings?: SettingsMap
        settings_by_path?: Record<string, SettingsMap>
        broker?: string; brokers?: string[]
      }
      if (body.brokers) setBrokers(body.brokers)
      if (body.broker && body.broker !== broker) setBroker(body.broker)
      // الخادم يرجّع صفًّا لكل مسار؛ ولو كان قديمًا بلا هذا الحقل نقرأ
      // `settings` على أنها المسار السريع (كما كانت) بلا اختراع صفٍّ بطيء.
      const byPath = body.settings_by_path ?? (body.settings ? { fast: body.settings } : null)
      if (byPath) {
        const merged = { ...defaults() }
        for (const { id: path } of ANALYSIS_PATHS) {
          for (const [id, value] of Object.entries(byPath[path] ?? {})) {
            // تقريب لأربع منازل = دقّة المخزن عند الحفظ، فلا يقرأ المالك
            // ذيلًا عشريًّا لا نهائيًّا ولا يظهر «تعديل غير محفوظ» كذبًا.
            // ⛔ والحقل الغائب من ردّ الخادم **يبقى على افتراض المحرّك** ولا
            //    ينهار إلى صفر — صفرٌ مكان غائب كذبة تُحفظ بكبسة واحدة.
            const base = merged[rowKey(path, id)]
            const row = { ...base, ...value } as Setting
            for (const f of DIAL_FIELDS) {
              const raw = (value as Partial<Setting>)[f.key]
              row[f.key] = round4(raw == null ? (base[f.key] as number) : (raw as number)) as never
            }
            row.weight = round4(value.weight ?? base.weight)
            merged[rowKey(path, id)] = row
          }
        }
        setSettings(merged)
        return merged
      }
    } catch { /* الخادم مطفي أو أُلغي الطلب */ }
    return null
  }, [accountId, symbol, broker])

  useEffect(() => {
    const controller = new AbortController()
    void loadSettings(controller.signal)
    return () => controller.abort()
  }, [loadSettings])

  const selected = scopes.map(([, value]) => value).find((value) =>
    (value.account_id ?? '') === accountId.trim() && value.symbol === symbol.trim().toUpperCase())
  // ختم المالك ٢٠٢٦-٠٨-٢١ — لكل مسار مساهماته هو: بطاقتا المسارين تصلان داخل
  // جسم القسم من ١٦٦ (`paths.fast` / `paths.slow`)، وكلٌّ يحمل تفصيل محلّليه.
  // قبل هذا كان صفّ البطيء يُغذّى من لا شيء فيبقى فارغًا أبدًا.
  const fusionAll = useStore((s) => s.sectionFusion)
  const fusionKey = `${accountId.trim() || 'بلا حساب'}::${symbol.trim().toUpperCase()}`
  const fusedPaths = fusionAll[fusionKey]?.paths
  const contributorsOf = (path: AnalysisPath) => (
    path === 'fast'
      ? (fusedPaths?.fast?.contributors ?? selected?.contributors ?? {})
      : (fusedPaths?.slow?.contributors ?? {})
  )

  // الصفوف المعدَّلة وغير المحفوظة — تُحسب من مسودّات الصفوف مقابل قيم الخادم
  const same = (a?: Setting, b?: Setting) => !!a && !!b
    && DIAL_FIELDS.every((f) => a[f.key] === b[f.key])
    && a.weight === b.weight
  // المفاتيح المعدَّلة عبر المسارين معًا — `fast:trend` / `slow:trend`
  const allKeys = ANALYSIS_PATHS.flatMap(({ id: path }) => ANALYZERS.map((id) => rowKey(path, id)))
  const splitKey = (key: string) => {
    const at = key.indexOf(':')
    return { path: key.slice(0, at) as AnalysisPath, id: key.slice(at + 1) }
  }
  const keyLabel = (key: string) => {
    const { path, id } = splitKey(key)
    return `${analyzerLabel(id)} (${ANALYSIS_PATHS.find((p) => p.id === path)?.label ?? path})`
  }
  const dirtyIds = allKeys.filter((key) => {
    const d = drafts[key]
    return d && !same(d, settings[key] ?? defaults()[key])
  })

  // «حفظ إعدادات الكل» — دفعة واحدة بتأكيد واحد، كل صفّ يمرّ ببوّابة الأوامر
  const saveAll = async () => {
    if (!accountId.trim() || !symbol.trim()) { setBulkNote({ ok: false, text: 'أدخل الحساب والأصل أولًا' }); return }
    if (!broker) { setBulkNote({ ok: false, text: 'لا يمكن الحفظ: الوسيط غير معروف لهذا النطاق بعد' }); return }
    const bad = dirtyIds.find((key) => {
      const d = drafts[key]
      return !d || [...DIAL_FIELDS.map((f) => d[f.key] as number), d.weight]
        .some((v) => !Number.isFinite(v) || v < 0 || v > 100)
    })
    if (bad) { setBulkNote({ ok: false, text: `قيمة خارج 0–100 عند «${keyLabel(bad)}» — صحّحها أولًا` }); return }
    // ⛔ الوزن لا يُرسل صفًّا صفًّا: كل أمر وزنٍ يشحن فرقه على النظائر
    //    الأربعة عشر (قاعدة المالك Q2)، فالأمر الثاني يزيح ما ثبّته الأوّل
    //    ولا يهبط الجدول كما كُتب — مقيس. فالعتبات تُرسل لكل صفّ، وجدول
    //    الوزن يُرسل **مرّة واحدة لكل مسار** بأمر جدول كامل مجموعه 100.
    const touchedPaths = [...new Set(dirtyIds.map((key) => splitKey(key).path))]
    const weightChanged = (path: AnalysisPath) => ANALYZERS.some((id) => {
      const k = rowKey(path, id)
      const draft = drafts[k] ?? settings[k]
      return draft && draft.weight !== (settings[k] ?? defaults()[k]).weight
    })
    const items: { action: string; payload: Record<string, unknown> }[] = dirtyIds.map((key) => ({
      action: 'analysis_setting',
      payload: {
        account_id: accountId.trim(), symbol: symbol.trim().toUpperCase(),
        analyzer_id: splitKey(key).id, path: splitKey(key).path,
        ...(broker ? { broker } : {}),
        settings: {
          required_depth: drafts[key].required_depth,
          confidence_threshold: drafts[key].confidence_threshold,
          strength_threshold: drafts[key].strength_threshold,
          stale_after_s: drafts[key].stale_after_s,
          direction_neutral_band: drafts[key].direction_neutral_band,
        },
      },
    }))
    for (const path of touchedPaths) {
      if (!weightChanged(path)) continue
      const table: Record<string, number> = {}
      let total = 0
      for (const id of ANALYZERS) {
        const k = rowKey(path, id)
        const value = (drafts[k] ?? settings[k] ?? defaults()[k]).weight
        table[id] = value
        total += value
      }
      if (Math.abs(total - 100) > 0.01) {
        const label = ANALYSIS_PATHS.find((p) => p.id === path)?.label ?? path
        setBulkNote({ ok: false, text: `مجموع أوزان «${label}» = ${nfmt(total, 4)} — لازم 100 بالضبط قبل الحفظ` })
        return
      }
      items.push({
        action: 'analysis_setting',
        payload: {
          account_id: accountId.trim(), symbol: symbol.trim().toUpperCase(),
          path, ...(broker ? { broker } : {}), weights: table,
        },
      })
    }
    setBusyAll(true); setBulkNote(null)
    const r = await confirmedCommandMany(items, `حفظ إعدادات ${items.length} محلّلًا (الحساب ${accountId.trim()} · ${symbol.trim().toUpperCase()})`)
    setBusyAll(false)
    if (r.done > 0) {
      // لقطة ما أُرسل — زرّ «تحديث» يتحقّق منها بقراءة مستقلّة (لا ثقة بردّ الحفظ)
      pendingRef.current = Object.fromEntries(dirtyIds.map((id) => [id, { ...drafts[id] }]))
    }
    if (r.ok) setBulkNote({ ok: true, text: `أُرسلت الدفعة (${r.done}/${items.length}) لبوّابة الأوامر — اضغط «تحديث» للتحقّق أنّها انضبطت فعلًا` })
    else if (r.failed[0]?.index === -1) setBulkNote({ ok: false, text: 'أُلغي — ما انبعت شي' })
    else setBulkNote({ ok: false, text: `وصل ${r.done} من ${items.length} — والباقي فشل: ${r.failed.map((f) => keyLabel(dirtyIds[f.index] ?? '')).join(' · ')}` })
  }

  // «تحديث» — قراءة مستقلّة من الخادم ومطابقة فعليّة لما أُرسل، ثم «تمّ الضبط»
  const verifyRefresh = async () => {
    setBusyAll(true)
    const fresh = await loadSettings()
    setBusyAll(false)
    const pending = pendingRef.current
    if (!fresh) { setBulkNote({ ok: false, text: 'ما قدرت أقرأ من الخادم — أعد المحاولة' }); return }
    if (!pending) { setBulkNote({ ok: true, text: 'قُرئت القيم من الخادم — ما في دفعة بانتظار تحقّق' }); return }
    const mismatch = Object.keys(pending).filter((id) => !same(pending[id], fresh[id]))
    if (mismatch.length === 0) {
      pendingRef.current = null
      setBulkNote({ ok: true, text: `✅ تمّ الضبط — ${Object.keys(pending).length} إعداد تطابق بعد القراءة من الخادم` })
    } else {
      setBulkNote({ ok: false, text: `⚠️ لسّا ما انضبط: ${mismatch.map(keyLabel).join(' · ')} — بوّابة الأوامر تنفّذ بالنبضة الجاية، جرّب «تحديث» بعد ثانية` })
    }
  }

  // تبديل النطاق مع تعديلات غير محفوظة = فقدان صامت (بند 2ب) — تحذير صريح
  const guardScope = (apply: () => void) => {
    if (dirtyIds.length && !window.confirm(`في ${dirtyIds.length} تعديل غير محفوظ — تبديل النطاق بيضيّعه. أكمل؟`)) return
    pendingRef.current = null; setBulkNote(null); setDrafts({})
    apply()
  }

  return (
    <div className="section chartsec" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div className="scard analysis-intro">
        <div className="st" style={{ fontWeight: 700, fontSize: 16 }}>قسم التحليل — 150</div>
        <div className="ss dim" style={{ marginTop: 4 }}>
          هون منشوف جاهزية التحليل ونتيجة المحلّلين ومساري التكّات والشموع. هذا القسم للبيانات والضبط فقط، وما بيفتح صفقة.
        </div>
      </div>
      <OwnerKeysBar />
      <AccountsPair />

      <div className="scard">
        <div className="st">نطاق التحليل والضبط</div>
        <div className="ss dim" style={{ marginBottom: 10 }}>الإعدادات معزولة بالحساب والأصل والمحلل. اللوحة لا تصنع قرار تداول. الحساب اللي بتكتبو تحت هو حساب البيانات — مو حساب التنفيذ.</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
          <label style={{ minWidth: 220, flex: 1 }}><span className="dim">الحساب (بيانات فقط)</span><input className="cfginput" value={accountId} onChange={(e) => setAccountId(e.target.value)} placeholder="أدخل معرّف الحساب" /></label>
          <label style={{ minWidth: 180, flex: 1 }}><span className="dim">الأصل</span><input className="cfginput" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} placeholder="أدخل رمز الأصل" /></label>
          {scopes.length ? <label style={{ minWidth: 260, flex: 1 }}><span className="dim">نطاق حي وصل من النواة (بيانات فقط)</span><select className="cfginput" value={selected ? `${selected.account_id ?? ''}::${selected.symbol}` : ''} onChange={(e) => { const found = analysis[e.target.value]; if (found) guardScope(() => { setAccountId(found.account_id ?? ''); setSymbol(found.symbol) }) }}><option value="">اختر نطاقًا حيًا</option>{scopes.map(([key, value]) => <option key={key} value={key}>{value.account_id || 'بلا حساب'} · {value.symbol} (بيانات)</option>)}</select></label> : null}
          {brokers.length > 1 ? <label style={{ minWidth: 180 }}><span className="dim">الوسيط</span><select className="cfginput" value={broker} onChange={(e) => guardScope(() => setBroker(e.target.value))}><option value="">اختر الوسيط</option>{brokers.map((b) => <option key={b} value={b}>{b}</option>)}</select></label>
            : broker ? <label style={{ minWidth: 160 }}><span className="dim">الوسيط {broker === liveBroker ? '(من البثّ الحي)' : '(محلول تلقائيًّا)'}</span><div className="cfginput" style={{ opacity: 0.8 }}>{broker}</div></label> : null}
        </div>
        {/* بند ٤ — صدق قبل الزرّ: بلا وسيط معروف، النواة سترفض الحفظ بصمت. نقولها هون. */}
        {!broker ? (
          <div className="ss" style={{ marginTop: 8, color: 'var(--amber)' }}>
            ⚠️ الوسيط غير معروف لهالنطاق بعد — الحفظ سيُرفض عند النواة حتى يصل بثّ أقسام حي يحمل الوسيط
            (هذا كان جذر «الزرّ ما بيحفظ»: 37 أمرًا مرّت وكلها بلا وسيط فرُفضت بصمت).
          </div>
        ) : null}
      </div>

      <Summary value={selected} />

      {!selected ? <div className="empty">لم يصل تحليل حي لهذا النطاق بعد. يمكن ضبط الإعدادات الآن، ولن تدخل أي نتيجة في الدمج حتى تستوفي العمق والعيار.</div> : null}

      <SectionPathsCards />

      <PathWeightsCard />

      {/* المحلّلون بعد ملخّص القسم، حتى يقرأ الإنسان الصورة قبل الجدول التفصيلي. */}
      <AnalystsPanel />

      <div className="scard" style={{ overflow: 'hidden', padding: 0 }}>
        <div style={{ padding: '11px 14px', borderBottom: '1px solid var(--glassb)', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10 }}>
          <div style={{ minWidth: 220 }}>
            <div className="st">المحللات المستقلة — صفٌّ لكل مسار</div>
            <div className="ss dim">كل محلّل له رؤيتان بإعدادات مستقلّة: <b>سريع</b> من التِكّات و<b>بطيء</b> من الشموع. تغيير إعداد في أحدهما لا يمسّ الآخر.</div>
            <div className="ss dim" style={{ marginTop: 4 }}>
              المسار السريع يتجدّد مع كل تِكّة، والبطيء مع كل شمعة مغلقة — فاختلاف رقميهما وصفٌ للسوق لا خطأ. والحقل الذي لا يرسله مساره يبقى «غير معروف».
            </div>
          </div>
          {/* بند 2ب — الثلاث قطع بمواصفة المالك الحرفيّة وبترتيبها */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginInlineStart: 'auto', flexWrap: 'wrap' }}>
            {dirtyIds.length ? <span className="amber" style={{ fontSize: 12.5 }}>{dirtyIds.length} تعديل غير محفوظ</span> : null}
            <button className="btn" disabled={busyAll || dirtyIds.length === 0 || !broker} onClick={() => void saveAll()}
              title={!broker ? 'ينتظر اسم الوسيط من البثّ الحي — لن يُرسل أمرًا ناقصًا' : 'يرسل التعديلات بعد تأكيدك لكل دفعة'}>
              {busyAll ? '⏳ …' : !broker ? '🔒 بانتظار الوسيط' : `💾 حفظ إعدادات الكل${dirtyIds.length ? ` (${dirtyIds.length})` : ''}`}
            </button>
            <button className="btn" disabled={busyAll} onClick={() => void verifyRefresh()} title="يقرأ من الخادم من جديد ويطابق ما أُرسل — تحقّق مستقلّ، لا ثقة بردّ الحفظ">
              🔄 تحديث
            </button>
          </div>
          {bulkNote ? <div style={{ flexBasis: '100%', fontSize: 13, color: bulkNote.ok ? 'var(--green)' : 'var(--amber)' }}>{bulkNote.text}</div> : null}
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14.5 }}>
            <thead><tr className="dim" style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
              <th style={{ padding: 8 }}>المحلل · المسار</th><th style={{ padding: 8 }}>الاتجاه</th><th style={{ padding: 8 }}>القوّة</th><th style={{ padding: 8 }}>عيار الثقة</th><th style={{ padding: 8 }}>العمق الحالي</th><th style={{ padding: 8 }}>العتبات (٠–١٠٠)</th><th style={{ padding: 8 }}>ضبط الوزن</th><th style={{ padding: 8 }}>الحصّة</th><th style={{ padding: 8 }}>الحالة</th><th style={{ padding: 8 }}>آخر تحديث</th><th style={{ padding: 8 }}>التنفيذ</th>
            </tr></thead>
            <tbody>{ANALYZERS.flatMap((id) => ANALYSIS_PATHS.map(({ id: path }) => {
              const k = rowKey(path, id)
              return (
                <AnalyzerRow key={`${accountId}:${symbol}:${k}`} id={id} path={path}
                  // كل صفّ من مساره هو — والحقل الذي لا يصل يبقى «غير معروف»،
                  // لا يُنسَخ من المسار الآخر ولا يُدسّ صفر مكانه (§٤ و§٣١).
                  contribution={contributorsOf(path)[id]}
                  setting={settings[k] ?? defaults()[k]} accountId={accountId} broker={broker} symbol={symbol}
                  onDraft={(key, value) => setDrafts((all) => (same(all[key], value) ? all : { ...all, [key]: value }))}
                  onSaved={(key, value) => setSettings((all) => ({ ...all, [key]: { ...value, revision: (all[key]?.revision ?? 0) + 1 } }))} />
              )
            }))}</tbody>
          </table>
        </div>
      </div>

      <details className="analysis-settings">
        <summary>⚙️ إعدادات التحليل والعيارات — افتحها عند الحاجة</summary>
        <div style={{ marginTop: 10 }}><AnalysisSettingsCard /></div>
      </details>
    </div>
  )
}
