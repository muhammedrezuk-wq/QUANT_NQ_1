// طبقة الترجمة (١٤ §٩): حقيقة خام → معنى عربي · دوال نقية قابلة للاختبار.
// المالك لا يقرأ إنجليزي/خام — فلا حالة نيّئة تظهر على الشاشة.

const STATE_AR: Record<string, string> = {
  running: 'شغّالة', stopped: 'واقفة', failed: 'فيها خلل',
  starting: 'عم تشتغل', stopping: 'عم توقف', unloaded: 'مسحوبة',
}
const HEALTH_AR: Record<string, string> = {
  healthy: 'سليمة', degraded: 'متعثّرة', unhealthy: 'فيها خلل',
}
// Owner 2026-08-22: رسائل DEGRADED التي تعني «لسّع وقتها ما جه» (تستنّى مدخلها
// أو تجمع نافذتها) ليست متعثّرة — تُعرض حالة طبيعية «تستنّى». فقط الـDEGRADED
// الناتج عن فشل حقيقي يظهر «متعثّرة».
const WAITING_MARKERS = [
  'NO_TICKS', 'NO_INPUT', 'NO_CANDLES', 'NO_CYCLES', 'AWAITING', 'WAITING',
  'NO_EQUITY', 'NOT_READY', 'NO_RESULT_YET', 'RECEIVING_OK_NO_RESULT',
  'NO_EVIDENCE', 'NO_DATA', 'NO_RESULT', 'NO_SIGNAL', 'INSUFFICIENT',
  'NO_POSITION', 'NO_ACCOUNT', 'NO_SPECS', 'NO_UPDATE',
]

export type Color = 'green' | 'amber' | 'red' | 'grey'

export interface HealthLike { state: string; message?: string }
export interface AtomLike { state: string; health?: HealthLike | null }

export function isWaitingMessage(msg: string | undefined): boolean {
  if (!msg) return false
  const upper = msg.toUpperCase()
  return WAITING_MARKERS.some((m) => upper.includes(m))
}

/** (نصّ الحالة بالعربي · اللون) — كل نتيجة مشتقّة من الحقيقة الخام. */
export function labelOf(a: AtomLike): [string, Color] {
  const h = a.health?.state
  if (a.state === 'running') {
    if (h === 'unhealthy') return [HEALTH_AR[h] ?? 'فيها خلل', 'red']
    if (h === 'degraded') {
      // ميّز «تستنّى مدخلها/وقتها» عن «متعثّرة فعلًا»
      if (isWaitingMessage(a.health?.message)) return ['تستنّى', 'grey']
      return [HEALTH_AR[h] ?? 'متعثّرة', 'amber']
    }
    return ['سليمة', 'green']
  }
  if (a.state === 'failed') return ['فيها خلل', 'red']
  if (a.state === 'stopped') return ['واقفة', 'grey']
  return [STATE_AR[a.state] ?? a.state, 'amber']
}

// نصّ رسالة الصحّة نفسها — القاموس في rabic.ts لأنّه طويل ويكبر مع الذرّات.
export { arabicHealth, arabicState } from './arabic'

/**
 * سعر بدقّة تتبع حجمه، لا برقم عشريّ ثابت.
 *
 * ٢٠٢٦-٠٩-٠١ (حكم المالك: «العملات الصفريّة الكثيرة العشريّة ما بيطلع سعرها
 * الحقيقي… لسعر الدخول ولوقف الخسارة والهدف»). العطل مقيس في العرض لا في
 * الذرّات: الذرّات تنشر السعر كاملًا بلا تدوير، لكنّ اللوحة كانت تقصّه —
 * خانات عشريّة ثابتة (٢ افتراضيًّا، و٦ للمستويات). فعملةٌ سعرها
 * 0.00001234 تُعرض 0.000012 فيضيع رقمان، و0.0000001234 تُعرض 0.
 * ورقم دخولٍ أو وقفِ خسارةٍ يُعرض صفرًا ليس رقمًا ناقصًا — هو رقم كاذب.
 *
 * القاعدة: أرقام **معنويّة** لا خانات ثابتة — تُحفظ أربع خانات بعد أوّل رقم
 * غير صفر مهما بَعُد. والصيغة ar-EG-u-nu-latn كبقيّة اللوحة (كانت en-US في
 * لوحة الكريبتو وحدها، خلافًا لقاعدة «لا إنكليزي باللوحة»).
 */
export function priceText(value: unknown): string {
  const n = Number(value)
  if (value == null || !Number.isFinite(n)) return '—'
  const a = Math.abs(n)
  let digits: number
  if (a === 0) digits = 2
  else if (a >= 1000) digits = 2
  else if (a >= 1) digits = 4
  else digits = Math.min(12, -Math.floor(Math.log10(a)) + 4)
  return n.toLocaleString('ar-EG-u-nu-latn', {
    maximumFractionDigits: digits, minimumFractionDigits: 0,
  })
}
