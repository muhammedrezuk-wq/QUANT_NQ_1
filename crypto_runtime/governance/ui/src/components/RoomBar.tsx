// إقفال القسم 150 · مرحلة ٤ (أمر المالك ٢٠٢٦-٠٨-٢٣): شريط القرار العلويّ.
// «مجمع القرار لازم يطلع بالصفحة الرئيسية — نسبة مايلة للشراء، مايلة للبيع،
// بشريط علوي لازم أشوفه، ثم تحته كل قسم إيش عم يحكي.»
// ⛔ الواجهة لا تحسب: كل رقم معروض وصل بحدث decision.room.state كما هو.
// الغائب مجهول معلن، ولا صفر كاذب ولا قفزة.
import { useStore } from '../core/store'
import type { DecisionRoom, RoomSectionRow } from '../core/store'

const SECTION_AR: Record<string, string> = {
  '150': 'التحليل', '200': 'البنية', '250': 'السيولة', '300': 'الإحصاء',
  '350': 'الاحتمالات', '400': 'الاستراتيجيات',
}
const sectionAr = (id: string) => SECTION_AR[id] ?? id

const SIGNAL_AR: Record<string, [string, string]> = {
  up: ['مايل للشراء', 'var(--green)'],
  down: ['مايل للبيع', 'var(--red)'],
  sideways: ['محايد', 'var(--dim)'],
  unknown: ['مجهول', 'var(--dim)'],
}
const sigAr = (s?: string): [string, string] => (s && SIGNAL_AR[s]) || SIGNAL_AR.unknown

const num = (n?: number | null, suffix = '') =>
  (n == null ? 'مجهول' : `${n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 1 })}${suffix}`)

function stateColor(state?: string): string {
  if (state === 'READY') return 'var(--green)'
  if (state === 'STALE') return 'var(--red)'
  if (state === 'ANALYZING') return 'var(--amber)'
  return 'var(--dim)'
}

function SectionChip({ row }: { row: RoomSectionRow }) {
  return (
    <span className="pill" title={`الحالة ${row.state ?? 'مجهول'} · العمر ${row.age_s ?? '؟'}ث`}
      style={{ display: 'inline-flex', gap: 5, alignItems: 'center', fontSize: 11.5,
               padding: '3px 8px', border: '1px solid var(--line)', borderRadius: 99,
               color: 'var(--ink)', background: 'var(--card, #fff1)' }}>
      <span style={{ width: 7, height: 7, borderRadius: 99, background: stateColor(row.state), flex: 'none' }} />
      <span>{sectionAr(row.section_id)}</span>
      <span className="num" style={{ color: row.readiness_pct == null ? 'var(--dim)' : 'var(--ink)' }}>
        {num(row.readiness_pct, '٪')}
      </span>
      {row.direction == null ? null : (
        <span style={{ color: row.direction > 0 ? 'var(--green)' : row.direction < 0 ? 'var(--red)' : 'var(--dim)', fontWeight: 700 }}>
          {row.direction > 0 ? '▲' : row.direction < 0 ? '▼' : '■'}
        </span>
      )}
    </span>
  )
}

export function RoomBar() {
  const rooms = useStore((s) => s.room)
  const entries = Object.entries(rooms)
  if (!entries.length) {
    return (
      <div className="card" style={{ padding: '8px 14px', marginBottom: 8, fontSize: 12.5, color: 'var(--dim)' }}>
        شريط القرار — بانتظار أول بطاقة قسم (الروم يظهر لحظة وصولها)
      </div>
    )
  }
  return (
    <div style={{ display: 'grid', gap: 6, marginBottom: 8 }}>
      {entries.slice(0, 3).map(([key, room]: [string, DecisionRoom]) => (
        <RoomRow key={key} room={room} />
      ))}
    </div>
  )
}

function RoomRow({ room }: { room: DecisionRoom }) {
  const [sigText, sigColor] = sigAr(room.signal)
  const dir = room.direction ?? null
  const pos = dir == null ? 50 : Math.max(0, Math.min(100, (dir + 100) / 2))
  const sections = room.sections ?? []
  const present = sections.filter((r) => r.section_id)
  const missing = room.sections_missing ?? []
  return (
    <div className="card" style={{ padding: '10px 14px', display: 'grid', gap: 7 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'baseline', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 13.5, fontWeight: 800 }}>القرار — الروم الحيّ</span>
          <span className="num" style={{ fontSize: 12.5, color: 'var(--dim)' }}>{room.symbol}</span>
          <span style={{ fontSize: 13, fontWeight: 800, color: sigColor }}>{sigText}</span>
        </div>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 12 }}>
          <span className="dim">الاتجاه <b className="num" style={{ color: dir == null ? 'var(--dim)' : dir > 0 ? 'var(--green)' : dir < 0 ? 'var(--red)' : 'var(--ink)' }}>{num(dir)}</b></span>
          <span className="dim">الثقة <b className="num">{room.confidence_defined === false ? 'غير معرّفة' : num(room.confidence, '٪')}</b></span>
          <span className="dim">الجاهزية <b className="num">{num(room.readiness_pct, '٪')}</b></span>
        </div>
      </div>
      {/* مسطرة الاتجاه −100..+100: موضع المؤشر هو رقم الاتجاه الواصل كما هو */}
      <div style={{ position: 'relative', height: 10, borderRadius: 99,
        background: 'linear-gradient(to left, rgba(251,113,133,.55), rgba(107,122,148,.35), rgba(52,211,153,.55))' }}>
        <div style={{ position: 'absolute', top: -3, bottom: -3, width: 4, borderRadius: 4,
          right: `calc(${pos}% - 2px)`, background: 'var(--ink)', boxShadow: '0 0 0 1px var(--bg1, #0003)' }} />
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
        {present.map((row) => <SectionChip key={row.section_id} row={row} />)}
        {missing.map((id) => (
          <span key={id} className="pill" style={{ fontSize: 11.5, padding: '3px 8px', border: '1px dashed var(--line)', borderRadius: 99, color: 'var(--dim)' }}>
            {sectionAr(id)} · بانتظار
          </span>
        ))}
      </div>
    </div>
  )
}
