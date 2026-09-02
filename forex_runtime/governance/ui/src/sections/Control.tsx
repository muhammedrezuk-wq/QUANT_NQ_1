// تحكّم — كل مفتاح في النظام في شاشة واحدة.
// حكم المالك 2026-08-16: «كل شي بنعمله زر، اعمله زر وحطّه فيها».
// وقاعدته الأقدم: حرّاس بلا مفاتيح = فشل · يد المالك قبل عينه.
// لا يوجد شراء ولا بيع مباشر هنا — كل أمر يمرّ من بوّابة 901 بتأكيد مزدوج.
import { useEffect, useState } from 'react'
import { useStore } from '../core/store'
import { arabicHealth } from '../core/arabic'
import { AccountsPair } from '../components/AccountsBar'

type Danger = 'halt' | 'kill_switch_reset' | 'activate_asset' | 'deactivate_asset'
  | 'asset_control' | 'execution_gate'

async function send(action: Danger, payload: Record<string, unknown>):
  Promise<{ ok: boolean; message?: string; error?: string }> {
  const first = await fetch('/gov/command', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, payload }),
  })
  const one = await first.json() as { token?: string; summary?: string; ttl_s?: number; error?: string }
  if (!first.ok || !one.token) return { ok: false, error: one.error ?? 'تعذّر طلب التأكيد' }
  if (!window.confirm(`⚠️ ${one.summary ?? 'أمر خطر'}؟\nالتأكيد صالح ${one.ttl_s ?? 60} ثانية`))
    return { ok: false, error: 'أُلغي' }
  const second = await fetch('/gov/command', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, confirm: one.token, payload }),
  })
  const two = await second.json() as { message?: string; error?: string }
  return second.ok ? { ok: true, message: two.message } : { ok: false, error: two.error ?? 'رفضت البوابة الأمر' }
}

// الذرّات التي يُسمح بإيقافها وتشغيلها من هنا — سلسلة التنفيذ والإدارة وحدها.
// لا نعرض 212 زرًّا: إيقاف ذرّة تحليل لا يوقف تداولًا، وإيقاف ذرّة نواة يقتل النظام.
const ATOMS: [number, string, string][] = [
  [451, 'تجميع القرار', 'قرار'], [453, 'حساب الدرجة', 'قرار'],
  [458, 'حلّ التعارض', 'قرار'], [466, 'الموافقة', 'قرار'],
  [467, 'إرسال القرار', 'قرار'], [468, 'التحكّم بالأصول', 'قرار'],
  [516, 'قاطع الأمان', 'مخاطر'], [518, 'دفتر مخاطر الأصل', 'مخاطر'],
  [513, 'تحجيم المركز', 'مخاطر'], [525, 'سعر الوقف الصلب', 'مخاطر'],
  [512, 'الوقف الهيكليّ', 'مخاطر'], [506, 'حدود الجلسة', 'مخاطر'],
  [523, 'محرّك العيار', 'وضع'], [576, 'المحرّك الدائم', 'وضع'],
  [581, 'محرّك الفرق', 'وضع'], [583, 'لقطة التنفيذ', 'وضع'],
  [578, 'منفّذ التحوّط', 'تنفيذ'], [586, 'بوّابة الرموز', 'تنفيذ'],
  [585, 'حارس الهامش', 'تنفيذ'], [584, 'شرعيّة الستوب', 'تنفيذ'],
  [551, 'باني الأمر', 'تنفيذ'], [552, 'بوّابة الأوامر', 'تنفيذ'],
  [563, 'تأكيد التنفيذ', 'تنفيذ'], [601, 'كاتب الجسر', 'تنفيذ'],
  [570, 'منسّق الإدارة', 'إدارة'], [572, 'التعادل', 'إدارة'],
  [573, 'التتبّع', 'إدارة'], [574, 'الإغلاق الجزئيّ', 'إدارة'],
  [575, 'مرسل الإدارة', 'إدارة'], [577, 'صيانة الستوب', 'إدارة'],
  [579, 'منفّذ التخريج', 'إدارة'], [580, 'منفّذ الترجيح', 'إدارة'],
  [609, 'مزامنة المراكز', 'جسر'], [611, 'قارئ الصفقات', 'جسر'],
  [618, 'جسر ميتاتريدر 5', 'جسر'], [619, 'حالة الحساب', 'جسر'],
  [622, 'تغذية سي‑تريدر', 'تحليل'],
]

