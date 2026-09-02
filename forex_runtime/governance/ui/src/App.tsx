import NewDashboard from './sections/NewDashboard'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from './core/store'
import { startEngine } from './core/engine'
import { dangerCommand } from './core/commands'
import { getTabOrder, TAB_ORDER_EVENT } from './core/appearance'
import TouchClipboard from './components/TouchClipboard'
import FeedLeds from './components/FeedLeds'
import AccountsBar from './components/AccountsBar'
import Network from './sections/Network'
import AtomModal from './sections/AtomModal'
import Diag from './sections/Diag'
import Stats from './sections/Stats'
import Log from './sections/Log'
import Portfolios from './sections/Portfolios'
import Connection from './sections/Connection'
import Alerts from './sections/Alerts'
import Market from './sections/Market'
import Monitor from './sections/Monitor'
import Settings from './sections/Settings'
import Security from './sections/Security'
import Charts from './sections/Charts'
import Atoms from './sections/Atoms'
import Analysis from './sections/Analysis'
import Lab from './sections/Lab'
import Backtest from './sections/Backtest'
import Structure from './sections/Structure'
import Liquidity from './sections/Liquidity'
import Statistics from './sections/Statistics'
import Probability from './sections/Probability'
import Strategies from './sections/Strategies'
import Decision from './sections/Decision'
import Risk from './sections/Risk'
import Execution from './sections/Execution'
import Home from './sections/Home'
import Scripts from './sections/Scripts'
import Control from './sections/Control'
import ChangeGovernor from './sections/ChangeGovernor'
import NQ from './sections/NQ'
import News from './sections/News'
import Mexc from './sections/Mexc'
import Universe from './sections/Universe'
import Senses from './sections/Senses'
import Judgement from './sections/Judgement'
import { applyColors, loadColors, CRYPTO_PRESET } from './core/appearance'

// أقسام اللوحة — القائمة القانونية انتقلت لـ core/sections.ts (يقرأها محرّر
// ترتيب التبويبات بالإعدادات كمان — بند ١٥ج بورقة ٩٩). «الشبكة» = النظام العام.
import { SECTIONS, CRYPTO_SECTIONS } from './core/sections'

const pad = (n: number, l: number) => String(n).padStart(l, '0')

type MarketInfo = {
  market: 'forex' | 'crypto'
  label: string
  alternate_port: number
  alternate_label: string
}

