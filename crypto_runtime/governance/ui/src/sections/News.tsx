// الأخبار (856) — ختم المالك ٢٠٢٦-٠٨-٢٠: «عملنا لنا صفحة أخبار بلوحتنا».
// مصدر الأجندة: تقويم ميتاتريدر المدمج عبر جسر التداول (منفذ /gov/calendar)،
// عناوينه عربية أصلًا وفيه العملة والأهمية والمتوقّع والسابق والفعلي —
// بلا مفتاح API ولا مصدر خارجي. والأخبار من جدول `news` بنفس الجسر.
// القوانين (ورقتا ٩٩ و٩٧): أرقام لاتينية · صفر إنكليزي بالواجهة · الغياب يُعلَن
// غيابًا · لا رقم مخترع — عناوين الأخبار تبقى بلغتها لأنها **بيانات لا واجهة**.
import { useEffect, useMemo, useState } from 'react'

interface CalEvent {
  id: string; title: string; country: string; currency: string
  impact: string; epoch: number; actual: string; forecast: string; previous: string
}
interface NewsRow {
  id: number; headline: string; headline_ar?: string; link: string; source: string
  sentiment: number | null; impact: string | null; published_at: number | null
  summary?: string; relevance?: string
}

// أسماء المصادر بالعربي — لا معرّفات خام على اللوحة (بند ١ بدفتر ٩٧).
const SOURCE_AR: Record<string, string> = {
  federal_reserve: 'الاحتياطي الفيدرالي',
  bea: 'مكتب التحليل الاقتصادي',
  cnbc_top: 'CNBC — الأهمّ',
  cnbc_markets: 'CNBC — الأسواق',
  marketwatch: 'ماركت ووتش',
  investing: 'إنفستنغ',
  yahoo_ndx: 'ياهو — ناسداك',
  yahoo_rss: 'ياهو',
}
const sourceAr = (s: string) => SOURCE_AR[s] ?? s

// سبب القبول: إمّا رمز من رموز المالك، أو كلمة اقتصاد كلّي — تُعرَّب هنا.
const REASON_AR: Record<string, string> = {
  'مصدر رسمي': 'مصدر رسمي',
  fed: 'الفيدرالي', 'federal reserve': 'الفيدرالي', fomc: 'اجتماع الفيدرالي',
  powell: 'باول', 'rate cut': 'خفض الفائدة', 'rate hike': 'رفع الفائدة',
  'interest rate': 'الفائدة', inflation: 'التضخّم', cpi: 'التضخّم',
  pce: 'التضخّم', payroll: 'الوظائف', jobless: 'إعانات البطالة',
  unemployment: 'البطالة', gdp: 'الناتج المحلّي', treasury: 'الخزانة',
  yield: 'العوائد', tariff: 'الرسوم الجمركية', dollar: 'الدولار',
  recession: 'الركود', stimulus: 'التحفيز', 'debt ceiling': 'سقف الدين',
}
const reasonAr = (r: string) => REASON_AR[r] ?? r

const num = (n: number) => n.toLocaleString('ar-EG-u-nu-latn')
const clock = (epoch: number) =>
  new Date(epoch * 1000).toLocaleTimeString('ar-EG-u-nu-latn', { hour12: false, hour: '2-digit', minute: '2-digit' })

const IMPACT: Record<string, [string, string, string]> = {
  HIGH: ['عالية', 'var(--red)', '🔴'],
  MEDIUM: ['متوسطة', 'var(--amber)', '🔸'],
  LOW: ['منخفضة', 'var(--dim)', '🔹'],
  NONE: ['بلا أثر', 'var(--dim)', '▫️'],
}
const impactOf = (k: string): [string, string, string] => IMPACT[k] ?? [k || '—', 'var(--dim)', '▫️']

