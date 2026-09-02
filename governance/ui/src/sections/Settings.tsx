// الإعدادات★ (٨٦٧) — تعديل حيّ لإعداد الذرة (كتابة). نموذج عربي مولّد من إعدادات الذرة
// المعلَنة (لا JSON) · يتحقّق بالباك-إند · يكتب سطريًّا · يرفع النسخة ويتأكّد من إعادة التحميل.
// النموذج نفسه (AtomConfigForm) تستعمله نافذة الذرة كمان (زر «تعديل» — طلب المالك).
// وفوقه بطاقة «عيارات القرار» (أمر المالك ٢٠٢٦-٠٨-١٩): العتبات عيارها بإيد المالك من
// اللوحة، كل رقم قابل للتغيير بدقّة 100.00 — قراءة من /gov/decision/settings، تعديل حصرًا
// عبر بوّابة الأوامر (تأكيد بخطوتين action=decision_setting)، وتحديث حي من decision.settings.state.
import { useEffect, useMemo, useState } from 'react'
import { useStore } from '../core/store'
import { useMarket } from '../core/market'
import { settingLabel } from '../core/settingLabels'
import { confirmedCommand } from '../core/commands'
import { SECTIONS } from '../core/sections'
import {
  COLOR_VARS, loadColors, saveColor, resetColors, type ColorsMap,
  loadZoom, saveZoom, ZOOM_MIN, ZOOM_MAX,
  getTabOrder, saveTabOrder, resetTabOrder, TAB_ORDER_EVENT,
} from '../core/appearance'

interface Setting {
  key: string; value: unknown; type: string; min: number | null; max: number | null
  live_value?: unknown; overridden?: boolean
}

