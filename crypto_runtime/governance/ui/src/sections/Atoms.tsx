// شاشة الذرات (قائمة) — شبكة بحجم خليّة مقروء ثابت؛ يلي ما بيسع بلمحة وحدة
// يتقسّم صفحات (قرار المالك 2026-08-19: «يلي ما بيسع بلقطة وحدة، اعملّه صفحتين» —
// لا سكرول، ولا تقليص لخليّة أضيق من اسمها العربي أبدًا).
import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useStore, type AtomRec } from '../core/store'
import { registerWidget } from '../core/widgets'

registerWidget({ id: 'atoms-grid', section: 'atoms', streams: ['snapshot:atoms'], title: 'شبكة الذرات' })

// أصغر خليّة تبقى مقروءة: اسم عربي بخط .cnm (13.5px) بلا قصّ فاضح + رقم المعرّف.
const MIN_CELL_W = 132
const MIN_CELL_H = 56
const GAP = 7

function gridFor(w: number, h: number, n: number): { cols: number; rows: number } {
  if (n <= 0 || w <= 0 || h <= 0) return { cols: 1, rows: 1 }
  const maxCols = Math.max(1, Math.floor((w + GAP) / (MIN_CELL_W + GAP)))
  const maxRows = Math.max(1, Math.floor((h + GAP) / (MIN_CELL_H + GAP)))
  const cols = Math.min(maxCols, n)
  const rows = Math.min(maxRows, Math.ceil(n / cols))
  return { cols, rows }
}

export default function Atoms() {
  const atomsMap = useStore((s) => s.atoms)
  const select = useStore((s) => s.select)
  const gridRef = useRef<HTMLDivElement>(null)
  const [grid, setGrid] = useState({ cols: 8, rows: 4 })
  const [page, setPage] = useState(0)

  const list = useMemo(() => {
    // ترتيب ثابت بالمعرّف: تغيّر لون/حالة الذرّة لا ينقل الزر إلى مكان آخر.
    // هكذا يستطيع المالك متابعة نفس الزر حتى مع وصول النبضات الحيّة.
    return Object.values(atomsMap).sort((a, b) => a.id - b.id)
  }, [atomsMap])

  const counts = useMemo(() => {
    const c: Record<string, number> = { green: 0, amber: 0, red: 0, grey: 0 }
    for (const a of list) c[a.color ?? 'grey']++
    return c
  }, [list])

  useLayoutEffect(() => {
    const el = gridRef.current
    if (!el) return
    let raf = 0
    const recompute = () => {
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => {
        const r = el.getBoundingClientRect()
        setGrid(gridFor(r.width, r.height, list.length || 1))
      })
    }
    recompute()
    const ro = new ResizeObserver(recompute)
    ro.observe(el)
    window.addEventListener('resize', recompute)
    return () => { cancelAnimationFrame(raf); ro.disconnect(); window.removeEventListener('resize', recompute) }
  }, [list.length])

  const perPage = Math.max(1, grid.cols * grid.rows)
  const pageCount = Math.max(1, Math.ceil(list.length / perPage))
  const safePage = Math.min(page, pageCount - 1)
  useLayoutEffect(() => { if (page !== safePage) setPage(safePage) }, [page, safePage])
  const shown = list.slice(safePage * perPage, safePage * perPage + perPage)

  return (
    <div className="atoms">
      <div className="strip">
        <span className="cnt green">● {counts.green} سليمة</span>
        <span className="cnt amber">● {counts.amber} متعثّرة</span>
        <span className="cnt red">● {counts.red} خلل</span>
        <span className="cnt grey">● {counts.grey} واقفة</span>
        <span className="cnt total">الكل {list.length}</span>
        {pageCount > 1 ? (
          <span className="cnt total" style={{ marginInlineStart: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
            <button className="btn" style={{ padding: '2px 10px', fontSize: 12 }} disabled={safePage === 0}
              onClick={() => setPage((p) => Math.max(0, p - 1))}>◀ السابقة</button>
            صفحة {safePage + 1} من {pageCount}
            <button className="btn" style={{ padding: '2px 10px', fontSize: 12 }} disabled={safePage >= pageCount - 1}
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}>التالية ▶</button>
          </span>
        ) : null}
      </div>

      <div
        className="fitgrid"
        ref={gridRef}
        style={{ gridTemplateColumns: `repeat(${grid.cols}, 1fr)`, gridTemplateRows: `repeat(${grid.rows}, 1fr)` }}
      >
        {shown.map((a: AtomRec) => (
          <button key={a.id} className={`cell ${a.color ?? 'grey'}`} onClick={() => select(a.id)} title={a.name_ar}>
            <span className="cid num">#{a.id}</span>
            <span className="cnm">{a.name_ar}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
