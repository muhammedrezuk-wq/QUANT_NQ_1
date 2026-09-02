// التنفيذ (863) — عيلة 550: بوّابة الأوامر · أسباب الحجب · الإدارة الذكية · النتائج.
// بند 15 شقّ 2 (دفتر 97): «من نفس المكان أقدر أشيل المنع بمجرّد ضغطة» — زرّا فتح/إقفال
// البوّابة هون، بنفس مسار «تحكّم» (بوّابة الأوامر 901 بتأكيد مزدوج — لا مسار جديد).
// بند 14: رسم سلسلة الذرّات نزل لطبقة مطويّة تُفتح عند الطلب — الجواب البسيط أوّلًا.
// بند ١٨ (ورقة ٩٩) — التسع بوابات الموحّدة: حُصرت من الكود فعليًّا (لا من الورق):
// كل ذرّة تملك سلطة نعم/لا حقيقية على المسار قرار→أمر→منصّة. الفلاتر 460-464/468
// ليست على المسار — هي مدخلات 454 التي تحجب وتسمّي الحاجب (blocked_by).
//   قرار:  454 فلتر القرار ← 466 موافقة القرار
//   أمر:   586 حلّ الرمز ← 585 الهامش ← 551 البناء ← 584 شرعية الستوب ← 552 البوّابة
//   كتابة: 601 كاتب الجسر · إدارة: 575 مرسل الإدارة
// لكل بطاقة: آخر رفض من أحداثها الحقيقية + زرّ فتح/إقفال حيث يدعمه الكود (552/575
// عبر 901 حصراً) + عيارها المحكوم DECISION_* بمكانها (454 — بمكوّن العيارات المشترك).
import { useState } from 'react'
import { useStore } from '../core/store'
import { arabicVisible, arabicHealth } from '../core/arabic'
import { confirmedCommand } from '../core/commands'
import { DialRow, useDecisionDials } from './Settings'
import { SectionConfigTable } from '../components/SectionAtoms'
import { AccountsPair } from '../components/AccountsBar'

const num = (n?: number | null, dp = 2) =>
  n == null ? '—' : n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: dp })

const time = (ts: number) => new Date(ts).toLocaleTimeString('ar-EG-u-nu-latn', { hour12: false })

const SIDE: Record<string, [string, string]> = { BUY: ['شراء', 'var(--green)'], SELL: ['بيع', 'var(--red)'] }

const KIND: Record<string, [string, string]> = {
  built: ['انبنى', 'var(--dim)'],
  skipped: ['ما بُني — توقّف قبل البوّابة', 'var(--amber)'],
  final: ['انبعت للمنصّة', 'var(--green)'],
  rejected: ['مرفوض بالبوّابة', 'var(--red)'],
}

// كل أسباب رفض 552 الحقيقيّة — من order_validation.py و_on_built نفسها
// (atom.py) لا افتراض. «disabled»/«halted» هنّ بالضبط سبب «البوّابة مقفولة».
const REJECT: Record<string, string> = {
  // order_validation.py — فحص شكل الأمر قبل أي قرار بوّابة
  BAD_ACTION: 'نوع أمر غير معروف',
  NO_SYMBOL: 'بلا رمز',
  BAD_SIDE: 'اتجاه غلط',
  BAD_VOLUME: 'حجم غلط',
  NO_TICKET: 'أمر تعديل/إغلاق بلا تذكرة صفقة',
  BAD_PRICE: 'سعر غلط',
  NO_STOP: 'بلا وقف خسارة',
  NO_TARGET: 'بلا هدف',
  BUY_LEVELS: 'مستويات الشراء غلط (الوقف لازم تحت السعر والهدف فوقه)',
  SELL_LEVELS: 'مستويات البيع غلط (الهدف لازم تحت السعر والوقف فوقه)',
  NO_BUDGET: 'مركز دائم بلا ميزانيّة مخاطرة موجبة',
  PERPETUAL_NO_TP: 'مركز دائم ومعه هدف — ممنوع، المركز الدائم ما بينسكر بهدف',
  // atom.py (552 نفسها) — قرار البوّابة بعد صحّة الشكل
  disabled: 'البوّابة مقفولة — قرارك إنت',
  halted: 'إيقاف طارئ فعّال',
  MISSING_ACCOUNT_BROKER_OR_SYMBOL: 'هويّة الحساب أو الوسيط أو الرمز ناقصة',
  MISSING_MAGIC: 'رقم تعريف الأمر (magic) مفقود',
  SYMBOL_NOT_ALLOWED: 'الرمز غير مسموح بقائمة الأصول',
  SPREAD_TOO_WIDE: 'الفرق السعريّ أوسع من الحدّ',
  CLOCK_NOT_SYNCED: 'ساعة النظام غير متزامنة',
  RECONCILIATION_NOT_MATCHED: 'مراكز المنصّة ما تطابقت مع سجلّنا بعد',
  REFERENCE_NOT_USABLE: 'السعر المرجعي غير صالح للاستعمال',
  EXPOSURE_STATE_NOT_USABLE: 'حالة التعرّض غير جاهزة لفتح مركز جديد',
}

// أسباب توقّف 551 (باني الأمر) الخمسة — لا يصل حتى إلى البوّابة أصلًا
const SKIP_REASON: Record<string, string> = {
  UPSTREAM_REJECTED: 'مرفوض من فوق (466 ما اعتمد القرار)',
  BAD_SYMBOL_OR_SIDE: 'رمز أو جهة غير صالحة',
  NO_SIZE_YET: 'ما وصل تحجيم للّوت بعد',
  INCOMPLETE_SIZE_DATA: 'بيانات التحجيم ناقصة (سعر/حجم/وقف)',
  INVALID_RISK_DISTANCE: 'مسافة المخاطرة غير صالحة (الوقف بالجهة الغلط)',
}

