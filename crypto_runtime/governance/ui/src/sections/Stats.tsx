// الإحصاء (٨٦٤) — عدّادات الناقل ودورة الحياة، مترجَمة ومجمَّعة (منفذ حقيقي: /gov/metrics).
import { useEffect, useState } from 'react'

interface Metrics { counters: Record<string, number> }

const AR: Record<string, string> = {
  'lifecycle.start.success': 'إقلاعات ناجحة',
  'lifecycle.start.failure': 'إقلاعات فاشلة',
  'lifecycle.stop.success': 'إيقافات',
  'lifecycle.stop.failure': 'إيقافات فاشلة',
  published: 'أحداث نُشرت',
  delivered: 'أحداث سُلّمت',
  no_subscribers: 'بلا مشترك',
  timeout: 'مهلات',
  error: 'أخطاء',
  replayed: 'أُعيدت',
}

export default function Stats() {
  const [m, setM] = useState<Metrics | null>(null)
  useEffect(() => {
    const load = () => fetch('/gov/metrics').then((r) => r.json()).then(setM).catch(() => {})
    load()
    const t = setInterval(load, 4000)
    return () => clearInterval(t)
  }, [])

  const agg: Record<string, number> = {}
  let other = 0
  if (m) {
    for (const [k, v] of Object.entries(m.counters)) {
      const ev = k.includes(':') ? k.slice(k.indexOf(':') + 1) : k
      if (AR[ev]) agg[ev] = (agg[ev] || 0) + v
      else other += v
    }
  }
  const rows = Object.entries(agg).sort((a, b) => b[1] - a[1])

  return (
    <div className="section">
      {/* بند 7 (دفتر 97): الصفحة تسمّت «عدّادات النواة» لأنّ هذا محتواها الحقيقي —
          الإحصاء التحليلي (متوسّط · انحراف · انحدار…) بقسم «الإحصاء التحليلي» */}
      <div className="ss dim" style={{ marginBottom: 10 }}>
        عدّادات ناقل النواة ودورة حياة الذرّات — كم حدث نُشر وسُلّم، وكم إقلاع نجح. هذه صحّة تشغيل، لا إحصاء سوق.
      </div>
      <div className="cards">
        {rows.map(([ev, n]) => (
          <div className="scard" key={ev}>
            <div className="st">{AR[ev]}</div>
            <div className="sv num">{n.toLocaleString('ar-EG-u-nu-latn')}</div>
          </div>
        ))}
        {other > 0 ? (
          <div className="scard"><div className="st">أحداث أخرى</div><div className="sv num">{other.toLocaleString('ar-EG-u-nu-latn')}</div></div>
        ) : null}
        {!m ? <div className="scard"><div className="st">جارِ التحميل…</div><div className="sv">—</div></div> : null}
      </div>
    </div>
  )
}
