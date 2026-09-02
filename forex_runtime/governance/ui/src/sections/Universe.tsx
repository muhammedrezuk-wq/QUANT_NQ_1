// ═══ قسم أسمر — الكون (الطبقة 1 بالخريطة الهندسية) ═══
// من يدخل؟ النواة بقوانينها الكاملة · الحلقة الخارجية بأقفالها الثلاثة · المصادر الحية.
import { useEffect, useState } from 'react'
import { useStore } from '../core/store'

interface Ring { core: string[]; outer: string[] }
interface Metrics {
  amount24_usd?: number | null; spread_ticks?: number | null
  daily_range_pct?: number | null; asset_class?: string | null
  market_segment?: string | null; bid?: number | null
  manual_allow_bypassed?: string[]
}
interface RejectedRow { symbol?: string; reasons?: string[]; metrics?: Metrics }
interface RejectedState { rejected?: RejectedRow[]; count?: number }
interface Thresholds {
  core_liquidity_usd_24h?: number; outer_liquidity_usd_24h?: number
  max_spread_ticks?: number; max_daily_range_pct?: number
  core_target_count?: number; outer_target_count?: number
}
interface MemberRow { symbol?: string; ring?: string } // eslint-disable-line
interface Snapshot {
  thresholds?: Thresholds; rejected?: RejectedRow[]
  core?: Metrics[]; outer?: Metrics[]; universe_version?: number
  scan_finished_at?: number; status?: string; error?: string
}
interface OverrideRow { decision?: string; scope?: string; reason?: string; operator?: string }

// أسباب الرفض التي ينشرها #1001 — بالعربي، والمجهول يبقى كما هو (لا قناع)
const REJECT_AR: Record<string, string> = {
  NOT_FUTURES_USDT: 'ليس عقدًا بتسوية USDT',
  FUTURES_METADATA_UNKNOWN: 'بيانات العقد ناقصة',
  MANUAL_DENY: 'إخراج يدويّ بأمرك',
  ENTRY_RESILIENCE: 'لم يصمد على شرط الدخول',
  LOW_AMOUNT: 'حجم تداول دون الحدّ',
  LOW_LIQUIDITY: 'سيولة دون الحدّ',
  SPREAD_TOO_WIDE: 'فرق السعر واسع',
  NEW_LISTING: 'إدراج حديث — بلا تاريخ كافٍ',
}
const rejectAr = (r: string) => REJECT_AR[r] ?? r

// ٢٠٢٦-٠٨-٢٩ — أمر المالك: «لازم أشوف على لوحة الكون ليش ما دخلت، ليش منتظرة —
// بدّك تخليني أعمى؟». اللقطة `crypto.universe.snapshot.state` كانت تحمل **كل
// شيء** (العتبات · المقاييس لكل رمز · أسباب الرفض · الدوران) واللوحة تعرض
// اسمين وحلقتين. فلا يُعرض السبب مجرّدًا: يُعرض **القفل الذي كُسر، برقمه
// المقيس مقابل عتبته المعلَنة** — فالسبب بلا رقم رأيٌ لا دليل.
const fmtUsd = (v?: number | null) =>
  v == null ? '—' : v >= 1e9 ? `${(v / 1e9).toFixed(2)} مليار$`
    : v >= 1e6 ? `${(v / 1e6).toFixed(1)} مليون$` : `${Math.round(v).toLocaleString('en-US')}$`

