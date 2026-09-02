// إقفال القسم 150 · مرحلة ٤ (أمر المالك ٢٠٢٦-٠٨-٢٣): لوحة المحلّلين.
// «كل واحد من المحلّلين أشوفه واحد واحد — إيش عم يقول، وزنه، آخر تسليم، متى الجاي.»
// المصدر حدث analysis.analysts.state فقط — كل محلّل أعلن نفسه بنفسه.
// ⛔ لا حساب هنا: أرقام معروضة كما وصلت؛ الغائب «بانتظار» والمجهول «مجهول».
import { useStore } from '../core/store'
import type { AnalystRow } from '../core/store'

const ANALYST_AR: Record<string, string> = {
  trend: 'الاتجاه', momentum: 'الزخم', volatility: 'التذبذب', volume: 'الحجم',
  spread: 'السبريد', candle: 'الشمعة', gap: 'الفجوات', session: 'الجلسات',
  time: 'الوقت', velocity: 'السرعة', acceleration: 'التسارع',
  volume_quality: 'جودة الحجم', noise: 'الضوضاء', correlation: 'الارتباط',
  relative_strength: 'القوة النسبية',
}
const analystAr = (id: string) => (ANALYST_AR[id] ? `${ANALYST_AR[id]} · ${id}` : id)

const num = (n?: number | null, suffix = '') =>
  (n == null ? 'مجهول' : `${n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 1 })}${suffix}`)

const MODE_AR: Record<string, string> = { live_tick: 'تِكّة (مستمر)', candle: 'شمعة' }

function DirectionCell({ v }: { v?: number | null }) {
  if (v == null) return <span className="dim">مجهول</span>
  const color = v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--dim)'
  return <span className="num" style={{ color, fontWeight: 700 }}>{num(v)}</span>
}

function StateCell({ state, ready }: { state?: string; ready?: boolean }) {
  if (!state) return <span className="dim">مجهول</span>
  const color = state === 'DECISION_READY' || ready === true ? 'var(--green)'
    : state === 'STALE' || state === 'ERROR' ? 'var(--red)' : 'var(--amber)'
  return <span style={{ color, fontSize: 11.5 }}>{state}</span>
}

export default function AnalystsPanel() {
  const panels = useStore((s) => s.analystsPanels)
  const entries = Object.entries(panels)
  if (!entries.length) {
    return (
      <div className="card" style={{ padding: '10px 14px', fontSize: 12.5, color: 'var(--dim)' }}>
        لوحة المحلّلين — بانتظار أول إعلان من مدير التحليل (150)
      </div>
    )
  }
  const [key, panel] = entries[entries.length - 1]
  const rows: AnalystRow[] = panel.analysts ?? []
  const present = rows.filter((r) => r.present)
  const waiting = rows.filter((r) => !r.present)
  return (
    <div className="card" style={{ padding: '10px 14px', display: 'grid', gap: 8 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13.5, fontWeight: 800 }}>المحلّلون — كل محلّل يعلن بنفسه</span>
          <span className="num dim" style={{ fontSize: 12 }}>{panel.symbol}</span>
        </div>
        <span className="dim" style={{ fontSize: 12 }}>
          سلّموا <b className="num">{present.length}</b> من <b className="num">{panel.expected ?? 15}</b>
          {waiting.length ? ` · بانتظار ${waiting.length}` : ''}
        </span>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table className="tbl" style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse', minWidth: 640 }}>
          <thead>
            <tr className="dim" style={{ textAlign: 'right' }}>
              <th style={{ padding: '4px 8px' }}>المحلّل</th>
              <th style={{ padding: '4px 8px' }}>اتجاهه</th>
              <th style={{ padding: '4px 8px' }}>الثقة</th>
              <th style={{ padding: '4px 8px' }}>الوزن</th>
              <th style={{ padding: '4px 8px' }}>التسليمات</th>
              <th style={{ padding: '4px 8px' }}>آخر تسليم</th>
              <th style={{ padding: '4px 8px' }}>الإيقاع</th>
              <th style={{ padding: '4px 8px' }}>الجاي</th>
              <th style={{ padding: '4px 8px' }}>الحالة</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} style={{ borderTop: '1px solid var(--line)' }}>
                <td style={{ padding: '4px 8px', fontWeight: 600 }}>{analystAr(r.id)}</td>
                <td style={{ padding: '4px 8px' }}><DirectionCell v={r.direction} /></td>
                <td className="num" style={{ padding: '4px 8px' }}>{num(r.confidence)}</td>
                <td className="num" style={{ padding: '4px 8px' }}>{num(r.weight)}</td>
                <td className="num" style={{ padding: '4px 8px' }}>{r.present ? num(r.deliveries) : '—'}</td>
                <td className="num" style={{ padding: '4px 8px' }}>
                  {r.age_s == null ? '—' : `${r.age_s.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 1 })} ث`}
                </td>
                <td style={{ padding: '4px 8px' }} className="dim">
                  {r.present ? (MODE_AR[r.mode ?? ''] ?? r.mode ?? 'مجهول') + (r.timeframe && r.mode === 'candle' ? ` ${r.timeframe}` : '') : '—'}
                </td>
                <td className="num" style={{ padding: '4px 8px' }}>
                  {r.next_expected_at == null
                    ? (r.mode === 'live_tick' ? 'مستمر' : '—')
                    : r.next_expected_at.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 0 })}
                </td>
                <td style={{ padding: '4px 8px' }}>
                  {r.present ? <StateCell state={r.state} ready={r.ready} /> : <span className="dim">بانتظار</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