export default function App() {
  const [active, setActive] = useState('dashboard')
  const ACTIVE_TAB_KEY = 'nq.active_tab'
  const goTo = (id: string) => {
    setActive(id)
    if (marketInfo?.market === 'forex') {
      try { localStorage.setItem(`${ACTIVE_TAB_KEY}.forex`, id) } catch { /* التخزين المحلي اختياري */ }
    }
  }
  const [cryptoEntered, setCryptoEntered] = useState(false)
  const [marketInfo, setMarketInfo] = useState<MarketInfo | null>(() => (
    window.location.port === '8091'
      ? { market: 'crypto', label: 'كريبتو', alternate_port: 8090, alternate_label: 'فوركس' }
      : { market: 'forex', label: 'فوركس', alternate_port: 8091, alternate_label: 'كريبتو' }
  ))
  useEffect(() => {
    fetch('/gov/market', { cache: 'no-store' })
      .then((r) => r.ok ? r.json() as Promise<MarketInfo> : null)
      .then((info) => { if (info) setMarketInfo(info) })
      .catch(() => {})
  }, [])
  // تذكّر آخر تبويب للفوركس فقط؛ التحديث الكامل لا يعيد المالك إلى البداية.
  useEffect(() => {
    if (marketInfo?.market !== 'forex') return
    try {
      const saved = localStorage.getItem(`${ACTIVE_TAB_KEY}.forex`)
      if (saved) setActive(saved)
    } catch { /* التخزين المحلي اختياري */ }
  }, [marketInfo?.market])
  useEffect(() => {
    if (marketInfo?.market !== 'forex') return
    try { localStorage.setItem(`${ACTIVE_TAB_KEY}.forex`, active) } catch { /* التخزين المحلي اختياري */ }
  }, [active, marketInfo?.market])

  const switchMarket = async () => {
    const next = marketInfo?.market === 'crypto' ? 'forex' : 'crypto'
    const response = await fetch('/unified/select', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ market: next }),
    }).catch(() => null)
    if (!response?.ok) {
      window.alert('تعذّر تبديل مسار السوق — تأكد أن Unified Hub يعمل على هذا العنوان')
      return
    }
    useStore.getState().resetLive()
    setMarketInfo({
      market: next,
      label: next === 'crypto' ? 'كريبتو' : 'فوركس',
      alternate_port: 8090,
      alternate_label: next === 'crypto' ? 'فوركس' : 'كريبتو',
    })
  }
  // بند ١٥ج (ورقة ٩٩) — ترتيب التبويبات بيد المالك من «الإعدادات › تخصيص الشكل»
  const [tabOrder, setTabOrder] = useState<string[]>(() => getTabOrder(SECTIONS.map((s) => s[0])))
  useEffect(() => {
    const onChange = () => setTabOrder(getTabOrder(SECTIONS.map((s) => s[0])))
    window.addEventListener(TAB_ORDER_EVENT, onChange)
    return () => window.removeEventListener(TAB_ORDER_EVENT, onChange)
  }, [])
  const cryptoMode = marketInfo?.market === 'crypto'
  useEffect(() => { if (cryptoMode && !cryptoEntered) { setCryptoEntered(true); setActive('universe') } }, [cryptoMode])
  const orderedSections = useMemo(
    () => (cryptoMode
      ? CRYPTO_SECTIONS
      : tabOrder
        .map((id) => SECTIONS.find((s) => s[0] === id))
        .filter((s): s is [string, string, boolean] => s != null)
        .filter(([id]) => !['mexc', 'universe', 'senses', 'judgement'].includes(id))),
    [tabOrder, cryptoMode],
  )
  const isCrypto = marketInfo?.market === 'crypto'
  useEffect(() => {
    // قسم أسمر: نفس اللوحة بلمسة لون — بلا طغيان على تخصيص المالك المحفوظ
    const custom = loadColors()
    if (marketInfo?.market === 'crypto' && Object.keys(custom).length === 0) applyColors(CRYPTO_PRESET)
    else applyColors(custom)
  }, [marketInfo?.market])
  const clockRef = useRef<HTMLDivElement>(null)
  const staleRef = useRef<HTMLDivElement>(null)
  const shellRef = useRef<HTMLDivElement>(null)

  // ختم المالك 2026-08-20: الثيم ثابت «داكن» — أزرار الثيمات أُزيلت من الشريط،
  // والألوان تُضبط من «الإعدادات › تخصيص الشكل» (بند ١٥ بورقة ٩٩).
  useEffect(() => { document.documentElement.setAttribute('data-theme', 'dark') }, [])

  // مراقب النسخة: لمّا يتغيّر بناء اللوحة، تعيد التحميل لحالها (بلا Ctrl+F5 يدوي)
  useEffect(() => {
    const own = Array.from(document.querySelectorAll('script[type="module"]'))
      .map((s) => (s as HTMLScriptElement).src).find((s) => s.includes('/assets/index-'))
    let base = own ? own.split('/').pop() ?? '' : ''
    const id = window.setInterval(async () => {
      try {
        const r = await fetch('/gov/version', { cache: 'no-store' })
        const { v } = (await r.json()) as { v?: string }
        if (!v || v === '?') return
        if (!base) { base = v; return }
        if (v !== base) window.location.reload()
      } catch { /* الخادم مطفي */ }
    }, 7000)
    return () => clearInterval(id)
  }, [])

  useEffect(() => {
    const stopEngine = startEngine()
    let raf = 0
    const tick = () => {
      const d = new Date()
      if (clockRef.current) {
        clockRef.current.textContent =
          `${pad(d.getHours(), 2)}:${pad(d.getMinutes(), 2)}:${pad(d.getSeconds(), 2)}.${pad(d.getMilliseconds(), 3)}`
      }
      const { conn, lastMsgAt } = useStore.getState()
      // النواة تسقط والمخزن يحتفظ بآخر لقطة. اللافتة تعلن ذلك.
      // لا نعيد كتابة className كل إطار — هذا كان يقلب اللوحة سوداء.
      if (staleRef.current && shellRef.current) {
        const frozen = conn === 'down'
        const wantShell = frozen ? 'shell frozen' : 'shell'
        const wantBan = frozen ? 'staleban on' : 'staleban'
        if (shellRef.current.className !== wantShell) shellRef.current.className = wantShell
        if (staleRef.current.className !== wantBan) staleRef.current.className = wantBan
        if (frozen) {
          const secs = lastMsgAt ? Math.floor((performance.now() - lastMsgAt) / 1000) : null
          const since = secs === null ? null
            : secs < 60 ? `${secs} ثانية`
            : secs < 3600 ? `${Math.floor(secs / 60)} دقيقة`
            : `${Math.floor(secs / 3600)} ساعة و${Math.floor((secs % 3600) / 60)} دقيقة`
          const text = since === null
            ? '⛔ النواة غير متّصلة — شغّل غرفة القيادة. المختبر يشتغل بدونها.'
            : `⛔ النواة مقطوعة منذ ${since} — الأرقام الحيّة مجمّدة. المختبر يشتغل على بيانات تاريخية.`
          if (staleRef.current.textContent !== text) staleRef.current.textContent = text
        }
      }
      // ٢٠٢٦-٠٩-٠٣: مؤشّرات الاستلام رجعت للشريط العلوي — بلاطة التغذية
      // بالرئيسية مطفية افتراضيًا والمالك ما عنده عين غير اللوحة.
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => { cancelAnimationFrame(raf); stopEngine() }
  }, [marketInfo?.market])

  const activeLabel = SECTIONS.find((s) => s[0] === active)?.[1] ?? ''

  return (
    <div className="shell" ref={shellRef}>
      <div className="staleban" ref={staleRef} />
      <header className="hdr">
        <div className="brand">
          <span className="nqmark">NQ</span>
          <span className="nqname">غرفة القيادة الكمّية</span>
          <small>محمد رزوق</small>
        </div>
        {marketInfo && (
          <div className="market-switch" title="هذه اللوحة معزولة عن السوق الآخر">
            {marketInfo.label} · لوحة مستقلة
          </div>
        )}
        <button
          className="refreshbtn"
          title="تحديث كامل — يعيد تحميل اللوحة وكل البيانات من النواة"
          onClick={() => window.location.replace('/?v=' + Date.now())}
        >🔄 تحديث</button>
        {!isCrypto && <button
          className="haltbtn"
          title="يوقف كل إرسال الأوامر فورًا — عبر بوّابة الأوامر (901) بتأكيد"
          onClick={async () => { const r = await dangerCommand('halt'); if (r.message) window.alert(r.message) }}
        >⛔ إيقاف طارئ</button>}
        <div className="hdr-right">
          <AccountsBar />
          <FeedLeds />
          <div className="clock num" ref={clockRef}>--:--:--.---</div>
        </div>
      </header>

      <nav className="nav">
        {isCrypto ? <span style={{ alignSelf: 'center', padding: '0 10px', color: 'var(--accent)', fontWeight: 700, fontSize: 12 }}>قسم أسمر · كريبتو</span> : null}
        {orderedSections.map(([id, label, on]) => (
          <button key={id} className={active === id ? 'active' : ''} disabled={!on} onClick={() => on && goTo(id)}>
            {on ? label : `${label} · قريبًا`}
          </button>
        ))}
      </nav>

      <main className="workspace">
        {active === 'mexc' ? (
          <Mexc />
        ) : active === 'universe' ? (
          <Universe />
        ) : active === 'senses' ? (
          <Senses />
        ) : active === 'judgement' ? (
          <Judgement />
        ) : active === 'dashboard' ? (
          <NewDashboard />
        ) : active === 'home' ? (
          <Home onGo={goTo} />
        ) : active === 'control' ? (
          <Control />
        ) : active === 'change' ? (
          <ChangeGovernor />
        ) : active === 'network' ? (
          <Network />
        ) : active === 'atoms' ? (
          <Atoms />
        ) : active === 'market' ? (
          <Market />
        ) : active === 'charts' ? (
          <Charts />
        ) : active === 'analysis' ? (
          <Analysis />
        ) : active === 'lab' ? (
          <Lab />
        ) : active === 'backtest' ? (
          <Backtest />
        ) : active === 'structure' ? (
          <Structure />
        ) : active === 'liquidity' ? (
          <Liquidity />
        ) : active === 'statistics' ? (
          <Statistics />
        ) : active === 'probability' ? (
          <Probability />
        ) : active === 'strategies' ? (
          <Strategies />
        ) : active === 'decision' ? (
          <Decision />
        ) : active === 'risk' ? (
          <Risk />
        ) : active === 'execution' ? (
          <Execution />
        ) : active === 'diag' ? (
          <Diag />
        ) : active === 'stats' ? (
          <Stats />
        ) : active === 'log' ? (
          <Log />
        ) : active === 'portfolios' ? (
          <Portfolios />
        ) : active === 'connection' ? (
          <Connection />
        ) : active === 'alerts' ? (
          <Alerts />
        ) : active === 'monitor' ? (
          <Monitor />
        ) : active === 'settings' ? (
          <Settings />
        ) : active === 'security' ? (
          <Security />
        ) : active === 'scripts' ? (
          <Scripts />
        ) : active === 'news' ? (
          <News />
        ) : active === 'nq' ? (
          <NQ />
        ) : (
          <div className="placeholder">
            <div className="ph-title">قسم «{activeLabel}»</div>
            <div className="ph-sub">لسا مو مبني — يُركَّب فوق نفس البنية لمّا يجي دورو.</div>
          </div>
        )}
      </main>

      <footer className="status">
        <span>طبقة الحوكمة التفاعلية</span>
        <span className="grow">السوق: <span className="num">{marketInfo?.label ?? '...'}</span> · الحوكمة لا تخزّن — كل رقم من نظامك الحيّ</span>
      </footer>

      <AtomModal />
      <TouchClipboard />
    </div>
  )
}
