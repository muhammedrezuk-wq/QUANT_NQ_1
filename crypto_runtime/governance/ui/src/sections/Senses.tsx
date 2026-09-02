// ═══ قسم أسمر — سجل الحواس الثماني عشرة (الخريطة الهندسية الرسمية) ═══
import { useEffect, useState } from 'react'

interface AtomRow { id: number; state?: string; health?: { state?: string; message?: string } | null }

const card: React.CSSProperties = { border: '1px solid var(--glassb)', background: 'var(--glass)', borderRadius: 12, padding: 14 }

// الرقم · الذرّة · الاسم · الطبقة (من الخريطة الهندسية — الترتيب رسمي)
const SENSES: [number, string, string, string][] = [
  [1, 2151, 'VWAP الجلسة ±1σ', 'رصد'],
  [2, 2152, 'قمة وقاع الجلسة', 'رصد'],
  [3, 2153, 'مستويات الأمس PDH/PDL/PDC', 'رصد'],
  [4, 2155, 'بروفايل الحجم POC/VAH/VAL', 'رصد'],
  [5, 2154, 'الأرقام المستديرة', 'رصد معزّز'],
  [6, 2156, 'وسيط المدى — البوابة الاقتصادية', 'رصد'],
  [7, 2157, 'الحجم مقابل MA20', 'رصد'],
  [8, 2158, 'علم الشذوذ — تجميد', 'حماية'],
  [9, 2170, 'العقود المفتوحة OI — قاضٍ', 'حكم وفيتو'],
  [10, 2171, 'مقياس الوقود', 'شرط وجوبي'],
  [11, 2172, 'معدل التمويل', 'رصد'],
  [12, 2173, 'العلاوة', 'رصد'],
  [13, 2174, 'حرارة بايننس', 'سياق'],
  [14, 2265, 'الجدران — دفتر 100 مستوى', 'شاهد'],
  [15, 2266, 'تدفق المنفّذين — القاضي الأول', 'حكم وفيتو'],
  [17, 2161, 'عدسة الدقيقة', 'تنفيذ عرض'],
  [18, 2159, 'سياق الأطر العليا — رتبة ألف/باء', 'ضابط رتبة'],
]

const MICRO: [number, string][] = [
  [2261, 'الميكرو سعر'], [2262, 'تدفق الدفتر OFI'], [2263, 'دلتا الحجم CVD'],
  [2264, 'أثر السعر'], [2267, 'التصفيات'],
]

const GATES: [string, string][] = [
  ['خبر < ٣٠د', '2108 · 2615 · 2616'],
  ['نافذة مفكرة ±١٠د', '2109'],
  ['شاذ: مدى 3 شموع >3× وسيط', '2158'],
  ['افتتاح لندن/نيويورك ±٥د', '2003 · 2111 · 2860'],
]