// ── بند ١٨: قواميس أسباب كل بوّابة — من كود ذرّتها نفسه، لا افتراض ──

// 454 فلتر القرار — رموز blocked_by (atom.py: فلاتر + تشغيلي + بوّابة الدرجة)
const BLOCKED_BY_AR: Record<string, string> = {
  confidence_filter: 'فلتر الثقة (460)',
  conditions_filter: 'فلتر الشروط (461)',
  timing_filter: 'فلتر التوقيت (462)',
  position_filter: 'حارس المراكز (463)',
  freshness_filter: 'فلتر الطزاجة (464)',
  asset_filter: 'التحكّم بالأصول (468)',
  signal_wait: 'الإشارة «انتظار» — ما في اتجاه يُنفَّذ',
  score_gate: 'الدرجة تحت العتبة الدنيا',
  calendar_unknown: 'التقويم الاقتصادي مجهول',
  calendar_window: 'داخل نافذة حدث اقتصادي',
  market_quality_unknown: 'جودة السوق مجهولة',
  market_quality_invalid: 'جودة السوق ساقطة',
}
// مُصدَّرة (حزمة ج، ج٢.٢): جدول الحواجز بصفحة القرار يستعمل نفس ترجمة أسماء
// الحواجز — 454 تسمّي الحاجز بنفس الرمز (blocked_by) في الموضعين.
export const blockedByAr = (token: string): string =>
  BLOCKED_BY_AR[token] ?? (token.startsWith('feed_') ? `التغذية غير نشطة (${token.slice(5)})` : arabicVisible(token, 'حاجب غير مترجَم'))

// 466 موافقة القرار
const APPROVAL_REASON: Record<string, string> = {
  BLOCKED_UPSTREAM: 'محجوب من فوق — فلتر القرار (454) ما مرّره',
  NO_ACTIONABLE_SIGNAL: 'ما في إشارة قابلة للتنفيذ — الاتجاه مو شراء/بيع',
}

// 586 بوّابة الرموز (stage=SYMBOL_RESOLUTION)
const SYMBOL_REASON: Record<string, string> = {
  SYMBOL_RESOLUTION_TIMEOUT: 'مهلة حلّ الرمز انتهت بلا جواب',
  MISSING_REQUEST_ID: 'بلا معرّف طلب',
  DUPLICATE_REQUEST_ID: 'معرّف الطلب مكرّر',
  MISSING_ACCOUNT_OR_SYMBOL: 'الحساب أو الرمز ناقص',
  SYMBOL_UNRESOLVED: 'الرمز ما انحلّ لهويّة وسيط حقيقية',
}

// 585 حارس الهامش (حكم risk.validation.completed)
const MARGIN_REASON: Record<string, string> = {
  MISSING_OR_DUPLICATE_REQUEST_ID: 'معرّف الطلب مفقود أو مكرّر',
  ACCOUNT_MARGIN_DATA_MISSING: 'بيانات هامش الحساب مفقودة',
  ACCOUNT_STATE_STALE: 'بيانات الحساب متقادمة',
  FREE_MARGIN_MISSING: 'الهامش الحرّ غير معروف',
  MARGIN_PER_VOLUME_MISSING: 'هامش اللوت غير معروف من الوسيط',
  INSUFFICIENT_FREE_MARGIN: 'الهامش الحرّ ما بيكفي للأمر',
  MANAGEMENT_NO_NEW_MARGIN: 'أمر إدارة — ما بيحتاج هامش جديد (مسموح)',
}

// 584 شرعيّة الستوب (stage=STOP_LEGALITY) — يكمّل قاموس 552 بأسبابه الخاصة
const STOP_REASON: Record<string, string> = {
  BAD_ACCOUNT_SYMBOL_OR_SIDE: 'الحساب أو الرمز أو الجهة ناقصة',
  NO_SYMBOL_SPECS: 'مواصفات الرمز ما وصلت من الوسيط بعد',
  INCOMPLETE_ORDER: 'الأمر ناقص (سعر/حجم/وقف)',
  VOLUME_BELOW_MINIMUM: 'الحجم تحت أدنى حدّ الوسيط',
  VOLUME_ABOVE_MAXIMUM: 'الحجم فوق أقصى حدّ الوسيط',
  BUY_LEVELS: 'مستويات الشراء غلط (الوقف لازم تحت السعر)',
  SELL_LEVELS: 'مستويات البيع غلط (الوقف لازم فوق السعر)',
  STOP_LEGALITY_VOLUME_TOO_SMALL: 'بعد تصحيح مسافة الوقف صار الحجم أصغر من المسموح',
}

// 601 كاتب جسر الدماغ (platform.brain_signal.write_failed / halted)
const WRITE_REASON: Record<string, string> = {
  ACCOUNT_ID_MISMATCH: 'رقم الحساب لا يطابق حساب الجسر',
  MISSING_ACCOUNT_ID: 'رقم الحساب مفقود',
  SYMBOL_UNRESOLVED: 'رمز الوسيط غير محلول',
  MISSING_REQUEST_ID_OR_BAD_ACTION: 'معرّف الطلب مفقود أو نوع الفعل غلط',
  MISSING_OR_FOREIGN_MAGIC: 'الرقم التعريفي (magic) مفقود أو غريب',
  CURRENT_ACCOUNTS_UNAVAILABLE_OR_BLOCKED: 'حسابات الجسر الحالية غير متاحة أو محجوبة',
  BRIDGE_ACCOUNT_ID_MISMATCH_OR_BLOCKED: 'حساب الجسر لا يطابق أو محجوب',
}

