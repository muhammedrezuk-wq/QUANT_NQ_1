// الاحتمالات (٨٦٥) — نماذج الاحتمال لكل رمز من مدير الاحتمالات (٣٥٠: probability.cycle.collected):
// اتجاه·انعكاس·اختراق·ارتداد·زخم·مدى (احتمال %+اتجاه) · هيرست (قيمة+نطاق) · الدمج · الثقة.
// ملاحظة صادقة: منهجية النماذج «سقالة قابلة لمراجعتك» (محادثة الذرات) — العرض حقيقي لما تُنتجه.
import { useStore } from '../core/store'
import { SectionAtomsHealth, SectionConfigTable } from '../components/SectionAtoms'

const MODELS: Array<[string, string]> = [
  ['trend_model', 'الاتجاه'], ['reversal_model', 'الانعكاس'], ['breakout_model', 'الاختراق'],
  ['pullback_model', 'الارتداد'], ['momentum_model', 'الزخم'], ['range_model', 'المدى'],
]
const DIR: Record<string, { a: string; c: string }> = { up: { a: '▲', c: 'green' }, down: { a: '▼', c: 'red' }, bullish: { a: '▲', c: 'green' }, bearish: { a: '▼', c: 'red' } }
const pct = (n?: number) => (n == null ? '—' : `${Math.round(n * 100)}%`)
const fx = (n?: number) => (n == null ? '—' : n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 3 }))

export default function Probability() {
  const probability = useStore((s) => s.probability)
  const syms = Object.keys(probability).sort()

  return (
    <div className="section chartsec">
      {/* بند ١٠ (ورقة ٩٩): لا صفحة فاضية وذرّات القسم حيّة — حالتها الفعلية بدل الفراغ */}
      {syms.length === 0 ? (
        <>
          <div className="empty">بانتظار أوّل دورة احتمالات من النواة… (تكتمل مع إغلاق الشموع)</div>
          <SectionAtomsHealth from={350} to={400} title="ذرّات قسم الاحتمالات — حالتها الحيّة الآن"
            note="ما وصلت دورة احتمالات بعد — هاي حالة ذرّات القسم نفسها من النواة (النماذج 350-359 + التعلّم 360-368)." />
        </>
      ) : (
      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))' }}>
        {syms.map((sym) => {
          const card = probability[sym] as typeof probability[string] & Record<string, unknown>
          const res = card?.results ?? {}
          const hurst = res['hurst']?.metadata
          const conf = res['confidence_aggregator']?.metadata?.probability
          const merged = res['models_merged']?.metadata
          return (
            <div className="scard" key={sym} style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontWeight: 700, fontSize: 14 }}>{sym}</span>
                {conf != null ? <span className="pill green" style={{ marginInlineStart: 'auto', fontSize: 11 }}>الثقة {pct(conf)}</span> : null}
              </div>
              <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', fontSize: 12 }}>
                <span>الاتجاه <b className="num">{fx(card.direction)}</b></span>
                <span>القوة <b className="num">{fx(card.strength)}</b></span>
                <span>الجاهزية <b className="num">{fx(card.current_depth)} / {fx(card.required_depth)}</b></span>
                <span className={card.ready === true ? 'green' : 'amber'}>{card.ready === true ? 'جاهز' : 'غير جاهز'}</span>
                {typeof card.timeframe === 'string' ? <span className="dim">الفريم {card.timeframe}</span> : null}
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2px 10px', fontSize: 12 }}>
                {MODELS.map(([id, ar]) => {
                  const u = res[id]
                  const m = u?.metadata
                  if (!m) return null
                  const d = DIR[m.direction ?? ''] ?? { a: '', c: 'grey' }
                  // بند 11 (دفتر 97): الجودة والثقة لكل نموذج على حدة — 50% «محايد
                  // بثقة منخفضة» شيء، و50% بدليل حقيقي شيء تاني. لا كتلة إجماليّة.
                  const quality = (u?.quality ?? (m as { quality?: string }).quality) as string | undefined
                  const conf = u?.confidence
                  const low = quality === 'low'
                  return (
                    <div key={id} style={{ display: 'flex', justifyContent: 'space-between', gap: 6, borderBottom: '1px solid var(--glassb)', padding: '2px 0' }}>
                      <span className="dim">{ar}{low ? <span style={{ color: 'var(--amber)' }}> (محايد — لا إشارة)</span> : null}</span>
                      <span style={{ display: 'flex', gap: 6, alignItems: 'baseline' }}>
                        {conf != null ? <span className={low ? 'amber' : 'dim'} style={{ fontSize: 10.5 }}>ثقة {pct(conf)}</span> : null}
                        <b className={`num ${low ? 'grey' : d.c}`}>{d.a} {pct(m.probability)}</b>
                      </span>
                    </div>
                  )
                })}
              </div>

              <div style={{ display: 'flex', gap: 10, fontSize: 12, marginTop: 1, flexWrap: 'wrap' }}>
                {merged?.direction ? <span>الاتجاه المدمج <b className={DIR[merged.direction]?.c ?? 'grey'}>{DIR[merged.direction]?.a ?? ''} {pct(merged.probability)}</b></span> : null}
                {hurst ? <span>هيرست <b className="num">{fx(hurst.value)}</b> <span className="dim">{hurst.band ?? ''}</span></span> : null}
                {res['confidence_aggregator']?.confidence != null ? <span>ثقة الدمج <b className="num">{pct(res['confidence_aggregator'].confidence)}</b></span> : null}
              </div>
            </div>
          )
        })}
      </div>
      )}
      {/* بند ٩ (ورقة ٩٩): معاملات القسم الحقيقية بجدول واحد — بنمط صفحة التحليل (150).
          هنا تظهر معاملات 351 الأربعة التي كانت الدليل القاطع بدفتر ٩٧. */}
      <SectionConfigTable from={350} to={400} title="معاملات ذرّات الاحتمالات والتعلّم (350-399) — ضبط جماعي" />
    </div>
  )
}
