// السوق (٨٥٢) — سعران لكل رمز، لا يختلطان أبدًا:
//   • المرجع  (سي‑تريدر) من `market.tick` — هو وحده ما يقود التحليل.
//   • التنفيذ (الوسيط/ميتاتريدر) من `market.broker_tick` — عرض فقط، بسبريده.
// ٢٠٢٦-٠٨-٣١ (ختم NQ) بحكم المالك: «مصدرنا الحقيقي سي‑تريدر، وميتاتريدر
// مصدره غير معروف وقد يكون متلاعبًا به — لا يغذّي محلّلًا ولا يحلّ محلّه»،
// و«غزارة تِكّاته ما تروح هدر: لازم توصل الشارت لأشوف سعر التنفيذ والسبريد».
import { useEffect, useState } from 'react'
import { useStore } from '../core/store'
import Connection from './Connection'

const fmt = (n: number) => n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 2, minimumFractionDigits: 2 })
const fmt5 = (n: number) => n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 5 })

function ageText(ts: number, now: number): { text: string; color: string } {
  // طوابع الناقل بالثواني و Date.now() بالميلي ثانية — بلا هذا التطبيع كان
  // العمر يُحسب من فجر 1970 فيظهر «ساكت منذ ~29 مليون دقيقة» والسعر حيّ.
  const tsMs = ts > 1e11 ? ts : ts * 1000
  const s = Math.max(0, (now - tsMs) / 1000)
  if (s < 3) return { text: 'حيّ الآن', color: 'var(--green)' }
  if (s < 15) return { text: `آخر تكّة قبل ${Math.round(s)} ث`, color: 'var(--green)' }
  if (s < 60) return { text: `آخر تكّة قبل ${Math.round(s)} ث`, color: 'var(--amber)' }
  return { text: `ساكت منذ ${Math.round(s / 60)} د`, color: 'var(--red)' }
}

export default function Market() {
  const market = useStore((s) => s.market)
  const broker = useStore((s) => s.brokerMarket)
  const symbols = Array.from(new Set([...Object.keys(market), ...Object.keys(broker)])).sort((a, b) => a.localeCompare(b))
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(t)
  }, [])
  const refCount = Object.keys(market).length
  return (
    <div className="section">
      <div className="ss dim" style={{ marginBottom: 10 }}>
        <b>المرجع</b> (سي‑تريدر) يقود التحليل. <b>التنفيذ</b> (ميتاتريدر) للعرض وإدارة المركز — ما بيدخل حكم التحليل.
        {symbols.length === 0
          ? ' بانتظار أوّل تكة.'
          : ` ${symbols.length} رمز.`}
        {symbols.length > 0 && refCount === 0 && (
          <span style={{ color: 'var(--red)' }}> ⚠️ لا سعر مرجع واصل — التحليل صامت، وما تراه أدناه سعر الوسيط وحده.</span>
        )}
      </div>
      {symbols.length === 0 ? (
        <div className="empty">جارِ استقبال أسعار السوق من النواة… الحسابان تحت ظاهرين حتى لو السعر لسا ما وصل.</div>
      ) : (
        <div className="cards">
          {symbols.map((sym) => {
            const r = market[sym]
            const b = broker[sym]
            const shown = r ?? b
            const age = shown ? ageText(shown.ts, now) : { text: 'لم تصل', color: 'var(--dim)' }
            return (
              <div className="scard" key={sym}>
                <div className="st">{sym}</div>
                <div className="sv num">{shown ? fmt((shown.bid + shown.ask) / 2) : 'مجهول'}</div>
                <div className="ss num" style={{ color: r ? 'var(--green)' : 'var(--red)' }}>
                  المرجع: {r ? `شراء ${fmt(r.ask)} · بيع ${fmt(r.bid)}` : 'لم يصل'}
                </div>
                <div className="ss num" style={{ color: b ? 'var(--amber)' : 'var(--dim)' }}>
                  التنفيذ ({b?.provider ?? 'الوسيط'}): {b ? `شراء ${fmt(b.ask)} · بيع ${fmt(b.bid)} · سبريد ${fmt5(b.spread)}` : 'لم يصل'}
                </div>
                <div className="ss" style={{ color: age.color, marginTop: 4 }}>{age.text}</div>
              </div>
            )
          })}
        </div>
      )}
      <div style={{ marginTop: 14 }}>
        <Connection embedded />
      </div>
    </div>
  )
}