// 575 مرسل الإدارة (execution.command.failed)
const MANAGE_FAIL_REASON: Record<string, string> = {
  MANAGEMENT_GATE_DISABLED: 'مرسل الإدارة مقفول — التعديل ما انبعت',
}

const MANAGE_ACTION: Record<string, string> = {
  MODIFY_SL: 'تعديل الوقف',
  CLOSE_PARTIAL: 'إغلاق جزئي',
  CLOSE: 'إغلاق',
}

const MANAGE_REASON: Record<string, string> = {
  breakeven: 'نقل الوقف للتعادل',
  trailing: 'تتبّع الوقف مع البنية',
  partial_take: 'جني ربح جزئي',
}

const MANAGE_STAGE: Record<string, [string, string]> = {
  intent: ['نيّة', 'var(--dim)'],
  command: ['أمر', 'var(--amber)'],
  written: ['انكتب للجسر', 'var(--green)'],
}

const OUTCOME_TYPE: Record<string, string> = {
  CLOSED: 'إغلاق كامل',
  PARTIAL: 'إغلاق جزئي',
}

// سلسلة فتح الصفقة + سلسلة الإدارة — بترتيب مرور الأمر (المعرّف = رقم الذرة الحقيقي)
const OPEN_CHAIN: [number, string][] = [
  [467, 'مرسل القرار'], [516, 'قاطع الأمان'], [512, 'الوقف الهيكلي'], [513, 'حجم اللوت'],
  [551, 'باني الأمر'], [552, 'المدقق (البوّابة)'], [601, 'كاتب الجسر'],
]
const MANAGE_CHAIN: [number, string][] = [
  [572, 'التعادل'], [573, 'التتبع'], [574, 'الإغلاق الجزئي'],
  [570, 'مدير الإدارة'], [575, 'مرسل الإدارة'],
]

const HEALTH_COLOR: Record<string, string> = { healthy: 'green', degraded: 'amber', unhealthy: 'red' }

// ── بند ١٨: تعريف التسع بوابات — بترتيب مرور القرار/الأمر الحقيقي ──
interface GateDef {
  id: number
  name: string
  family: string
  role: string
  switchGate?: '552' | '575' // مفتاح 901 موجود بالكود لهاتين حصرًا (execution_gate)
  dialNames?: string[]       // عيارات DECISION_* التي تستهلكها هذه الذرّة فعليًّا
}

const GATES: GateDef[] = [
  { id: 454, name: 'فلتر القرار', family: 'قرار',
    role: 'يفحص القرار على الفلاتر الست (ثقة·شروط·توقيت·مراكز·طزاجة·أصول) والتقويم وجودة السوق والتغذية — ويسمّي الحاجب.',
    dialNames: ['DECISION_MIN_SCORE', 'DECISION_FILTER_TTL_S'] },
  { id: 466, name: 'موافقة القرار', family: 'قرار',
    role: 'الموافقة الأخيرة: يعتمد القرار الذي مرّ الفلاتر واتجاهه شراء أو بيع.' },
  { id: 586, name: 'بوّابة الرموز', family: 'أمر',
    role: 'يحلّ رمز الأمر لهويّة وسيط حقيقية قبل كل شيء — المجهول يُرفض.' },
  { id: 585, name: 'حارس الهامش', family: 'أمر',
    role: 'يفحص الهامش الحرّ ويحجزه قبل تمرير الأمر — هامش ما بيكفي = رفض.' },
  { id: 551, name: 'باني الأمر', family: 'أمر',
    role: 'يبني الأمر من القرار المعتمد والتحجيم — الناقص لا يُبنى أصلًا.' },
  { id: 584, name: 'شرعيّة الستوب', family: 'أمر',
    role: 'يفحص شرعية الوقف/الهدف ومسافات الوسيط — يصحّح ما يقبل التصحيح ويرفض الباقي.' },
  { id: 552, name: 'مدقّق الأمر (البوّابة)', family: 'أمر', switchGate: '552',
    role: 'البوّابة النهائية: أمر صالح + بوّابة مفتوحة = قرار نهائي ينبعت للجسر.' },
  { id: 601, name: 'كاتب جسر الدماغ', family: 'كتابة',
    role: 'يكتب القرار النهائي لجسر المنصّة — هوية الحساب والرمز والمعرّف لازم كاملة.' },
  { id: 575, name: 'مرسل الإدارة', family: 'إدارة', switchGate: '575',
    role: 'يبعت تعديلات الصفقات المفتوحة (تعادل·تتبّع·جني جزئي) — لا يفتح صفقة أبدًا.' },
]

const FAMILY_COLOR: Record<string, string> = { 'قرار': 'green', 'أمر': 'amber', 'كتابة': 'red', 'إدارة': 'grey' }

