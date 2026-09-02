// الأمان وخزنة الأسرار — إدارة كاملة من اللوحة (أمر المالك: «مو من أكواد»).
// إنشاء الخزنة · إضافة سرّ · حذف سرّ · رؤية الأسماء الموجودة — بلا فتح ملف.
// القيم السرّيّة لا تُرسل للّوحة ولا تُعرض ولا تُسجَّل: الأسماء فقط.
import { useEffect, useState } from 'react'
import { arabicVisible } from '../core/arabic'

interface ToolResult { ok: boolean; code: number; output: string }
interface VaultStatus { exists: boolean; path?: string; size?: number; kdf?: string; valid?: boolean }
interface AuditRow { at: string; source: string; op: string; key: string; result: string; vault?: string }
interface VaultReply { ok?: boolean; message?: string; error?: string; keys?: string[]; status?: VaultStatus; audit?: AuditRow[]; windows_bound?: boolean }

const OP_AR: Record<string, string> = {
  init: 'إنشاء الخزنة', list: 'فتح وقراءة الأسماء', set: 'إضافة/تعديل سرّ',
  remove: 'حذف سرّ', rotate: 'تغيير عبارة المرور',
}
const SRC_AR: Record<string, string> = { panel: 'اللوحة', cli: 'سطر الأوامر' }