export default function Senses() {
  const [atoms, setAtoms] = useState<Record<number, AtomRow>>({})
  useEffect(() => {
    const load = () => fetch('/gov/atoms', { cache: 'no-store' }).then(r => r.json())
      .then((d: { atoms?: AtomRow[] }) => {
        const m: Record<number, AtomRow> = {}
        for (const a of d.atoms || []) m[a.id] = a
        setAtoms(m)
      }).catch(() => {})
    load()
    const h = window.setInterval(load, 20000)
    return () => window.clearInterval(h)
  }, [])

  // أمر المالك ٢٠٢٦-٠٨-٢٩: «فيها محذوف ظاهر».
  // عشر ذرّات من هذا السجلّ `startup_mode: manual` — والمحمّل يحذفها من جدول
  // التشغيل عند الإقلاع فلا تُسجَّل أصلًا. كانت بطاقاتها تُرسم فارغة («—»)
  // فتبدو ذرّةً معطوبة، وهي ليست معطوبة ولا مشغَّلة: هي **مستبعَدة**.
  // فلا تُرسم بطاقة لغير محمَّل — ويُعلَن عددها وأسماؤها بسطر صريح تحت،
  // كي لا يكون الإخفاء كذبًا بالصمت.
  const loaded = (id: number) => atoms[id] != null
  const dot = (id: number) => {
    const st = atoms[id]?.health?.state
    return st === 'healthy' ? 'var(--green)' : st === 'degraded' ? 'var(--amber)' : st ? 'var(--red)' : 'var(--dim)'
  }
  const msg = (id: number) => (atoms[id]?.health?.message || '').slice(0, 64)

  return (
    <div className="section" style={{ display: 'grid', gap: 14, alignContent: 'start' }}>
      <div style={{ ...card, display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <strong style={{ color: 'var(--accent)' }}>الحواس — ماذا يحدث؟</strong>
        <span style={{ color: 'var(--dim)', fontSize: 12 }}>كل حاسة بوقتها الخاص · فيتو تكي حصري للمنفّذين والعقود المفتوحة</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(290px, 1fr))', gap: 10 }}>
        {SENSES.filter(([, id]) => loaded(id)).map(([no, id, name, layer]) => (
          <div key={id} style={{ border: '1px solid var(--glassb)', borderRadius: 10, padding: 10, display: 'grid', gap: 3 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span className="num" style={{ color: 'var(--dim)', fontSize: 11, width: 20 }}>{String(no).padStart(2, '0')}</span>
              <span style={{ width: 8, height: 8, borderRadius: 8, background: dot(id), display: 'inline-block' }} />
              <b style={{ fontSize: 13 }}>{name}</b>
              <span style={{ flexGrow: 1 }} />
              <span style={{ color: 'var(--dim)', fontSize: 11 }}>{layer}</span>
            </div>
            <div style={{ color: 'var(--dim)', fontSize: 11, paddingInlineStart: 28 }}>{id} · {msg(id) || '—'}</div>
          </div>
        ))}
        {MICRO.filter(([id]) => loaded(id)).map(([id, name]) => (
          <div key={id} style={{ border: '1px solid var(--glassb)', borderRadius: 10, padding: 10, display: 'grid', gap: 3, opacity: .95 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <span style={{ width: 8, height: 8, borderRadius: 8, background: dot(id), display: 'inline-block' }} />
              <b style={{ fontSize: 13 }}>{name}</b>
              <span style={{ flexGrow: 1 }} />
              <span style={{ color: 'var(--dim)', fontSize: 11 }}>ميكرو بنية الدفتر</span>
            </div>
            <div style={{ color: 'var(--dim)', fontSize: 11, paddingInlineStart: 16 }}>{id} · {msg(id) || '—'}</div>
          </div>
        ))}
        {SENSES.filter(([, id]) => !loaded(id)).concat(
          MICRO.filter(([id]) => !loaded(id)).map(([id, name]) => [0, id, name, ''] as [number, string, string, string]),
        ).length ? (
          <div style={{ border: '1px dashed var(--glassb)', borderRadius: 10, padding: 10, display: 'grid', gap: 4 }}>
            <b style={{ fontSize: 13, color: 'var(--dim)' }}>حواسّ مستبعَدة من التشغيل</b>
            <div style={{ color: 'var(--dim)', fontSize: 11 }}>
              موجودة بالشجرة وبملفّ أحمد، لكن إعدادها <code>startup_mode: manual</code> —
              فالمحمّل يستبعدها عند الإقلاع ولا تُسجَّل. ليست معطوبة، وليست شغّالة.
            </div>
            <div style={{ color: 'var(--dim)', fontSize: 11 }}>
              {SENSES.filter(([, id]) => !loaded(id)).map(([, id, name]) => `${name} (${id})`)
                .concat(MICRO.filter(([id]) => !loaded(id)).map(([id, name]) => `${name} (${id})`))
                .join(' · ')}
            </div>
          </div>
        ) : null}
      </div>

      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>بوابات الحماية الأربع — متى تتجمد اليد؟</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 8 }}>
          {GATES.map(([g, who]) => (
            <div key={g} style={{ border: '1px solid var(--glassb)', borderRadius: 8, padding: 8, fontSize: 12 }}>
              <b>{g}</b>
              <div style={{ color: 'var(--dim)', marginTop: 2 }}>{who}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