function GatesBoard() {
  const atoms = useStore((s) => s.atoms)
  const gate = useStore((s) => s.gate)
  const orders = useStore((s) => s.execOrders)
  // اشتراكات دقيقة — كل بوّابة تُقرأ من حدثها الحقيقي هي نفسها، لا استنتاج
  const filtered = useStore((s) => s.streams['decision.filtered.state']) as
    { metadata?: { passed?: boolean; blocked_by?: string[] } } | undefined
  const approval = useStore((s) => s.streams['decision.approved.state']) as
    { metadata?: { approved?: boolean; reason?: string | null } } | undefined
  const verdict = useStore((s) => s.streams['risk.validation.completed']) as
    { approved?: boolean; reason?: string } | undefined
  const writeFail = useStore((s) => s.streams['platform.brain_signal.write_failed']) as
    { reason?: string } | undefined
  const writeHalted = useStore((s) => s.streams['platform.brain_signal.halted']) as
    { reason?: string } | undefined
  const manageFail = useStore((s) => s.streams['execution.command.failed']) as
    { reason?: string } | undefined
  const { dials } = useDecisionDials()
  const [busy, setBusy] = useState<string>('')
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [showDials, setShowDials] = useState<Record<number, boolean>>({})

  // نفس أمر «تحكّم» حرفيًّا: execution_gate عبر بوّابة الأوامر 901 بتأكيد مزدوج
  const setGateEnabled = async (def: GateDef, enabled: boolean) => {
    if (!def.switchGate) return
    setBusy(def.switchGate)
    const r = await confirmedCommand('execution_gate', { gate: def.switchGate, enabled })
    setNotes((n) => ({ ...n, [def.id]: r.ok ? `🟢 ${r.message ?? (enabled ? 'فُتحت' : 'أُقفلت')}` : `🛑 ${r.message ?? 'ما تمّ'}` }))
    setBusy('')
  }

  // آخر رفض من execution.order.rejected — التمييز بحقل stage الذي تضعه الذرّة نفسها:
  // 584 تكتب STOP_LEGALITY و586 تكتب SYMBOL_RESOLUTION و552 لا تكتب stage إطلاقًا.
  const lastReject = (stage: string | null): { reason?: string; symbol?: string } | undefined =>
    orders.find((o) => o.kind === 'rejected'
      && ((o as unknown as { stage?: string }).stage ?? null) === stage) as { reason?: string; symbol?: string } | undefined
  const lastSkip = orders.find((o) => o.kind === 'skipped')

  // حالة/آخر منع لكل بوّابة — من حدثها؛ وإلا من صحّة ذرّتها (بلا اختراع)
  const info = (def: GateDef): { status: string; color: string; refusal?: string } => {
    const a = atoms[def.id]
    const base = a == null
      ? { status: 'مو محمّلة بالنواة', color: 'grey' }
      : a.state !== 'running'
        ? { status: 'واقفة', color: 'red' }
        : { status: arabicHealth(a.health?.message) || 'شغّالة', color: HEALTH_COLOR[a.health?.state ?? ''] ?? 'grey' }
    switch (def.id) {
      case 454: {
        const m = filtered?.metadata
        if (m?.passed === true) return { ...base, status: 'مرّرت آخر قرار ✓', color: 'green' }
        const by = m?.blocked_by ?? []
        if (by.length) return { ...base, refusal: by.map(blockedByAr).join(' · ') }
        // ما وصل حدث حي بهالجلسة بعد — 454 تنشر آخر حجب برسالة صحّتها نفسها
        // (last_blocked=miss=…&nbsp;fail=…) فنفكّه بنفس الترجمة، لا «حقل تقني».
        const msg = a?.health?.message ?? ''
        const lb = /last_blocked=(.+)$/.exec(msg)
        if (lb && lb[1].trim() !== '-') {
          const BUCKET: Record<string, string> = { miss: 'غائب', stale: 'بائت', mism: 'دورة مغايرة', fail: 'فاشل', op: 'تشغيلي' }
          const refusal = lb[1].trim().split(/\s+/).map((seg) => {
            const [bucket, items] = seg.split('=')
            const names = (items ?? '').split(',').filter(Boolean).map(blockedByAr).join(' · ')
            return items ? `${BUCKET[bucket] ?? bucket}: ${names}` : blockedByAr(seg)
          }).join(' · ')
          return { ...base, status: arabicHealth(msg.slice(0, lb.index).trim()), refusal }
        }
        return base
      }
      case 466: {
        const m = approval?.metadata
        if (m?.approved === true) return { ...base, status: 'اعتمدت آخر قرار ✓', color: 'green' }
        if (m?.reason) return { ...base, refusal: APPROVAL_REASON[m.reason] ?? arabicVisible(m.reason, 'سبب غير معروف') }
        return base
      }
      case 586: {
        const r = lastReject('SYMBOL_RESOLUTION')
        return r?.reason ? { ...base, refusal: `${SYMBOL_REASON[r.reason] ?? arabicVisible(r.reason, 'سبب غير معروف')}${r.symbol ? ` — ${r.symbol}` : ''}` } : base
      }
      case 585: {
        if (verdict?.approved === false && verdict.reason) {
          return { ...base, refusal: MARGIN_REASON[verdict.reason] ?? arabicVisible(verdict.reason, 'سبب غير مترجَم') }
        }
        if (verdict?.approved === true) return { ...base, status: 'وافق على آخر فحص هامش ✓', color: 'green' }
        return base
      }
      case 551: {
        return lastSkip?.reason
          ? { ...base, refusal: `${SKIP_REASON[lastSkip.reason] ?? arabicVisible(lastSkip.reason, 'سبب غير مترجَم')}${lastSkip.symbol ? ` — ${lastSkip.symbol}` : ''}` }
          : base
      }
      case 584: {
        const r = lastReject('STOP_LEGALITY')
        return r?.reason ? { ...base, refusal: `${STOP_REASON[r.reason] ?? arabicVisible(r.reason, 'سبب غير مترجَم')}${r.symbol ? ` — ${r.symbol}` : ''}` } : base
      }
      case 552: {
        const halted = gate?.status === 'HALTED' || gate?.status === 'PARTIAL_HALT'
        const open = gate?.status === 'LIVE'
        const r = lastReject(null)
        const refusal = r?.reason ? `${REJECT[r.reason] ?? arabicVisible(r.reason, 'سبب غير مترجَم')}${r.symbol ? ` — ${r.symbol}` : ''}` : undefined
        if (halted) return { status: '🛑 إيقاف طارئ — الأوامر بتضلّ مقفولة', color: 'red', refusal }
        if (open) return { status: '⚠️ مفتوحة — الأمر الصالح بينبعت للمنصّة فعليًّا', color: 'red', refusal }
        if (gate) return { status: '🔒 مقفولة — كل أمر بيُرفض جوّاها، ولا صفقة بتنفتح', color: 'green', refusal }
        return { ...base, refusal }
      }
      case 601: {
        if (writeHalted) return { ...base, refusal: 'موقوف بالطوارئ — ما بيكتب شي للجسر' }
        if (writeFail?.reason) return { ...base, refusal: WRITE_REASON[writeFail.reason] ?? arabicVisible(writeFail.reason, 'سبب غير مترجَم') }
        return base
      }
      case 575: {
        const disabled = (a?.health?.message ?? '').includes('DISABLED')
        const refusal = manageFail?.reason
          ? MANAGE_FAIL_REASON[manageFail.reason] ?? arabicVisible(manageFail.reason, 'سبب غير مترجَم')
          : undefined
        if (a && disabled) return { status: '🔒 مقفول — التعديلات ما بتنبعت', color: 'amber', refusal }
        if (a && a.state === 'running') return { status: 'شغّال — يبعت تعديلات الصفقات المفتوحة', color: 'green', refusal }
        return { ...base, refusal }
      }
      default: return base
    }
  }

  // مفتاح الفتح/الإقفال: حالة 552 من حدث بوّابتها، و575 من صحّة ذرّته (DISABLED)
  const isOpen = (def: GateDef): boolean | null => {
    if (def.switchGate === '552') return gate ? gate.status === 'LIVE' : null
    if (def.switchGate === '575') {
      const a = atoms[575]
      if (!a) return null
      return a.state === 'running' && !(a.health?.message ?? '').includes('DISABLED')
    }
    return null
  }

  return (
    <div className="scard" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '11px 14px', borderBottom: '1px solid var(--glassb)' }}>
        <div className="st" style={{ fontWeight: 700 }}>🚧 بوابات القرار والتنفيذ التسع — بترتيب مرور الأمر</div>
        <div className="ss dim">
          كل بطاقة تقول سبب آخر منع من أحداث بوّابتها الحقيقية. المفاتيح موجودة حيث يدعمها الكود (552 و575 عبر بوّابة الأوامر 901
          بتأكيد مزدوج)؛ وبوّابة عتبتها من سجلّ العيارات المحكوم تنضبط من نفس البطاقة. الفلاتر 460-468 مدخلات الفلتر 454 — يسمّيها لمّا تحجب.
        </div>
      </div>
      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(290px, 1fr))', padding: 10 }}>
        {GATES.map((def, i) => {
          const st = info(def)
          const open = isOpen(def)
          const gateDials = (dials ?? []).filter((d) => def.dialNames?.includes(d.name))
          return (
            <div className="scard" key={def.id} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, flexWrap: 'wrap' }}>
                <span className="num dim" style={{ fontSize: 11 }}>{i + 1}</span>
                <b style={{ fontSize: 14.5 }}>{def.name}</b>
                <span className="num dim" style={{ fontSize: 11 }}>#{def.id}</span>
                <span className={`pill ${FAMILY_COLOR[def.family]}`} style={{ fontSize: 11, marginInlineStart: 'auto' }}>{def.family}</span>
              </div>
              <div className="ss dim" style={{ marginTop: 0 }}>{def.role}</div>
              <div style={{ fontSize: 13.5, color: `var(--${st.color})`, fontWeight: 600 }}>{st.status}</div>
              {st.refusal ? (
                <div style={{ fontSize: 13, color: 'var(--red)' }}>آخر منع: {st.refusal}</div>
              ) : (
                <div className="ss dim" style={{ marginTop: 0 }}>ما في منع مسجَّل من أحداثها بهالجلسة</div>
              )}
              {def.switchGate ? (
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 2 }}>
                  {open === true
                    ? <button className="btn" disabled={busy === def.switchGate} onClick={() => void setGateEnabled(def, false)}>⏹️ سكّرها</button>
                    : <button className="btn" disabled={busy === def.switchGate} onClick={() => void setGateEnabled(def, true)}>▶️ افتحها{def.id === 552 ? ' — بيصير الأمر الصالح ينبعت فعليًّا' : ''}</button>}
                  {notes[def.id] ? <span style={{ fontSize: 12.5 }}>{notes[def.id]}</span> : null}
                </div>
              ) : null}
              {def.dialNames ? (
                gateDials.length ? (
                  <div style={{ marginTop: 2 }}>
                    <button className="btn" style={{ fontSize: 12 }} onClick={() => setShowDials((s) => ({ ...s, [def.id]: !s[def.id] }))}>
                      {showDials[def.id] ? '▴ خبّي عياراتها' : `⚙️ عياراتها المحكومة (${gateDials.length}) — اضبطها من هون`}
                    </button>
                    {showDials[def.id] ? (
                      <div className="cards" style={{ gridTemplateColumns: '1fr', marginTop: 8 }}>
                        {gateDials.map((d) => <DialRow key={d.name} dial={d} />)}
                      </div>
                    ) : null}
                  </div>
                ) : <div className="ss dim">عياراتها المحكومة ما وصلت من الخادم بعد</div>
              ) : null}
            </div>
          )
        })}
      </div>
      <div className="ss dim" style={{ padding: '0 14px 10px' }}>
        الحصر من الكود لا من الورق: هدول التسع وحدهنّ يملكن سلطة نعم/لا على مسار قرار→أمر→منصّة.
        باقي المحطّات (تحجيم 513 · وقف هيكلي 512 · قاطع أمان 516) تُغذّي ولا تبوّب — وقاطع الأمان له بطاقته بقسم «المخاطر».
      </div>
    </div>
  )
}

