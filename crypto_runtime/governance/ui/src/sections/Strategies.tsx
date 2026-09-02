// الاستراتيجيات (٤٠٠) — بطاقة قسم الاستراتيجيات الوصفية (strategy.section.live):
// 400 أصبح قسمًا وصفيًا ينتج direction/strength/confidence/depth/weight/ratio
// بدل BUY/SELL. اللوحة تعرض هذه البطاقة من sectionCards (حساب::وسيط::رمز::400).
import { useStore } from '../core/store'
import { SectionAtomsHealth, SectionConfigTable } from '../components/SectionAtoms'

const SECTION_LABEL = '400'
const pct = (n?: number) => (n == null ? '—' : `${Math.round(n * 100)}%`)
const num = (n?: number) => (n == null ? '—' : n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 3 }))
const SIGNAL: Record<string, string> = { positive_strategic_lean: 'ميل استراتيجي صاعد', negative_strategic_lean: 'ميل استراتيجي هابط', balanced_strategic_context: 'سياق استراتيجي متوازن' }

// حالة البطاقة
const STATE: Record<string, { t: string; c: string }> = {
  READY: { t: 'جاهز', c: 'green' },
  DECISION_READY: { t: 'جاهز للقرار', c: 'green' },
  ANALYZING: { t: 'قيد التحليل', c: 'amber' },
  NOT_READY: { t: 'غير جاهز', c: 'grey' },
  STALE: { t: 'متقادم', c: 'red' },
  INVALID: { t: 'غير صالح', c: 'red' },
  ERROR: { t: 'خطأ', c: 'red' },
}

function dirValue(u?: Record<string, unknown>): number | undefined {
  const v = u?.['direction'] as number | undefined
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

export default function Strategies() {
  const sectionCards = useStore((s) => s.sectionCards)
  // اجمع بطاقات قسم 400 من كل النطاقات
  const cards = Object.entries(sectionCards)
    .filter(([key]) => key.split('::')[3] === SECTION_LABEL)
    .sort(([a], [b]) => a.localeCompare(b, 'ar'))

  return (
    <div className="section chartsec">
      {cards.length === 0 ? (
        <>
          <div className="empty">بانتظار بطاقة قسم الاستراتيجيات (strategy.section.live) من النواة…</div>
          <SectionAtomsHealth from={400} to={450} title="ذرّات قسم الاستراتيجيات — حالتها الحيّة الآن"
            note="ما وصلت بطاقة قسم بعد — هاي حالة ذرّات القسم نفسها من النواة، مو تخمين." />
        </>
      ) : (
        <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))' }}>
          {cards.map(([key, card]) => {
            const u = ((card as Record<string, unknown>).unified ?? card) as Record<string, unknown>
            const parts = key.split('::')
            const symbol = parts[2] ?? '?'
            const dir = dirValue(u)
            const rawState = String(u['state'] ?? '')
            const st = STATE[rawState] ?? { t: rawState || '—', c: 'grey' }
            const dirCls = dir == null ? 'grey' : dir > 0 ? 'green' : dir < 0 ? 'red' : 'grey'
            const dirArrow = dir == null ? '·' : dir > 0 ? '▲' : dir < 0 ? '▼' : '▬'
            const strength = u['strength'] as number | undefined
            const confidence = u['confidence'] as number | undefined
            const depth = u['current_depth'] as number | undefined
            const reqDepth = u['required_depth'] as number | undefined
            const weight = u['weight'] as number | undefined
            const ratio = u['ratio'] as number | undefined
            const weightApplied = u['weight_applied'] as number | undefined
            const ready = u['ready'] as boolean | undefined
            const complete = u['complete'] as boolean | undefined
            const warnings = Array.isArray(u['warnings']) ? u['warnings'] as unknown[] : []
            const missing = Array.isArray(u['missing']) ? u['missing'] as unknown[] : []
            const activeWeight = u['active_weight'] as number | undefined
            const availableWeight = u['available_weight'] as number | undefined
            const missingWeight = u['missing_weight'] as number | undefined
            const contextFactor = u['context_factor'] as number | undefined
            const timeframe = u['timeframe'] as string | undefined

            return (
              <div className="scard" key={key} style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontWeight: 700, fontSize: 14 }}>{symbol}</span>
                  <span className="dim" style={{ fontSize: 11 }}>{parts[1]}</span>
                  <span className={`pill ${st.c}`} style={{ marginInlineStart: 'auto', fontSize: 11 }}>{st.t}</span>
                </div>

                {/* الاتجاه الوصفي */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <b className={`num ${dirCls}`} style={{ fontSize: 15 }}>{dirArrow} {dir == null ? '—' : num(dir)}</b>
                  <span className="dim" style={{ fontSize: 11 }}>{SIGNAL[String(u['signal'] ?? '')] ?? 'اتجاه غير معروف'}</span>
                  <span className="dim" style={{ fontSize: 11 }}>{ready ? 'مفعّل' : 'غير مفعّل'}</span>
                </div>

                {/* القوة / الثقة / العمق */}
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 12px', fontSize: 12 }}>
                  <span>القوة <b className="num">{num(strength)}</b></span>
                  <span>الثقة <b className="num">{pct(confidence)}</b></span>
                  <span>
                    العمق <b className="num">{pct(depth)}</b>
                    {reqDepth != null ? <span className="dim"> / {pct(reqDepth)}</span> : null}
                  </span>
                </div>

                {/* الوزن / الحصة */}
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 12px', fontSize: 12 }}>
                  <span>الوزن <b className="num">{pct(weight)}</b></span>
                  {weightApplied != null ? <span>فعّال <b className="num">{pct(weightApplied)}</b></span> : null}
                  {activeWeight != null ? <span>نشط <b className="num">{pct(activeWeight)}</b></span> : null}
                  {availableWeight != null ? <span>متاح <b className="num">{pct(availableWeight)}</b></span> : null}
                  {missingWeight != null ? <span>غائب <b className="num">{pct(missingWeight)}</b></span> : null}
                  {contextFactor != null ? <span>عامل السياق <b className="num">{pct(contextFactor)}</b></span> : null}
                  {ratio != null ? <span>الحصّة <b className="num">{num(ratio)}</b></span> : null}
                </div>
                <div className="ss dim" style={{ fontSize: 11 }}>
                  الجاهزية {complete ? 'مكتملة' : 'غير مكتملة'}{timeframe ? ` · الفريم ${timeframe}` : ''}{missing.length ? ` · ناقص ${missing.length}` : ''}{warnings.length ? ` · تحذير ${warnings.length}` : ''}
                </div>
              </div>
            )
          })}
        </div>
      )}
      <SectionConfigTable from={400} to={450} title="معاملات ذرّات الاستراتيجيات (400-449) — ضبط جماعي" />
    </div>
  )
}