async function vault(body: Record<string, unknown>): Promise<VaultReply> {
  try {
    const r = await fetch('/gov/vault', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    return (await r.json()) as VaultReply
  } catch (e) {
    return { ok: false, error: 'خطأ اتصال: ' + String(e) }
  }
}

function readState(output: string): string {
  return output.match(/SECURITY_STATE=([^\s]+)/)?.[1] ?? 'UNKNOWN'
}

function label(state: string): { text: string; color: string } {
  if (state === 'READY') return { text: 'جاهزة', color: 'var(--green)' }
  if (state === 'NOT_CONFIGURED') return { text: 'الخزنة غير منشأة', color: 'var(--amber)' }
  if (state === 'LOCKED') return { text: 'الخزنة مقفلة', color: 'var(--red)' }
  if (state === 'DISABLED') return { text: 'الأمان معطّل', color: 'var(--red)' }
  if (state === 'DEPENDENCY_MISSING') return { text: 'مكتبة الأمان ناقصة', color: 'var(--amber)' }
  return { text: 'غير مثبتة', color: 'var(--red)' }
}

const inputStyle: React.CSSProperties = {
  background: 'rgba(0,0,0,.3)', border: '1px solid var(--line)', borderRadius: 8,
  color: 'var(--ink)', padding: '7px 10px', fontSize: 13, minWidth: 190,
  fontFamily: 'inherit',
}

// باللمس ما في ضغطة مطوّلة، والعبارة والتوكن طويلان ولا يُكتبان بالإصبع.
// زرّ لصق صريح جنب كل خانة — ولا يعتمد على قائمة يخفيها النظام.
function Paste({ onText }: { onText: (t: string) => void }) {
  const [hint, setHint] = useState('')
  const paste = async () => {
    if (!navigator.clipboard || !window.isSecureContext) { setHint('على الجهاز فقط'); return }
    try {
      const t = await navigator.clipboard.readText()
      if (t) { onText(t); setHint('✓') } else setHint('فاضية')
    } catch { setHint('ممنوع') }
    window.setTimeout(() => setHint(''), 1500)
  }
  return (
    <button className="btn" onClick={() => void paste()} title="لصق من الحافظة"
      style={{ padding: '6px 10px' }}>
      {hint || '📋 لصق'}
    </button>
  )
}

export default function Security() {
  const [check, setCheck] = useState<ToolResult | null>(null)
  const [checking, setChecking] = useState(false)
  const [raw, setRaw] = useState(false)

  const [status, setStatus] = useState<VaultStatus | null>(null)
  const [audit, setAudit] = useState<AuditRow[]>([])
  const [pass, setPass] = useState('')
  const [pass2, setPass2] = useState('')
  const [keys, setKeys] = useState<string[] | null>(null)
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [confirmDel, setConfirmDel] = useState('')
  const [bound, setBound] = useState(false)
  const [confirmArchive, setConfirmArchive] = useState(false)

  const say = (r: VaultReply) => setMsg({ text: (/[A-Za-z]/.test(r.message ?? r.error ?? '') ? 'تفصيل تقني غير مترجم' : (r.message ?? r.error ?? '')), ok: !!r.ok })

  const loadStatus = async () => {
    const r = await vault({ op: 'status' })
    if (r.status) setStatus(r.status)
    if (r.audit) setAudit(r.audit)
    setBound(!!r.windows_bound)
  }

  const runCheck = async () => {
    setChecking(true)
    try {
      const r = await fetch('/gov/tool/security', { method: 'POST' })
      const j = (await r.json()) as ToolResult & { error?: string }
      setCheck(j.error ? { ok: false, code: -1, output: j.error } : j)
    } catch (e) {
      setCheck({ ok: false, code: -1, output: 'خطأ اتصال: ' + String(e) })
    }
    setChecking(false)
  }

  useEffect(() => {
    void runCheck(); void loadStatus()
    const t = window.setInterval(() => { void runCheck(); void loadStatus() }, 30000)
    return () => window.clearInterval(t)
  }, [])

  const guard = async (fn: () => Promise<VaultReply>) => {
    setBusy(true)
    const r = await fn()
    say(r); setBusy(false); void loadStatus()
    return r
  }

  const doInit = () => {
    if (pass !== pass2) { setMsg({ text: 'العبارتان غير متطابقتين.', ok: false }); return }
    void guard(async () => {
      const r = await vault({ op: 'init', passphrase: pass })
      if (r.ok) { setPass2(''); setKeys([]) }
      return r
    })
  }

  const doOpen = () => void guard(async () => {
    const r = await vault({ op: 'list', passphrase: pass })
    setKeys(r.ok ? (r.keys ?? []) : null)
    return r
  })

  const doAdd = () => void guard(async () => {
    const r = await vault({ op: 'set', passphrase: pass, key: newKey.trim(), value: newValue })
    if (r.ok) {
      setNewKey(''); setNewValue('')
      const l = await vault({ op: 'list', passphrase: pass })
      if (l.ok) setKeys(l.keys ?? [])
    }
    return r
  })

  const doRemove = (k: string) => void guard(async () => {
    const r = await vault({ op: 'remove', passphrase: pass, key: k })
    setConfirmDel('')
    if (r.ok) setKeys((prev) => (prev ?? []).filter((x) => x !== k))
    return r
  })

  const cur = check ? label(readState(check.output)) : { text: 'جارِ الفحص…', color: 'var(--dim)' }
  const exists = !!status?.exists

  return (
    <div className="section" style={{ display: 'flex', flexDirection: 'column', gap: 14, overflow: 'auto' }}>
      <div className="cards">
        <div className="scard">
          <div className="st">طبقة الأمان</div>
          <div className="sv" style={{ color: cur.color }}>{cur.text}</div>
          <div className="ss dim">فحص بلا عرض للقيم السرّيّة</div>
        </div>
        <div className="scard">
          <div className="st">خزنة الأسرار</div>
          <div className="sv" style={{ color: exists ? 'var(--green)' : 'var(--amber)' }}>
            {exists ? 'موجودة' : 'غير موجودة'}
          </div>
          <div className="ss dim">{exists ? `${status?.kdf ?? '—'} · ${status?.size ?? 0} بايت` : 'تُنشأ من هنا بزرّ'}</div>
        </div>
        <div className="scard">
          <div className="st">الأسرار المحفوظة</div>
          <div className="sv">{keys ? keys.length : '—'}</div>
          <div className="ss dim">{keys ? 'الأسماء فقط — القيم لا تخرج أبدًا' : 'افتح الخزنة لتعرف'}</div>
        </div>
        <div className="scard">
          <div className="st">عبارة المرور</div>
          <div className="sv green">لا تُخزَّن</div>
          <div className="ss dim">ولا تخرج من هذا الجهاز</div>
        </div>
      </div>

      {msg ? (
        <div className="scard" style={{ borderColor: msg.ok ? 'var(--green)' : 'var(--red)' }}>
          <div className="ss" style={{ color: msg.ok ? 'var(--green)' : 'var(--red)' }}>
            {msg.ok ? '✅ ' : '⛔ '}{msg.text}
          </div>
        </div>
      ) : null}

      {!exists ? (
        <div className="scard" style={{ borderColor: 'var(--amber)' }}>
          <div className="st" style={{ fontSize: 15, color: 'var(--ink)' }}>إنشاء الخزنة</div>
          <div className="ss dim" style={{ marginBottom: 10 }}>
            تختار عبارة مرور (١٢ محرفًا فأكثر) وتحفظها بنفسك — <b>لا تُخزَّن بأي مكان</b>،
            ولو ضاعت لا يمكن استرجاع ما بداخلها. وهي نفسها التي تفتح أسرار النظام كلّه.
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <input type="password" autoComplete="new-password" style={inputStyle}
              placeholder="عبارة المرور" value={pass} onChange={(e) => setPass(e.target.value)} />
            <Paste onText={setPass} />
            <input type="password" autoComplete="new-password" style={inputStyle}
              placeholder="أعِدها للتأكيد" value={pass2} onChange={(e) => setPass2(e.target.value)} />
            <Paste onText={setPass2} />
            <button className="btn" disabled={busy || pass.length < 12} onClick={doInit}>
              {busy ? '⏳ …' : '🔐 أنشئ الخزنة'}
            </button>
          </div>
        </div>
      ) : (
        <div className="scard">
          <div className="st" style={{ fontSize: 15, color: 'var(--ink)' }}>افتح الخزنة</div>
          <div className="ss dim" style={{ marginBottom: 10 }}>
            العبارة تُستعمل لحظة العمليّة فقط، ولا تُحفظ ولا تُرسل لأي مكان خارج هذا الجهاز.
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
            <input type="password" autoComplete="current-password" style={inputStyle}
              placeholder="عبارة المرور" value={pass} onChange={(e) => setPass(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') doOpen() }} />
            <Paste onText={setPass} />
            <button className="btn" disabled={busy || !pass} onClick={doOpen}>
              {busy ? '⏳ …' : '🔓 أرِني الأسرار الموجودة'}
            </button>
            {keys ? (
              <button className="btn" onClick={() => { setKeys(null); setPass(''); setMsg(null) }}>
                🔒 أقفل
              </button>
            ) : null}
          </div>
        </div>
      )}

      {exists && !keys ? (
        <div className="scard" style={{ borderColor: 'var(--amber)' }}>
          <div className="st" style={{ fontSize: 15, color: 'var(--amber)' }}>ما بتذكّر عبارة المرور؟</div>
          <div className="ss dim" style={{ marginBottom: 10 }}>
            ما في استرجاع — هيك تصميمها عمدًا (٦٠٠ ألف دورة اشتقاق)، ولا أحد يقدر يفتحها بدونها.
            الحلّ الوحيد: <b>نزيح القديمة جانبًا</b> (ما تنحذف — تبقى على القرص باسم مؤرَّخ)
            وتنشئ وحدة جديدة بعبارة تختارها. <b>واللي جوّا القديمة بيروح.</b>
          </div>
          {confirmArchive ? (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <span className="ss" style={{ color: 'var(--red)' }}>متأكّد؟ اللي جوّاتها ما بيرجع.</span>
              <button className="btn" style={{ borderColor: 'var(--red)', color: 'var(--red)' }}
                disabled={busy}
                onClick={() => void guard(async () => {
                  const r = await vault({ op: 'archive' })
                  if (r.ok) { setConfirmArchive(false); setPass(''); setKeys(null) }
                  return r
                })}>
                نعم، أزِحها وابدأ جديدة
              </button>
              <button className="btn" onClick={() => setConfirmArchive(false)}>تراجع</button>
            </div>
          ) : (
            <button className="btn" onClick={() => setConfirmArchive(true)}>
              🗄 أزِح القديمة وابدأ جديدة
            </button>
          )}
        </div>
      ) : null}

      {keys ? (
        <>
          <div className="scard" style={{ borderColor: bound ? 'var(--green)' : undefined }}>
            <div className="st" style={{ fontSize: 15, color: 'var(--ink)' }}>
              الفتح بحساب ويندوز {bound ? '— مربوطة ✅' : ''}
            </div>
            <div className="ss dim" style={{ marginBottom: 10 }}>
              {bound
                ? 'الخزنة مربوطة بحساب ويندوز تبعك: بتنفتح لحالها بلا عبارة وبلا متغيّر بيئة. وعبارتك تبقى صالحة كما هي.'
                : 'بتربطها بحساب ويندوز تبعك مرّة وحدة، وبعدها بتنفتح لحالها بلا ما تكتب عبارة كل مرّة. الملفّ الناتج ما بينفع على جهاز تاني ولا بحساب تاني — وعبارتك بتضلّ صالحة.'}
            </div>
            <button className="btn" disabled={busy}
              onClick={() => void guard(() => vault({ op: 'bind_windows', passphrase: pass }))}>
              {busy ? '⏳ …' : bound ? '🔁 أعِد الربط' : '🪟 اربطها بحساب ويندوز'}
            </button>
          </div>

          <div className="scard">
            <div className="st" style={{ fontSize: 15, color: 'var(--ink)' }}>
              الأسرار المحفوظة ({keys.length})
            </div>
            <div className="ss dim" style={{ marginBottom: 10 }}>
              الأسماء فقط. <b>القيمة لا تُعرض ولا تُرسل للّوحة أبدًا</b> — لتغييرها اكتبها من جديد بالأسفل.
            </div>
            {keys.length === 0 ? (
              <div className="ss dim">الخزنة فاضية — أضف أوّل سرّ بالأسفل.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                {keys.map((k) => (
                  <div key={k} style={{
                    display: 'flex', alignItems: 'center', gap: 10, padding: '7px 10px',
                    background: 'rgba(0,0,0,.2)', borderRadius: 8,
                  }}>
                    <span style={{ flex: 1, fontFamily: 'monospace', fontSize: 13 }} dir="ltr">{k}</span>
                    <span className="ss dim">••••••••</span>
                    {confirmDel === k ? (
                      <>
                        <span className="ss" style={{ color: 'var(--red)' }}>متأكّد؟</span>
                        <button className="btn" style={{ borderColor: 'var(--red)', color: 'var(--red)' }}
                          disabled={busy} onClick={() => doRemove(k)}>نعم، احذفه</button>
                        <button className="btn" onClick={() => setConfirmDel('')}>تراجع</button>
                      </>
                    ) : (
                      <button className="btn" onClick={() => setConfirmDel(k)}>🗑 حذف</button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="scard">
            <div className="st" style={{ fontSize: 15, color: 'var(--ink)' }}>إضافة سرّ (أو تعديل قيمته)</div>
            <div className="ss dim" style={{ marginBottom: 10 }}>
              الاسم بالإنكليزيّة: حروف وأرقام و <code>_ . -</code> فقط.
              مثال يخصّ منصّة تلغرام: <code dir="ltr">telegram_bot_token</code>
            </div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
              <input type="text" dir="ltr" style={inputStyle} placeholder="اسم السرّ"
                value={newKey} onChange={(e) => setNewKey(e.target.value)} />
              <button className="btn" style={{ padding: '6px 10px' }}
                onClick={() => setNewKey('telegram_bot_token')}>توكن تلغرام</button>
              <input type="password" autoComplete="off" dir="ltr" style={{ ...inputStyle, minWidth: 260 }}
                placeholder="القيمة (مخفيّة)" value={newValue} onChange={(e) => setNewValue(e.target.value)} />
              <Paste onText={setNewValue} />
              <button className="btn" disabled={busy || !newKey.trim() || !newValue} onClick={doAdd}>
                {busy ? '⏳ …' : '➕ احفظ بالخزنة'}
              </button>
            </div>
          </div>
        </>
      ) : null}

      {audit.length ? (
        <div className="scard">
          <div className="st" style={{ fontSize: 15, color: 'var(--ink)' }}>سجلّ عمليّات الخزنة</div>
          <div className="ss dim" style={{ marginBottom: 8 }}>ماذا جرى ومتى ومن أين — <b>بلا أي قيمة</b>.</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {audit.map((a, i) => (
              <div key={i} className="ss" style={{ display: 'flex', gap: 10, alignItems: 'baseline' }}>
                <span className="dim" style={{ minWidth: 132 }} dir="ltr">{a.at}</span>
                <span style={{ color: a.result === 'ok' ? 'var(--green)' : 'var(--red)' }}>
                  {a.result === 'ok' ? '✅' : '⛔'}
                </span>
                <span>{OP_AR[a.op] ?? arabicVisible(a.op, 'عمليّة غير مترجَمة')}</span>
                {a.key !== '-' ? <code dir="ltr" style={{ fontSize: 11.5 }}>{a.key}</code> : null}
                <span className="dim">· {SRC_AR[a.source] ?? arabicVisible(a.source, 'مصدر غير مترجَم')}</span>
                {a.vault && a.vault.includes('غير خزنة النظام')
                  ? <span style={{ color: 'var(--amber)' }}>· على خزنة غير خزنة النظام</span> : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="scard">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ flex: 1 }}>
            <div className="st" style={{ fontSize: 15, color: 'var(--ink)' }}>فحص الأمان والخزنة</div>
            <div className="ss dim">فحص طبقة الأمان، صيغة الخزنة، القفل، والتكوين. لا يطبع أي مفتاح أو قيمة.</div>
          </div>
          <button className="btn" disabled={checking} onClick={() => void runCheck()}>
            {checking ? '⏳ عم يفحص…' : 'افحص الآن'}
          </button>
        </div>
        {check ? (
          <div style={{ marginTop: 10 }}>
            <button className="btn" style={{ fontSize: 11, padding: '3px 10px' }} onClick={() => setRaw(!raw)}>
              {raw ? 'خبّي نتيجة الفحص' : 'ورّيني نتيجة الفحص'}
            </button>
            {raw ? <pre dir="ltr" style={{ marginTop: 6, maxHeight: 260, overflow: 'auto', fontSize: 11.5, background: 'rgba(0,0,0,.25)', borderRadius: 8, padding: 10, whiteSpace: 'pre-wrap' }}>{check.output}</pre> : null}
          </div>
        ) : null}
      </div>
    </div>
  )
}