// ——— حزمة ج (ج٣، ختم 22): بطاقة المركز والهدف الدائم لكل رمز — من 581 مباشرة ———
const TARGET_ACTION_AR: Record<string, string> = {
  HOLD: 'ثبات — لا تعديل', REDUCE: 'تخفيض', HEDGE: 'تحوّط', REBALANCE: 'إعادة توازن', ADD: 'زيادة', BLOCKED: 'محجوب',
}
const TARGET_STATUS_AR: Record<string, { text: string; cls: string }> = {
  WAITING: { text: 'بانتظار المدخلات', cls: 'grey' }, BLOCKED: { text: 'محجوب', cls: 'red' }, READY: { text: 'جاهز — هدف محسوب', cls: 'green' },
}
const TARGET_REASON_AR: Record<string, string> = {
  PORTFOLIO_STATE_MISSING: 'حالة المحفظة غائبة', SYSTEM_NOT_ALIVE: 'النظام غير حيّ',
  NETTING_UNSUPPORTED: 'وضع Netting غير مدعوم', ACCOUNT_MODE_UNKNOWN: 'وضع الحساب مجهول',
  HARD_STOP_FROZEN: 'الوقف الصلب مجمَّد', PORTFOLIO_FROZEN: 'المحفظة مجمَّدة',
  RISK_REBALANCE: 'إعادة توازن للمخاطر', NO_DIRECTION: 'بلا اتجاه',
  MISSING_R_PRICE_DIAL_OR_SPECS: 'الميزانية/السعر/العيار/المواصفات ناقصة', NEUTRAL_KEEP_GROSS: 'إبقاء الإجمالي محايدًا',
}
const PORTFOLIO_STATE_AR: Record<string, { text: string; cls: string }> = {
  UNKNOWN: { text: 'مجهولة', cls: 'grey' }, FROZEN: { text: 'مجمَّدة', cls: 'red' },
  PAUSED: { text: 'موقوفة', cls: 'amber' }, WARNING: { text: 'تحذير', cls: 'amber' }, HEDGING: { text: 'تحوّط', cls: 'amber' },
}

