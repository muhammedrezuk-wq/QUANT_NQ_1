// الاتصال — الحسابان جنب بعض، مو مدفونين.
// تحليل = سي-تريدر (بيانات فقط). تنفيذ = ميتاتريدر 5 (عليه الصفقة).
import { useStore } from '../core/store'
import { AccountsPair } from '../components/AccountsBar'

interface Term { account_id: string; connected: boolean; trade_allowed: boolean; expert_allowed: boolean }

const ANALYSIS_BRIDGES: Array<[number, string]> = [
  [622, 'تغذية سي‑تريدر — مصدر التحليل'],
]
const EXEC_BRIDGES: Array<[number, string]> = [
  [618, 'جسر ميتاتريدر 5 — تكة التنفيذ'],
  [601, 'كاتب جسر الدماغ'],
  [611, 'قارئ الصفقات'],
  [619, 'حالة الحساب'],
  [609, 'مزامنة المراكز'],
]

function BridgeList({ rows }: { rows: Array<[number, string]> }) {
  const atoms = useStore((s) => s.atoms)
  return (
    <div className="loglist">
      {rows.map(([id, name]) => {
        const a = atoms[id]
        return (
          <div className="logrow" key={id}>
            <span className="ln">{name}</span>
            <span className="dim num">#{id}</span>
            <span className={`${a?.color ?? 'grey'}`} style={{ marginInlineStart: 'auto' }}>● {a?.label_ar ?? 'غير محمّلة'}</span>
          </div>
        )
      })}
    </div>
  )
}

export default function Connection({ embedded = false }: { embedded?: boolean }) {
  const term = useStore((s) => s.streams['platform.terminal_state']) as Term | undefined
  const conn = useStore((s) => s.conn)
  const yn = (b?: boolean) => (b ? 'نعم' : 'لا')
  return (
    <div className={embedded ? undefined : 'section'} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <AccountsPair />
      <div className="cards">
        <div className="scard">
          <div className="st">منصّة ميتاتريدر 5</div>
          <div className={`sv ${term?.connected ? 'green' : 'red'}`}>{term?.connected ? 'متّصلة' : '—'}</div>
          <div className="ss">حساب التنفيذ {term?.account_id ?? '—'} — عليه تُفتح الصفقات فعليًّا</div>
        </div>
        <div className="scard">
          <div className="st">التداول مسموح</div>
          <div className={`sv ${term?.trade_allowed ? 'green' : 'red'}`}>{term ? yn(term.trade_allowed) : '—'}</div>
        </div>
        <div className="scard">
          <div className="st">الإكسبرت مسموح</div>
          <div className={`sv ${term?.expert_allowed ? 'green' : 'red'}`}>{term ? yn(term.expert_allowed) : '—'}</div>
        </div>
        <div className="scard">
          <div className="st">اللوحة ↔ النواة</div>
          <div className={`sv ${conn === 'live' ? 'green' : 'red'}`}>{conn === 'live' ? 'متّصلة' : 'مقطوعة'}</div>
        </div>
      </div>
      <div className="scard">
        <div className="st">ذرّات التحليل (سي‑تريدر)</div>
        <div className="ss dim" style={{ marginBottom: 6 }}>مصدر الأسعار للتحليل — لا يكتب أمرًا للمنصّة.</div>
        <BridgeList rows={ANALYSIS_BRIDGES} />
      </div>
      <div className="scard">
        <div className="st">ذرّات التنفيذ (ميتاتريدر 5)</div>
        <div className="ss dim" style={{ marginBottom: 6 }}>تكة الوسيط + الحساب + المراكز + كتابة الأمر. مو مصدر التحليل.</div>
        <BridgeList rows={EXEC_BRIDGES} />
      </div>
    </div>
  )
}
