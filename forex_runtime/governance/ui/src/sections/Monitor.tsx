// المراقبة (860) — أحداث النظام المهمّة لحظة بلحظة، مترجَمة عربي من القاموس
// المشترك (core/streams): اسم محدَّد لكل حدث + تفصيل حمولته (رمز · جهة · سبب)،
// والتكرار المتتالي المتطابق يندمج بعدّاد ×N بدل رشق أسطر متطابقة (بند 17 بدفتر 97).
import { useStore } from '../core/store'
import { eventAr } from '../core/streams'

export default function Monitor() {
  const events = useStore((s) => s.events)
  return (
    <div className="section">
      <div className="loglist">
        {events.length === 0 ? (
          <div className="empty">بترقّب الأحداث المهمّة… (وقّف/شغّل ذرة من الشبكة لتشوفها هون فورًا)</div>
        ) : null}
        {events.map((e) => {
          // أي اسم حدث غائب عن القاموس المشترك يظهر عربيًّا صريحًا لا خامًا
          const rowOf = (name: string): [string, string] => {
            const known = eventAr(name)
            if (known) return known
            return ['حدث', 'grey']
          }
          const [ar, c] = rowOf(e.name)
          return (
            <div className="logrow" key={e.id}>
              <span className="lt num">{new Date(e.ts).toLocaleTimeString('ar-EG-u-nu-latn', { hour12: false })}</span>
              <span className={c}>● {ar}</span>
              {e.detail ? <span className="dim">{e.detail}</span> : null}
              {e.n > 1 ? <span className="num amber" style={{ marginInlineStart: 'auto' }}>تكرّر ×{e.n}</span> : null}
            </div>
          )
        })}
      </div>
    </div>
  )
}
