// البنية (٨٥٣ب) — بنية السوق لكل رمز من ذرة النشر (٢١٠: market.structure.updated):
// الاتجاه · الطور · آخر تحوّل (BOS/CHoCH) · القمّة/القاع الخارجي · الثقة. داتا حقيقية فقط.
import { useStore } from '../core/store'
import { arabicVisible } from '../core/arabic'
import { SectionAtomsHealth, SectionConfigTable } from '../components/SectionAtoms'

// ترجمات مؤكَّدة من ذرات البنية (٢٠٧ اتجاه · ٢٠٨ طور)
const TREND: Record<string, { t: string; c: string }> = {
  uptrend: { t: 'صاعد', c: 'green' }, downtrend: { t: 'هابط', c: 'red' },
  range: { t: 'عرضي', c: 'grey' }, transition: { t: 'انتقالي', c: 'amber' },
}
const PHASE: Record<string, string> = { neutral: 'محايد', early: 'مبكّر', established: 'راسخ', extended: 'ممتدّ' }
const SHIFT: Record<string, string> = { bos: 'كسر هيكل', choch: 'تغيّر طابع' }
const DIR: Record<string, string> = { up: 'صاعد', down: 'هابط', bullish: 'صاعد', bearish: 'هابط' }
const SWING: Record<string, string> = { high: 'قمّة', low: 'قاع', higher_high: 'قمّة أعلى', higher_low: 'قاع أعلى', lower_high: 'قمّة أدنى', lower_low: 'قاع أدنى', none: 'لا يوجد' }
const STATUS: Record<string, string> = { ok: 'جاهزة', insufficient_data: 'بيانات غير كافية', stale: 'متقادمة', error: 'خطأ' }
const num = (n?: number | null) => (n == null ? '—' : n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 3 }))
const pct = (n?: number) => (n == null ? '—' : `${Math.round(n * 100)}%`)

export default function Structure() {
  const structure = useStore((s) => s.structure)
  const syms = Object.keys(structure).sort()

  return (
    <div className="section chartsec">
      {/* بند ١٠ (ورقة ٩٩): لا صفحة فاضية وذرّات القسم حيّة — حالتها الفعلية بدل الفراغ */}
      {syms.length === 0 ? (
        <>
          <div className="empty">بانتظار أوّل دورة بنية من النواة… (تكتمل مع إغلاق الشموع)</div>
          <SectionAtomsHealth from={200} to={250} title="ذرّات قسم البنية — حالتها الحيّة الآن"
            note="ما وصلت دورة بنية بعد — هاي حالة ذرّات القسم نفسها من النواة، مو تخمين." />
        </>
      ) : (
      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))' }}>
        {syms.map((sym) => {
          const d = structure[sym]
          const st = d.structure ?? ({} as StructureShape)
          const insufficient = d.status !== 'ok'
          const tr = TREND[st.trend] ?? { t: arabicVisible(st.trend, 'اتجاه غير معروف'), c: 'grey' }
          const shift = st.last_shift ?? { type: null, direction: null }
          const shiftT = shift.type ? (SHIFT[shift.type] ?? arabicVisible(shift.type, 'تحوّل غير مترجَم')) : null
          const shiftD = shift.direction ? (DIR[shift.direction] ?? arabicVisible(shift.direction, 'اتجاه غير معروف')) : ''
          const swing = st.swing ? (SWING[st.swing] ?? arabicVisible(st.swing, 'تأرجح غير معروف')) : '—'
          return (
            <div className="scard" key={sym} style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontWeight: 700, fontSize: 14 }}>{sym}</span>
                <span className={`pill ${tr.c}`} style={{ marginInlineStart: 'auto', fontSize: 12 }}>{tr.t}</span>
              </div>

              {insufficient ? (
                <div className="dim" style={{ fontSize: 12 }}>بيانات غير كافية بعد — لم تتكوّن بنية صالحة.</div>
              ) : (
                <>
                  <div style={{ display: 'flex', gap: 10, fontSize: 12, flexWrap: 'wrap' }}>
                    <span>الطور <b>{PHASE[st.phase] ?? arabicVisible(st.phase, 'طور غير مترجَم')}</b></span>
                    <span>الاتجاه المعلن <b>{arabicVisible(d.signal, 'غير معروف')}</b></span>
                    <span>الثقة <b className="num">{pct(d.confidence)}</b></span>
                    <span className="dim">الجودة {d.quality === 'good' ? 'جيّدة' : 'ضعيفة'}</span>
                  </div>

                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {shiftT ? (
                      <span className="pill amber" style={{ fontSize: 11 }}>آخر تحوّل: {shiftT} {shiftD}</span>
                    ) : (
                      <span className="pill grey" style={{ fontSize: 11 }}>لا تحوّل بعد</span>
                    )}
                  </div>

                  <div style={{ display: 'flex', gap: 10, fontSize: 12, flexWrap: 'wrap' }}>
                    <span>التأرجح <b>{swing}</b></span>
                    <span>سعر التأرجح <b className="num">{num(st.swing_price)}</b></span>
                    <span>الداخلي <b>{st.internal ? arabicVisible(st.internal, 'غير معروف') : '—'}</b></span>
                  </div>
                  <div style={{ display: 'flex', gap: 10, fontSize: 12, flexWrap: 'wrap' }}>
                    <span>الجاهزية <b className="num">{num(d.current_depth)} / {num(d.required_depth)}</b></span>
                    <span className={d.complete ? 'green' : 'amber'}>{d.complete ? 'مكتملة' : 'غير مكتملة'}</span>
                    <span>قمّة خارجية <b className="num">{num(st.external_high)}</b></span>
                    <span>قاع خارجي <b className="num">{num(st.external_low)}</b></span>
                    <span>النتيجة <b className="num">{num(d.score)}</b></span>
                    <span className="dim">الحالة {STATUS[d.status] ?? arabicVisible(d.status, 'غير معروفة')}</span>
                    {d.metadata?.timeframe ? <span className="dim">الفريم {d.metadata.timeframe}</span> : null}
                  </div>
                </>
              )}
            </div>
          )
        })}
      </div>
      )}
      <SectionAtomsHealth from={200} to={250} title="ذرّات قسم البنية — حالتها الحيّة الآن"
        note="حالة الذرّات المكوّنة للبنية، منفصلة عن بطاقة نتيجة السوق." />
      {/* بند ٩ (ورقة ٩٩): معاملات القسم الحقيقية بجدول واحد — بنمط صفحة التحليل (150) */}
      <SectionConfigTable from={200} to={250} title="معاملات ذرّات البنية (200-249) — ضبط جماعي" />
    </div>
  )
}

type StructureShape = {
  trend: string; phase: string; swing: string; swing_price: number | null
  external_high: number | null; external_low: number | null; internal: string
  last_shift: { type: string | null; direction: string | null }
}