export function AtomConfigForm({ atomId, sandbox = false }: { atomId: number; sandbox?: boolean }) {
  const atoms = useStore((s) => s.atoms)
  const [settings, setSettings] = useState<Setting[] | null>(null)
  const [vals, setVals] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const cfgPath = sandbox ? `/gov/lab/config/${atomId}` : `/gov/atoms/${atomId}/config`

  const load = () => {
    setMsg(''); setSettings(null)
    fetch(cfgPath)
      .then((r) => r.json())
      .then((d: { settings?: Setting[]; error?: string }) => {
        const s = d.settings ?? []
        setSettings(s)
        const v: Record<string, string> = {}
        for (const x of s) v[x.key] = x.type === 'array' || x.type === 'object' ? JSON.stringify(x.value) : String(x.value)
        setVals(v)
        if (d.error) setMsg(d.error)
      })
      .catch(() => setSettings([]))
  }

  useEffect(() => { load() }, [atomId, sandbox]) // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    if (!settings) return
    const name = atoms[atomId]?.name_ar ?? `#${atomId}`
    if (!sandbox && !window.confirm(`تعديل إعدادات «${name}» وإعادة تحميلها حيًّا؟`)) return
    setBusy(true); setMsg('')
    const updates: Record<string, unknown> = {}
    try {
      for (const setting of settings) {
        const raw = vals[setting.key]
        updates[setting.key] = setting.type === 'array' || setting.type === 'object' ? JSON.parse(raw) : raw
      }
      const r = await fetch(cfgPath, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(updates),
      })
      const j = (await r.json()) as { message?: string; error?: string; ok?: boolean }
      setMsg(j.message ?? j.error ?? (r.ok ? 'تمّ' : 'ما تمّ'))
      if (sandbox && r.ok) load()
    } catch {
      setMsg('خطأ اتصال')
    }
    setBusy(false)
  }

  async function resetSandbox() {
    if (!sandbox) return
    setBusy(true); setMsg('')
    try {
      const r = await fetch(`/gov/lab/config/${atomId}/reset`, { method: 'POST' })
      const j = (await r.json()) as { message?: string }
      setMsg(j.message ?? 'رجعت للأصل')
      load()
    } catch {
      setMsg('خطأ اتصال')
    }
    setBusy(false)
  }

  if (settings == null) return <div className="dim">جارِ جلب الإعدادات…</div>
  if (settings.length === 0) return <div className="empty">هالذرة ما عندها إعدادات قابلة للتعديل.</div>
  return (
    <>
      <div className="cards">
        {settings.map((s) => (
          <div className="scard" key={s.key}>
            <div className="st">{settingLabel(s.key)}</div>
            {s.type === 'boolean' ? (
              // البند ٢٢ (المرحلة أ): مفتاح الأمان يُعرَض مفتاحًا حقيقيًّا،
              // وحالته من القيمة المنطقيّة نفسها لا من «هل النصّ غير فارغ» —
              // كتابة «لا» بالمربّع القديم كانت تُقرأ True فتفتح البوّابة.
              <label className="cfgswitch">
                <input
                  type="checkbox"
                  checked={vals[s.key] === 'true'}
                  onChange={(e) => setVals((v) => ({ ...v, [s.key]: e.target.checked ? 'true' : 'false' }))}
                />
                <span className={vals[s.key] === 'true' ? 'on' : 'off'}>
                  {vals[s.key] === 'true' ? 'مفتوح' : 'مقفل'}
                </span>
              </label>
            ) : (
              <input
                className="cfginput num"
                value={vals[s.key] ?? ''}
                inputMode="decimal"
                onChange={(e) => setVals((v) => ({ ...v, [s.key]: e.target.value }))}
              />
            )}
            <div className="ss">
              {s.min != null ? `أدنى ${s.min}` : ''}{s.min != null && s.max != null ? ' · ' : ''}{s.max != null ? `أقصى ${s.max}` : ''}
              {sandbox && s.overridden ? ' · مختبر (الحي مختلف)' : ''}
            </div>
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
        <button className="btn start" disabled={busy} onClick={save}>
          {sandbox ? 'حفظ للمختبر فقط' : 'حفظ وتطبيق حيّ'}
        </button>
        {sandbox ? (
          <button className="btn" disabled={busy} onClick={() => void resetSandbox()}>
            ارجع لأصل التداول
          </button>
        ) : null}
        {msg ? <span className="dim">{msg}</span> : null}
      </div>
    </>
  )
}

// ——— عيارات القرار (سجلّ المُعامِلات المحكوم — القيمة المعتمدة تعلو المانيفست) ———
// Dial/DialRow/useDecisionDials مُصدَّرة عمدًا (بند ١٨ بورقة ٩٩): بطاقات البوابات
// بصفحة «التنفيذ» تعيد استعمال نفس المكوّن — لا نسخة ثانية منه.

export interface Dial {
  name: string; atom: string; key: string; value: number
  status: string; source: string; version: number
  display: 'percent' | 'raw' | 'integer'
  bounds: [number, number]
  where: string
}

// التسميات العربية بترتيب العرض المعتمد — والأرقام تبقى غربية (عرف اللوحة)
const DIAL_AR: Record<string, string> = {
  // مفاتيح المالك (ورقة المفاتيح الأربعة ٢٦-٠٨) — أول القائمة عمدًا.
  MASTER_KEY: 'المفتاح الرئيسي — يحرّك الثلاثة معًا (50 = محايد · أعلى = أسرع·أضيق·أشدّ)',
  HORIZON_PROFILE_ACTIVE: 'تفعيل الشخصية المولّدة — 1 سارية · 0 ظل (بأمر «فعل»)',
  ANALYSIS_SPEED: 'مفتاح السرعة — النوافذ (50 = مثل اليوم · أعلى = أسرع)',
  TRADING_HORIZON: 'مفتاح الأفق — بُعد النظر (50 = مثل اليوم · أعلى = أضيق/سكالب · أدنى = سوينغ)',
  QUALITY_BAR: 'مفتاح الحدود — علامة النجاح (50 = مثل اليوم · أعلى = أشدّ قبولًا)',
  RISK_DIAL: 'عيار المخاطرة — بوابة الزيادة (100 = مثل اليوم · 0 = لا إضافة)',
  // عيارا وزن المسارين وعمقيهما — مستوى القسم لا مستوى المحلّل (اجوبة §٢٢).
  // كانا يظهران بأسمائهما الإنكليزية الخام لأنّ القاموس لم يعرفهما.
  ANALYSIS_FAST_WEIGHT: 'وزن المسار السريع · تِكّات',
  ANALYSIS_SLOW_WEIGHT: 'وزن المسار البطيء · شموع',
  ANALYSIS_FAST_REQUIRED_DEPTH: 'العمق المطلوب — السريع',
  ANALYSIS_SLOW_REQUIRED_DEPTH: 'العمق المطلوب — البطيء',
  DECISION_NEUTRAL_BAND: 'عتبة الحياد',
  DECISION_CONFLICT_RATIO: 'نسبة التعارض',
  DECISION_MIN_PARTICIPATION: 'المشاركة الدنيا',
  DECISION_DIRECTIONAL_WEIGHT: 'وزن الاستراتيجيات الاتجاهية',
  DECISION_CONTEXT_WEIGHT: 'وزن الأدلة السياقية',
  DECISION_MIN_CONFIDENCE: 'الثقة الدنيا للدليل',
  DECISION_LOW_QUALITY_FACTOR: 'خصم الجودة المنخفضة',
  DECISION_MIN_SCORE: 'الدرجة الدنيا للبوابة',
  DECISION_FILTER_TTL_S: 'مهلة نضارة الفلاتر (ثوانٍ)',
  DECISION_MAX_PER_SYMBOL: 'أقصى مراكز للرمز الواحد',
}

// مقياس العرض: النسب تُعرض ×100 بعشريتين مع «%»، الخام بعشريتين، الصحيح بلا كسور
const shownValue = (d: Dial): string =>
  d.display === 'percent' ? (d.value * 100).toFixed(2)
    : d.display === 'integer' ? String(Math.round(d.value))
      : d.value.toFixed(2)

export function DialRow({ dial }: { dial: Dial }) {
  const [draft, setDraft] = useState(() => shownValue(dial))
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  // كل قيمة/نسخة جديدة تصل من النواة تعيد ضبط المسودّة على الواقع
  useEffect(() => { setDraft(shownValue(dial)) }, [dial.value, dial.version, dial.status]) // eslint-disable-line react-hooks/exhaustive-deps

  const integer = dial.display === 'integer'
  const lo = dial.display === 'percent' ? dial.bounds[0] * 100 : dial.bounds[0]
  const hi = dial.display === 'percent' ? dial.bounds[1] * 100 : dial.bounds[1]
  const fmtBound = (b: number) => (integer ? String(Math.round(b)) : b.toFixed(2))

  const save = async () => {
    const n = Number(draft)
    if (draft.trim() === '' || !Number.isFinite(n)) { setNote({ ok: false, text: 'أدخل رقمًا صالحًا' }); return }
    if (n < lo || n > hi) { setNote({ ok: false, text: `القيمة خارج الحدود — من ${fmtBound(lo)} إلى ${fmtBound(hi)}` }); return }
    // دقّة عشريتين على مقياس العرض، ثم التحويل لمقياس التخزين (كسور 0-1 للنسب)
    const stored = integer ? Math.round(n)
      : dial.display === 'percent' ? Math.round(n * 100) / 10000
        : Math.round(n * 100) / 100
    setBusy(true); setNote(null)
    const r = await confirmedCommand('decision_setting', { name: dial.name, value: stored })
    setBusy(false)
    setNote(r.ok
      ? { ok: true, text: r.message ?? 'أُرسل — الاعتماد يظهر هنا لحظة ما تطبّقه الذرّة' }
      : { ok: false, text: r.message ?? 'تعذّر الإرسال' })
  }

  const approved = dial.status === 'APPROVED'
  return (
    <div className="scard">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <div className="st" style={{ fontWeight: 700 }}>{DIAL_AR[dial.name] ?? dial.name}</div>
        <span className={`pill ${approved ? 'green' : 'grey'}`}>
          {approved ? `معتمد ✓ v${dial.version}` : 'غير معتمد — قيمة المانيفست'}
        </span>
      </div>
      {/* حزمة ج (ج٤): نمط المطلوبة/الفعالة/الحالة — الفعّالة هي القيمة الجارية،
          والمطلوبة تساويها إن كانت معتمدة (أمر المالك هو ما يعمل به النظام)،
          وتبقى «لا طلب مسجَّل» قبل أول اعتماد — لا اختراع قيمة مطلوبة غير موجودة. */}
      <div className="ss dim" style={{ marginTop: 4 }}>الفعّالة (ما يعمل به النظام الآن)</div>
      <div className={`sv num${approved ? ' green' : ''}`}>{shownValue(dial)}{dial.display === 'percent' ? '%' : ''}</div>
      <div className="ss dim" style={{ marginTop: 2 }}>
        المطلوبة (آخر أمر مالك): {approved ? <b className="num">{shownValue(dial)}{dial.display === 'percent' ? '%' : ''}</b> : 'لا طلب مسجَّل بعد — قيمة المانيفست الأولية سارية'}
      </div>
      <input className="cfginput num" type="number" inputMode="decimal"
        min={lo} max={hi} step={integer ? 1 : 0.01} value={draft}
        onChange={(e) => setDraft(e.target.value)} />
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 8, flexWrap: 'wrap' }}>
        <button className="btn" disabled={busy} onClick={save}>{busy ? 'جارٍ الإرسال…' : 'حفظ'}</button>
        <span className="ss num" style={{ marginTop: 0 }}>أدنى {fmtBound(lo)} · أقصى {fmtBound(hi)}</span>
      </div>
      {note ? <div style={{ fontSize: 12.5, marginTop: 5, color: note.ok ? 'var(--green)' : 'var(--amber)' }}>{note.text}</div> : null}
      <div className="ss dim" title={dial.where}
        style={{ marginTop: 6, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{dial.where}</div>
    </div>
  )
}

/** يجلب العيارات المحكومة ويعيد الجلب مع كل نشرة اعتماد حيّة — مشترك بين
 *  بطاقة «عيارات القرار» هنا وبطاقات البوابات بصفحة «التنفيذ» (بند ١٨). */
export function useDecisionDials(onlyNames?: string[], excludeNames: string[] = []): { dials: Dial[] | null; err: string } {
  const [dials, setDials] = useState<Dial[] | null>(null)
  const [err, setErr] = useState('')
  // الحدث الحي decision.settings.state يصل عبر المحرّك إلى streams تلقائيًّا؛
  // كل نشرة (لحظة تطبيق الذرّة اعتماد المالك) تعيد الجلب من سجلّ المُعامِلات.
  const live = useStore((s) => s.streams['decision.settings.state'])
  // مفاتيح ثابتة حتى لا يعيد المكوّن جلب السجل في كل إعادة رسم بسبب مصفوفة جديدة.
  const onlyKey = onlyNames?.join('|') ?? ''
  const excludeKey = excludeNames.join('|')

  useEffect(() => {
    const controller = new AbortController()
    fetch('/gov/decision/settings', { signal: controller.signal })
      .then((r) => r.json())
      .then((d: { dials?: Dial[] }) => {
        const order = Object.keys(DIAL_AR)
        const filtered = (d.dials ?? []).filter((dial) =>
          (!onlyNames || onlyNames.includes(dial.name)) && !excludeNames.includes(dial.name))
        setDials(filtered.slice().sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name)))
        setErr('')
      })
      .catch(() => setErr('تعذّر جلب العيارات — تأكّد أن خادم الحوكمة شغّال'))
    return () => controller.abort()
  }, [live, onlyKey, excludeKey])

  return { dials, err }
}

