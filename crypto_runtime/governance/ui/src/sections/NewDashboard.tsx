// اللوحة الجديدة — البناء من الصفر حول أربعة أسئلة (أمر المالك ٢٠٢٦-٠٨-٢٣)
// القواعد الـ21 من ورقة إعادة البناء + رؤية المالك المسجّلة حرفيًا.
// ⛔ الواجهة لا تحسب رقمًا واحدًا — كل معروض وصل بالحدث.
// ٢٠٢٦-٠٨-٢٩ (ختم NQ): أنماط هذه اللوحة (`dl-*`) كانت في `styles-dashboard.css`
// **بلا استيراد من أي ملف** — فلم تدخل حزمة البناء إطلاقًا، فظهر التبويب نصًّا
// خامًا بلا ألوان ولا تقطيع (مقيس: صفر ذكر لـ`dl-` في built/assets/*.css).
// يُستورَد هنا بنفس نمط `CryptoDashboard.tsx` مع `crypto-dashboard.css`.
import '../styles-dashboard.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../core/store'
import { dangerCommand } from '../core/commands'
import { useTwinAccounts } from '../components/AccountsBar'
import { isWaitingMessage } from '../core/i18n'

// ═══════════════════════════════════════════════════════════
// الأدوات المشتركة
// ═══════════════════════════════════════════════════════════

const num = (n?: number | null, suffix = '') =>
  n == null ? 'مجهول' : `${n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 1 })}${suffix}`

const pct = (n?: number | null) =>
  n == null ? 'مجهول' : `${n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 1 })}٪`

type Color4 = 'green' | 'amber' | 'red' | 'grey'
const C: Record<Color4, string> = {
  green: 'var(--dl-green)',
  amber: 'var(--dl-amber)',
  red: 'var(--dl-red)',
  grey: 'var(--dl-grey)',
}

// ═══════════════════════════════════════════════════════════
// ١ — هل أنا بأمان؟ (الشريط العلوي)
// ═══════════════════════════════════════════════════════════

interface SafetyStatus {
  color: Color4
  label: string
  detail: string
}

function useSafety(): SafetyStatus {
  const risk = useStore((s) => s.risk)
  const gate = useStore((s) => s.gate)
  const execution = useStore((s) => s.execution)
  const conn = useStore((s) => s.conn)

  return useMemo(() => {
    if (conn !== 'live') return { color: 'red', label: 'انقطع الاتصال', detail: 'النواة غير متصلة أو لا يصل بثّها' }
    if (risk?.halted || execution?.halted) return { color: 'red', label: 'متوقف', detail: 'التداول موقوف' }
    if (gate?.status !== 'LIVE') return { color: 'amber', label: 'البوّابة مقفلة', detail: 'التداول غير مسموح حتى يثبت فتح البوّابة' }
    return { color: 'green', label: 'آمن — التداول مسموح', detail: 'كل الحرّاس سليمون' }
  }, [risk, gate, execution, conn])
}

// ═══════════════════════════════════════════════════════════
// ٢ — الروم الحيّ (الشريط الثاني)
// ═══════════════════════════════════════════════════════════

const SECTION_AR: Record<string, string> = {
  '150': 'التحليل', '200': 'البنية', '250': 'السيولة', '300': 'الإحصاء',
  '350': 'الاحتمالات', '400': 'الاستراتيجيات',
}

const SIGNAL_AR: Record<string, [string, Color4]> = {
  up: ['مايل للشراء', 'green'], down: ['مايل للبيع', 'red'],
  sideways: ['محايد', 'grey'], unknown: ['مجهول', 'grey'],
}

