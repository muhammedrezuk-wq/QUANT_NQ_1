// شريط الاستلام من فوق — عين المالك الوحيدة على القطع ووقت الوسيط.
// لا حساب: عمر آخر حدث وصل، و`timestamp_source` كما نشرته ٦١٨.
import { useEffect, useState } from 'react'
import { useStore } from '../core/store'

type Tone = 'green' | 'amber' | 'red' | 'dim'

function ageOf(flows: Record<string, number>, name: string, now: number): number {
  const t = flows[name]
  return t == null ? Infinity : (now - t) / 1000
}

function liveTone(conn: string, age: number): { tone: Tone; text: string } {
  if (conn !== 'live') return { tone: 'dim', text: 'لا نعرف' }
  if (age === Infinity) return { tone: 'red', text: 'صامت' }
  if (age < 5) return { tone: 'green', text: 'حيّ' }
  if (age < 30) return { tone: 'amber', text: `${Math.round(age)}ث` }
  return { tone: 'red', text: 'مقطوع' }
}

export default function FeedLeds() {
  const conn = useStore((s) => s.conn)
  const flows = useStore((s) => s.flows)
  const mt = useStore((s) => s.streams['feed.mt5.tick']) as
    | { timestamp_source?: string; clock_valid?: boolean; clock_domain?: string }
    | undefined
  const div = useStore((s) => s.streams['execution.reference_divergence.state']) as
    | { classification?: string; status?: string }
    | undefined
  const [now, setNow] = useState(() => performance.now())

  useEffect(() => {
    const id = window.setInterval(() => setNow(performance.now()), 1000)
    return () => window.clearInterval(id)
  }, [])

  const ct = liveTone(conn, ageOf(flows, 'feed.ctrader.tick', now))
  const mt5 = liveTone(conn, ageOf(flows, 'feed.mt5.tick', now))

  let timeTone: Tone = 'dim'
  let timeText = '—'
  let timeTitle = 'ما وصلت تكة تنفيذ بعد'
  if (conn !== 'live') {
    timeText = 'لا نعرف'
    timeTitle = 'النواة مقطوعة — لا قراءة لزمن التكة'
  } else if (mt5.tone === 'red') {
    timeTone = 'red'
    timeText = 'مقطوع'
    timeTitle = 'ما في استلام MT5 — الزمن التشغيلي واقف'
  } else if (mt?.timestamp_source === 'received' || mt?.clock_valid === false) {
    timeTone = 'amber'
    timeText = 'استلام'
    timeTitle = 'طابع الوسيط مرفوض عند الباب — الزمن الداخلي من وصول الجهاز (UTC)'
  } else if (mt?.timestamp_source === 'broker' || mt?.clock_valid === true) {
    timeTone = 'green'
    timeText = 'UTC'
    timeTitle = 'طابع الوسيط صالح — الزمن الداخلي UTC من التكة'
  } else if (mt) {
    timeTone = 'green'
    timeText = 'وصل'
    timeTitle = 'تكة وصلت بلا حقل مصدر زمن'
  }

  let divTone: Tone = 'dim'
  let divText = '—'
  let divTitle = '٥٨٢ ما نشر بعد'
  const cls = div?.classification
  if (cls === 'STALE' || cls === 'CLOCK_INVALID' || cls === 'INSUFFICIENT_DATA') {
    divTone = cls === 'INSUFFICIENT_DATA' ? 'amber' : 'red'
    divText = cls === 'STALE' ? 'تقادم' : cls === 'CLOCK_INVALID' ? 'ساعة' : 'ناقص'
    divTitle = `٥٨٢ ${cls}`
  } else if (cls === 'SUSPICIOUS_DIVERGENCE') {
    divTone = 'amber'
    divText = 'ريبة'
    divTitle = '٥٨٢ مراقبة — نقلة كبيرة (لا إيقاف)'
  } else if (cls === 'EXPECTED_DIVERGENCE' || cls === 'LEVEL_OFFSET_ONLY' || cls === 'NORMAL') {
    divTone = 'green'
    divText = cls === 'NORMAL' ? 'مطابق' : cls === 'LEVEL_OFFSET_ONLY' ? 'إزاحة' : 'متوقع'
    divTitle = `٥٨٢ ${cls}`
  }

  const chip = (label: string, tone: Tone, text: string, title: string) => (
    <span className={`feedled ${tone}`} title={title}>
      <i className="feeddot" />
      <b>{label}</b>
      <em>{text}</em>
    </span>
  )

  return (
    <div className="feedleds" aria-label="استلام التغذية والزمن">
      {chip('سي‑تريدر', ct.tone, ct.text, 'آخر تكة تحليل — صامت/مقطوع يعني ما في SendingTime داخل')}
      {chip('MT5', mt5.tone, mt5.text, 'آخر تكة تنفيذ من الجسر')}
      {chip('وقت', timeTone, timeText, timeTitle)}
      {chip('انحراف', divTone, divText, divTitle)}
    </div>
  )
}
