// نسخ ولصق باللمس — أمر المالك 2026-08-14: «عم استعمل لمس، ما في ضغطة مطوّلة،
// لا نسخ ولا لصق داخل اللوحة».
//
// السبب: نافذة WebView2 باللمس ما بتطلّع قائمة الضغط المطوّلة، و`SHOW_DEFAULT_MENUS`
// بـpywebview بتخصّ شريط قوائم التطبيق لا قائمة الصفحة. فبنيناها بأنفسنا:
// ضغطة مطوّلة (٥٠٠ms) بأي مكان → قائمة عربيّة صغيرة: نسخ · لصق · تحديد الكل · مسح.
//
// حدّ صادق: `navigator.clipboard` يشتغل بسياق آمن فقط — أي على الجهاز نفسه
// (`127.0.0.1`). من الموبايل عبر الشبكة (http عادي) المتصفّح يمنعه، فنقول ذلك
// صراحةً بدل زرّ يبدو شغّالًا ولا يفعل شيئًا.
import { useEffect, useRef, useState } from 'react'

const LONG_PRESS_MS = 500
const MOVE_TOLERANCE = 12

type Item = { label: string; run: () => void | Promise<void> }

function isEditable(el: Element | null): el is HTMLInputElement | HTMLTextAreaElement {
  if (!el) return false
  const tag = el.tagName
  return (tag === 'INPUT' || tag === 'TEXTAREA') && !(el as HTMLInputElement).disabled
}

function secureClipboard(): boolean {
  return typeof navigator !== 'undefined' && !!navigator.clipboard && window.isSecureContext
}

