// المدير (850) — إدارة وتشغيل: صحة النظام + المنصّة + تحكّم الأصل (تجميع الحيّ).
// بند 7 (دفتر 97، مثال المالك الحرفي): لا محتوى ماليًّا هنا — الرصيد والربح
// مكانهما «المحافظ» و«الرئيسية» وهما فيهما فعلًا.
import { useMemo } from 'react'
import { useStore } from '../core/store'
import AssetControl from './AssetControl'

interface Term { connected: boolean; trade_allowed: boolean }

export default function Manager() {
  const atoms = useStore((s) => s.atoms)
  const term = useStore((s) => s.streams['platform.terminal_state']) as Term | undefined
  const conn = useStore((s) => s.conn)

  const c = useMemo(() => {
    const o: Record<string, number> = { green: 0, amber: 0, red: 0, grey: 0, total: 0 }
    for (const a of Object.values(atoms)) { o[a.color ?? 'grey']++; o.total++ }
    return o
  }, [atoms])

  return (
    <div className="section">
      <div className="cards">
        <div className="scard"><div className="st">النظام</div><div className={`sv ${c.red ? 'red' : 'green'}`}>{c.red ? 'فيه خلل' : 'سليم'}</div><div className="ss">{c.total} ذرة · {c.green} سليمة · {c.amber} بانتظار</div></div>
        <div className="scard"><div className="st">خلل حقيقي</div><div className={`sv num ${c.red ? 'red' : 'green'}`}>{c.red}</div></div>
        {/* بند 7 (دفتر 97، مثال المالك الحرفي): المحتوى المالي لا يُحشر تحت
            «المدير» — الرصيد والربح مكانهما «المحافظ» و«الرئيسية» وهما فيهما فعلًا */}
        <div className="scard"><div className="st">المنصّة</div><div className={`sv ${term?.connected ? 'green' : 'red'}`}>{term?.connected ? 'متّصلة' : conn === 'live' ? '…' : 'مقطوعة'}</div><div className="ss">{term?.trade_allowed ? 'التداول مسموح' : ''}</div></div>
        <div className="scard"><div className="st">اللوحة ↔ النواة</div><div className={`sv ${conn === 'live' ? 'green' : 'red'}`}>{conn === 'live' ? 'حيّة' : 'مقطوعة'}</div></div>
      </div>
      <AssetControl />
    </div>
  )
}
