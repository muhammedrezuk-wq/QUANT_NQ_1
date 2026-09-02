// نظام البلاطات — ختم المالك ٢٠٢٦-٠٨-٢٠:
// «حط مجموع كل اللوحات بلوحة وحدة… وأنا بصغّر وبكبّر بالطول وبالعرض وبغيّر المكان».
// شبكة ١٢×١٢: كل بلاطة إلها موضع (x,y) ومقاس (w,h) بوحدات الشبكة.
//   • سحب من رأس البلاطة  → تغيير المكان
//   • سحب من الحافّة اليسرى → العرض
//   • سحب من الحافّة السفلية → الطول
//   • سحب من الزاوية       → الاثنان معًا
// كل شيء محفوظ محليًّا (localStorage) مثل «تخصيص الشكل» (بند ١٥ بورقة ٩٩)،
// ومعه «رجوع للافتراضي». صفر مكتبة جديدة — أحداث المؤشّر الأصلية فقط.
import { useCallback, useEffect, useRef, useState } from 'react'

export const COLS = 12
export const ROWS = 12
const KEY = 'nq_tiles_v1'

export interface TileDef {
  id: string
  title: string
  render: () => React.ReactNode
  /** القسم الذي يفتحه زرّ ⤢ (إن وُجد) */
  go?: string
}
export interface TilePos {
  x: number; y: number; w: number; h: number; on: boolean
  /** مطويّة بشريط الأسفل — تحتفظ بمكانها ومقاسها لحين استعادتها */
  min?: boolean
  /** مكبَّرة على الشاشة كاملة — تحتفظ بمكانها ومقاسها لحين استعادتها */
  max?: boolean
}
export type Layout = Record<string, TilePos>

function readSaved(): Layout | null {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as Layout) : null
  } catch { return null }
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

export function useLayout(def: Layout) {
  const [layout, setLayout] = useState<Layout>(() => ({ ...def, ...(readSaved() ?? {}) }))
  const save = useCallback((next: Layout) => {
    setLayout(next)
    try { localStorage.setItem(KEY, JSON.stringify(next)) } catch { /* تخزين ممتلئ */ }
  }, [])
  const reset = useCallback(() => {
    try { localStorage.removeItem(KEY) } catch { /* لا شيء */ }
    setLayout({ ...def })
  }, [def])
  return { layout, save, reset }
}