const GROUPS = ['تحليل', 'قرار', 'مخاطر', 'وضع', 'تنفيذ', 'إدارة', 'جسر']

// بند 18 (دفتر 97): كل مجموعة تُشرح بجملة واحدة — شو مسؤوليّتها بالضبط
const GROUP_DESC: Record<string, string> = {
  'تحليل': 'مصدر أسعار التحليل من سي‑تريدر. بيانات فقط — لا يفتح صفقة ولا يكتب للمنصّة.',
  'قرار': 'تجمع الأدلّة وتحسب الدرجة وتفلتر وتقرّر: شراء أم بيع أم انتظار.',
  'مخاطر': 'تحرس رأس المال: قاطع الأمان، حدود الخسارة، حجم اللوت، وأسعار الوقف.',
  'وضع': 'تحسب المركز الدائم المستهدَف لكل أصل والفرق بينه وبين الواقع.',
  'تنفيذ': 'تحوّل القرار الموافَق لأمر حقيقي وتفحصه وتبعثه للمنصّة عبر البوّابة.',
  'إدارة': 'تعدّل الصفقات المفتوحة: تعادل، تتبّع وقف، وجني ربح جزئي — لا تفتح صفقة.',
  'جسر': 'الوصل مع منصّة ميتاتريدر 5: أسعار، حساب، مراكز، وكتابة الأوامر.',
}

type AtomRow = { id: number; state?: string; version?: string; health?: { state?: string; message?: string } }
type TradingStatus = 'stopped' | 'waiting' | 'open'

// مؤشّر حيّ ملاصق لكل زر: اللون يصف حالة المسار الذي يتحكّم به الزر الآن.
function TradingLed({ status, text }: { status: TradingStatus; text: string }) {
  const color = status === 'open' ? '#2ecc71' : status === 'waiting' ? '#f1c40f' : '#e74c3c'
  const label = status === 'open' ? 'مفتوح' : status === 'waiting' ? 'مفتوح — ينتظر' : 'متوقّف'
  return (
    <span title={text} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, marginInlineEnd: 7, fontSize: 11.5, color, whiteSpace: 'nowrap' }}>
      <span aria-label={label} style={{ width: 9, height: 9, borderRadius: '50%', background: color, boxShadow: `0 0 6px ${color}`, display: 'inline-block' }} />
      {text}
    </span>
  )
}

