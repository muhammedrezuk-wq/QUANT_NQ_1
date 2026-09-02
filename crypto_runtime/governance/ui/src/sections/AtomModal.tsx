// تفاصيل الذرة — نافذة مشتركة، تُفتح من الشبكة أو من القائمة (store.selectedId).
import { useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { useStore } from '../core/store'
import { execute, atomStop, atomStart } from '../core/commands'
import { AtomConfigForm } from './Settings'

export default function AtomModal() {
  const atom = useStore((s) => (s.selectedId != null ? s.atoms[s.selectedId] : undefined))
  const select = useStore((s) => s.select)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)

  async function cmd(act: 'stop' | 'start') {
    if (!atom) return
    setBusy(true)
    const name = atom.name_ar ?? String(atom.id)
    const res = await execute(act === 'stop' ? atomStop(atom.id, name) : atomStart(atom.id, name))
    setBusy(false)
    if (!res.ok && res.message !== 'أُلغي') window.alert('ما تمّ: ' + res.message)
  }

  return (
    <AnimatePresence>
      {atom && (
        <motion.div
          className="overlay"
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          transition={{ duration: 0.15 }}
          onClick={() => select(null)}
        >
          <motion.div
            className="modal"
            initial={{ scale: 0.92, y: 12, opacity: 0 }} animate={{ scale: 1, y: 0, opacity: 1 }} exit={{ scale: 0.96, opacity: 0 }}
            transition={{ type: 'spring', stiffness: 320, damping: 26 }}
            onClick={(e) => e.stopPropagation()}
          >
            <button className="x" onClick={() => select(null)}>×</button>
            <h3>{atom.name_ar} <span className="dim num">#{atom.id}</span></h3>
            <div className={`pill ${atom.color ?? 'grey'}`}>● {atom.label_ar}</div>
            {atom.restart_count > 0 && <div className="dim">أُعيد تشغيلها {atom.restart_count} مرّة</div>}
            {atom.last_error && <div className="err">تفصيل العطل متاح في السجل</div>}
            <div className="actions">
              {atom.state === 'running' && (
                <button className="btn stop" disabled={busy} onClick={() => cmd('stop')}>وقّف</button>
              )}
              {atom.state === 'stopped' && (
                <button className="btn start" disabled={busy} onClick={() => cmd('start')}>شغّل</button>
              )}
              <button
                className="btn"
                style={editing ? { borderColor: 'var(--accent)', color: 'var(--accent)' } : {}}
                onClick={() => setEditing(!editing)}
              >{editing ? 'خبّي التعديل' : 'تعديل'}</button>
            </div>
            {editing && atom ? (
              <div style={{ marginTop: 12, maxHeight: 320, overflow: 'auto' }}>
                <AtomConfigForm atomId={atom.id} />
              </div>
            ) : null}
            <div className="dim">التفاصيل التقنية محفوظة في سجل النظام.</div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