export default function TouchClipboard() {
  const [menu, setMenu] = useState<{ x: number; y: number; items: Item[] } | null>(null)
  const [toast, setToast] = useState('')
  const timer = useRef<number | null>(null)
  const origin = useRef<{ x: number; y: number } | null>(null)
  const target = useRef<Element | null>(null)

  const say = (t: string) => {
    setToast(t)
    window.setTimeout(() => setToast(''), 1800)
  }

  const cancel = () => {
    if (timer.current !== null) { window.clearTimeout(timer.current); timer.current = null }
    origin.current = null
  }

  const buildItems = (el: Element | null): Item[] => {
    const items: Item[] = []
    const selected = String(window.getSelection?.() ?? '')
    const editable = isEditable(el) ? el : null

    const copyText = editable
      ? (editable.value.substring(editable.selectionStart ?? 0, editable.selectionEnd ?? 0) || editable.value)
      : selected

    if (copyText) {
      items.push({
        label: '📄 نسخ',
        run: async () => {
          if (!secureClipboard()) { say('النسخ متاح على جهاز النظام فقط'); return }
          await navigator.clipboard.writeText(copyText)
          say('اننسخ')
        },
      })
    }
    if (editable) {
      items.push({
        label: '📋 لصق',
        run: async () => {
          if (!secureClipboard()) { say('اللصق متاح على جهاز النظام فقط'); return }
          try {
            const text = await navigator.clipboard.readText()
            if (!text) { say('الحافظة فاضية'); return }
            const setter = Object.getOwnPropertyDescriptor(
              editable instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
              'value',
            )?.set
            // React يراقب القيمة عبر setter خاص؛ الكتابة المباشرة لا توقظه.
            setter?.call(editable, text)
            editable.dispatchEvent(new Event('input', { bubbles: true }))
            say('انلصق')
          } catch {
            say('المتصفّح منع قراءة الحافظة')
          }
        },
      })
      items.push({ label: '🔤 تحديد الكل', run: () => { editable.focus(); editable.select() } })
      if (editable.value) {
        items.push({
          label: '✖️ مسح',
          run: () => {
            const setter = Object.getOwnPropertyDescriptor(
              editable instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype,
              'value',
            )?.set
            setter?.call(editable, '')
            editable.dispatchEvent(new Event('input', { bubbles: true }))
          },
        })
      }
    }
    if (!items.length) items.push({ label: 'ما في شي للنسخ هون', run: () => undefined })
    return items
  }

  useEffect(() => {
    const start = (x: number, y: number, el: Element | null) => {
      cancel()
      origin.current = { x, y }
      target.current = el
      timer.current = window.setTimeout(() => {
        setMenu({ x, y, items: buildItems(el) })
        if ('vibrate' in navigator) navigator.vibrate?.(12)
      }, LONG_PRESS_MS)
    }

    const onTouchStart = (e: TouchEvent) => {
      const t = e.touches[0]
      if (!t) return
      start(t.clientX, t.clientY, document.elementFromPoint(t.clientX, t.clientY))
    }
    const onTouchMove = (e: TouchEvent) => {
      const t = e.touches[0]
      if (!t || !origin.current) return
      if (Math.abs(t.clientX - origin.current.x) > MOVE_TOLERANCE
        || Math.abs(t.clientY - origin.current.y) > MOVE_TOLERANCE) cancel()
    }
    // الفأرة أيضًا: زرّ يمين يعطي نفس القائمة، فالسلوك واحد باللمس والفأرة.
    const onContextMenu = (e: MouseEvent) => {
      e.preventDefault()
      setMenu({ x: e.clientX, y: e.clientY, items: buildItems(document.elementFromPoint(e.clientX, e.clientY)) })
    }
    const close = () => setMenu(null)

    document.addEventListener('touchstart', onTouchStart, { passive: true })
    document.addEventListener('touchmove', onTouchMove, { passive: true })
    document.addEventListener('touchend', cancel, { passive: true })
    document.addEventListener('touchcancel', cancel, { passive: true })
    document.addEventListener('contextmenu', onContextMenu)
    document.addEventListener('scroll', close, true)
    return () => {
      document.removeEventListener('touchstart', onTouchStart)
      document.removeEventListener('touchmove', onTouchMove)
      document.removeEventListener('touchend', cancel)
      document.removeEventListener('touchcancel', cancel)
      document.removeEventListener('contextmenu', onContextMenu)
      document.removeEventListener('scroll', close, true)
    }
  }, [])

  return (
    <>
      {menu ? (
        <>
          <div onClick={() => setMenu(null)} onTouchStart={() => setMenu(null)}
            style={{ position: 'fixed', inset: 0, zIndex: 9998 }} />
          <div style={{
            position: 'fixed', zIndex: 9999,
            left: Math.min(menu.x, window.innerWidth - 170),
            top: Math.min(menu.y, window.innerHeight - 40 - menu.items.length * 42),
            background: 'var(--panel, #161b26)', border: '1px solid var(--line, #2a3242)',
            borderRadius: 10, padding: 5, minWidth: 160,
            boxShadow: '0 10px 30px rgba(0,0,0,.55)',
          }}>
            {menu.items.map((it, i) => (
              <button key={i}
                onClick={() => { void it.run(); setMenu(null) }}
                style={{
                  display: 'block', width: '100%', textAlign: 'right', background: 'transparent',
                  border: 0, color: 'var(--ink, #e6ebf2)', padding: '10px 12px', fontSize: 14,
                  borderRadius: 7, cursor: 'pointer', fontFamily: 'inherit',
                }}>
                {it.label}
              </button>
            ))}
          </div>
        </>
      ) : null}
      {toast ? (
        <div style={{
          position: 'fixed', zIndex: 9999, bottom: 70, left: '50%', transform: 'translateX(-50%)',
          background: 'var(--panel, #161b26)', border: '1px solid var(--line, #2a3242)',
          borderRadius: 20, padding: '8px 18px', fontSize: 13, color: 'var(--ink, #e6ebf2)',
          boxShadow: '0 8px 24px rgba(0,0,0,.5)',
        }}>{toast}</div>
      ) : null}
    </>
  )
}