export function DecisionDialsCard({ excludeNames = [], onlyNames, includeExtras = true }: {
  excludeNames?: string[]; onlyNames?: string[]; includeExtras?: boolean
}) {
  const { dials, err } = useDecisionDials(onlyNames, excludeNames)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div className="scard">
        <div className="st" style={{ fontWeight: 700 }}>عيارات القرار</div>
        <div className="ss dim">
          عتبات سلسلة القرار المحكومة — كل رقم قابل للتغيير بدقّة 100.00، والتعديل يمرّ ببوّابة الأوامر بتأكيد.
          المعتمد يعلو قيمة المانيفست حيًّا بلا إعادة تشغيل؛ وغير المعتمد تبقى قيمة المانيفست هي السارية.
        </div>
        {err ? <div style={{ marginTop: 6, fontSize: 13, color: 'var(--amber)' }}>{err}</div> : null}
      </div>
      {dials == null
        ? <div className="dim">جارِ جلب العيارات…</div>
        : dials.length === 0
          ? <div className="empty">ما وصل أي عيار — سجلّ المُعامِلات فاضي.</div>
          : <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))' }}>
              {dials.map((d) => <DialRow key={d.name} dial={d} />)}
            </div>}
      {includeExtras ? <SpeedPerAssetCard /> : null}
      {includeExtras ? <HorizonShadowCard /> : null}
    </div>
  )
}

// كل ما يخص قسم التحليل يُدار من قسم التحليل نفسه، لا من الإعدادات العامة.
export const ANALYSIS_DIAL_NAMES = [
  'MASTER_KEY', 'HORIZON_PROFILE_ACTIVE', 'ANALYSIS_SPEED', 'TRADING_HORIZON', 'QUALITY_BAR',
  'ANALYSIS_FAST_WEIGHT', 'ANALYSIS_SLOW_WEIGHT',
  'ANALYSIS_FAST_REQUIRED_DEPTH', 'ANALYSIS_SLOW_REQUIRED_DEPTH',
]

