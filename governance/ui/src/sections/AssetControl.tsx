// تحكم الأصل عبر 901 فقط — لا يوجد BUY/SELL مباشر من هذه الشاشة.
import { useEffect, useState } from 'react'
import { useStore } from '../core/store'
import { arabicVisible } from '../core/arabic'

type Command = 'PAUSE' | 'RESUME' | 'FREEZE' | 'UNFREEZE' | 'CALIBRATE' | 'FORCE_RECONCILE' | 'SET_BUDGET'

const COMMANDS: Array<[Command, string]> = [
  ['PAUSE', 'إيقاف مؤقت'],
  ['RESUME', 'متابعة'],
  ['FREEZE', 'تجميد الزيادات'],
  ['UNFREEZE', 'رفع التجميد'],
  ['CALIBRATE', 'ضبط العيار'],
  ['FORCE_RECONCILE', 'مطابقة فورية'],
  ['SET_BUDGET', 'تعديل ميزانية المخاطرة'],
]

async function send(action: 'asset_control' | 'activate_asset' | 'deactivate_asset' | 'execution_gate', payload: Record<string, unknown>): Promise<{ ok: boolean; message?: string; error?: string }> {
  const first = await fetch('/gov/command', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, payload }),
  })
  const one = await first.json() as { token?: string; summary?: string; ttl_s?: number; error?: string }
  if (!first.ok || !one.token) return { ok: false, error: one.error ?? 'تعذّر طلب التأكيد' }
  if (!window.confirm(`⚠️ ${one.summary ?? 'أمر خطر'}؟\nالتأكيد صالح ${one.ttl_s ?? 60} ثانية`)) return { ok: false, error: 'أُلغي' }
  const second = await fetch('/gov/command', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action, confirm: one.token, payload }),
  })
  const two = await second.json() as { message?: string; error?: string }
  return second.ok ? { ok: true, message: two.message } : { ok: false, error: two.error ?? 'رفضت البوابة الأمر' }
}

// ——— حزمة ج (ج٥، ختم 22): حالة تفعيل الأصل لكل رمز — من 576 مباشرة ———
const ENTRY_STATUS_AR: Record<string, { text: string; cls: string }> = {
  OPENED: { text: 'مفعّل — فُتح زوج البداية المحايد ✓', cls: 'green' },
  ALREADY_ACTIVE: { text: 'مفعّل (بالفعل)', cls: 'green' },
  MISSING_INPUTS: { text: 'غير مفعّل — مدخلات ناقصة (سعر/حجم/عيار)', cls: 'amber' },
  LOT_TOO_SMALL: { text: 'غير مفعّل — الحجم المحسوب أصغر من الحد الأدنى', cls: 'amber' },
  REJECTED: { text: 'رُفض التفعيل', cls: 'red' },
}

function AssetActivationBoard() {
  const states = useStore((s) => s.symbolStreams['perpetual.entry.state'] ?? {}) as Record<string, Record<string, unknown>>
  const rejects = useStore((s) => s.symbolStreams['perpetual.entry.rejected'] ?? {}) as Record<string, Record<string, unknown>>
  const counts = useStore((s) => s.assetRejectCounts)
  const syms = Array.from(new Set([...Object.keys(states), ...Object.keys(rejects)])).sort()
  return (
    <div className="scard" style={{ marginTop: 14, padding: 0, overflow: 'hidden' }}>
      <div style={{ padding: '11px 14px', borderBottom: '1px solid var(--glassb)' }}>
        <div className="st" style={{ fontWeight: 700 }}>حالة تفعيل الأصل — لكل رمز (576)</div>
        <div className="ss dim">
          من <code>perpetual.entry.state</code>/<code>perpetual.entry.rejected</code> مباشرة (لا حدث آخر يحمل هذه الحالة).
          الكود الحالي في 576 لا ينشر «من فعّله» (owner_command_id) ولا «متى» بحدث الحالة نفسه — يُعرض غيابهما صراحةً، لا اختراعًا.
        </div>
      </div>
      <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', padding: 10 }}>
        {syms.length === 0 ? <div className="dim" style={{ fontSize: 13.5 }}>لم يصل أي حدث تفعيل/رفض من 576 بعد.</div> : syms.map((sym) => {
          const st = states[sym]
          const rej = rejects[sym]
          const status = st ? String(st.status ?? '') : ''
          const info = st ? (ENTRY_STATUS_AR[status] ?? { text: arabicVisible(status, status), cls: 'grey' }) : { text: 'لا حالة بعد', cls: 'grey' }
          const n = counts[sym] ?? 0
          return (
            <div className="scard" key={sym} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <b>{sym}</b>
                <span className={`pill ${info.cls}`} style={{ marginInlineStart: 'auto' }}>{info.text}</span>
              </div>
              <div className="ss dim">من فعّله: غير متاح من الحدث الحالي · متى: غير متاح من الحدث الحالي</div>
              {n > 0 ? (
                <div className="ss" style={{ color: 'var(--red)' }}>
                  رفض «بلا أصل» (NO_PARENT_AUTHORITY): {n} مرّة
                  {rej?.origin ? ` — آخره من ${arabicVisible(rej.origin, String(rej.origin))}` : ''}
                </div>
              ) : null}
            </div>
          )
        })}
      </div>
    </div>
  )
}