function TargetCard({ symbol }: { symbol: string }) {
  const t = useStore((s) => s.symbolStreams['perpetual.target.state']?.[symbol]) as Record<string, unknown> | undefined
  if (!t) return null
  const status = String(t.status ?? '')
  const st = TARGET_STATUS_AR[status] ?? { text: arabicVisible(status, status), cls: 'grey' }
  const action = String(t.action ?? '')
  const portfolio = PORTFOLIO_STATE_AR[String(t.state ?? '')] ?? { text: arabicVisible(t.state, String(t.state ?? '—')), cls: 'grey' }
  const g = (k: string) => { const v = t[k]; return typeof v === 'number' ? num(v, 4) : undefined }
  return (
    <div className="scard" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <b style={{ fontSize: 14.5 }}>{symbol}</b>
        <span className={`pill ${st.cls}`}>{st.text}</span>
        <span className="pill grey" style={{ marginInlineStart: 'auto' }}>{TARGET_ACTION_AR[action] ?? arabicVisible(action, action)}</span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', fontSize: 12.5 }}>
        <span><span className="dim">المركز الحالي</span> <b className="num">شراء {g('current_buy') ?? '—'} · بيع {g('current_sell') ?? '—'}</b></span>
        <span><span className="dim">الصافي الحالي</span> <b className="num">{g('current_net') ?? '—'}</b></span>
        <span><span className="dim">الهدف الصافي</span> {g('target_net') != null ? <b className="num">{g('target_net')}</b> : <span className="dim">لم يُحسب بعد</span>}</span>
        <span><span className="dim">الهدف الإجمالي</span> {g('target_gross') != null ? <b className="num">{g('target_gross')}</b> : <span className="dim">لم يُحسب بعد</span>}</span>
        <span><span className="dim">فرق الشراء/البيع</span> <b className="num">{g('delta_buy') ?? '—'} / {g('delta_sell') ?? '—'}</b></span>
        <span><span className="dim">حالة المحفظة</span> <span className={`pill ${portfolio.cls}`} style={{ padding: '1px 8px' }}>{portfolio.text}</span></span>
      </div>
      {g('risk_dial') != null ? (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', fontSize: 12.5 }}>
          <span><span className="dim">عيار المخاطرة</span> <b className="num">{g('risk_dial')}</b></span>
          <span><span className="dim">الهدف الكامل</span> <b className="num">{g('base_target') ?? '—'}</b></span>
          <span><span className="dim">الزيادة المسموحة</span> <b className="num">{g('allowed_increase') ?? '—'}</b></span>
          <span><span className="dim">المتبقي من السقف</span> <b className="num">{g('remaining_RB') ?? '—'}</b></span>
          <span><span className="dim">ميزانية الإضافة المتبقية</span> <b className="num">{g('remaining_add_budget') ?? '—'}</b></span>
        </div>
      ) : null}
      {t.reason ? <div className="ss dim">السبب: {TARGET_REASON_AR[String(t.reason)] ?? arabicVisible(t.reason, String(t.reason))}</div> : null}
      <div className="ss dim" style={{ fontSize: 11 }}>معرّف القرار {String(t.decision_id ?? '—')} · معرّف طلب البوابة {String(t.gate_request_id ?? '—')}</div>
    </div>
  )
}