// مقارنة الفعلي بالمتوقّع — **وصف رقميّ فقط**.
// ⛔ لا نقول «إيجابي» أو «سلبي»: معنى الاتجاه يختلف باختلاف المؤشّر
//    (ارتفاع مطالبات البطالة سيّئ، وارتفاع الناتج جيّد) — وما لا نملكه لا نخترعه.
function parseNum(v: string): number | null {
  if (!v) return null
  let s = v.trim().replace(/,/g, '').replace(/%/g, '')
  let mult = 1
  const last = s.slice(-1).toLowerCase()
  if (last === 'k') { mult = 1e3; s = s.slice(0, -1) }
  else if (last === 'm') { mult = 1e6; s = s.slice(0, -1) }
  else if (last === 'b') { mult = 1e9; s = s.slice(0, -1) }
  const n = Number(s)
  return Number.isFinite(n) ? n * mult : null
}
function compareActual(actual: string, forecast: string): string {
  const a = parseNum(actual), f = parseNum(forecast)
  if (a == null || f == null) return ''
  const d = a - f
  if (Math.abs(d) < 1e-9) return 'مطابق للمتوقّع'
  const t = Math.abs(d).toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
  return d > 0 ? `أعلى بـ${t}` : `أقلّ بـ${t}`
}

function ageText(epoch: number, nowSec: number): string {
  const s = Math.max(0, nowSec - epoch)
  if (s < 90) return 'الآن'
  if (s < 3600) return `قبل ${num(Math.round(s / 60))} دقيقة`
  if (s < 86400) return `قبل ${num(Math.round(s / 3600))} ساعة`
  return `قبل ${num(Math.round(s / 86400))} يوم`
}

const CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'ALL']
const LEVELS: Array<[string, string]> = [['HIGH', 'عالية فقط'], ['MEDIUM', 'متوسطة فما فوق'], ['LOW', 'الكل']]

function chip(on: boolean): React.CSSProperties {
  return {
    fontSize: 11, padding: '2px 9px', borderRadius: 7, cursor: 'pointer',
    fontFamily: 'inherit', whiteSpace: 'nowrap',
    background: on ? 'var(--accent)' : 'transparent',
    color: on ? '#06121c' : 'var(--dim)',
    border: `1px solid ${on ? 'var(--accent)' : 'var(--glassb)'}`,
  }
}

