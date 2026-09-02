// الحسابان جنب بعض — عين المالك على التحليل مقابل التنفيذ.
// سي-تريدر = بيانات فقط. ميتاتريدر 5 = اللي بتنفتح عليه الصفقة.
// الأرقام من البثّ كما وصلت — الغائب يُعلَن غيابًا، لا اختراع.
import { useEffect, useState } from 'react'
import { useStore } from '../core/store'

type Live = 'live' | 'silent' | 'unknown'

export type TwinAccounts = {
  analysisId: string | null
  analysisBroker: string | null
  execId: string | null
  execBroker: string | null
  execEquity: number | null
  execConnected: boolean | null
  tradeAllowed: boolean | null
  analysisLive: Live
  execLive: Live
  sameNumber: boolean
}

const asId = (v: unknown): string | null => {
  if (v == null || v === '') return null
  const s = String(v).trim()
  return s ? s : null
}

function liveOf(conn: string, flows: Record<string, number>, name: string, now: number): Live {
  if (conn !== 'live') return 'unknown'
  const t = flows[name]
  if (t == null) return 'silent'
  return (now - t) / 1000 < 30 ? 'live' : 'silent'
}

export function useTwinAccounts(): TwinAccounts {
  const conn = useStore((s) => s.conn)
  const flows = useStore((s) => s.flows)
  const analysis = useStore((s) => s.analysis)
  const rooms = useStore((s) => s.room)
  const fusion = useStore((s) => s.sectionFusion)
  const ct = useStore((s) => s.streams['feed.ctrader.tick']) as { account_id?: unknown; broker?: unknown } | undefined
  const term = useStore((s) => s.streams['platform.terminal_state']) as
    | { account_id?: unknown; connected?: boolean; trade_allowed?: boolean }
    | undefined
  const acc = useStore((s) => s.streams['platform.account.state']) as
    | { account_id?: unknown; broker?: unknown; equity?: number }
    | undefined
  const [now, setNow] = useState(() => performance.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(performance.now()), 1000)
    return () => window.clearInterval(id)
  }, [])

  const firstAnalysis = Object.values(analysis)[0]
  const firstRoom = Object.values(rooms)[0]
  const firstFusion = Object.values(fusion)[0]

  const analysisId = asId(ct?.account_id)
    ?? asId(firstAnalysis?.account_id)
    ?? asId(firstRoom?.account_id)
    ?? asId(firstFusion?.account_id)
  const analysisBroker = asId(ct?.broker) ?? asId(firstRoom?.broker) ?? asId(firstFusion?.broker)
  const execId = asId(term?.account_id) ?? asId(acc?.account_id)
  const execBroker = asId(acc?.broker)
  const execEquity = typeof acc?.equity === 'number' ? acc.equity : null
  const execConnected = term ? !!term.connected : null
  const tradeAllowed = term ? !!term.trade_allowed : null
  const analysisLive = liveOf(conn, flows, 'feed.ctrader.tick', now)
  const execLive = liveOf(conn, flows, 'feed.mt5.tick', now)
  const sameNumber = !!(analysisId && execId && analysisId === execId)

  return {
    analysisId, analysisBroker, execId, execBroker, execEquity,
    execConnected, tradeAllowed, analysisLive, execLive, sameNumber,
  }
}

const LIVE_AR: Record<Live, string> = { live: 'حيّ', silent: 'صامت', unknown: 'لا نعرف' }
const liveTone = (v: Live) => (v === 'live' ? 'green' : v === 'silent' ? 'red' : 'dim')

export default function AccountsBar() {
  const a = useTwinAccounts()
  return (
    <div className="acctbar" aria-label="حساب التحليل وحساب التنفيذ">
      <span
        className={`acctchip analysis ${liveTone(a.analysisLive)}`}
        title="سي-تريدر — بيانات وتحليل فقط. ما بتنفتح عليه صفقة."
      >
        <i className="feeddot" />
        <b>تحليل</b>
        <em className="num">{a.analysisId ?? '—'}</em>
        <small>{LIVE_AR[a.analysisLive]}</small>
      </span>
      <span
        className={`acctchip exec ${a.execConnected === false ? 'red' : liveTone(a.execLive)}`}
        title="ميتاتريدر 5 — حساب التنفيذ. عليه بتنفتح الصفقة فعليًّا."
      >
        <i className="feeddot" />
        <b>تنفيذ</b>
        <em className="num">{a.execId ?? '—'}</em>
        <small>{a.execConnected === false ? 'مقطوع' : LIVE_AR[a.execLive]}</small>
      </span>
      {a.sameNumber ? (
        <span className="acctchip amber" title="رقم واحد على الدورين — التصميم حسابان منفصلان">
          ⚠️ نفس الرقم
        </span>
      ) : null}
    </div>
  )
}

const money = (n: number) => n.toLocaleString('ar-EG-u-nu-latn', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export function AccountsPair() {
  const a = useTwinAccounts()
  return (
    <div className="acctpair">
      <div className="scard acctcard analysis">
        <div className="st">حساب التحليل — سي‑تريدر</div>
        <div className={`sv num ${liveTone(a.analysisLive)}`}>{a.analysisId ?? 'ما وصل'}</div>
        <div className="ss">بيانات وأسعار فقط — ما بتنفتح عليه صفقة أبدًا.</div>
        <div className="ss">{a.analysisBroker ? `الوسيط ${a.analysisBroker} · ` : ''}التغذية {LIVE_AR[a.analysisLive]}</div>
      </div>
      <div className="scard acctcard exec">
        <div className="st">حساب التنفيذ — ميتاتريدر 5</div>
        <div className={`sv num ${a.execConnected === false ? 'red' : liveTone(a.execLive)}`}>{a.execId ?? 'ما وصل'}</div>
        <div className="ss">عليه تُفتح الصفقات فعليًّا — منفصل عن التحليل.</div>
        <div className="ss">
          {a.execConnected == null ? 'الاتصال: ما وصل' : a.execConnected ? 'المنصّة متّصلة' : 'المنصّة مقطوعة'}
          {a.tradeAllowed == null ? '' : a.tradeAllowed ? ' · التداول مسموح' : ' · التداول موقف'}
          {a.execEquity != null ? ` · القيمة ${money(a.execEquity)}` : ''}
          {a.execBroker ? ` · ${a.execBroker}` : ''}
        </div>
      </div>
      {a.sameNumber ? (
        <div className="ss" style={{ gridColumn: '1 / -1', color: 'var(--amber)' }}>
          ⚠️ رقم الحساب واحد على الدورين. التصميم حسابان منفصلان: تحليل ≠ تنفيذ.
        </div>
      ) : null}
    </div>
  )
}