/** بلاطة واحدة: رأس للسحب + ثلاثة مقابض للتحجيم. */
function Tile({ def, pos, cell, onChange, onGo, onHide, onMin, onMax, isMax }: {
  def: TileDef; pos: TilePos; cell: { w: number; h: number }
  onChange: (p: TilePos) => void
  onGo?: (id: string) => void
  onHide: () => void
  onMin: () => void
  onMax: () => void
  isMax: boolean
}) {
  // ⛔ ممنوع تحديث حالة React أثناء السحب: كانت كل حركة ماوس تعيد رسم كل
  // البلاطات (ومنها الشارت والشبكة) فتصير الحركة ثقيلة وتقفز قفزات الشبكة.
  // الآن: البلاطة تتبع المؤشّر بكسلًا بكسل بتعديل مباشر على عنصرها (بلا رسم)،
  // والحساب على الشبكة يجري مرّة واحدة عند رفع الإصبع.
  const box = useRef<HTMLDivElement>(null)
  const start = useRef<{ px: number; py: number; mode: string; w: number; h: number } | null>(null)
  const last = useRef<{ dx: number; dy: number }>({ dx: 0, dy: 0 })
  const raf = useRef(0)

  const paint = () => {
    raf.current = 0
    const s = start.current
    const el = box.current
    if (!s || !el) return
    const { dx, dy } = last.current
    if (s.mode === 'move') {
      el.style.transform = `translate(${dx}px, ${dy}px)`
    } else {
      // الحافّة اليسرى هي الطرف البعيد بالعربي: سحبها لليسار يوسّع البلاطة.
      if (s.mode.includes('w')) el.style.width = `${Math.max(60, s.w - dx)}px`
      if (s.mode.includes('h')) el.style.height = `${Math.max(60, s.h + dy)}px`
    }
  }

  const begin = (e: React.PointerEvent, mode: string) => {
    e.preventDefault()
    e.stopPropagation()
    try { (e.target as HTMLElement).setPointerCapture(e.pointerId) } catch { /* بلا التقاط */ }
    const el = box.current
    const r = el?.getBoundingClientRect()
    start.current = { px: e.clientX, py: e.clientY, mode, w: r?.width ?? 0, h: r?.height ?? 0 }
    last.current = { dx: 0, dy: 0 }
    if (el) {
      el.style.zIndex = '9'
      el.style.transition = 'none'
      el.style.opacity = mode === 'move' ? '0.85' : '1'
    }
  }

  const move = (e: React.PointerEvent) => {
    if (!start.current) return
    last.current = { dx: e.clientX - start.current.px, dy: e.clientY - start.current.py }
    if (!raf.current) raf.current = requestAnimationFrame(paint)
  }

  const end = () => {
    const s = start.current
    const el = box.current
    start.current = null
    if (raf.current) { cancelAnimationFrame(raf.current); raf.current = 0 }
    if (el) {
      el.style.transform = ''
      el.style.width = ''
      el.style.height = ''
      el.style.zIndex = ''
      el.style.opacity = ''
    }
    if (!s) return
    const { dx, dy } = last.current
    if (Math.abs(dx) < 3 && Math.abs(dy) < 3) return
    // خطوة الشبكة تُحسب مرّة واحدة هنا — لا أثناء الحركة.
    const gx = Math.round(-dx / Math.max(1, cell.w))
    const gy = Math.round(dy / Math.max(1, cell.h))
    const p = { ...pos }
    if (s.mode === 'move') {
      p.x = clamp(pos.x + gx, 0, COLS - pos.w)
      p.y = clamp(pos.y + gy, 0, 60)
    }
    if (s.mode.includes('w')) p.w = clamp(pos.w + gx, 2, COLS - pos.x)
    if (s.mode.includes('h')) p.h = clamp(pos.h + gy, 2, 60)
    if (p.x !== pos.x || p.y !== pos.y || p.w !== pos.w || p.h !== pos.h) onChange(p)
  }

  const grip: React.CSSProperties = { position: 'absolute', zIndex: 3, touchAction: 'none' }
  const btn: React.CSSProperties = {
    background: 'none', border: 'none', color: 'var(--dim)', cursor: 'pointer',
    font: 'inherit', fontSize: 11, padding: 0, lineHeight: 1,
  }

  return (
    <div ref={box} className="scard" style={{
      gridColumn: `${pos.x + 1} / span ${pos.w}`,
      gridRow: `${pos.y + 1} / span ${pos.h}`,
      padding: 0, display: 'flex', flexDirection: 'column',
      minHeight: 0, minWidth: 0, overflow: 'hidden', position: 'relative',
    }}>
      <div
        onPointerDown={(e) => { if (!isMax) begin(e, 'move') }}
        onPointerMove={move}
        onPointerUp={end}
        onPointerCancel={end}
        title={isMax ? 'مكبَّرة — صغّرها لتحريكها' : 'اسحب لتغيير المكان'}
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6,
          padding: '3px 9px', borderBottom: '1px solid var(--glassb)', flex: 'none',
          cursor: isMax ? 'default' : 'move', touchAction: 'none', userSelect: 'none',
        }}>
        <span className="st" style={{ fontSize: 11, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {isMax ? '' : '⠿ '}{def.title}
        </span>
        <span style={{ display: 'flex', gap: 8, flex: 'none' }}>
          {def.go && onGo ? (
            <button onClick={() => onGo(def.go as string)} title="افتح القسم كامل" style={btn}>↗</button>
          ) : null}
          <button onClick={onMin} title="تصغير — تنزل لشريط الأسفل" style={btn}>—</button>
          <button onClick={onMax} title={isMax ? 'استعادة المقاس' : 'تكبير على الشاشة'} style={btn}>{isMax ? '⛶' : '⛶'}</button>
          <button onClick={onHide} title="أخفِ البلاطة" style={btn}>✕</button>
        </span>
      </div>

      <div style={{ flex: 1, minHeight: 0, minWidth: 0, overflow: 'auto' }}>{def.render()}</div>

      {/* مقابض التحجيم: يسار (عرض) · تحت (طول) · الزاوية (الاثنان) — تختفي عند التكبير */}
      {isMax ? null : (<>
      <div onPointerDown={(e) => begin(e, 'w')} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
        title="اسحب لتغيير العرض"
        style={{ ...grip, insetInlineStart: 0, top: 0, bottom: 0, width: 6, cursor: 'ew-resize' }} />
      <div onPointerDown={(e) => begin(e, 'h')} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
        title="اسحب لتغيير الطول"
        style={{ ...grip, insetInlineStart: 0, insetInlineEnd: 0, bottom: 0, height: 6, cursor: 'ns-resize' }} />
      <div onPointerDown={(e) => begin(e, 'wh')} onPointerMove={move} onPointerUp={end} onPointerCancel={end}
        title="اسحب لتغيير الطول والعرض"
        style={{ ...grip, insetInlineStart: 0, bottom: 0, width: 14, height: 14, cursor: 'nwse-resize' }} />
      </>)}
    </div>
  )
}