export const DECISION_DIAL_NAMES = [
  'DECISION_NEUTRAL_BAND', 'DECISION_CONFLICT_RATIO', 'DECISION_MIN_PARTICIPATION',
  'DECISION_DIRECTIONAL_WEIGHT', 'DECISION_CONTEXT_WEIGHT', 'DECISION_MIN_CONFIDENCE',
  'DECISION_LOW_QUALITY_FACTOR', 'DECISION_MIN_SCORE', 'DECISION_FILTER_TTL_S',
  'DECISION_MAX_PER_SYMBOL',
]
export const RISK_DIAL_NAMES = ['RISK_DIAL']

export function AnalysisSettingsCard() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <DecisionDialsCard onlyNames={ANALYSIS_DIAL_NAMES} includeExtras />
      <DeclaredParametersCard />
    </div>
  )
}

// أرقام لاتينية بعشريات مضبوطة — عرف اللوحة (نفس مساعد صفحة التنفيذ)
const num = (n?: number | null, dp = 2) =>
  n == null || !Number.isFinite(n) ? '—' : n.toLocaleString('en-US', { maximumFractionDigits: dp, minimumFractionDigits: 0 })

// ——— ظل شخصية الأفق (ورقة المؤشر الموحد v1.0 §71 — عرض فقط) ———
// يولَّد من مفتاح السرعة بمعادلات المالك الحرفية ولا يطبَّق على أي ذرّة
// حتى أمر تفعيله (مراحل الهجرة §61: ظل ← توليد ← مقارنة ← تفعيل).
function humanHorizon(seconds: number): string {
  if (!Number.isFinite(seconds)) return '—'
  if (seconds < 60) return `${num(seconds, 2)} ث`
  if (seconds < 3600) return `${num(seconds / 60, 1)} د`
  if (seconds < 86400) return `${num(seconds / 3600, 1)} س`
  return `${num(seconds / 86400, 1)} يوم`
}

function HorizonShadowCard() {
  const p = useStore((s) => s.streams['horizon.profile.state']) as Record<string, unknown> | undefined
  if (!p) return null
  const g = (path: string): number | undefined => {
    const [a, b] = path.split('.')
    const section = p[a] as Record<string, unknown> | undefined
    const v = b ? section?.[b] : p[a]
    return typeof v === 'number' ? v : undefined
  }
  const row = (label: string, value: string) => (
    <span><span className="dim">{label}</span> <b className="num">{value}</b></span>
  )
  return (
    <div className="scard">
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <div className="st" style={{ fontWeight: 700 }}>شخصية الأفق المولّدة</div>
        {p.applies === true
          ? <span className="pill green">سارية — بأمرك «فعل»</span>
          : <span className="pill amber">ظلّ — لا تُطبَّق حتى أمرك</span>}
      </div>
      <div className="ss dim">
        مولّدة من مفتاح الأفق ({num(g('key_value') ?? 0, 2)}) بمعادلات ورقة المؤشر الموحد.
        {p.applies === true
          ? ' وهي الآن ما يعمل به النظام في عتبات القرار و166 وهستيريسيس 581 — وأي عيار تعتمده بيدك بعد التفعيل يعلو المولَّد. الإطفاء: عيار «تفعيل الشخصية» = 0.'
          : ' هذه القيم للعرض والمقارنة فقط (مراحل ورقتك: ظل ← مقارنة ← تفعيل بكلمتك).'}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '5px 16px', fontSize: 12.5, marginTop: 8 }}>
        {row('الأفق', humanHorizon(g('horizon_seconds') ?? NaN))}
        {row('المسار السريع', `${num(g('166.fast_weight') ?? 0, 1)}%`)}
        {row('المسار البطيء', `${num(g('166.slow_weight') ?? 0, 1)}%`)}
        {row('الطزاجة', `${num(g('166.live_stale_after_s') ?? 0, 2)} ث`)}
        {row('المتوسطان', `${g('151.ema_fast') ?? '—'} / ${g('151.ema_slow') ?? '—'}`)}
        {row('الزخم', `${g('152.roc_period') ?? '—'} · ${g('152.impulse_window') ?? '—'}`)}
        {row('نافذة السرعة', `${g('162.baseline_window') ?? '—'}`)}
        {row('نطاق الحياد', `${num(g('458.neutral_band') ?? 0, 3)}`)}
        {row('إيقاع الإدارة', `${num(g('523.mgmt_cadence_s') ?? 0, 1)} ث`)}
        {row('مدخل/مخرج الاتجاه', `${num(g('581.s_enter') ?? 0, 2)} / ${num(g('581.s_exit') ?? 0, 2)}`)}
      </div>
      <div className="ss dim" style={{ fontSize: 11, marginTop: 6 }}>
        المصدر: {String(p.formula_version ?? '—')} · مؤشر الورقة {num(g('profile_index') ?? 0, 2)} (= 101 − مفتاح الأفق)
      </div>
    </div>
  )
}

// ——— سرعة التحليل لكل أصل (ملحق المالك ٢٦-٠٨: «كل حساب له شغل، كل أصل بسرعته») ———
// القيمة العامة تسري على الكل؛ وتخصيص (حساب + أصل) يعلوها فور اعتماده.
// لإلغاء تخصيص أصلٍ: أعطِه نفس الرقم العام.
interface SpeedScopeRow { name: string; scope: string; value: number; status: string; version: number }