function RoomBar() {
  const rooms = useStore((s) => s.room)
  const entries = Object.entries(rooms)
  if (!entries.length) {
    return <div className="dl-waiting">الروم — بانتظار أول بطاقة قسم…</div>
  }
  return (
    <div className="dl-room-bar">
      {entries.slice(0, 2).map(([key, room]) => {
        const sig = SIGNAL_AR[room.signal ?? 'unknown'] ?? SIGNAL_AR.unknown
        const dir = room.direction ?? null
        const pos = dir == null ? 50 : Math.max(0, Math.min(100, (dir + 100) / 2))
        const sections = room.sections ?? []
        const present = sections.filter((r) => r.section_id)
        const missing = room.sections_missing ?? []
        return (
          <div key={key} className="dl-room-card">
            <div className="dl-room-head">
              <span className="dl-room-symbol">{room.symbol}</span>
              <span style={{ color: C[sig[1]], fontWeight: 800, fontSize: 16 }}>{sig[0]}</span>
              <span className="dl-room-meta">
                اتجاه <b style={{ color: dir == null ? C.grey : dir > 0 ? C.green : dir < 0 ? C.red : C.grey }}>{num(dir)}</b>
                {' · '}ثقة <b>{room.confidence_defined === false ? 'غير معرّفة' : pct(room.confidence)}</b>
                {' · '}جاهزية <b>{pct(room.readiness_pct)}</b>
              </span>
            </div>
            <div className="dl-dir-bar">
              <div className="dl-dir-marker" style={{ right: `calc(${pos}% - 2px)` }} />
            </div>
            <div className="dl-sections">
              {present.map((row) => {
                const ready = row.state === 'READY'
                const sc: Color4 = row.state === 'STALE' ? 'red' : row.state === 'ANALYZING' ? 'amber' : ready ? 'green' : 'grey'
                return (
                  <span key={row.section_id} className="dl-chip" data-color={sc}>
                    {SECTION_AR[row.section_id] ?? row.section_id}
                    <b>{pct(row.readiness_pct)}</b>
                    {row.direction != null && (
                      <span style={{ color: row.direction > 0 ? C.green : row.direction < 0 ? C.red : C.grey }}>
                        {row.direction > 0 ? '▲' : row.direction < 0 ? '▼' : '■'}
                      </span>
                    )}
                  </span>
                )
              })}
              {missing.map((id) => (
                <span key={id} className="dl-chip dl-chip-waiting">{SECTION_AR[id] ?? id} · بانتظار</span>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
// ٣ — لماذا لست في صفقة؟ (الموانع)
// ═══════════════════════════════════════════════════════════

interface Blocker {
  id: string
  label: string
  value: string
  threshold: string
  color: Color4
}

// حواجز النظام على مستوى السلسلة — تُقرأ من صحّة الذرات (REST، ما تحتاج WS):
// تغذية واقعة أو تحجيم مقطوع ما بيقدر يوصل «كل الموانع راحلة»
const BARRIER_ATOMS: Array<{ id: number; label: string }> = [
  { id: 613, label: 'تغذية السوق' },
  { id: 622, label: 'تغذية cTrader (FIX)' },
  { id: 618, label: 'تغذية جسر المنصة' },
  { id: 619, label: 'حالة الحساب' },
  { id: 513, label: 'التحجيم' },
  { id: 708, label: 'سجلّ الرموز' },
  { id: 552, label: 'بوّابة التنفيذ' },
]

function useBlockers(): Blocker[] {
  const rooms = useStore((s) => s.room)
  const decision = useStore((s) => s.decision)
  const risk = useStore((s) => s.risk)
  const atoms = useStore((s) => s.atoms)

  return useMemo(() => {
    const blockers: Blocker[] = []
    // حواجز النظام من صحّة الذرات (تتقدّم على أي شي — تغذية/تحجيم/بوّابة)
    for (const b of BARRIER_ATOMS) {
      const a = atoms[b.id]
      if (!a?.health) continue
      const hs = (a.health.state ?? '').toLowerCase()
      const msg = a.health.message ?? ''
      if (hs === 'unhealthy' || hs === 'error') {
        blockers.push({ id: `bar-${b.id}`, label: b.label, value: msg.slice(0, 60) || 'معطوب', threshold: `الذرّة ${b.id}`, color: 'red' })
      } else if (hs === 'degraded' && !isWaitingMessage(msg)) {
        blockers.push({ id: `bar-${b.id}`, label: b.label, value: msg.slice(0, 60) || 'متعثّر', threshold: `الذرّة ${b.id}`, color: 'amber' })
      }
    }
    // من الروم — كل قسم NOT_READY هو مانع
    for (const [, room] of Object.entries(rooms)) {
      for (const sec of room.sections ?? []) {
        if (sec.state && sec.state !== 'READY' && sec.readiness_pct != null) {
          blockers.push({
            id: sec.section_id,
            label: SECTION_AR[sec.section_id] ?? sec.section_id,
            value: `جاهزيته ${pct(sec.readiness_pct)}`,
            threshold: sec.required_depth != null ? `يحتاج عمق ${num(sec.required_depth)}` : 'يحتاج نضوج',
            color: sec.readiness_pct > 70 ? 'amber' : 'grey',
          })
        }
      }
      for (const id of room.sections_missing ?? []) {
        blockers.push({
          id, label: SECTION_AR[id] ?? id,
          value: 'بانتظار', threshold: 'ما وصلت بطاقته بعد',
          color: 'grey',
        })
      }
    }
    // من المخاطر
    if (risk?.halted) {
      blockers.push({ id: 'halt', label: 'إيقاف عام', value: 'موقوف', threshold: 'بقرار المالك', color: 'red' })
    }
    return blockers.slice(0, 10)
  }, [rooms, decision, risk, atoms])
}

function WhyNotPanel() {
  const blockers = useBlockers()
  const rooms = useStore((s) => s.room)
  const hasRooms = Object.keys(rooms).length > 0
  // «جاهز» ما ينقال إلا إذا وصلنا حكم (روم) ومافي مانع — وإلا نعلن الانتظار
  const allReady = blockers.length === 0 && hasRooms
  return (
    <div className="dl-panel">
      <div className="dl-panel-title">لماذا لست في صفقة؟</div>
      {allReady ? (
        <div className="dl-all-clear" style={{ color: C.green }}>
          كل الموانع راحلة — النظام جاهز للدخول إذا جاء قرار
        </div>
      ) : blockers.length === 0 ? (
        <div className="dl-all-clear dim">
          بانتظار أول بطاقة قسم — ما في حكم بعد
        </div>
      ) : (
        <div className="dl-blockers">
          {blockers.map((b) => (
            <div key={b.id} className="dl-blocker" data-color={b.color}>
              <span className="dl-blocker-name">{b.label}</span>
              <span className="dl-blocker-val">{b.value}</span>
              <span className="dl-blocker-th dim">{b.threshold}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
// ٤ — ما المعطوب؟
// ═══════════════════════════════════════════════════════════

interface SickAtom {
  id: number
  name: string
  state: string
  message: string
  color: Color4
}

function useSickAtoms(): { sick: SickAtom[]; waiting: SickAtom[] } {
  const atoms = useStore((s) => s.atoms)
  return useMemo(() => {
    const sick: SickAtom[] = []
    const waiting: SickAtom[] = []
    for (const a of Object.values(atoms)) {
      const hs = (a.health?.state ?? '').toLowerCase()
      const msg = a.health?.message ?? ''
      // النواة ترسل الحالة حروفًا صغيرة ('unhealthy') — مقارنة كبيرة ما تطابق أبدًا
      // فبتطلع «كل الذرّات سليمة» و10 مو سليمة (الكذبة المقيسة 2026-08-24)
      if (hs === 'unhealthy' || hs === 'error') {
        sick.push({ id: a.id, name: a.name_ar || a.name, state: a.state, message: msg, color: 'red' })
      } else if (hs === 'degraded' && !isWaitingMessage(msg)) {
        waiting.push({ id: a.id, name: a.name_ar || a.name, state: a.state, message: msg, color: 'amber' })
      }
    }
    sick.sort((x, y) => x.id - y.id)
    waiting.sort((x, y) => x.id - y.id)
    return { sick: sick.slice(0, 12), waiting: waiting.slice(0, 12) }
  }, [atoms])
}

const GUARD_CHAIN: Array<{ id: number; label: string; purpose: string }> = [
  { id: 586, label: 'حلّ الرمز', purpose: 'الرمز معروف عند الوسيط' },
  { id: 585, label: 'حارس الهامش', purpose: 'الهامش والاحتياطي معروفان' },
  { id: 551, label: 'باني الأمر', purpose: 'الأمر مكتمل الهوية والسعر' },
  { id: 584, label: 'شرعية الوقف', purpose: 'الوقف والهدف صالحان' },
  { id: 552, label: 'مدقّق الأمر', purpose: 'القرار النهائي قبل الجسر' },
  { id: 601, label: 'كاتب الجسر', purpose: 'الحساب والرقم السحري ومنع التكرار' },
  { id: 901, label: 'بوابة الأوامر', purpose: 'أمر المالك وحالة الإيقاف' },
]

function GuardChainPanel() {
  const atoms = useStore((s) => s.atoms)
  const risk = useStore((s) => s.risk)
  const gate = useStore((s) => s.gate)
  return (
    <div className="dl-panel dl-guard-chain">
      <div className="dl-panel-title">سلسلة فتح الصفقة — لا تخمين</div>
      <div className="dl-guard-note">كل سطر يبيّن حالة الحارس. «لم تصل» ليست نجاحًا ولا فشلًا.</div>
      <div className="dl-guard-list">
        {GUARD_CHAIN.map((g) => {
          const a = atoms[g.id]
          const state = (a?.health?.state ?? '').toLowerCase()
          const healthy = state === 'healthy'
          const bad = state === 'unhealthy' || state === 'error'
          const color: Color4 = healthy ? 'green' : bad ? 'red' : state === 'degraded' ? 'amber' : 'grey'
          const status = healthy ? 'جاهز' : bad ? 'يمنع — ' + (a?.health?.message || 'فشل') : state === 'degraded' ? 'تحذير — ' + (a?.health?.message || 'متعثّر') : 'لم تصل حالته بعد'
          return (
            <div key={g.id} className="dl-guard-row" data-color={color}>
              <span className="dl-guard-light" />
              <b className="dl-guard-id">{g.id}</b>
              <span className="dl-guard-name">{g.label}</span>
              <span className="dl-guard-purpose">{g.purpose}</span>
              <span className="dl-guard-status">{status}</span>
            </div>
          )
        })}
      </div>
      <div className="dl-guard-summary" data-color={risk?.halted || gate?.status === 'HALTED' ? 'red' : 'grey'}>
        {risk?.halted || gate?.status === 'HALTED' ? 'يوجد إيقاف فعّال — فتح الصفقة ممنوع' : 'الحكم النهائي يُؤخذ من السلسلة نفسها، وليس من لون اللوحة فقط'}
      </div>
    </div>
  )
}

function WhatBrokenPanel() {
  const { sick, waiting } = useSickAtoms()
  return (
    <div className="dl-panel">
      <div className="dl-panel-title">ما المعطوب؟</div>
      {sick.length === 0 && waiting.length === 0 ? (
        <div className="dl-all-clear" style={{ color: C.green }}>كل الذرّات سليمة — لا شيء معطوب</div>
      ) : (
        <>
          {sick.length > 0 && (
            <>
              <div className="dl-sub-title" style={{ color: C.red }}>معطوب ({sick.length})</div>
              <div className="dl-sick-list">
                {sick.map((a) => (
                  <div key={a.id} className="dl-sick-item" data-color="red">
                    <span className="dl-sick-id">{a.id}</span>
                    <span className="dl-sick-name">{a.name}</span>
                    <span className="dl-sick-msg">{a.message.slice(0, 60)}</span>
                  </div>
                ))}
              </div>
            </>
          )}
          {waiting.length > 0 && (
            <>
              <div className="dl-sub-title" style={{ color: C.amber }}>ينتظر مدخله ({waiting.length}) — ليس معطوبًا</div>
              <div className="dl-sick-list">
                {waiting.slice(0, 6).map((a) => (
                  <div key={a.id} className="dl-sick-item" data-color="amber">
                    <span className="dl-sick-id">{a.id}</span>
                    <span className="dl-sick-name">{a.name}</span>
                    <span className="dl-sick-msg">{a.message.slice(0, 60)}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
// ٥ — ماذا فعل النظام؟ (الخط الزمني)
// ═══════════════════════════════════════════════════════════

const ACT_AR: Record<string, string> = {
  'execution.order.built': 'بني أمر',
  'execution.order.requested': 'طُلب أمر',
  'execution.order.rejected': 'رُفض أمر',
  'trading.final_decision': 'قرار نهائي',
  'decision.gate.passed': 'عبَر البوّابة',
  'decision.gate.blocked': 'حُجب عند البوّابة',
  'perpetual.entry.rejected': 'رُفض تفعيل',
  'execution.command.ack': 'وصل تأكيد',
  'execution.command.failed': 'فشل أمر',
}

function TimelinePanel() {
  const execOrders = useStore((s) => s.execOrders)
  const events = useStore((s) => s.events)
  return (
    <div className="dl-panel">
      <div className="dl-panel-title">ماذا فعل النظام؟</div>
      {execOrders.length === 0 && events.length === 0 ? (
        <div className="dl-all-clear dim">لم ينفّذ النظام أي إجراء بعد — بانتظار أول قرار</div>
      ) : (
        <div className="dl-timeline">
          {execOrders.slice(0, 10).map((o, i) => {
            const act = ACT_AR[o.kind ?? ''] ?? o.kind ?? 'إجراء'
            const color: Color4 = o.kind === 'rejected' ? 'red' : o.kind === 'skipped' ? 'amber' : 'green'
            return (
              <div key={i} className="dl-tl-item" data-color={color}>
                <span className="dl-tl-dot" />
                <span className="dl-tl-act">{act}</span>
                <span className="dl-tl-sym">{(o as Record<string, unknown>).symbol ?? ''}</span>
                <span className="dl-tl-time dim">
                  {o.ts ? new Date(o.ts).toLocaleTimeString('ar', { hour12: false }) : '—'}
                </span>
              </div>
            )
          })}
          {events.filter((e) => ACT_AR[e.name]).slice(0, 5).map((e, i) => (
            <div key={`e${i}`} className="dl-tl-item" data-color="grey">
              <span className="dl-tl-dot" />
              <span className="dl-tl-act">{ACT_AR[e.name]}</span>
              <span className="dl-tl-detail dim">{e.detail?.slice(0, 50)}</span>
              <span className="dl-tl-time dim">
                {e.ts ? new Date(e.ts).toLocaleTimeString('ar', { hour12: false }) : '—'}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
// ٦ — المحلّلون (التفصيل خلف الطبقة الثانية)
// ═══════════════════════════════════════════════════════════

function AnalystsGrid() {
  const panels = useStore((s) => s.analystsPanels)
  const entries = Object.entries(panels)
  const [expanded, setExpanded] = useState(false)
  if (!entries.length) return null
  const panel = entries[entries.length - 1][1]
  const rows = (panel.analysts ?? []).filter((r) => r.present)
  return (
    <div className="dl-panel">
      <button className="dl-expand-btn" onClick={() => setExpanded(!expanded)}>
        {expanded ? '▲ إخفاء المحلّلين' : '▼ المحلّلون — كل محلّل بنفسه'}
        <span className="dim" style={{ marginInlineStart: 8 }}>
          {rows.length} من {panel.expected ?? 15}
        </span>
      </button>
      {expanded && (
        <div className="dl-analysts-table">
          <table>
            <thead>
              <tr><th>المحلّل</th><th>اتجاهه</th><th>الثقة</th><th>الوزن</th><th>التسليمات</th><th>آخر</th><th>الإيقاع</th><th>الجاي</th><th>الحالة</th></tr>
            </thead>
            <tbody>
              {(panel.analysts ?? []).map((r) => (
                <tr key={r.id} className={r.present ? '' : 'dl-waiting-row'}>
                  <td>{r.id}</td>
                  <td style={{ color: r.direction != null && r.direction > 0 ? C.green : r.direction != null && r.direction < 0 ? C.red : C.grey, fontWeight: 700 }}>{num(r.direction)}</td>
                  <td>{num(r.confidence)}</td>
                  <td>{num(r.weight)}</td>
                  <td>{r.present ? num(r.deliveries) : '—'}</td>
                  <td>{r.age_s != null ? `${r.age_s}ث` : '—'}</td>
                  <td className="dim">{r.mode === 'live_tick' ? 'مستمر' : r.mode === 'candle' ? `شمعة ${r.timeframe ?? ''}` : '—'}</td>
                  <td>{r.next_expected_at != null ? num(r.next_expected_at) : '—'}</td>
                  <td style={{ color: r.ready ? C.green : C.amber }}>{r.state ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
// الشريط السفلي (الاتصال + الإنذار المبكر)
// ═══════════════════════════════════════════════════════════

const EARLY_AR: Record<string, string> = {
  'tools.device_resources.state': 'موارد الجهاز',
  'telemetry.carrier.state': 'ناقل التلمترية',
  'time.clock.quality.state': 'جودة الساعة',
}

function EarlyWarningBar() {
  const streams = useStore((s) => s.streams)
  const flows = useStore((s) => s.flows)
  const conn = useStore((s) => s.conn)
  return (
    <div className="dl-early-bar">
      {Object.entries(EARLY_AR).map(([event, ar]) => {
        const arrived = flows[event] != null
        const payload = streams[event] as Record<string, unknown> | undefined
        const status = arrived
          ? (typeof payload?.status === 'string' ? payload.status : 'وصل')
          : 'لم يصل بعد'
        const color: Color4 = arrived
          ? status.includes('OK') || status === 'وصل' ? 'green'
          : status.includes('DEGRADED') || status.includes('STALE') ? 'amber' : 'grey'
          : 'grey'
        return (
          <span key={event} className="dl-early-chip" data-color={color}>
            <span className="dl-early-dot" />
            {ar}
            <b className="dl-early-status">{status}</b>
          </span>
        )
      })}
      <span className="dl-conn" title={conn === 'live' ? 'الاتصال حيّ' : conn === 'connecting' ? 'جارٍ الاتصال' : 'الاتصال مقطوع'}>
        {conn === 'live' ? '🟢' : conn === 'connecting' ? '🟡' : '🔴'}
      </span>
    </div>
  )
}

// ═══════════════════════════════════════════════════════════
// التخطيط الرئيسي
// ═══════════════════════════════════════════════════════════

export default function NewDashboard() {
  const safety = useSafety()
  const market = useStore((s) => s.market)
  const accts = useTwinAccounts()
  const [now, setNow] = useState(Date.now())

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  // اختصار الطوارئ — من أي شاشة
  const emergencyRef = useRef<HTMLButtonElement>(null)
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && e.ctrlKey) {
        e.preventDefault()
        emergencyRef.current?.click()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  const tickSymbols = Object.entries(market).slice(0, 3)

  return (
    <div className="dl-root">
      {/* ═══ ١ — هل أنا بأمان؟ ═══ */}
      <header className="dl-safety-bar" data-color={safety.color}>
        <span className="dl-safety-label">{safety.label}</span>
        <span className="dl-safety-detail">{safety.detail}</span>
        <div className="dl-prices">
          <span className="dl-price" title="سي-تريدر — بيانات فقط">
            تحليل <b className="num">{accts.analysisId ?? '—'}</b>
          </span>
          <span className="dl-price" title="ميتاتريدر 5 — عليه الصفقة">
            تنفيذ <b className="num">{accts.execId ?? '—'}</b>
          </span>
          {tickSymbols.map(([sym, t]) => (
            <span key={sym} className="dl-price">
              {sym} <b className="num">{t.bid?.toFixed(1)}</b>
            </span>
          ))}
        </div>
        <button
          ref={emergencyRef}
          className="dl-emergency"
          onClick={async () => {
            if (!window.confirm('⚠️ إيقاف طارئ — إيقاف الدخول الجديد فقط؟')) return
            const result = await dangerCommand('halt')
            window.alert(result.message || (result.ok ? 'تم إرسال الإيقاف الطارئ' : 'تعذّر إرسال الإيقاف الطارئ'))
          }}
        >
          إيقاف طارئ
        </button>
      </header>

      {/* ═══ ٢ — الروم الحيّ ═══ */}
      <RoomBar />

      {/* ═══ ٣ — الأقسام الأربعة ═══ */}
      <main className="dl-main">
        <WhyNotPanel />
        <GuardChainPanel />
        <WhatBrokenPanel />
        <TimelinePanel />
      </main>

      {/* ═══ ٤ — المحلّلون (تفصيل قابل للفتح) ═══ */}
      <AnalystsGrid />

      {/* ═══ الشريط السفلي ═══ */}
      <EarlyWarningBar />
    </div>
  )
}