export default function News() {
  const [currency, setCurrency] = useState('USD')
  const [level, setLevel] = useState('MEDIUM')
  const [events, setEvents] = useState<CalEvent[] | null>(null)
  const [available, setAvailable] = useState(true)
  const [news, setNews] = useState<NewsRow[] | null>(null)
  const [now, setNow] = useState(() => Date.now() / 1000)

  useEffect(() => {
    let alive = true
    const load = () => {
      fetch(`/gov/calendar?currency=${currency}&impact=${level}`)
        .then((r) => r.json())
        .then((d: { events?: CalEvent[]; available?: boolean }) => {
          if (!alive) return
          setEvents(d.events ?? [])
          setAvailable(d.available !== false)
        })
        .catch(() => { if (alive) setEvents([]) })
      fetch('/gov/news?limit=30')
        .then((r) => r.json())
        .then((d: { news?: NewsRow[] }) => { if (alive) setNews(d.news ?? []) })
        .catch(() => { if (alive) setNews([]) })
    }
    load()
    const id = window.setInterval(load, 30000)
    return () => { alive = false; window.clearInterval(id) }
  }, [currency, level])

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(id)
  }, [])

  const { next, soon, done } = useMemo(() => {
    const list = events ?? []
    const d = list.filter((e) => e.actual)
    const s = list.filter((e) => !e.actual)
    return { next: s.find((e) => e.epoch >= now) ?? null, soon: s, done: d }
  }, [events, now])

  const untilText = (epoch: number): string => {
    const secs = Math.round(epoch - now)
    if (secs <= 0) return 'الآن'
    if (secs < 3600) return `بعد ${num(Math.max(1, Math.round(secs / 60)))} دقيقة`
    const h = Math.floor(secs / 3600)
    const m = Math.round((secs % 3600) / 60)
    return `بعد ${num(h)} ساعة${m ? ` و${num(m)} دقيقة` : ''}`
  }

  const row = (e: CalEvent) => {
    const [label, color, icon] = impactOf(e.impact)
    return (
      <div key={e.id} style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '5px 2px', borderBottom: '1px solid var(--glassb)', minWidth: 0 }}>
        <span className="num" style={{ flex: 'none', fontSize: 13, fontWeight: 700 }}>{clock(e.epoch)}</span>
        <span style={{ flex: 'none', color }}>{icon}</span>
        <span style={{ flex: 1, minWidth: 0, fontSize: 12.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={e.title}>
          {e.title}
        </span>
        <span className="num dim" style={{ flex: 'none', fontSize: 11 }}>
          {e.actual ? `الفعلي ${e.actual}` : untilText(e.epoch)}
        </span>
        {e.actual && compareActual(e.actual, e.forecast) ? (
          <span style={{ flex: 'none', fontSize: 10.5, color: 'var(--accent)' }}>
            📐 {compareActual(e.actual, e.forecast)}
          </span>
        ) : null}
        <span className="num dim" style={{ flex: 'none', fontSize: 11 }}>
          {e.forecast ? `متوقّع ${e.forecast}` : ''}{e.previous ? ` · سابق ${e.previous}` : ''}
        </span>
        <span style={{ flex: 'none', fontSize: 10.5, color }}>{label}</span>
      </div>
    )
  }

  return (
    <div className="section" style={{ height: '100%', minWidth: 0, display: 'grid', gridTemplateRows: 'auto auto 1fr', gap: 8, overflow: 'hidden' }}>

      {/* المفاتيح */}
      <div className="scard" style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 12px', flexWrap: 'wrap' }}>
        <span className="st" style={{ fontSize: 11.5 }}>العملة</span>
        {CURRENCIES.map((c) => (
          <button key={c} style={chip(c === currency)} onClick={() => setCurrency(c)}>
            {c === 'ALL' ? 'الكل' : c}
          </button>
        ))}
        <span className="feedsep" />
        <span className="st" style={{ fontSize: 11.5 }}>الأهمية</span>
        {LEVELS.map(([k, t]) => (
          <button key={k} style={chip(k === level)} onClick={() => setLevel(k)}>{t}</button>
        ))}
        <span className="dim" style={{ fontSize: 11, marginInlineStart: 'auto' }}>
          المصدر: تقويم المنصّة عبر جسر التداول · يتحدّث كل {num(30)} ثانية
        </span>
      </div>

      {/* الحدث القادم */}
      <div className="scard" style={{ padding: '8px 14px', borderColor: next ? impactOf(next.impact)[1] : 'var(--glassb)' }}>
        {!available ? (
          <span style={{ color: 'var(--red)', fontSize: 13 }}>جسر التداول غير موجود — شغّل الإكسبرت على المنصّة.</span>
        ) : next ? (
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap', minWidth: 0 }}>
            <span style={{ fontSize: 15, fontWeight: 800, color: impactOf(next.impact)[1] }}>
              {impactOf(next.impact)[2]} {untilText(next.epoch)}
            </span>
            <span style={{ fontSize: 14, fontWeight: 700 }}>{next.title}</span>
            <span className="num dim" style={{ fontSize: 12 }}>{clock(next.epoch)} · {next.currency}</span>
            {next.forecast ? <span className="num dim" style={{ fontSize: 12 }}>متوقّع {next.forecast}</span> : null}
            {next.previous ? <span className="num dim" style={{ fontSize: 12 }}>سابق {next.previous}</span> : null}
          </div>
        ) : (
          <span className="dim" style={{ fontSize: 13 }}>
            {events === null ? 'جارِ القراءة…' : 'ما ضل حدث اليوم بهالفلتر.'}
          </span>
        )}
      </div>

      {/* الأجندة + الأخبار */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.4fr) minmax(0,1fr)', gap: 8, minHeight: 0 }}>

        <div className="scard" style={{ padding: 0, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
          <div className="st" style={{ fontSize: 11.5, padding: '4px 10px', borderBottom: '1px solid var(--glassb)', flex: 'none' }}>
            أجندة اليوم — {events ? num(events.length) : '…'} حدث
          </div>
          <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '2px 10px' }}>
            {events === null ? <span className="dim" style={{ fontSize: 12 }}>جارِ القراءة…</span>
              : events.length === 0 ? <span className="dim" style={{ fontSize: 12 }}>ما في أحداث بهالفلتر اليوم.</span>
                : (<>
                  {soon.length ? <div className="dim" style={{ fontSize: 10.5, margin: '6px 0 2px' }}>— القادم —</div> : null}
                  {soon.map(row)}
                  {done.length ? <div className="dim" style={{ fontSize: 10.5, margin: '8px 0 2px' }}>— صدر اليوم —</div> : null}
                  {done.map(row)}
                </>)}
          </div>
        </div>

        <div className="scard" style={{ padding: 0, display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
          <div className="st" style={{ fontSize: 11.5, padding: '4px 10px', borderBottom: '1px solid var(--glassb)', flex: 'none' }}>
            آخر الأخبار — {news ? num(news.length) : '…'}
          </div>
          <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: '2px 10px' }}>
            {news === null ? <span className="dim" style={{ fontSize: 12 }}>جارِ القراءة…</span>
              : news.length === 0 ? <span className="dim" style={{ fontSize: 12 }}>ما وصل خبر بعد — الجسر ما كتب شيئًا.</span>
                : news.map((n) => (
                  <div key={n.id} style={{ padding: '5px 2px', borderBottom: '1px solid var(--glassb)', minWidth: 0 }}>
                    {n.headline_ar ? (
                      <div style={{ fontSize: 12.5, lineHeight: 1.6 }}>{n.headline_ar}</div>
                    ) : null}
                    {/* الأصل يبقى ظاهرًا تحت الترجمة — أمانة للمصدر، وحتى يُرى
                        ما لم يُترجَم بعد بدل أن يختفي الخبر أو يُخترع نصّه. */}
                    <div style={{ fontSize: n.headline_ar ? 10.5 : 12, lineHeight: 1.5, direction: 'ltr', textAlign: 'left', color: n.headline_ar ? 'var(--dim)' : undefined }}>
                      {n.headline}
                    </div>
                    {n.summary ? (
                      <div className="dim" style={{ fontSize: 10.5, lineHeight: 1.5, marginTop: 2, direction: 'ltr', textAlign: 'left', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                        {n.summary}
                      </div>
                    ) : null}
                    <div className="dim" style={{ fontSize: 10.5, marginTop: 3, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                      {n.impact ? (
                        <span style={{ padding: '0 5px', borderRadius: 5, border: `1px solid ${impactOf(n.impact)[1]}`, color: impactOf(n.impact)[1] }}>
                          {impactOf(n.impact)[0]}
                        </span>
                      ) : null}
                      {n.relevance ? (
                        <span style={{ padding: '0 5px', borderRadius: 5, border: '1px solid var(--glassb)' }}
                          title="سبب وصول هذا الخبر إليك">
                          {reasonAr(n.relevance)}
                        </span>
                      ) : null}
                      <span className="num">{n.published_at ? ageText(n.published_at, now) : 'بلا وقت'}</span>
                      <span>· {sourceAr(n.source) || 'بلا مصدر'}</span>
                      {n.headline_ar ? null : <span>· لم يُترجَم بعد</span>}
                    </div>
                  </div>
                ))}
          </div>
        </div>
      </div>
    </div>
  )
}