/** لكل سبب: اسم القفل، والرقم المقيس، والعتبة — بالعربي وبأرقام لاتينية. */
function lockDetail(reason: string, m?: Metrics, th?: Thresholds): { lock: string; got: string; need: string } {
  switch (reason) {
    case 'LIQUIDITY_BELOW_OUTER':
      return { lock: '① السيولة (حجم 24 ساعة)', got: fmtUsd(m?.amount24_usd), need: `≥ ${fmtUsd(th?.outer_liquidity_usd_24h)}` }
    case 'LIQUIDITY_UNKNOWN':
      return { lock: '① السيولة', got: 'لم تصل', need: `≥ ${fmtUsd(th?.outer_liquidity_usd_24h)}` }
    case 'SPREAD_TOO_WIDE':
      return { lock: '② فرق السعر (بالتكّات)', got: `${m?.spread_ticks ?? '—'} تكّة`, need: `≤ ${th?.max_spread_ticks ?? '—'} تكّات` }
    case 'SPREAD_UNKNOWN':
      return { lock: '② فرق السعر', got: 'لا عرض/طلب', need: `≤ ${th?.max_spread_ticks ?? '—'} تكّات` }
    case 'TICK_SIZE_UNKNOWN':
      return { lock: '② فرق السعر', got: 'حجم التكّة مجهول', need: 'مواصفات العقد' }
    case 'RANGE_TOO_WIDE':
      return { lock: '③ مدى اليوم', got: `${m?.daily_range_pct?.toFixed?.(2) ?? '—'}%`, need: `≤ ${th?.max_daily_range_pct ?? '—'}%` }
    case 'DAILY_RANGE_UNKNOWN':
      return { lock: '③ مدى اليوم', got: 'لم يصل', need: `≤ ${th?.max_daily_range_pct ?? '—'}%` }
    case 'NOT_FUTURES_USDT':
      return { lock: 'حدّ الكون الصلب', got: m?.market_segment ?? 'غير USDT', need: 'عقد تسويته USDT' }
    case 'FUTURES_METADATA_UNKNOWN':
      return { lock: 'حدّ الكون الصلب', got: 'بيانات العقد ناقصة', need: 'عملة العقد والتسوية معلومتان' }
    case 'NON_CRYPTO':
      return { lock: 'صنف الأصل', got: 'ليس عملة رقميّة', need: 'أصل رقميّ أصيل' }
    case 'ASSET_CLASS_UNKNOWN':
      return { lock: 'صنف الأصل', got: 'مجهول', need: 'أصل رقميّ أصيل' }
    case 'MANUAL_DENY':
      return { lock: 'أمرك', got: 'أخرجتَه بيدك', need: 'اضغط «إلغاء التجاوز» ليعود للفرز' }
    default:
      return { lock: reason, got: '—', need: '—' }
  }
}

interface AtomRow { id: number; state?: string; health?: { state?: string; message?: string } | null }

const card: React.CSSProperties = { border: '1px solid var(--glassb)', background: 'var(--glass)', borderRadius: 12, padding: 14 }
const dim: React.CSSProperties = { color: 'var(--dim)', fontSize: 12 }

const SOURCES: [number, string, string][] = [
  [2620, 'مصدر MEXC — WebSocket', 'تيك · عمق 100 · صفقات'],
  [2621, 'مصدر MEXC — REST', 'شموع · OI · تمويل'],
  [2622, 'مصدر Binance', 'علاوة · OI عالمي'],
  [1001, 'مدير الكون', 'الفرز والحلقتان'],
  [1002, 'تغذية السوق', 'بث الرموز المقبولة'],
  [2708, 'سجل الرموز', 'دقة التكة لكل عقد'],
]

