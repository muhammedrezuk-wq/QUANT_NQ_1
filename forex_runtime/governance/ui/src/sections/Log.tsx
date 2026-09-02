// السجل (٨٦٢) — جورنال النواة مترجَم للعربي (منفذ حقيقي: /gov/journal)
// + السجل الموحّد الكامل بزر واحد (ورقة ٠٩ مكوّن ٢): نواة + صفقات + بوّابة الأوامر
//   بخط زمني واحد، وأخطاء الذرات المعلّقة جنبهن — بلا فتح عشرين ملف.
// + سجلّا اليوم النصّيان (بنية السجلّات ٢٠٢٦-٠٨-١٩ بأمر المالك):
//   «أخطاء اليوم» من var\logs\errors-YYYYMMDD.log (تكتبه الذرة 719)
//   «صفقات اليوم» من var\logs\trades-YYYYMMDD.log (تكتبه الذرة 720)
//   منفذ حقيقي: /gov/day-logs — ملفّ غائب ليس عطلًا: ما انكتب شيء اليوم بعد.
import { useEffect, useState } from 'react'
import { useStore } from '../core/store'
import { arabicVisible } from '../core/arabic'

interface Entry { ts: number; atom_id: number; action: string }
interface UItem {
  ts: number; src: 'core' | 'trade' | 'gate'; kind: string
  atom_id?: number; symbol?: string; side?: string; volume?: number
  price?: number; ticket?: number; status?: string; operator?: string
}
interface ULog { items: UItem[]; errors: Array<{ atom_id: number; name_ar: string; error: string }> }
interface DaySide { file: string; exists: boolean; count_today: number; lines: string[]; truncated?: boolean; error?: string }
interface DayLogs { date: string; errors: DaySide; trades: DaySide }

const ACT: Record<string, string> = {
  started: 'اشتغلت',
  stopped: 'وقفت',
  hot_loaded: 'حُمّلت حيًّا',
  hot_unloaded: 'سُحبت',
  hot_reloaded: 'أُعيد تحميلها',
  failed: 'فشلت',
  config_updated: 'تعدّل إعدادها',
  restarted: 'أُعيد تشغيلها',
}
const GATE_AR: Record<string, string> = { halt: 'إيقاف طارئ', kill_switch_reset: 'تصفير قاطع الأمان' }
const GATE_ST: Record<string, string> = { PENDING: 'معلّق', DONE: 'نُفِّذ', EXPIRED: 'انتهت مهلته', REJECTED: 'مرفوض' }

const t = (ts: number) => new Date(ts * 1000).toLocaleTimeString('ar-EG-u-nu-latn', { hour12: false })