function TargetsBoard() {
  const targets = useStore((s) => s.symbolStreams['perpetual.target.state'] ?? {})
  const syms = Object.keys(targets).sort()
  return (
    <div className="scard" style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '11px 14px', borderBottom: '1px solid var(--glassb)' }}>
        <div className="st" style={{ fontWeight: 700 }}>حالة المركز والهدف الدائم لكل رمز (581)</div>
        <div className="ss dim">الفرق بين المركز الحالي والهدف المحسوب — من `perpetual.target.state` مباشرة، بمعرّفَي القرار والبوابة كما وصلا من البوابة 467.</div>
      </div>
      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', padding: 10 }}>
        {syms.length === 0
          ? <div className="dim" style={{ fontSize: 13.5 }}>لم يصل هدف دائم من 581 بعد.</div>
          : syms.map((s) => <TargetCard key={s} symbol={s} />)}
      </div>
    </div>
  )
}

function Chain({ title, ids }: { title: string; ids: [number, string][] }) {
  const atoms = useStore((s) => s.atoms)
  return (
    <div className="scard">
      <div className="st">{title}</div>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6, marginTop: 8 }}>
        {ids.map(([id, label], i) => {
          const a = atoms[id]
          const color = a ? (HEALTH_COLOR[a.health?.state ?? ''] ?? 'grey') : 'grey'
          return (
            <span key={id} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {i > 0 ? <span className="dim">←</span> : null}
              <span className={`pill ${color}`} title={a ? ('حالة متاحة في التشخيص') : 'الذرة مو محمّلة بالنواة'}>
                {label}
              </span>
            </span>
          )
        })}
      </div>
      <div className="ss dim">أخضر = شغّالة · كهرماني = مستنّية مدخل (طبيعي قبل أول أمر) · رمادي = مو محمّلة</div>
    </div>
  )
}