export default function Universe() {
  const [ring, setRing] = useState<Ring>({ core: [], outer: [] })
  const [atoms, setAtoms] = useState<Record<number, AtomRow>>({})
  // ٢٠٢٦-٠٨-٢٩ — أمر المالك: «وين العملات المرفوضة · بدي أدخل عملة أو أخرجها».
  // المحرّك كان يملك الثلاثة كاملة والواجهة لا تعرض واحدة منها:
  //   • #1001 ينشر crypto.universe.rejected.state بالأسباب — ما كان يُعرض.
  //   • /gov/universe/override ينفذ ALLOW/DENY/NEUTRAL — بلا زرّ.
  //   • /gov/universe/scan يطلب مسحًا فوريًّا — بلا زرّ.
  // «حرّاسٌ بلا مفاتيح = فشل»: القدرة الموجودة بلا مقبض كأنّها غير موجودة.
  const rejectedState = useStore((s) => s.streams['crypto.universe.rejected.state']) as RejectedState | undefined
  const snap = useStore((s) => s.streams['crypto.universe.snapshot.state']) as Snapshot | undefined
  const th = snap?.thresholds
  const rejected = snap?.rejected ?? rejectedState?.rejected ?? []
  // مُدخَلة بأمر المالك متجاوزةً أقفالًا — تُعلَن بأقفالها لا تُخبَّأ
  const forced = [...(snap?.core ?? []), ...(snap?.outer ?? [])]
    .filter((r) => (r as Metrics & { manual_allow_bypassed?: string[] }).manual_allow_bypassed?.length)
  const [overrides, setOverrides] = useState<Record<string, OverrideRow>>({})
  const [sym, setSym] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const loadOverrides = () =>
    fetch('/gov/universe/overrides', { cache: 'no-store' }).then(r => r.json())
      .then((d: { overrides?: Record<string, OverrideRow> }) => setOverrides(d.overrides || {}))
      .catch(() => {})

  useEffect(() => {
    const load = () => {
      fetch('/gov/mexc/universe', { cache: 'no-store' }).then(r => r.json()).then((u: Ring) => setRing({ core: u.core || [], outer: u.outer || [] })).catch(() => {})
      fetch('/gov/atoms', { cache: 'no-store' }).then(r => r.json())
        .then((d: { atoms?: AtomRow[] }) => {
          const m: Record<number, AtomRow> = {}
          for (const a of d.atoms || []) m[a.id] = a
          setAtoms(m)
        }).catch(() => {})
      void loadOverrides()
    }
    load()
    const h = window.setInterval(load, 20000)
    return () => window.clearInterval(h)
  }, [])

  // الرمز يُطبَّع لصيغة عقود MEXC (BTC_USDT) قبل الإرسال — الخادم يرفض غيرها
  const normalized = sym.trim().toUpperCase().replace(/[\s-]+/g, '_')
  const validSym = /^[A-Z0-9]+_[A-Z0-9]+$/.test(normalized)

  const sendOverride = async (decision: 'ALLOW' | 'DENY' | 'NEUTRAL') => {
    if (!validSym || busy) return
    const verb = decision === 'ALLOW' ? 'إدخال' : decision === 'DENY' ? 'إخراج' : 'إلغاء التجاوز على'
    if (!window.confirm(`${verb} ${normalized} — تأكيد؟`)) return
    setBusy(true); setMsg('')
    try {
      const r = await fetch('/gov/universe/override', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: normalized, decision, reason: 'أمر المالك من اللوحة', operator: 'owner' }),
      })
      const j = await r.json().catch(() => ({}))
      setMsg(r.ok ? `تمّ: ${verb} ${normalized}` : `تعذّر: ${(j as { error?: string }).error || r.status}`)
      if (r.ok) { setSym(''); await loadOverrides() }
    } catch { setMsg('تعذّر الوصول لخادم الحوكمة') } finally { setBusy(false) }
  }

  const rescan = async () => {
    if (busy) return
    if (!window.confirm('طلب مسح فوريّ لكون MEXC كامل — تأكيد؟')) return
    setBusy(true); setMsg('')
    try {
      const r = await fetch('/gov/universe/scan', { method: 'POST' })
      setMsg(r.ok ? 'طُلب المسح — النتيجة تصل خلال دورة الفرز' : `تعذّر: ${r.status}`)
    } catch { setMsg('تعذّر الوصول لخادم الحوكمة') } finally { setBusy(false) }
  }

  const hstate = (id: number) => atoms[id]?.health?.state
  const hmsg = (id: number) => atoms[id]?.health?.message || ''

  return (
    <div className="section" style={{ display: 'grid', gap: 14, alignContent: 'start' }}>
      <div style={{ ...card, display: 'flex', gap: 16, flexWrap: 'wrap', alignItems: 'center' }}>
        <strong style={{ color: 'var(--accent)' }}>الكون — من يدخل؟</strong>
        <span style={dim}>القراءة حق كل عقد · الصفقة امتياز الفلاتر (الخريطة الهندسية)</span>
        <span style={{ flexGrow: 1 }} />
        <span>النواة <b className="num" style={{ color: 'var(--green)' }}>{ring.core.length}</b></span>
        <span>الخارجية <b className="num" style={{ color: 'var(--amber)' }}>{ring.outer.length}</b></span>
        <span style={dim}>تحديث كل ٢٠ث</span>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 14 }}>
        <div style={card}>
          <div style={{ fontWeight: 700, marginBottom: 8, color: 'var(--green)' }}>النواة — سهر كامل بكل الحواس ({ring.core.length})</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {ring.core.map(s => (
              <span key={s} style={{ border: '1px solid var(--glassb)', borderRadius: 8, padding: '3px 9px', fontSize: 12 }}>{s}</span>
            ))}
            {!ring.core.length ? <span style={dim}>الفرز يجري…</span> : null}
          </div>
        </div>
        <div style={card}>
          <div style={{ fontWeight: 700, marginBottom: 8, color: 'var(--amber)' }}>الحلقة الخارجية — ثلاثة أقفال ({ring.outer.length})</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {ring.outer.map(s => (
              <span key={s} style={{ border: '1px solid var(--glassb)', borderRadius: 8, padding: '3px 9px', fontSize: 12 }}>{s}</span>
            ))}
            {!ring.outer.length ? <span style={dim}>لا أعضاء حالياً</span> : null}
          </div>
          <div style={{ ...dim, marginTop: 8 }}>① بوابة الجلسة بسبريدها الحقيقي · ② رتبة ألف كاملة · ③ نصف الحجم</div>
        </div>
      </div>

      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>بيدك — إدخال عملة أو إخراجها</div>
        <div style={{ ...dim, marginBottom: 10 }}>
          يعلو قرارك على الفرز الآليّ. <b>إدخال</b> يضمّ الرمز ولو رفضه الفرز ·
          <b> إخراج</b> يمنعه ولو قبله · <b>إلغاء التجاوز</b> يعيده لحكم الفرز.
          صيغة عقود MEXC: <code>BTC_USDT</code>. وكلّ زرّ يسأل تأكيدًا قبل التنفيذ.
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input className="search" style={{ maxWidth: 220 }} placeholder="مثال: DOGE_USDT"
            value={sym} onChange={e => setSym(e.target.value)} />
          <button className="btn" disabled={!validSym || busy} onClick={() => void sendOverride('ALLOW')}>إدخال</button>
          <button className="btn" disabled={!validSym || busy} onClick={() => void sendOverride('DENY')}>إخراج</button>
          <button className="btn" disabled={!validSym || busy} onClick={() => void sendOverride('NEUTRAL')}>إلغاء التجاوز</button>
          <span style={{ flexGrow: 1 }} />
          <button className="btn" disabled={busy} onClick={() => void rescan()}>مسح الكون الآن</button>
        </div>
        {sym && !validSym ? <div style={{ ...dim, marginTop: 6, color: 'var(--amber)' }}>الصيغة المطلوبة قاعدة_عملة — مثل BTC_USDT</div> : null}
        {msg ? <div style={{ ...dim, marginTop: 6, color: 'var(--accent)' }}>{msg}</div> : null}
        {/* NEUTRAL لا يُحذف من ملفّ التجاوزات بل يُخزَّن بقرار NEUTRAL. فكان
            يبقى ظاهرًا «مُدخَلًا» بعد إلغائه — أمر المالك: «لمّا أشيلها تنشال».
            يُرشَّح هنا: التجاوز القائم هو ALLOW أو DENY وحدهما. */}
        {(() => {
          const active = Object.entries(overrides)
            .filter(([, o]) => o.decision === 'ALLOW' || o.decision === 'DENY')
          return (
            <>
              <div style={{ marginTop: 12, fontWeight: 700 }}>تجاوزاتك الحالية ({active.length})</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
                {active.map(([s, o]) => (
                  <span key={s} style={{ border: '1px solid var(--glassb)', borderRadius: 8, padding: '3px 9px', fontSize: 12,
                    color: o.decision === 'DENY' ? 'var(--red)' : 'var(--green)' }}>
                    {s} · {o.decision === 'DENY' ? 'مُخرَج بأمرك' : 'مُدخَل بأمرك'}
                  </span>
                ))}
                {!active.length ? <span style={dim}>لا تجاوزات — الكون كلّه بحكم الفرز الآليّ</span> : null}
              </div>
            </>
          )
        })()}
      </div>

      {/* الأقفال بأرقامها المعلَنة — «لازم نعرف كل شي من نظرة على اللوحة» */}
      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8 }}>الأقفال الثلاثة — بأرقامها الحيّة</div>
        <div style={{ ...dim, marginBottom: 10 }}>
          هذه العتبات من إعداد <b>#1001</b> نفسه لا من ورقة — تصل مع كل لقطة فرز.
          والمرفوضة أدناه يُعرض لكلٍّ منها <b>القفل الذي كسره ورقمه المقيس مقابل عتبته</b>.
        </div>
        {!th ? (
          <div style={dim}>لم تصل لقطة فرز بعد — اضغط «مسح الكون الآن» فوق.</div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 8 }}>
            {([
              ['① سيولة الحلقة الخارجية', `${fmtUsd(th.outer_liquidity_usd_24h)} فأكثر / 24س`],
              ['① سيولة النواة', `${fmtUsd(th.core_liquidity_usd_24h)} فأكثر / 24س`],
              ['② أقصى فرق سعر', `${th.max_spread_ticks ?? '—'} تكّات`],
              ['③ أقصى مدى يوميّ', `${th.max_daily_range_pct ?? '—'}%`],
              ['مقاعد النواة', `${th.core_target_count ?? '—'}`],
              ['مقاعد الحلقة الخارجية', `${th.outer_target_count ?? '—'}`],
            ] as [string, string][]).map(([k, v]) => (
              <div key={k} style={{ border: '1px solid var(--glassb)', borderRadius: 8, padding: '6px 9px' }}>
                <div style={{ ...dim, fontSize: 10.5 }}>{k}</div>
                <div className="num" style={{ fontSize: 13, fontWeight: 600 }}>{v}</div>
              </div>
            ))}
          </div>
        )}
        {snap?.scan_finished_at ? (
          <div style={{ ...dim, marginTop: 8 }}>
            آخر فرز: نسخة الكون {snap.universe_version ?? '—'} · فُحص {rejected.length + (snap.core?.length ?? 0) + (snap.outer?.length ?? 0)} عقدًا
          </div>
        ) : null}
      </div>

      {forced.length ? (
        <div style={{ ...card, borderColor: 'var(--amber)' }}>
          <div style={{ fontWeight: 700, marginBottom: 8, color: 'var(--amber)' }}>
            مُدخَلة بأمرك فوق الفرز ({forced.length})
          </div>
          <div style={{ ...dim, marginBottom: 8 }}>
            دخلت بقرارك لا بالفرز. <b>الأقفال التي تجاوزتها معروضة كاملة</b> — قرارك يعلو،
            لكن ما قاله الفرز لا يُخفى عنك.
          </div>
          <div style={{ display: 'grid', gap: 6 }}>
            {forced.map((r, i) => {
              const m = r as Metrics & { symbol?: string; manual_allow_bypassed?: string[] }
              return (
                <div key={m.symbol ?? i} style={{ border: '1px solid var(--glassb)', borderRadius: 8, padding: '6px 9px' }}>
                  <b style={{ fontSize: 13 }}>{m.symbol}</b>
                  {(m.manual_allow_bypassed || []).map((rs) => {
                    const d = lockDetail(rs, m, th)
                    return (
                      <div key={rs} style={{ ...dim, fontSize: 11.5, marginTop: 3 }}>
                        تجاوز {d.lock} — المقيس <b className="num">{d.got}</b> · المطلوب <b className="num">{d.need}</b>
                      </div>
                    )
                  })}
                </div>
              )
            })}
          </div>
        </div>
      ) : null}

      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 8, color: 'var(--red)' }}>
          المرفوضة — ولماذا بالضبط ({rejected.length})
        </div>
        <div style={{ ...dim, marginBottom: 10 }}>
          كل عقد فحصه <b>#1001</b> ولم يدخل، <b>بالقفل الذي كسره ورقمه</b>.
          حدّ ثابت في كود الاستراتيجية: <b>عقود التسوية بـUSDT وحدها مؤهَّلة</b>.
        </div>
        {rejected.length ? (
          <div style={{ display: 'grid', gap: 6, maxHeight: 420, overflow: 'auto' }}>
            {rejected.map((r, i) => (
              <div key={`${r.symbol}-${i}`} style={{ border: '1px solid var(--glassb)', borderRadius: 8, padding: '6px 9px' }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                  <b style={{ fontSize: 13, minWidth: 130 }}>{r.symbol}</b>
                  <span style={{ ...dim, fontSize: 11 }}>
                    سيولة {fmtUsd(r.metrics?.amount24_usd)} · فرق {r.metrics?.spread_ticks ?? '—'} تكّة · مدى {r.metrics?.daily_range_pct?.toFixed?.(2) ?? '—'}%
                  </span>
                </div>
                {(r.reasons || []).map((rs) => {
                  const d = lockDetail(rs, r.metrics, th)
                  return (
                    <div key={rs} style={{ fontSize: 11.5, marginTop: 3, color: 'var(--red)' }}>
                      ✗ {d.lock} — المقيس <b className="num">{d.got}</b> · المطلوب <b className="num">{d.need}</b>
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        ) : (
          <div style={dim}>
            لم تصل قائمة المرفوضات بعد — تُنشر مع كل دورة فرز يقوم بها #1001.
            اضغط «مسح الكون الآن» فوق لطلبها حالًا.
          </div>
        )}
      </div>

      <div style={card}>
        <div style={{ fontWeight: 700, marginBottom: 10 }}>المصادر الحية</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 10 }}>
          {SOURCES.map(([id, name, role]) => {
            const st = hstate(id)
            const color = st === 'healthy' ? 'var(--green)' : st === 'degraded' ? 'var(--amber)' : st ? 'var(--red)' : 'var(--dim)'
            return (
              <div key={id} style={{ border: '1px solid var(--glassb)', borderRadius: 10, padding: 10 }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <span style={{ width: 8, height: 8, borderRadius: 8, background: color, display: 'inline-block' }} />
                  <b style={{ fontSize: 13 }}>{id} · {name}</b>
                </div>
                <div style={{ ...dim, marginTop: 4 }}>{role}</div>
                <div style={{ ...dim, marginTop: 2 }}>{hmsg(id).slice(0, 72)}</div>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