function SpeedPerAssetCard() {
  const live = useStore((s) => s.streams['decision.settings.state'])
  const targets = useStore((s) => s.symbolStreams['perpetual.target.state'] ?? {})
  const [rows, setRows] = useState<SpeedScopeRow[]>([])
  const [symbol, setSymbol] = useState('')
  const [account, setAccount] = useState('')
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [tick, setTick] = useState(0)

  const knownSymbols = useMemo(() => Object.keys(targets).sort(), [targets])
  const defaultAccount = useMemo(() => {
    for (const sym of Object.keys(targets)) {
      const acc = (targets[sym] as Record<string, unknown>)?.account_id
      if (acc) return String(acc)
    }
    return ''
  }, [targets])
  useEffect(() => { if (!account && defaultAccount) setAccount(defaultAccount) }, [defaultAccount, account]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const controller = new AbortController()
    fetch('/gov/parameters', { signal: controller.signal })
      .then((r) => r.json())
      .then((d: { parameters?: SpeedScopeRow[] }) => {
        setRows((d.parameters ?? []).filter((p) => p.name === 'ANALYSIS_SPEED'))
      })
      .catch(() => undefined)
    return () => controller.abort()
  }, [live, tick])

  const globalRow = rows.find((r) => r.scope === 'global')
  const overrides = rows.filter((r) => r.scope !== 'global' && r.status === 'APPROVED')
  const scopeParts = (scope: string) => scope.split('')

  const save = async () => {
    const n = Number(draft)
    if (!symbol.trim() || !account.trim()) { setNote({ ok: false, text: 'اكتب الأصل والحساب' }); return }
    if (draft.trim() === '' || !Number.isFinite(n) || n < 1 || n > 100) { setNote({ ok: false, text: 'الرقم من 1.00 إلى 100.00' }); return }
    setBusy(true); setNote(null)
    const r = await confirmedCommand('decision_setting', {
      name: 'ANALYSIS_SPEED', value: Math.round(n * 100) / 100,
      account_id: account.trim(), symbol: symbol.trim().toUpperCase(),
    })
    setBusy(false)
    setNote(r.ok ? { ok: true, text: 'انحفظت — بتوصل للنظام بثوانٍ' } : { ok: false, text: r.error ?? 'تعذّر الحفظ' })
    if (r.ok) { setDraft(''); setTimeout(() => setTick((t) => t + 1), 2500) }
  }

  return (
    <div className="scard">
      <div className="st" style={{ fontWeight: 700 }}>سرعة التحليل لكل أصل</div>
      <div className="ss dim">
        العامة (فوق) تسري على كل الأصول والحسابات. التخصيص هنا يعلوها للأصل المسمّى فقط —
        ولإلغاء تخصيص: أعطِ الأصل نفس الرقم العام{globalRow ? ` (${globalRow.value.toFixed(2)})` : ''}.
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 10, alignItems: 'center' }}>
        <input list="speed-symbols" value={symbol} onChange={(e) => setSymbol(e.target.value)}
          placeholder="الأصل (مثل BTCUSD)" style={{ width: 150 }} />
        <datalist id="speed-symbols">{knownSymbols.map((s) => <option key={s} value={s} />)}</datalist>
        <input value={account} onChange={(e) => setAccount(e.target.value)}
          placeholder="الحساب" style={{ width: 120 }} className="num" />
        <input value={draft} onChange={(e) => setDraft(e.target.value)}
          placeholder="السرعة 1-100" style={{ width: 110 }} className="num" inputMode="decimal" />
        <button className="btn" disabled={busy} onClick={save}>{busy ? '…' : 'حفظ للأصل'}</button>
      </div>
      {note ? <div style={{ marginTop: 6, fontSize: 13, color: note.ok ? 'var(--green)' : 'var(--amber)' }}>{note.text}</div> : null}
      <div style={{ marginTop: 10 }}>
        {overrides.length === 0
          ? <div className="dim" style={{ fontSize: 13 }}>لا تخصيصات — كل الأصول على الرقم العام{globalRow ? ` (${globalRow.value.toFixed(2)})` : ''}.</div>
          : overrides.map((o) => {
              const [acc, sym] = scopeParts(o.scope)
              return (
                <div key={o.scope} style={{ display: 'flex', gap: 10, fontSize: 13.5, padding: '3px 0' }}>
                  <b>{sym || o.scope}</b>
                  <span className="dim">حساب {acc || '—'}</span>
                  <b className="num" style={{ marginInlineStart: 'auto' }}>{o.value.toFixed(2)}</b>
                  <span className="pill green" style={{ padding: '0 8px' }}>v{o.version} ✓</span>
                </div>
              )
            })}
      </div>
    </div>
  )
}

// ——— المُعامِلات المعلنة (بند ٢٢ حزمة أ — أ٧، حكم ق١) ———
// الستّة التحليلية التي تحكم بوّابة READY ولم يكن لها أي طريق اعتماد. نفس نمط
// DialRow حرفيًّا: قراءة من /gov/parameters (قراءة فقط mode=ro)، الاعتماد حصرًا
// عبر بوّابة الأوامر (تأكيد بخطوتين action=parameter_approve → ٩٠١ تكتب بالسجلّ)،
// وتحديث حيّ من parameter.approved.state لحظة تنفيذ البوّابة.

export interface GovParameter {
  name: string; scope: string; value: number; source: string; status: string
  version: number; approved_by: string; approved_at: number
  governs: string; declared_at: string; approvable: boolean
}

// التسميات العربية بترتيب العرض — والأرقام تبقى غربية (عرف اللوحة)
const PARAM_AR: Record<string, string> = {
  MOVEMENT_FLOOR: 'أرضية الحركة',
  ABNORMALITY_GAIN: 'كسب الشذوذ',
  INTEGRITY_BLEND: 'مزيج التماسك',
  CONFIDENCE_BLEND: 'مزيج الثقة',
  DEPTH_BLEND: 'مزيج العمق',
  STALE_AFTER_S: 'مهلة النضارة (ثوانٍ)',
}

// دقّة عشريتين — إلا القيم الصغرى (أرضية الحركة 0.000001): تدويرها لعشريتين
// كان سيعرض 0.00 ويكذب على المالك، فتُعرض وتُرسل كما هي.
const paramShown = (v: number): string =>
  v !== 0 && Math.abs(v) < 0.01 ? String(v) : v.toFixed(2)