export function TileBoard({ defs, layout, save, reset, onGo }: {
  defs: TileDef[]; layout: Layout
  save: (l: Layout) => void; reset: () => void
  onGo?: (id: string) => void
}) {
  const boxRef = useRef<HTMLDivElement>(null)
  const [cell, setCell] = useState({ w: 100, h: 40 })
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    const el = boxRef.current
    if (!el) return
    const calc = () => setCell({
      w: Math.max(1, el.clientWidth / COLS),
      h: Math.max(1, el.clientHeight / ROWS),
    })
    calc()
    const ro = new ResizeObserver(calc)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const off = defs.filter((d) => !layout[d.id]?.on)
  // بلاطة مكبَّرة تأخذ اللوح كلّه وحدها؛ والمطويّات تنزل لشريط الأسفل.
  const maxDef = defs.find((d) => layout[d.id]?.on && layout[d.id]?.max)
  const shown = maxDef ? [maxDef] : defs.filter((d) => layout[d.id]?.on && !layout[d.id]?.min)
  const docked = defs.filter((d) => layout[d.id]?.on && layout[d.id]?.min && !layout[d.id]?.max)

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', gap: 6, minHeight: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, flex: 'none' }}>
        <button className="btn" onClick={() => setAdding((v) => !v)}>➕ أضف بلاطة ({off.length})</button>
        <button className="btn" onClick={reset}>↺ رجوع للافتراضي</button>
        <span className="dim" style={{ fontSize: 11 }}>
          اسحب رأس البلاطة لتنقلها · حافّتها اليسرى للعرض · السفلية للطول
        </span>
      </div>

      {adding ? (
        <div className="scard" style={{ display: 'flex', flexWrap: 'wrap', gap: 4, padding: '6px 8px', flex: 'none' }}>
          {off.length === 0 ? <span className="dim" style={{ fontSize: 11.5 }}>كل البلاطات معروضة</span>
            : off.map((d) => (
              <button key={d.id} className="btn" style={{ fontSize: 11 }}
                onClick={() => {
                  const cur = layout[d.id] ?? { x: 0, y: 0, w: 4, h: 4, on: false }
                  save({ ...layout, [d.id]: { ...cur, on: true } })
                  setAdding(false)
                }}>{d.title}</button>
            ))}
        </div>
      ) : null}

      <div ref={boxRef} style={{
        flex: 1, minHeight: 0, minWidth: 0, display: 'grid',
        gridTemplateColumns: `repeat(${COLS}, minmax(0, 1fr))`,
        gridAutoRows: `${cell.h}px`, gap: 6, overflowY: 'auto', overflowX: 'hidden',
        alignContent: 'start',
      }}>
        {shown.map((d) => {
          const isMax = !!layout[d.id]?.max
          const pos = isMax
            ? { ...layout[d.id], x: 0, y: 0, w: COLS, h: ROWS }
            : layout[d.id]
          return (
            <Tile key={d.id} def={d} pos={pos} cell={cell} onGo={onGo} isMax={isMax}
              onChange={(p) => save({ ...layout, [d.id]: p })}
              onHide={() => save({ ...layout, [d.id]: { ...layout[d.id], on: false, max: false } })}
              onMin={() => save({ ...layout, [d.id]: { ...layout[d.id], min: true, max: false } })}
              onMax={() => save({ ...layout, [d.id]: { ...layout[d.id], max: !isMax, min: false } })} />
          )
        })}
      </div>

      {/* شريط المطويّات — تحت، مثل شريط المهام: كبسة تُرجع البلاطة لمكانها ومقاسها */}
      {docked.length ? (
        <div className="scard" style={{ display: 'flex', flexWrap: 'wrap', gap: 4, padding: '4px 8px', flex: 'none' }}>
          <span className="dim" style={{ fontSize: 10.5, alignSelf: 'center' }}>مطويّة:</span>
          {docked.map((d) => (
            <button key={d.id} className="btn" style={{ fontSize: 11 }}
              title="استعادة البلاطة لمكانها ومقاسها"
              onClick={() => save({ ...layout, [d.id]: { ...layout[d.id], min: false } })}>
              {d.title}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  )
}