export default function Control() {
  const term = useStore((s) => s.streams['platform.terminal_state']) as { account_id?: string } | undefined
  const acct = useStore((s) => s.streams['platform.account.state']) as { account_id?: string } | undefined
  // حالة البوّابة الحقيقيّة من حدث 552 نفسه (execution.gate.state) — لا من رسالة
  // الصحّة: startsWith('LIVE') كان النمط الشبح المثبَت خطؤه بورقة 90-31.
  const gate = useStore((s) => s.gate)
  const risk = useStore((s) => s.risk)
  const [account, setAccount] = useState('')
  const [symbol, setSymbol] = useState('BTCUSD')
  const [budget, setBudget] = useState('100')
  const [dial, setDial] = useState('50')
  const [maxPer, setMaxPer] = useState('10')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const [atoms, setAtoms] = useState<Record<number, AtomRow>>({})

  useEffect(() => {
    const found = term?.account_id || acct?.account_id || ''
    if (found && !account) setAccount(String(found))
  }, [term?.account_id, acct?.account_id, account])

  // حالة الذرّات حيّة — الزرّ يجب أن يعرف ما يوقفه قبل أن يوقفه.
  useEffect(() => {
    let alive = true
    const pull = async () => {
      try {
        const body = await (await fetch('/gov/atoms')).json() as { atoms?: AtomRow[] } | AtomRow[]
        if (!alive) return
        // الحوكمة ترجع {connected, atoms}، مع دعم القائمة القديمة فقط للتوافق.
        const rows = Array.isArray(body) ? body : (body.atoms ?? [])
        const map: Record<number, AtomRow> = {}
        for (const r of rows) map[r.id] = r
        setAtoms(map)
      } catch { /* اللوحة لا تكذب: تعذّر القراءة يترك الحالة كما هي */ }
    }
    void pull()
    const t = setInterval(() => void pull(), 4000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  const run = async (action: Danger, payload: Record<string, unknown>, ok: string) => {
    setBusy(true); setMessage('')
    const result = await send(action, payload)
    setMessage(result.ok ? `🟢 ${result.message ?? ok}` : `🛑 ${result.error ?? 'فشل الأمر'}`)
    setBusy(false)
  }

  const asset = (command: string, extra: Record<string, unknown> = {}) =>
    run('asset_control',
      { account_id: account.trim(), symbol: symbol.trim().toUpperCase(), command, ...extra },
      'وصل الأمر إلى بوّابة 901')

  const atomPower = async (id: number, on: boolean) => {
    if (!window.confirm(`${on ? 'تشغيل' : 'إيقاف'} الذرّة ${id}؟`)) return
    setBusy(true); setMessage('')
    try {
      const r = await fetch(`/gov/atoms/${id}/${on ? 'start' : 'stop'}`, { method: 'POST' })
      setMessage(r.ok ? `🟢 الذرّة ${id} ${on ? 'شُغّلت' : 'أُوقفت'}` : `🛑 فشل على الذرّة ${id}`)
    } catch (e) { setMessage(`🛑 ${String(e)}`) }
    setBusy(false)
  }

  // ——— بند 18 (دفتر 97): مسارات موجَّهة فوق الأزرار الخام، لا بدلًا عنها ———
  // كل خطوة خطرة تمرّ بتأكيدها المعتاد (يد المالك قبل عينه) — المسار يرتّب، ما يتجاوز.
  const startAtomStep = async (id: number, nm: string): Promise<string> => {
    if (!window.confirm(`تشغيل «${nm}» (${id})؟ — خطوة من المسار`)) return `تخطّيت «${nm}»`
    try {
      const r = await fetch(`/gov/atoms/${id}/start`, { method: 'POST' })
      return r.ok ? `▶️ «${nm}» اشتغلت` : `🛑 «${nm}»: فشل التشغيل`
    } catch (e) { return `🛑 «${nm}»: ${String(e)}` }
  }

  const pathOpenTrading = async () => {
    setBusy(true)
    const steps: string[] = []
    const push = (s: string) => { steps.push(s); setMessage(steps.join('  ·  ')) }
    const killActive = risk?.kill_switch_state === true || msgOf(516).includes('KILL_SWITCH_ACTIVE')
    if (killActive) {
      const r = await send('kill_switch_reset', {})
      push(r.ok ? '♻️ صُفّر قاطع الأمان' : `🛑 توقّف المسار عند القاطع: ${r.error ?? ''}`)
      if (!r.ok) { setBusy(false); return }
    } else push('قاطع الأمان سليم ✓')
    for (const [id, nm] of [[618, 'جسر ميتاتريدر 5'], [601, 'كاتب الجسر']] as [number, string][]) {
      if (atoms[id] && atoms[id].state !== 'running') push(await startAtomStep(id, nm))
      else push(`«${nm}» شغّال ✓`)
    }
    if (gate ? gate.status !== 'LIVE' : true) {
      const r = await send('execution_gate', { gate: 'both', enabled: true })
      push(r.ok ? '🚪 فُتحت البوّابتان (552 + 575)' : `🛑 توقّف المسار عند البوّابة: ${r.error ?? ''}`)
      if (!r.ok) { setBusy(false); return }
    } else push('البوّابة مفتوحة ✓')
    const connectedAccount = term?.account_id || acct?.account_id
    if (!connectedAccount) {
      push('🔴 توقّف المسار: لا يوجد حساب تنفيذ مؤكّد خلف 601 — الكاتب وحده لا يختار حسابًا')
    } else if (msgOf(576) && msgOf(576).includes('active=0')) {
      push('⚠️ باقي خطوة بإيدك: فعّل الأصل من بطاقة «الأصل» تحت — بدها ميزانيّة وما منخترعها عنّك')
    } else push(`تمّ — البوّابات مفتوحة للحساب المتصل ${connectedAccount}`)
    setBusy(false)
  }

  const pathStopAll = async () => {
    setBusy(true)
    const steps: string[] = []
    const push = (s: string) => { steps.push(s); setMessage(steps.join('  ·  ')) }
    const h = await send('halt', {})
    push(h.ok ? '🛑 أُعلن الإيقاف الطارئ' : `🛑 فشل الإيقاف الطارئ: ${h.error ?? ''}`)
    const g = await send('execution_gate', { gate: 'both', enabled: false })
    push(g.ok ? '🚪 أُقفلت البوّابتان (552 + 575)' : `⚠️ البوّابتان: ${g.error ?? ''}`)
    setBusy(false)
  }

  const pill = (id: number) => {
    const row = atoms[id]
    if (!row) return <span className="pill unk">—</span>
    if (row.state !== 'running') return <span className="pill dead">متوقّفة</span>
    const h = row.health?.state
    if (h === 'healthy') return <span className="pill ok">سليمة</span>
    if (h === 'degraded') return <span className="pill warn">تنتظر</span>
    return <span className="pill unk">—</span>
  }

  const atomStatus = (id: number): TradingStatus => {
    const row = atoms[id]
    if (!row || row.state !== 'running') return 'stopped'
    if (row.health?.state === 'healthy') return 'open'
    return 'waiting'
  }
  const gateStatus = (): TradingStatus => {
    if (!gate || gate.status !== 'LIVE' || risk?.halted || risk?.kill_switch_state === true) return 'stopped'
    return 'open'
  }
  const tradingStatus = (): TradingStatus => {
    // 601 كاتب الجسر لا ينشئ حسابًا ولا يختاره. لا نعلن المسار مفتوحًا
    // ما لم يصل account_id حيّ من المنصّة نفسها.
    const connectedAccount = term?.account_id || acct?.account_id
    if (!connectedAccount || gateStatus() === 'stopped' || atomStatus(601) === 'stopped' || atomStatus(618) === 'stopped') return 'stopped'
    if ([451, 453, 458, 466, 467, 468, 516, 552, 575].some((id) => atomStatus(id) === 'waiting')) return 'waiting'
    return 'open'
  }
  const statusText = (status: TradingStatus, subject = 'مسار التداول') =>
    status === 'open' ? `${subject}: مفتوح` : status === 'waiting' ? `${subject}: مفتوح — ينتظر` : `${subject}: متوقّف`

  const needAsset = busy || !account.trim() || !symbol.trim()

  // 90-16: «شو مانع التداول الآن» بجملة واحدة بدل التفتيش.
  // كل سطر مقروء من صحّة الذرّة نفسها — لا استنتاج ولا ذاكرة في اللوحة.
  // القاعدة: ما لا نستطيع قراءته يُعرض «غير معروف»، لا «تمام».
  const msgOf = (id: number) => atoms[id]?.health?.message ?? ''
  const isRunning = (id: number) => atoms[id]?.state === 'running'
  const blockers: [string, boolean | null, string][] = [
    ['قاطع الأمان مفتوح — كل الإرسال متوقّف',
      msgOf(516) ? msgOf(516).includes('KILL_SWITCH_ACTIVE') : null,
      'اضغط «صفّر قاطع الأمان» فوق'],
    ['بوّابة الأوامر مقفولة — الأمر يُرفض ولا يصل المنصّة',
      gate ? gate.status !== 'LIVE' : null,
      'اضغط «افتح بوّابة الأوامر»'],
    ['مرسل الإدارة موقوف — الصفقة المفتوحة بلا تعادل ولا تتبّع',
      msgOf(575) ? msgOf(575).includes('DISABLED') : null,
      'اضغط «شغّل مرسل الإدارة»'],
    ['الأصل غير مفعَّل — لا هدف ولا أمر يتكوّن أصلًا',
      msgOf(576) ? msgOf(576).includes('active=0') : null,
      'اضغط «فعّل الأصل»'],
    ['جسر ميتاتريدر 5 لا يعمل — لا سعر ولا حساب',
      atoms[618] ? !isRunning(618) : null, 'شغّل الذرّة 618'],
    ['كاتب الجسر لا يعمل — الأمر لا يُكتب للمنصّة',
      atoms[601] ? !isRunning(601) : null, 'شغّل الذرّة 601'],
    ['قياس الانزلاق غير قابل للاستعمال',
      msgOf(563) ? msgOf(563).includes('NO_REQUESTED_PRICE') : null,
      'الوصل بين الطلب والتنفيذ انكسر — لا تُرقّع، ارجع للجذر'],
  ]
  const active = blockers.filter(([, on]) => on === true)
  const unknown = blockers.filter(([, on]) => on === null)

  return (
    <div>
      <AccountsPair />
      <div className="scard" style={{
        marginTop: 14,
        borderColor: active.length ? '#5a2020' : unknown.length ? '#4a4420' : '#1e5a2e',
      }}>
        <div className="st" style={{ color: active.length ? '#ff8080' : unknown.length ? '#f1c40f' : '#2ecc71' }}>
          {active.length
            ? `🔴 التداول متوقّف — ${active.length} مانع`
            : unknown.length
              ? '🟡 لا مانع معروف — وبعض الحالات غير مقروءة'
              : '🟢 لا شيء يمنع التداول'}
        </div>
        {active.map(([why, , how]) => (
          <div key={why} style={{ borderRight: '3px solid #e74c3c', paddingRight: 12, margin: '10px 0' }}>
            <b>{why}</b><br /><span className="ss dim">{how}</span>
          </div>
        ))}
        {unknown.length ? (
          <div className="ss dim" style={{ marginTop: 8 }}>
            غير مقروء الآن: {unknown.map(([why]) => why.split('—')[0].trim()).join(' · ')}
            <br />اللوحة لا تقول «تمام» عن حالة لم تصلها.
          </div>
        ) : null}
        {!active.length && !unknown.length ? (
          <div className="ss dim">القاطع مغلق · البوّابتان مفتوحتان · الأصل مفعَّل · الجسر يعمل.
            وهذا لا يعني أنّ صفقةً ستُفتح — يعني أنّ لا شيء يمنعها.</div>
        ) : null}
      </div>
      {/* بند 18 (دفتر 97) — «الخريطة»: ثلاث مسارات جاهزة فوق الأزرار الخام.
          كل مسار يضغط تسلسل الأزرار الصحيح بترتيبه، وكل خطوة خطرة بتأكيدها. */}
      <div className="scard" style={{ marginTop: 14, borderColor: 'var(--accent)' }}>
        <div className="st" style={{ color: 'var(--ink)' }}>🗺️ مسارات جاهزة — بدل ما تدوّر بين المفاتيح</div>
        <div className="ss dim">المسار بينفّذ الخطوات الصحيحة بترتيبها الصحيح، ويسألك تأكيدًا عند كل خطوة خطرة. الأزرار الدقيقة كلّها باقية تحت لمن بدّه يمسك التفاصيل.</div>
        <div style={{ display: 'flex', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
          <button className="btn" disabled={busy} onClick={() => void pathOpenTrading()}
            title="قاطع الأمان ← ذرّتا الجسر ← البوّابتان — وبيقلّك إذا باقي خطوة بإيدك"><TradingLed status={tradingStatus()} text={statusText(tradingStatus())} />▶️ افتح التداول (خطوة خطوة)</button>
          <button className="btn" disabled={busy} onClick={() => void pathStopAll()}
            title="إيقاف طارئ + إقفال البوّابتين"><TradingLed status={tradingStatus()} text={statusText(tradingStatus())} />🛑 أوقف كل شيء</button>
          <button className="btn" disabled={busy} onClick={() => void run('halt', {}, 'أُعلن الإيقاف الطارئ')}
            title="الإيقاف الطارئ وحده — بلا لمس البوّابات"><TradingLed status={tradingStatus()} text={statusText(tradingStatus())} />⛔ مسار الطوارئ فقط</button>
        </div>
      </div>

      <div className="scard" style={{ marginTop: 14, borderColor: '#5a2020' }}>
        <div className="st" style={{ color: '#ff8080' }}>🛑 الطوارئ</div>
        <div className="ss dim">الإيقاف الطارئ يوقف كل إرسال فورًا ويعلو على كل زرّ آخر في هذه الشاشة.
          والتصفير لا يُرفع تلقائيًّا أبدًا — بيدك وحدها.</div>
        <div style={{ display: 'flex', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
          <button className="btn" disabled={busy}
            onClick={() => void run('halt', {}, 'أُعلن الإيقاف الطارئ')}><TradingLed status={tradingStatus()} text={statusText(tradingStatus())} />🛑 إيقاف طارئ شامل</button>
          <button className="btn" disabled={busy}
            onClick={() => void run('kill_switch_reset', {}, 'صُفّر قاطع الأمان')}><TradingLed status={atomStatus(516)} text={statusText(atomStatus(516), 'قاطع الأمان')} />♻️ صفّر قاطع الأمان</button>
        </div>
      </div>

      <div className="scard" style={{ marginTop: 14 }}>
        <div className="st" style={{ color: 'var(--ink)' }}>🚪 بوّابتا التنفيذ</div>
        <div className="ss dim"><b>بوّابة الأوامر (552)</b> تقرّر إن كان الأمر الصالح يصل المنصّة فعلًا ·
          <b> مرسل الإدارة (575)</b> يحمل التعديلات على صفقة مفتوحة ولا يفتح صفقة أبدًا.
          <br />فتح البوّابة لا يرفع إيقافًا طارئًا أمرت به.</div>
        <div style={{ display: 'flex', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
          <button className="btn" disabled={busy} onClick={() => void run('execution_gate', { gate: '552', enabled: true }, 'فُتحت بوّابة الأوامر')}><TradingLed status={gateStatus()} text={statusText(gateStatus(), 'بوّابة الأوامر')} />▶️ افتح بوّابة الأوامر</button>
          <button className="btn" disabled={busy} onClick={() => void run('execution_gate', { gate: '552', enabled: false }, 'أُوقفت بوّابة الأوامر')}><TradingLed status={gateStatus()} text={statusText(gateStatus(), 'بوّابة الأوامر')} />⏹️ أوقف بوّابة الأوامر</button>
          <button className="btn" disabled={busy} onClick={() => void run('execution_gate', { gate: '575', enabled: true }, 'شُغّل مرسل الإدارة')}><TradingLed status={atomStatus(575)} text={statusText(atomStatus(575), 'مرسل الإدارة')} />▶️ شغّل مرسل الإدارة</button>
          <button className="btn" disabled={busy} onClick={() => void run('execution_gate', { gate: '575', enabled: false }, 'أُوقف مرسل الإدارة')}><TradingLed status={atomStatus(575)} text={statusText(atomStatus(575), 'مرسل الإدارة')} />⏹️ أوقف مرسل الإدارة</button>
          <button className="btn" disabled={busy} onClick={() => void run('execution_gate', { gate: 'both', enabled: true }, 'فُتح الاثنان')}><TradingLed status={gateStatus()} text={statusText(gateStatus(), 'بوّابتا التنفيذ')} />▶️ افتح الاثنين</button>
          <button className="btn" disabled={busy} onClick={() => void run('execution_gate', { gate: 'both', enabled: false }, 'أُوقف الاثنان')}><TradingLed status={gateStatus()} text={statusText(gateStatus(), 'بوّابتا التنفيذ')} />🛑 أوقف الاثنين</button>
        </div>
      </div>

      <div className="scard" style={{ marginTop: 14 }}>
        <div className="st" style={{ color: 'var(--ink)' }}>🎯 الأصل</div>
        <div className="cards" style={{ marginTop: 10 }}>
          <label className="scard"><span className="ss">حساب التنفيذ (ميتاتريدر 5)</span>
            <input className="cfginput num" value={account} onChange={(e) => setAccount(e.target.value)} placeholder="يُقرأ تلقائيًا" /></label>
          <label className="scard"><span className="ss">الأصل</span>
            <input className="cfginput" value={symbol} onChange={(e) => setSymbol(e.target.value)} /></label>
          <label className="scard"><span className="ss">الميزانية بالدولار</span>
            <input className="cfginput num" value={budget} onChange={(e) => setBudget(e.target.value)} inputMode="decimal" /></label>
          <label className="scard"><span className="ss">العيار (0–100)</span>
            <input className="cfginput num" value={dial} onChange={(e) => setDial(e.target.value)} inputMode="decimal" /></label>
          <label className="scard"><span className="ss">أقصى مراكز للرمز</span>
            <input className="cfginput num" value={maxPer} onChange={(e) => setMaxPer(e.target.value)} inputMode="numeric" /></label>
        </div>
        <div className="ss dim" style={{ marginTop: 6 }}>
          «أقصى مراكز للرمز» يحرس <b>فتح</b> مركز جديد فقط، ولا يمنع إدارة أو إغلاق ما هو مفتوح.
          كان 1 بينما التحوّط الدائم يفتح زوجًا — مركزين على الرمز نفسه — فحجب كلّ قرار لاحق.
        </div>
        <div style={{ display: 'flex', gap: 10, marginTop: 10, flexWrap: 'wrap' }}>
          <button className="btn" disabled={needAsset || !(Number(budget) > 0)}
            onClick={() => void run('activate_asset', { account_id: account.trim(), symbol: symbol.trim().toUpperCase(), budget: Number(budget) }, 'طُلب زوج البداية المحايد')}><TradingLed status={atomStatus(576)} text={statusText(atomStatus(576), 'الأصل')} />▶️ فعّل الأصل</button>
          <button className="btn" disabled={needAsset}
            onClick={() => void run('deactivate_asset', { account_id: account.trim(), symbol: symbol.trim().toUpperCase() }, 'أُلغي التفعيل')}><TradingLed status={atomStatus(576)} text={statusText(atomStatus(576), 'الأصل')} />⏹️ ألغِ التفعيل</button>
          <button className="btn" disabled={needAsset} onClick={() => void asset('PAUSE')}><TradingLed status={atomStatus(576)} text={statusText(atomStatus(576), 'الأصل')} />⏸️ إيقاف مؤقت</button>
          <button className="btn" disabled={needAsset} onClick={() => void asset('RESUME')}><TradingLed status={atomStatus(576)} text={statusText(atomStatus(576), 'الأصل')} />▶️ متابعة</button>
          <button className="btn" disabled={needAsset} onClick={() => void asset('FREEZE')}><TradingLed status={atomStatus(576)} text={statusText(atomStatus(576), 'الأصل')} />🧊 جمّد الزيادات</button>
          <button className="btn" disabled={needAsset} onClick={() => void asset('UNFREEZE')}><TradingLed status={atomStatus(576)} text={statusText(atomStatus(576), 'الأصل')} />🔥 ارفع التجميد</button>
          <button className="btn" disabled={needAsset} onClick={() => void asset('FORCE_RECONCILE')}><TradingLed status={atomStatus(576)} text={statusText(atomStatus(576), 'الأصل')} />🔄 مطابقة فوريّة</button>
          <button className="btn" disabled={needAsset || !(Number(dial) >= 0)} onClick={() => void asset('CALIBRATE', { dial: Number(dial) })}><TradingLed status={atomStatus(576)} text={statusText(atomStatus(576), 'الأصل')} />🎚️ اضبط العيار</button>
          <button className="btn" disabled={needAsset || !(Number(budget) > 0)} onClick={() => void asset('SET_BUDGET', { risk_budget: Number(budget) })}><TradingLed status={atomStatus(576)} text={statusText(atomStatus(576), 'الأصل')} />💵 اضبط الميزانية</button>
          <button className="btn" disabled={needAsset || !(Number(maxPer) >= 1)} onClick={() => void asset('SET_MAX_PER_SYMBOL', { max_per_symbol: Number(maxPer) })}><TradingLed status={atomStatus(576)} text={statusText(atomStatus(576), 'الأصل')} />🔢 اضبط أقصى مراكز للرمز</button>
        </div>
      </div>

      {GROUPS.map((group) => (
        <div className="scard" style={{ marginTop: 14 }} key={group}>
          <div className="st" style={{ color: 'var(--ink)' }}>⚙️ ذرّات — {group}</div>
          {/* بند 18/3 — جملة واحدة تشرح مسؤوليّة المجموعة */}
          <div className="ss dim">{GROUP_DESC[group]}</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: 8 }}>
            <tbody>
              {ATOMS.filter(([, , g]) => g === group).map(([id, name]) => (
                <tr key={id} style={{ borderBottom: '1px solid var(--line)' }}>
                  <td style={{ padding: '6px 8px', fontFamily: 'ui-monospace,monospace', width: 60 }}>{id}</td>
                  <td style={{ padding: '6px 8px' }}>{name}</td>
                  <td style={{ padding: '6px 8px', width: 90 }}>{pill(id)}</td>
                  <td className="ss dim" style={{ padding: '6px 8px' }}>
                    {arabicHealth(atoms[id]?.health?.message)}</td>
                  <td style={{ padding: '6px 8px', width: 200, textAlign: 'left' }}>
                    <button className="btn" disabled={busy} onClick={() => void atomPower(id, true)}><TradingLed status={atomStatus(id)} text={statusText(atomStatus(id), name)} />▶️ شغّل</button>{' '}
                    <button className="btn" disabled={busy} onClick={() => void atomPower(id, false)}><TradingLed status={atomStatus(id)} text={statusText(atomStatus(id), name)} />⏹️ أوقف</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}

      {message ? (
        <div className="scard" style={{ marginTop: 14, position: 'sticky', bottom: 10 }}>
          <b>{message}</b>
        </div>
      ) : null}
    </div>
  )
}