export default function Execution() {
  const execution = useStore((s) => s.execution)
  const orders = useStore((s) => s.execOrders)
  const manage = useStore((s) => s.execManage)
  const outcomes = useStore((s) => s.execOutcomes)
  const [showChains, setShowChains] = useState(false)

  const c = execution?.counts ?? {}

  return (
    <div className="section" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <AccountsPair />
      <div className="ss dim">هالقسم يشتغل على حساب التنفيذ (ميتاتريدر). حساب التحليل فوق ما بينكتب عليه أمر.</div>
      {/* بند ١٨ (ورقة ٩٩): بطاقة موحّدة لكل بوّابة — السبب + المفتاح + العيار بمكانها.
          بطاقة 552 المفردة القديمة صارت وحدة من تسع بنفس مسار 901 حرفيًّا. */}
      <GatesBoard />

      {/* حزمة ج (ج٣، ختم 22): بطاقة لكل رمز — حالة المركز والهدف الدائم من 581 */}
      <TargetsBoard />

      <div className="cards">
        <div className="scard"><div className="st">أوامر انبنت</div><div className="sv num">{c.built ?? 0}</div></div>
        <div className="scard"><div className="st">ما بُنيت (551 توقّف)</div><div className="sv num">{c.order_skipped ?? 0}</div></div>
        <div className="scard"><div className="st">مرفوضة بالبوّابة (552)</div><div className="sv num">{c.rejected ?? 0}</div></div>
        <div className="scard"><div className="st">قرار نهائي (بينبعت للجسر)</div><div className={`sv num ${(c.decision_finalized ?? 0) > 0 ? 'red' : ''}`}>{c.decision_finalized ?? 0}</div></div>
        <div className="scard"><div className="st">أوامر إدارة</div><div className="sv num">{c.manage_commands ?? 0}</div></div>
        <div className="scard"><div className="st">انكتبت للجسر</div><div className="sv num">{c.manage_queued ?? 0}</div></div>
        <div className="scard"><div className="st">نتائج محقَّقة</div><div className="sv num">{c.outcomes ?? 0}</div></div>
      </div>

      {(() => {
        const rejectR = execution?.reject_reasons ?? {}
        const skipR = execution?.skip_reasons ?? {}
        const rows = [
          ...Object.entries(rejectR).map(([reason, n]) => ({ reason, n, label: REJECT[reason] ?? arabicVisible(reason, 'سبب غير مترجَم'), src: 'البوّابة 552' })),
          ...Object.entries(skipR).map(([reason, n]) => ({ reason, n, label: SKIP_REASON[reason] ?? arabicVisible(reason, 'سبب غير مترجَم'), src: 'باني الأمر 551' })),
        ].sort((a, b) => b.n - a.n)
        if (rows.length === 0) return null
        return (
          <div className="scard">
            <div className="st">ليش ما وصلت الأوامر؟ — تجميع كل الأسباب من بداية التشغيل</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4, marginTop: 8 }}>
              {rows.map((r) => (
                <div key={r.src + r.reason} style={{ display: 'flex', gap: 10, alignItems: 'baseline', fontSize: 13 }}>
                  <span className="num" style={{ fontWeight: 700, minWidth: 28, textAlign: 'end' }}>{r.n}</span>
                  <span>{r.label}</span>
                  <span className="dim" style={{ fontSize: 11, marginInlineStart: 'auto' }}>{r.src}</span>
                </div>
              ))}
            </div>
          </div>
        )
      })()}

      {/* بند 14 (دفتر 97): الرسم التقنيّ لا يُدفن الجواب البسيط — طبقة مطويّة تُفتح عند الطلب */}
      <div>
        <button className="btn" style={{ fontSize: 12.5 }} onClick={() => setShowChains(!showChains)}>
          {showChains ? '▴ خبّي التفصيل التقني' : '▾ التفصيل التقني — سلسلة الذرّات (للفحص عند الحاجة، مو لازم لفهم الحالة)'}
        </button>
        {showChains ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 10 }}>
            <Chain title="سلسلة فتح الصفقة (بترتيب مرور الأمر)" ids={OPEN_CHAIN} />
            <Chain title="سلسلة الإدارة الذكية (تعادل · تتبّع · جني جزئي)" ids={MANAGE_CHAIN} />
          </div>
        ) : null}
      </div>

      <div className="scard">
        <div className="st">آخر الأوامر (أحدث أولًا — وقت الوصول للوحة)</div>
        {orders.length === 0 ? (
          <div className="empty">ما مرق ولا أمر بعد — أوّل ما القرار يبعت طلب، بتشوفه هون لحظيًّا.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
            {orders.map((o, i) => {
              const [sideTxt, sideColor] = SIDE[o.side ?? ''] ?? [o.side ?? '—', 'var(--dim)']
              const [kindTxt, kindColor] = KIND[o.kind]
              return (
                <div key={`${o.ts}-${i}`} style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'baseline', borderBottom: '1px solid var(--glassb)', paddingBottom: 6 }}>
                  <span className="num dim" style={{ fontSize: 12 }}>{time(o.ts)}</span>
                  <span style={{ fontWeight: 700 }}>{o.symbol ?? '—'}</span>
                  <span style={{ color: sideColor, fontWeight: 700 }}>{sideTxt}</span>
                  {o.kind === 'rejected' ? (
                    <span style={{ color: 'var(--red)' }}>{REJECT[o.reason ?? ''] ?? arabicVisible(o.reason, 'سبب غير مترجَم')}</span>
                  ) : o.kind === 'skipped' ? (
                    <span style={{ color: 'var(--amber)' }}>{SKIP_REASON[o.reason ?? ''] ?? arabicVisible(o.reason, 'سبب غير مترجَم')}</span>
                  ) : (
                    <>
                      <span className="num">الحجم {num(o.volume)}</span>
                      <span className="num dim">السعر {num(o.reference_price, 6)}</span>
                      <span className="num" style={{ color: 'var(--red)' }}>وقف {num(o.stop_loss, 6)}</span>
                      <span className="num" style={{ color: 'var(--green)' }}>هدف {num(o.take_profit, 6)}</span>
                      {o.reward_risk != null ? <span className="num dim">ر:م {num(o.reward_risk, 1)}</span> : null}
                    </>
                  )}
                  <span style={{ color: kindColor, marginInlineStart: 'auto' }}>{kindTxt}</span>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="scard">
        <div className="st">الإدارة الذكية للصفقات المفتوحة (أحدث أولًا)</div>
        {manage.length === 0 ? (
          <div className="empty">ما في إدارة بعد — بتشتغل بس لمّا يكون في مركز مفتوح بالمنصّة.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
            {manage.map((m, i) => {
              const [stageTxt, stageColor] = MANAGE_STAGE[m.stage]
              return (
                <div key={`${m.ts}-${i}`} style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'baseline', borderBottom: '1px solid var(--glassb)', paddingBottom: 6 }}>
                  <span className="num dim" style={{ fontSize: 12 }}>{time(m.ts)}</span>
                  <span style={{ fontWeight: 700 }}>{m.symbol || '—'}</span>
                  <span>{MANAGE_ACTION[m.action ?? ''] ?? arabicVisible(m.action, 'إجراء غير مترجَم')}</span>
                  {m.reason ? <span className="dim">({MANAGE_REASON[m.reason] ?? arabicVisible(m.reason, 'سبب غير مترجَم')})</span> : null}
                  {m.stop_loss != null ? <span className="num">وقف جديد {num(m.stop_loss, 6)}</span> : null}
                  {m.volume != null ? <span className="num">حجم {num(m.volume)}</span> : null}
                  {m.ticket != null ? <span className="num dim">تذكرة {m.ticket}</span> : null}
                  <span style={{ color: stageColor, marginInlineStart: 'auto' }}>{stageTxt}</span>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="scard">
        <div className="st">النتائج المحقَّقة (من تأكيد التنفيذ 563)</div>
        {outcomes.length === 0 ? (
          <div className="empty">ما في نتيجة بعد — أوّل إغلاق (كامل أو جزئي) بيظهر هون بربحه أو خسارته.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
            {outcomes.map((o, i) => {
              const [sideTxt, sideColor] = SIDE[o.side ?? ''] ?? [o.side ?? '—', 'var(--dim)']
              const win = (o.profit ?? 0) >= 0
              return (
                <div key={`${o.ts}-${i}`} style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'baseline', borderBottom: '1px solid var(--glassb)', paddingBottom: 6 }}>
                  <span className="num dim" style={{ fontSize: 12 }}>{time(o.ts)}</span>
                  <span style={{ fontWeight: 700 }}>{o.symbol ?? '—'}</span>
                  <span style={{ color: sideColor }}>{sideTxt}</span>
                  <span className="dim">{OUTCOME_TYPE[o.event_type ?? ''] ?? arabicVisible(o.event_type, 'نتيجة غير مترجَمة')}</span>
                  <span className="num dim">دخول {num(o.entry_price, 6)} → خروج {num(o.exit_price, 6)}</span>
                  <span className="num" style={{ color: win ? 'var(--green)' : 'var(--red)', fontWeight: 700, marginInlineStart: 'auto' }}>
                    {win ? '+' : ''}{num(o.profit)}
                  </span>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="ss dim">
        كل شي بهالقسم من بثّ النواة الحيّ — الأصل الخام: أحداث «execution.*» و«trading.final_decision» (بتشوفها بقسم المراقبة).
        اللوحة ما بتخزّن: السجلّات دوّارة (آخر 40) وبتصفّر مع إعادة الفتح — التاريخ الكامل عند مصدره.
      </div>

      <SectionConfigTable from={550} to={600} title="معاملات ذرّات التنفيذ (550-599) — ضبط جماعي" />
    </div>
  )
}