export default function AssetControl() {
  const term = useStore((s) => s.streams['platform.terminal_state']) as { account_id?: string } | undefined
  const accountState = useStore((s) => s.streams['platform.account.state']) as { account_id?: string } | undefined
  const [account, setAccount] = useState('')
  const [symbol, setSymbol] = useState('BTCUSD')
  const [command, setCommand] = useState<Command>('PAUSE')
  const [budget, setBudget] = useState('100')
  const [dial, setDial] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    const found = term?.account_id || accountState?.account_id || ''
    if (found && !account) setAccount(String(found))
  }, [term?.account_id, accountState?.account_id, account])

  const run = async () => {
    setBusy(true); setMessage('')
    const payload: Record<string, unknown> = { account_id: account.trim(), symbol: symbol.trim().toUpperCase(), command }
    if (command === 'SET_BUDGET') payload.risk_budget = Number(budget)
    if (command === 'CALIBRATE') payload.dial = Number(dial)
    const result = await send('asset_control', payload)
    setMessage(result.ok ? `🟢 ${result.message ?? 'وصل الأمر إلى بوابة 901'}` : `🛑 ${result.error ?? 'فشل الأمر'}`)
    setBusy(false)
  }

  const activate = async () => {
    setBusy(true); setMessage('')
    const result = await send('activate_asset', {
      account_id: account.trim(), symbol: symbol.trim().toUpperCase(), budget: Number(budget),
    })
    setMessage(result.ok ? `🟢 ${result.message ?? 'طُلب زوج البداية المحايد عبر 901'}` : `🛑 ${result.error ?? 'فشل التفعيل'}`)
    setBusy(false)
  }

  // حكم المالك 2026-08-15: كل قفل له مفتاح على اللوحة. حالة «مفعّل» في 576
  // محفوظة على القرص، وبلا هذا الزرّ كان كل ضغط تفعيل يردّ «مفعّل أصلًا»
  // ولا تُفتح صفقة أبدًا — قفل بلا مفتاح.
  const deactivate = async () => {
    setBusy(true); setMessage('')
    const result = await send('deactivate_asset', {
      account_id: account.trim(), symbol: symbol.trim().toUpperCase(),
    })
    setMessage(result.ok ? `🟢 ${result.message ?? 'أُلغي التفعيل — صار الأصل جاهزًا للبدء من جديد'}` : `🛑 ${result.error ?? 'فشل إلغاء التفعيل'}`)
    setBusy(false)
  }

  // حكم المالك 2026-08-16: «حطّلهم زرار على لوحة المدير يتفعّلوا ويتوقّفوا».
  // البوّابتان كانتا تُفتحان بتعديل ملفّ وانتظار ترقية حيّة — ومرّة قُرئ
  // المانيفست نصف مكتوب فبقي التنفيذ مقفولًا والملفّ يقول مفتوح.
  const gate = async (which: '552' | '575' | 'both', enabled: boolean) => {
    setBusy(true); setMessage('')
    const result = await send('execution_gate', { gate: which, enabled })
    setMessage(result.ok
      ? `🟢 ${result.message ?? (enabled ? 'فُتحت البوّابة' : 'أُوقفت البوّابة')}`
      : `🛑 ${result.error ?? 'فشل الأمر'}`)
    setBusy(false)
  }

  return (
    <>
    <div className="scard" style={{ marginTop: 14 }}>
      <div className="st" style={{ color: 'var(--ink)' }}>بوّابتا التنفيذ — مفتاحك المباشر</div>
      <div className="ss dim">
        <b>بوّابة الأوامر (552)</b> تقرّر إن كان الأمر الصالح يوصل المنصّة فعلًا.
        <b> مرسل الإدارة (575)</b> يحمل التعديلات (تعادل · تتبّع · إغلاق جزئيّ) على صفقة مفتوحة — ولا يفتح صفقة أبدًا.
        <br />الإيقاف الطارئ يبقى فوق هذين الزرّين: فتح البوّابة لا يرفع إيقافًا أمرت به.
      </div>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
        <button className="btn" disabled={busy} onClick={() => void gate('552', true)}>▶️ افتح بوّابة الأوامر</button>
        <button className="btn" disabled={busy} onClick={() => void gate('552', false)}>⏹️ أوقف بوّابة الأوامر</button>
        <button className="btn" disabled={busy} onClick={() => void gate('575', true)}>▶️ شغّل مرسل الإدارة</button>
        <button className="btn" disabled={busy} onClick={() => void gate('575', false)}>⏹️ أوقف مرسل الإدارة</button>
        <button className="btn" disabled={busy} onClick={() => void gate('both', false)}>🛑 أوقف الاثنين معًا</button>
      </div>
    </div>
    <div className="scard" style={{ marginTop: 14 }}>
      <div className="st" style={{ color: 'var(--ink)' }}>تحكم الأصل — بوابة 901</div>
      <div className="ss dim">إيقاف مؤقت · متابعة · تجميد الزيادات · رفع التجميد · ضبط العيار · إعادة مطابقة · ضبط الميزانية. لا شراء ولا بيع مباشر من هنا.</div>
      <div className="cards" style={{ marginTop: 10 }}>
        <label className="scard"><span className="ss">الحساب</span><input className="cfginput num" value={account} onChange={(e) => setAccount(e.target.value)} placeholder="يُقرأ تلقائيًا" /></label>
        <label className="scard"><span className="ss">الأصل</span><input className="cfginput" value={symbol} onChange={(e) => setSymbol(e.target.value)} /></label>
        <label className="scard"><span className="ss">الأمر</span><select className="cfginput" value={command} onChange={(e) => setCommand(e.target.value as Command)}>{COMMANDS.map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select></label>
        {command === 'SET_BUDGET' ? <label className="scard"><span className="ss">ميزانية المخاطرة بالدولار</span><input className="cfginput num" value={budget} onChange={(e) => setBudget(e.target.value)} inputMode="decimal" /></label> : null}
        {command === 'CALIBRATE' ? <label className="scard"><span className="ss">العيار</span><input className="cfginput num" value={dial} onChange={(e) => setDial(e.target.value)} inputMode="decimal" placeholder="أدخل القيمة" /></label> : null}
      </div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 10 }}>
        <button className="btn" disabled={busy || !account.trim() || !symbol.trim()} onClick={() => void run()}>{busy ? '⏳ عم يرسل…' : 'أرسل عبر 901'}</button>
        {message ? <span className="dim">{message}</span> : null}
      </div>
      <div className="scard" style={{ marginTop: 14 }}>
        <div className="st" style={{ color: 'var(--ink)' }}>تفعيل أصل — زوج بداية محايد</div>
        <div className="ss dim">يفتح شراءً وبيعًا متساويين بالميزانية المحددة (الدستور §1) بعد تأكيد مزدوج — من اللوحة، لا سكربتات خارجية.</div>
        <div className="cards" style={{ marginTop: 10 }}>
          <label className="scard"><span className="ss">الميزانية بالدولار</span><input className="cfginput num" value={budget} onChange={(e) => setBudget(e.target.value)} inputMode="decimal" /></label>
        </div>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 10, flexWrap: 'wrap' }}>
          <button className="btn" disabled={busy || !account.trim() || !symbol.trim() || !(Number(budget) > 0)} onClick={() => void activate()}>{busy ? '⏳ عم يرسل…' : '▶️ فعّل الأصل'}</button>
          <button className="btn" disabled={busy || !account.trim() || !symbol.trim()} onClick={() => void deactivate()}>{busy ? '⏳ عم يرسل…' : '⏹️ ألغِ التفعيل'}</button>
        </div>
        <div className="ss dim" style={{ marginTop: 8 }}>«ألغِ التفعيل» يمسح حالة «مفعّل» المحفوظة. إذا صار الأصل مفعّلًا بالورق وما في ولا مركز بالسوق، اضغطه ثم «فعّل الأصل» من جديد.</div>
      </div>
      <AssetActivationBoard />
    </div>
    </>
  )
}
