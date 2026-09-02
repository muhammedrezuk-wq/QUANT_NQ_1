// السيولة (٨٥٣ج) — سيولة السوق لكل رمز من ذرة النشر (٢٦٠: market.liquidity.updated):
// كنس · فجوة FVG · تجمّع علوي/سفلي · مستوى شرائي/بيعي. قيم مؤكَّدة من ذرات ٢٥٢..٢٥٥.
import { useStore } from '../core/store'
import { arabicVisible } from '../core/arabic'
import { SectionAtomsHealth, SectionConfigTable } from '../components/SectionAtoms'

const HEAD: Record<string, { t: string; c: string }> = {
  sweep: { t: 'كنس سيولة', c: 'amber' },
  fvg_bullish: { t: 'فجوة صاعدة', c: 'green' },
  fvg_bearish: { t: 'فجوة هابطة', c: 'red' },
  none: { t: 'هادئ', c: 'grey' },
}
const POOL: Record<string, string> = { pool_high: 'تجمّع علوي', pool_low: 'تجمّع سفلي', none: '—' }
const SWEEP_DIR: Record<string, string> = { buy_side: 'جهة الشراء', sell_side: 'جهة البيع' }
const FVG: Record<string, string> = { fvg_bullish: 'صاعدة', fvg_bearish: 'هابطة', none: '—' }
const num = (n?: number | null) => (n == null ? '—' : n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 3 }))

export default function Liquidity() {
  const liquidity = useStore((s) => s.liquidity)
  const syms = Object.keys(liquidity).sort()

  return (
    <div className="section chartsec">
      {/* بند ١٠ (ورقة ٩٩): لا صفحة فاضية وذرّات القسم حيّة — حالتها الفعلية بدل الفراغ */}
      {syms.length === 0 ? (
        <>
          <div className="empty">بانتظار أوّل دورة سيولة من النواة… (تكتمل مع إغلاق الشموع)</div>
          <SectionAtomsHealth from={250} to={300} title="ذرّات قسم السيولة — حالتها الحيّة الآن"
            note="ما وصلت دورة سيولة بعد — هاي حالة ذرّات القسم نفسها من النواة، مو تخمين." />
        </>
      ) : (
      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))' }}>
        {syms.map((sym) => {
          const d = liquidity[sym]
          const lq = d.liquidity ?? ({} as LiqShape)
          const insufficient = d.status !== 'ok'
          const head = HEAD[d.signal] ?? { t: arabicVisible(d.signal, 'حالة غير معروفة'), c: 'grey' }
          const sweep = lq.sweep ?? { signal: 'none', direction: null, price: null }
          const fvg = lq.fvg ?? { signal: 'none', gap_top: null, gap_bottom: null }
          return (
            <div className="scard" key={sym} style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontWeight: 700, fontSize: 14 }}>{sym}</span>
                <span className={`pill ${head.c}`} style={{ marginInlineStart: 'auto', fontSize: 12 }}>{head.t}</span>
              </div>

              {insufficient ? (
                <div className="dim" style={{ fontSize: 12 }}>بيانات غير كافية بعد — لم تتكوّن سيولة صالحة.</div>
              ) : (
                <>
                  <div style={{ display: 'flex', gap: 10, fontSize: 12, flexWrap: 'wrap' }}>
                    <span>الثقة <b className="num">{num(d.confidence)}</b></span>
                    <span>الجاهزية <b className="num">{num(d.current_depth)} / {num(d.required_depth)}</b></span>
                    <span>الضغط <b className="num">{num(d.liquidity_pressure)}</b></span>
                    <span>جودة السيولة <b className="num">{num(d.liquidity_quality)}</b></span>
                    {d.metadata?.timeframe ? <span className="dim">الفريم {d.metadata.timeframe}</span> : null}
                  </div>
                  <div style={{ display: 'flex', gap: 10, fontSize: 12, flexWrap: 'wrap' }}>
                    <span>التجمّع <b>{POOL[lq.pool] ?? arabicVisible(lq.pool, 'غير مترجَم')}</b></span>
                    <span className="dim">الجودة {d.quality === 'good' ? 'جيّدة' : 'ضعيفة'}</span>
                  </div>

                  <div style={{ display: 'flex', gap: 10, fontSize: 12 }}>
                    <span>سيولة شرائية (فوق) <b className="num">{num(lq.buyside_level)}</b></span>
                  </div>
                  <div style={{ display: 'flex', gap: 10, fontSize: 12 }}>
                    <span>سيولة بيعية (تحت) <b className="num">{num(lq.sellside_level)}</b></span>
                  </div>

                  <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {sweep.signal && sweep.signal !== 'none' ? (
                      <span className="pill amber" style={{ fontSize: 11 }}>
                        كنس {sweep.direction ? (SWEEP_DIR[sweep.direction] ?? arabicVisible(sweep.direction, 'جهة غير مترجَمة')) : ''} @ {num(sweep.price)}
                      </span>
                    ) : null}
                    {fvg.signal && fvg.signal !== 'none' ? (
                      <span className={`pill ${fvg.signal === 'fvg_bullish' ? 'green' : 'red'}`} style={{ fontSize: 11 }}>
                        فجوة {FVG[fvg.signal] ?? ''} [{num(fvg.gap_bottom)} – {num(fvg.gap_top)}]
                      </span>
                    ) : null}
                    {(!sweep.signal || sweep.signal === 'none') && (!fvg.signal || fvg.signal === 'none') ? (
                      <span className="pill grey" style={{ fontSize: 11 }}>لا كنس ولا فجوة الآن</span>
                    ) : null}
                  </div>
                </>
              )}
            </div>
          )
        })}
      </div>
      )}
      {/* بند ٩ (ورقة ٩٩): معاملات القسم الحقيقية بجدول واحد — بنمط صفحة التحليل (150) */}
      <SectionConfigTable from={250} to={300} title="معاملات ذرّات السيولة (250-299) — ضبط جماعي" />
    </div>
  )
}

type LiqShape = {
  pool: string
  buyside_level: number | null; sellside_level: number | null
  sweep: { signal: string; direction: string | null; price: number | null }
  fvg: { signal: string; gap_top: number | null; gap_bottom: number | null }
}
