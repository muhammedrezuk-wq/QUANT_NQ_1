// المحافظ (٨٥٨) — حسابك الحقيقي + الصفقات المفتوحة (بثّ حيّ: platform.account.state / positions.state).
import { useStore } from '../core/store'
import { SectionAtomsHealth } from '../components/SectionAtoms'
import { AccountsPair } from '../components/AccountsBar'

interface Account { account_id: string; balance: number; equity: number; margin: number; free_margin: number; leverage: number; broker?: string }
interface Pos { ticket: number; symbol: string; side: string; volume: number; entry_price: number; current_price: number }
interface Positions { floating_pnl: number; positions: Pos[] }

const money = (n?: number) => (n == null ? '—' : n.toLocaleString('ar-EG-u-nu-latn', { minimumFractionDigits: 2, maximumFractionDigits: 2 }))

export default function Portfolios() {
  const acc = useStore((s) => s.streams['platform.account.state']) as Account | undefined
  const pos = useStore((s) => s.streams['platform.positions.state']) as Positions | undefined
  if (!acc) {
    // بند ١٠ (ورقة ٩٩): بدل الفراغ — حالة ذرّات الجسر التي تجيب بيانات الحساب نفسها
    return (
      <div className="section chartsec">
        <AccountsPair />
        <div className="empty">جارِ استقبال بيانات حساب التنفيذ من النواة…</div>
        <SectionAtomsHealth ids={[618, 619, 609, 611]} title="ذرّات جسر الحساب — حالتها الحيّة الآن"
          note="الرصيد والصفقات من حساب التنفيذ: جسر ميتاتريدر 5 (618) · حالة الحساب (619) · مزامنة المراكز (609) · قارئ الصفقات (611). حساب سي‑تريدر بيانات فقط وما بيظهر هون كرصيد." />
      </div>
    )
  }
  const pnl = pos?.floating_pnl ?? 0
  const list = pos?.positions ?? []
  return (
    <div className="section" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div className="cards">
        <div className="scard"><div className="st">الرصيد</div><div className="sv num">${money(acc.balance)}</div><div className="ss">{acc.broker ?? ''}</div></div>
        {/* «الأسهم» كانت ترجمة غلط لـEquity — قيمة الحساب الفعليّة بالربح العائم */}
        <div className="scard"><div className="st">قيمة الحساب</div><div className="sv num">${money(acc.equity)}</div></div>
        <div className="scard"><div className="st">ربح مفتوح</div><div className={`sv num ${pnl >= 0 ? 'green' : 'red'}`}>{pnl >= 0 ? '+' : ''}{money(pnl)}</div></div>
        <div className="scard"><div className="st">الهامش المستخدم</div><div className="sv num">${money(acc.margin)}</div></div>
        <div className="scard"><div className="st">السيولة الحرّة</div><div className="sv num">${money(acc.free_margin)}</div></div>
        {/* بند 6 (دفتر 97): كل رقم حساب يُوسم — هذا حساب «تنفيذ» (ميتاتريدر 5) */}
        <div className="scard"><div className="st">الرافعة</div><div className="sv num">1:{acc.leverage}</div><div className="ss">حساب تنفيذ {acc.account_id}</div></div>
      </div>
      <div className="loglist" style={{ flex: 1 }}>
        {list.length === 0 ? <div className="empty">لا صفقات مفتوحة</div> : null}
        {list.map((p) => (
          <div className="logrow" key={p.ticket}>
            <span className="ln">{p.symbol}</span>
            <span className={p.side === 'BUY' ? 'green' : 'red'}>{p.side === 'BUY' ? 'شراء' : 'بيع'}</span>
            <span className="dim num">حجم {p.volume}</span>
            <span className="dim num">دخول {money(p.entry_price)}</span>
            <span className="num" style={{ marginInlineStart: 'auto' }}>السعر الآن {money(p.current_price)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
