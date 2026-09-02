// إقفال القسم 150 · مرحلة ٥ (٢٠٢٦-٠٨-٢٣): الإنذار المبكّر — الأحداث الثلاثة العمياء.
// «مشاكل لوحة.md»: ثلاثة أحداث تصل الوصلة ولا تجد سطرًا يعرضها — وهي التي تخبر
// المالك أنّ الجهاز أو الساعة أو الناقل بدأ يتعثّر. الآن لها شريط فوق كل شي.
// ⛔ لا حساب: يُعرض ما وصل فقط؛ من لم يصل قطّ = «لم يصل بعد».
import { useStore } from '../core/store'

interface ChipSpec { event: string; ar: string }
const CHIPS: ChipSpec[] = [
  { event: 'tools.device_resources.state', ar: 'موارد الجهاز' },
  { event: 'telemetry.carrier.state', ar: 'ناقل التلمترية' },
  { event: 'time.clock.quality.state', ar: 'جودة الساعة' },
]

type MaybeRec = Record<string, unknown>

function pickStatus(p: MaybeRec | undefined): string {
  if (!p) return ''
  for (const key of ['status', 'state', 'quality', 'health']) {
    const v = p[key]
    if (typeof v === 'string' && v) return v
  }
  return 'وصل'
}

function statusColor(s: string): string {
  const u = s.toUpperCase()
  if (u.includes('OK') || u.includes('READY') || u.includes('GOOD') || u.includes('HEALTHY') || s === 'وصل') return 'var(--green)'
  if (u.includes('STALE') || u.includes('DEGRADED') || u.includes('WARN')) return 'var(--amber)'
  if (u.includes('FAIL') || u.includes('ERROR') || u.includes('BAD')) return 'var(--red)'
  return 'var(--dim)'
}

export function EarlyWarningStrip() {
  const streams = useStore((s) => s.streams)
  const flows = useStore((s) => s.flows)
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
      {CHIPS.map(({ event, ar }) => {
        const payload = streams[event] as MaybeRec | undefined
        const arrived = flows[event] != null
        const status = arrived ? pickStatus(payload) : 'لم يصل بعد'
        const color = arrived ? statusColor(status) : 'var(--dim)'
        return (
          <span key={event} title={event}
            style={{ display: 'inline-flex', gap: 6, alignItems: 'center', fontSize: 11.5,
                     padding: '2px 9px', border: `1px ${arrived ? 'solid' : 'dashed'} var(--line)`,
                     borderRadius: 99, color: 'var(--ink)' }}>
            <span style={{ width: 7, height: 7, borderRadius: 99, background: color, flex: 'none' }} />
            <span>{ar}</span>
            <span className="num" style={{ color, fontSize: 11 }}>{status}</span>
          </span>
        )
      })}
    </div>
  )
}