function unifiedText(i: UItem, names: Record<number, string>): [string, string] {
  // المعروف يُترجم، والغريب باسمه الخام — لا «إجراء غير مترجَم» يعمي التشخيص (نمط أ بدفتر 97)
  if (i.src === 'core') return ['نواة', `${names[i.atom_id ?? -1] ?? `#${i.atom_id}`} — ${ACT[i.kind] ?? i.kind}`]
  if (i.src === 'trade') {
    const side = i.side === 'BUY' ? 'شراء' : 'بيع'
    return ['صفقة', i.kind === 'OPENED'
      ? `فتح ${side} ${i.symbol} حجم ${i.volume} @ ${i.price} (تذكرة ${i.ticket})`
      : `إغلاق ${side} ${i.symbol} @ ${i.price ?? '—'} (تذكرة ${i.ticket})`]
  }
  return ['بوّابة', `${GATE_AR[i.kind] ?? arabicVisible(i.kind, 'أمر غير مترجَم')} — ${GATE_ST[i.status ?? ''] ?? arabicVisible(i.status, 'حالة غير مترجَمة')}`]
}

const SRC_COLOR: Record<string, string> = { 'نواة': 'var(--dim)', 'صفقة': 'var(--green)', 'بوّابة': 'var(--amber)' }

type Mode = 'atoms' | 'unified' | 'errors' | 'trades'

// لون سطر السجلّ النصّي من محتواه (السطر نفسه يحمل مستواه بالعربي)
function lineColor(line: string, kind: 'errors' | 'trades'): string {
  if (kind === 'errors') {
    if (line.includes('| حرج |') || line.includes('| خطأ |')) return 'var(--red)'
    if (line.includes('| تحذير |')) return 'var(--amber)'
    return 'var(--dim)'
  }
  if (line.includes('رُفض') || line.includes('فشل')) return 'var(--red)'
  if (line.includes('فُتحت') || line.includes('نُفّذ')) return 'var(--green)'
  if (line.includes('أُغلقت') || line.includes('اختفى')) return 'var(--amber)'
  return 'var(--fg)'
}

export default function Log() {
  const [items, setItems] = useState<Entry[]>([])
  const [unified, setUnified] = useState<ULog | null>(null)
  const [mode, setMode] = useState<Mode>('atoms')
  const [loading, setLoading] = useState(false)
  const [day, setDay] = useState<DayLogs | null>(null)
  const [dayFail, setDayFail] = useState(false)
  const names = useStore((s) => s.namesAr)

  useEffect(() => {
    const load = () =>
      fetch('/gov/journal?n=80')
        .then((r) => r.json())
        .then((d: Entry[]) => setItems([...d].reverse()))
        .catch(() => {})
    load()
    const timer = setInterval(load, 4000)
    return () => clearInterval(timer)
  }, [])

  // سجلّا اليوم النصّيان: سحب دوري وهما ظاهران فقط
  useEffect(() => {
    if (mode !== 'errors' && mode !== 'trades') return
    const load = () =>
      fetch('/gov/day-logs?n=300')
        .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json() })
        .then((d: DayLogs) => { setDay(d); setDayFail(false) })
        .catch(() => setDayFail(true))
    load()
    const timer = setInterval(load, 5000)
    return () => clearInterval(timer)
  }, [mode])

  const pullUnified = async () => {
    setLoading(true)
    try {
      const r = await fetch('/gov/unified-log')
      setUnified((await r.json()) as ULog)
      setMode('unified')
    } catch { setUnified(null) }
    setLoading(false)
  }

  const side: DaySide | null = day == null ? null : mode === 'errors' ? day.errors : mode === 'trades' ? day.trades : null
  const emptyText = mode === 'errors'
    ? 'ما انكتب ولا سطر أخطاء اليوم — أول تحذير أو خطأ بالنواة أو الذرات يفتح الملف لحاله (الذرة 719 شغّالة وتسمع).'
    : 'ولا حدث صفقة أو أمر انكتب اليوم بعد — أول فتح/إغلاق/رفض/نتيجة أمر ينكتب فورًا (الذرة 720 شغّالة وتسمع).'

  return (
    <div className="section" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn" style={mode === 'atoms' ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}} onClick={() => setMode('atoms')}>سجل الذرات</button>
        <button className="btn" style={mode === 'unified' ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}} disabled={loading} onClick={pullUnified}>
          {loading ? '⏳ عم يسحب…' : '📜 السجل الموحّد الكامل'}
        </button>
        <button className="btn" style={mode === 'errors' ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}} onClick={() => setMode('errors')}>⚠️ أخطاء اليوم</button>
        <button className="btn" style={mode === 'trades' ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}} onClick={() => setMode('trades')}>💱 صفقات اليوم</button>
        {mode === 'unified' && unified ? <span className="dim" style={{ fontSize: 12 }}>{unified.items.length} سطر — نواة + صفقات + بوّابة، بخط زمني واحد</span> : null}
        {(mode === 'errors' || mode === 'trades') && side ? (
          <span className="dim" style={{ fontSize: 12 }}>
            <span className="num">{side.count_today}</span> سطر اليوم — <span className="num" dir="ltr">{side.file}</span>
          </span>
        ) : null}
      </div>

      {mode === 'atoms' ? (
        <div className="loglist" style={{ flex: 1 }}>
          {items.length === 0 ? <div className="empty">لا سجلّ بعد</div> : null}
          {items.map((e, i) => (
            <div className="logrow" key={`${e.ts}-${i}`}>
              <span className="lt num">{t(e.ts)}</span>
              <span className="ln">{names[e.atom_id] ?? `#${e.atom_id}`}</span>
              <span className="la">{ACT[e.action] ?? e.action}</span>
            </div>
          ))}
        </div>
      ) : mode === 'errors' || mode === 'trades' ? (
        dayFail ? (
          <div className="empty">المنفذ جديد — سكّر غرفة القيادة وافتحها من جديد حتى يشتغل «سجلّا اليوم».</div>
        ) : day == null ? (
          <div className="empty">⏳ عم يقرأ ملفّ اليوم…</div>
        ) : side && side.error ? (
          <div className="empty" style={{ color: 'var(--red)' }}>تعذّرت قراءة الملفّ: <span dir="ltr">{side.error}</span></div>
        ) : side && (!side.exists || side.count_today === 0) ? (
          <div className="empty">{emptyText}</div>
        ) : side ? (
          <div className="loglist" style={{ flex: 1 }}>
            {side.truncated ? <div className="dim" style={{ fontSize: 11, padding: '2px 6px' }}>الملفّ كبير — معروض آخر جزء منه فقط (الملفّ الكامل على القرص)</div> : null}
            {[...side.lines].reverse().map((line, i) => (
              <div key={`${i}-${line.slice(0, 24)}`} dir="auto"
                style={{ color: lineColor(line, mode), fontSize: 12, lineHeight: 1.9,
                         whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                         borderBottom: '1px solid rgba(128,128,128,.12)', padding: '2px 6px' }}>
                {line}
              </div>
            ))}
          </div>
        ) : null
      ) : unified == null ? (
        <div className="empty">ما قدرت أسحب — أعد فتح غرفة القيادة لو المنفذ جديد.</div>
      ) : (
        <>
          {unified.errors.length ? (
            <div className="scard" style={{ borderColor: 'var(--red)' }}>
              <div className="st">أخطاء معلّقة على الذرات ({unified.errors.length})</div>
              {unified.errors.map((e) => (
                <div key={e.atom_id} className="ss" style={{ color: 'var(--red)' }}>● {e.name_ar}: <span dir="ltr">{e.error}</span></div>
              ))}
            </div>
          ) : null}
          <div className="loglist" style={{ flex: 1 }}>
            {unified.items.map((i, k) => {
              const [src, text] = unifiedText(i, names)
              return (
                <div className="logrow" key={`${i.ts}-${k}`}>
                  <span className="lt num">{t(i.ts)}</span>
                  <span style={{ color: SRC_COLOR[src], fontSize: 11, minWidth: 44 }}>{src}</span>
                  <span className="la">{text}</span>
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