// حزمة ج (ج٤): سجلّ تدقيق المُعامِلات — جدول `parameters_audit` موجود فعلًا بمخزن
// المعايرة (shared/parameter_registry.py)، يُقرأ عبر منفذ جديد `/gov/parameters/audit`
// (يحتاج إعادة تشغيل الخادم مثل بقية المنافذ الجديدة). عيارات القرار (decision_dials)
// ليس لها جدول تدقيق بالكود — فلا سجل تغييرات يُعرض لها (غياب مُعلَن، لا اختراع).
interface ParamAuditRow {
  audit_id: number; name: string; scope: string; old_json: string; new_json: string
  version: number; changed_at: number; changed_by: string; command_id: string
}
function useParametersAudit(): { rows: ParamAuditRow[] | null; err: string } {
  const [rows, setRows] = useState<ParamAuditRow[] | null>(null)
  const [err, setErr] = useState('')
  const live = useStore((s) => s.streams['parameter.approved.state'])
  useEffect(() => {
    const controller = new AbortController()
    fetch('/gov/parameters/audit?limit=200', { signal: controller.signal })
      .then((r) => r.json())
      .then((d: { available?: boolean; audit?: ParamAuditRow[] }) => {
        if (d.available === false) { setRows([]); setErr('منفذ /gov/parameters/audit غير حيّ بعد — يحتاج إعادة تشغيل الخادم'); return }
        setRows(d.audit ?? []); setErr('')
      })
      .catch(() => setErr('تعذّر جلب سجلّ التدقيق'))
    return () => controller.abort()
  }, [live])
  return { rows, err }
}

function auditValue(json: string): string {
  try {
    const v = (JSON.parse(json) as { value?: unknown }).value
    return typeof v === 'number' ? v.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 6 }) : String(v ?? '—')
  } catch { return '—' }
}

export function ParameterRow({ param, audit, auditErr }: { param: GovParameter; audit: ParamAuditRow[] | null; auditErr: string }) {
  const [draft, setDraft] = useState(() => paramShown(param.value))
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [showAudit, setShowAudit] = useState(false)
  const myAudit = (audit ?? []).filter((r) => r.name === param.name)
  // كل قيمة/نسخة جديدة تصل من السجلّ تعيد ضبط المسودّة على الواقع
  useEffect(() => { setDraft(paramShown(param.value)) }, [param.value, param.version, param.status]) // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    const n = Number(draft)
    if (draft.trim() === '' || !Number.isFinite(n)) { setNote({ ok: false, text: 'أدخل رقمًا صالحًا' }); return }
    const stored = n !== 0 && Math.abs(n) < 0.01 ? n : Math.round(n * 100) / 100
    setBusy(true); setNote(null)
    const r = await confirmedCommand('parameter_approve', { name: param.name, value: stored })
    setBusy(false)
    setNote(r.ok
      ? { ok: true, text: r.message ?? 'أُرسل — الاعتماد يظهر هنا لحظة ما تنفّذه بوّابة الأوامر (٩٠١)' }
      : { ok: false, text: r.message ?? 'تعذّر الإرسال' })
  }

  const approved = param.status === 'APPROVED'
  return (
    <div className="scard">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
        <div className="st" style={{ fontWeight: 700 }}>{PARAM_AR[param.name] ?? param.name}</div>
        <span className={`pill ${approved ? 'green' : 'grey'}`}>
          {approved ? `معتمد ✓ v${param.version}` : 'غير معتمد — القيمة الأولية العادلة سارية'}
        </span>
      </div>
      {/* حزمة ج (ج٤): الفعّالة/المطلوبة/الحالة صراحة — لا اختراع قيمة مطلوبة قبل أول اعتماد */}
      <div className="ss dim" style={{ marginTop: 4 }}>الفعّالة (ما يعمل به النظام الآن)</div>
      <div className={`sv num${approved ? ' green' : ''}`}>{paramShown(param.value)}</div>
      <div className="ss dim" style={{ marginTop: 2 }}>
        المطلوبة (آخر أمر مالك): {approved ? <b className="num">{paramShown(param.value)}</b> : 'لا طلب مسجَّل بعد — القيمة الأولية العادلة سارية'}
      </div>
      <input className="cfginput num" type="number" inputMode="decimal" step={0.01}
        value={draft} onChange={(e) => setDraft(e.target.value)} />
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 8, flexWrap: 'wrap' }}>
        <button className="btn" disabled={busy} onClick={save}>{busy ? 'جارٍ الإرسال…' : 'اعتماد'}</button>
        <span className="ss num" style={{ marginTop: 0 }}>{param.name}</span>
      </div>
      {note ? <div style={{ fontSize: 12.5, marginTop: 5, color: note.ok ? 'var(--green)' : 'var(--amber)' }}>{note.text}</div> : null}
      <div className="ss dim" title={`يحكم: ${param.governs} · المصدر بالكود: ${param.declared_at}`}
        style={{ marginTop: 6, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
        يحكم: {param.governs} · المصدر بالكود: {param.declared_at}
      </div>
      {/* سجل تغييرات — جدول parameters_audit موجود بالكود فعلًا؛ لا زرّ رجوع
          لأن لا آلية رجوع فعلية بالكود (901/parameter_registry لا يملكان استرجاعًا) —
          الرجوع لقيمة سابقة يحتاج اعتمادًا يدويًّا جديدًا بنفس القيمة القديمة. */}
      {auditErr ? <div className="ss dim" style={{ marginTop: 6 }}>{auditErr}</div> : myAudit.length ? (
        <div style={{ marginTop: 6 }}>
          <button className="btn" style={{ fontSize: 11.5 }} onClick={() => setShowAudit(!showAudit)}>
            {showAudit ? '▴ خبّي سجل التغييرات' : `▾ سجل التغييرات (${myAudit.length})`}
          </button>
          {showAudit ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3, marginTop: 6, fontSize: 11.5 }}>
              {myAudit.map((a) => (
                <div key={a.audit_id} className="dim">
                  {new Date(a.changed_at * 1000).toLocaleString('ar-EG-u-nu-latn', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
                  {' — '}{auditValue(a.old_json)} ← <b className="num" style={{ color: 'var(--ink)' }}>{auditValue(a.new_json)}</b>
                  {' '}(v{a.version} · {a.changed_by || 'غير معروف'})
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : <div className="ss dim" style={{ marginTop: 6 }}>لا سجل تغييرات بعد لهذا المُعامِل — لا زرّ رجوع (لا آلية رجوع فعلية بالكود).</div>}
    </div>
  )
}

export function DeclaredParametersCard() {
  const [params, setParams] = useState<GovParameter[] | null>(null)
  const [err, setErr] = useState('')
  // الحدث الحي parameter.approved.state يصل عبر المحرّك إلى streams تلقائيًّا؛
  // كل نشرة (لحظة تطبيق ٩٠١ اعتماد المالك) تعيد الجلب من السجلّ.
  const live = useStore((s) => s.streams['parameter.approved.state'])

  useEffect(() => {
    const controller = new AbortController()
    fetch('/gov/parameters', { signal: controller.signal })
      .then((r) => r.json())
      .then((d: { available?: boolean; parameters?: GovParameter[] }) => {
        const order = Object.keys(PARAM_AR)
        setParams((d.parameters ?? []).filter((p) => p.approvable)
          .slice().sort((a, b) => order.indexOf(a.name) - order.indexOf(b.name)))
        setErr(d.available === false ? 'سجلّ المُعامِلات غير متاح بعد — يتكوّن مع أول تشغيل للنواة' : '')
      })
      .catch(() => setErr('تعذّر جلب المُعامِلات — تأكّد أن خادم الحوكمة شغّال'))
    return () => controller.abort()
  }, [live])
  const { rows: auditRows, err: auditErr } = useParametersAudit()

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div className="scard">
        <div className="st" style={{ fontWeight: 700 }}>المُعامِلات المعلنة</div>
        <div className="ss dim">
          الستّة التحليلية التي تحكم بوّابة الجاهزية (READY) — القيمة الأولية العادلة هي المعلنة بالسجلّ
          وتبقى سارية غير رسمية حتى يعتمدها المالك من هنا (حكم ق١). بقاء أي واحدة غير معتمدة يبقي كل
          بطاقات العقد provisional. الاعتماد يمرّ ببوّابة الأوامر بتأكيد بخطوتين؛ وعيارات القرار لها
          بطاقتها فوق ولا تُعتمد من هنا.
        </div>
        {err ? <div style={{ marginTop: 6, fontSize: 13, color: 'var(--amber)' }}>{err}</div> : null}
      </div>
      {params == null
        ? <div className="dim">جارِ جلب المُعامِلات…</div>
        : params.length === 0
          ? <div className="empty">ما وصل أي مُعامِل معلن — سجلّ المُعامِلات فاضي.</div>
          : <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(250px, 1fr))' }}>
              {params.map((p) => <ParameterRow key={p.name} param={p} audit={auditRows} auditErr={auditErr} />)}
            </div>}
    </div>
  )
}

// ——— بند ١٥ (ورقة ٩٩) — لوحة «تخصيص الشكل»: أزرار بإيد المالك مو طلبات ———
// (أ) لوح ألوان حرّ يضبط متغيّرات CSS فورًا · (ب) تكبير عام · (ج) ترتيب التبويبات.
// كلّه localStorage — بلا أي مكتبة خارجية جديدة.

/** القيمة الحالية للمتغيّر: المخصَّص المحفوظ، وإلا قيمة الإطلالة الحالية (إن كانت hex). */
function currentHex(id: string, colors: ColorsMap): string {
  if (colors[id]) return colors[id]
  if (id === 'glass') return '#ffffff'
  const raw = getComputedStyle(document.documentElement).getPropertyValue(id).trim()
  return /^#[0-9a-fA-F]{6}$/.test(raw) ? raw : '#888888'
}

function AppearancePanel() {
  const [colors, setColors] = useState<ColorsMap>(() => loadColors())
  const [zoom, setZoomState] = useState<number>(() => loadZoom())
  const [order, setOrder] = useState<string[]>(() => getTabOrder(SECTIONS.map((s) => s[0])))

  // لو غيّر الترتيبَ طرفٌ آخر (تبويب ثانٍ مفتوح) — انعكس هون كمان
  useEffect(() => {
    const sync = () => setOrder(getTabOrder(SECTIONS.map((s) => s[0])))
    window.addEventListener(TAB_ORDER_EVENT, sync)
    return () => window.removeEventListener(TAB_ORDER_EVENT, sync)
  }, [])

  const labelOf = (id: string) => SECTIONS.find((s) => s[0] === id)?.[1] ?? id

  const move = (idx: number, dir: -1 | 1) => {
    const next = order.slice()
    const j = idx + dir
    if (j < 0 || j >= next.length) return
    // «الذرات» هو التبويب الخامس الثابت، ولا يُسمح بسحبه أو تبديل مكانه.
    if (next[idx] === 'atoms' || next[j] === 'atoms') return
    ;[next[idx], next[j]] = [next[j], next[idx]]
    setOrder(next)
    saveTabOrder(next) // يُطبَّق على شريط الملاحة فورًا (App يستمع للحدث)
  }

  const customized = Object.keys(colors).length > 0

  return (
    <div className="scard">
      <div className="st" style={{ fontWeight: 700 }}>🎨 تخصيص الشكل</div>
      <div className="ss dim">
        كل شي هون بإيدك وبيتطبّق فورًا وبيتحفظ على هالجهاز — ما بتحتاج تطلب من حدا.
        الألوان الحرّة تعلو كل الإطلالات الجاهزة؛ زر «رجوع للون الأساسي» يمسح كل تعديلاتك ويرجّع ألوان الفوركس الأصلية فورًا.
      </div>

      {/* (أ) لوح الألوان الحرّ */}
      <div style={{ marginTop: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>لوح الألوان — اختر اللون يلي بدّك ياه</div>
          <span className={customized ? 'settings-mode custom' : 'settings-mode base'}>
            {customized ? '● وضع مخصّص' : '● الوضع الأساسي'}
          </span>
          <button className="btn" style={{ fontSize: 12 }} disabled={!customized}
            onClick={() => { resetColors(); setColors({}) }}>🎨 رجوع للون الأساسي</button>
        </div>
        <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', marginTop: 8 }}>
          {COLOR_VARS.map((def) => (
            <label key={def.id} className="scard" style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}
              title={def.hint ?? ''}>
              <input type="color" value={currentHex(def.id, colors)}
                style={{ width: 42, height: 32, border: 'none', background: 'none', padding: 0, cursor: 'pointer' }}
                onChange={(e) => setColors(saveColor(def.id, e.target.value))} />
              <span style={{ display: 'grid', gap: 2 }}>
                <span style={{ fontSize: 13.5 }}>{def.label}</span>
                {colors[def.id] ? <span className="num dim" style={{ fontSize: 11 }}>{colors[def.id]} — مخصَّص</span>
                  : <span className="dim" style={{ fontSize: 11 }}>من الإطلالة</span>}
              </span>
            </label>
          ))}
        </div>
      </div>

      {/* (ب) التكبير العام */}
      <div style={{ marginTop: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>🔍 تكبير عام</div>
          <input type="range" min={ZOOM_MIN} max={ZOOM_MAX} step={1} value={zoom}
            style={{ flex: 1, minWidth: 180, maxWidth: 380 }}
            onChange={(e) => { const v = Number(e.target.value); setZoomState(v); saveZoom(v) }} />
          <span className="num" style={{ fontWeight: 700, minWidth: 48 }}>{zoom}%</span>
          <button className="btn" style={{ fontSize: 12 }} disabled={zoom === 100}
            onClick={() => { setZoomState(100); saveZoom(100) }}>↩️ 100%</button>
        </div>
        <div className="ss dim">يضبط حجم الخطّ الأساس وتكبير كل الأقسام — عدا صفحة «الشبكة» تبقى بدقّتها الأصلية.</div>
      </div>

      {/* (ج) ترتيب التبويبات */}
      <div style={{ marginTop: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>🧭 ترتيب التبويبات</div>
          <span className="ss dim" style={{ marginTop: 0 }}>فوق/تحت — والشريط فوق بيترتّب لحظيًّا</span>
          <button className="btn" style={{ fontSize: 12 }}
            onClick={() => { resetTabOrder(); setOrder(getTabOrder(SECTIONS.map((s) => s[0]))) }}>↩️ الترتيب الافتراضي</button>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: 6, marginTop: 8 }}>
          {order.map((id, idx) => (
            <div key={id} style={{ display: 'flex', alignItems: 'center', gap: 8, border: '1px solid var(--glassb)', borderRadius: 9, padding: '5px 9px', background: 'var(--glass)' }}>
              <span className="num dim" style={{ fontSize: 11, minWidth: 20 }}>{idx + 1}</span>
              <span style={{ fontSize: 13.5, flex: 1 }}>{labelOf(id)}</span>
              <button className="btn" style={{ fontSize: 11, padding: '2px 8px' }} disabled={idx === 0}
                onClick={() => move(idx, -1)} title="فوق">▲</button>
              <button className="btn" style={{ fontSize: 11, padding: '2px 8px' }} disabled={idx === order.length - 1}
                onClick={() => move(idx, 1)} title="تحت">▼</button>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default function Settings() {
  const atoms = useStore((s) => s.atoms)
  const [id, setId] = useState<number | ''>('')
  const options = useMemo(() => Object.values(atoms).sort((a, b) => a.id - b.id), [atoms])
  // أمر المالك ٢٠٢٦-٠٨-٢٩: «شيل هدول من الإعدادات».
  // «عيارات القرار» و«سرعة التحليل لكل أصل» و«المُعامِلات المعلنة» ثلاثتها
  // سجلّات فوركسيّة (shared/decision_dials.py كلّه ذرّات فوركس، و/gov/parameters
  // يردّ available=false على الكريبتو) — فكانت تظهر بقسم أسمر بطاقاتٍ فارغة
  // بنصوص فوركس. تُخفى هنا بحسب السوق، ولا يتغيّر شيء بالفوركس.
  const market = useMarket()
  const governedDials = market === 'forex'

  return (
    <div className={`section settings-page${governedDials ? ' settings-forex' : ''}`} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      {governedDials ? (
        <div className="settings-hero">
          <div className="settings-hero-mark">⚙️</div>
          <div>
            <div className="st" style={{ fontSize: 17, fontWeight: 700 }}>إعدادات الفوركس</div>
            <div className="ss dim">تخصيص الشكل والإعدادات العامة — كل قسم يحتفظ بمفاتيحه الخاصة.</div>
          </div>
          <div className="settings-legend"><span><i className="settings-dot green" /> حيّ</span><span><i className="settings-dot amber" /> يحتاج انتباه</span><span><i className="settings-dot red" /> خطر</span></div>
        </div>
      ) : null}
      {governedDials ? <DecisionDialsCard excludeNames={[...ANALYSIS_DIAL_NAMES, ...DECISION_DIAL_NAMES, ...RISK_DIAL_NAMES]} includeExtras={false} /> : null}
      {/* بند ١٥ — لوحة تخصيص الشكل: مبنيّة حول بطاقة «عيارات القرار» لا فوقها */}
      <AppearancePanel />
      <select className="search" value={id} onChange={(e) => setId(e.target.value === '' ? '' : Number(e.target.value))}>
        <option value="">اختر ذرة لتعديل إعداداتها…</option>
        {options.map((a) => <option key={a.id} value={a.id}>{a.name_ar} #{a.id}</option>)}
      </select>
      {id !== '' ? <AtomConfigForm atomId={id} /> : null}
    </div>
  )
}
