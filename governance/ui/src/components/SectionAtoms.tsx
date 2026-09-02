// بندا ٩ و١٠ بورقة ٩٩ — قطعتان مشتركتان لصفحات الأقسام:
// • SectionAtomsHealth (بند ١٠): بدل الصفحة الفاضية «بانتظار أوّل دورة…» —
//   حالة ذرّات القسم الحيّة نفسها (عدّادات + بطاقة لكل ذرّة) من مخزون اللوحة
//   الذي يتغذّى من /gov/atoms (تمرير /api/atoms من النواة كل ٤ ثوانٍ). لا حساب،
//   عرض ما وصل فقط — والغياب يُعلَن غيابًا.
// • SectionConfigTable (بند ٩): جدول واحد لكل قسم يعرض المعاملات الحقيقية
//   القابلة للضبط لذرّات القسم (من config/config_schema عبر /gov/atoms/{id}/config
//   القائم) — بنمط صفحة التحليل 150: حفظ المعدَّل دفعة + «تحديث» يتحقّق بقراءة
//   مستقلّة + إشعار «تمّ الضبط». الحفظ بنفس آلية الضبط القائمة (POST نفسه الذي
//   يستعمله نموذج الذرّة) — لا معاملات مخترعة: ما لا config له لا يظهر.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useStore, type AtomRec } from '../core/store'
import { settingLabel } from '../core/settingLabels'

// ── بند ١٠: صحّة ذرّات القسم ──────────────────────────────────────────────

const COLOR_AR: Record<string, string> = { green: 'سليمة', amber: 'متعثّرة', red: 'فيها خلل', grey: 'واقفة/مجهولة' }

export function SectionAtomsHealth({ from, to, ids, title, note }: {
  from?: number
  to?: number
  ids?: number[]
  title: string
  note?: string
}) {
  const atoms = useStore((s) => s.atoms)
  const conn = useStore((s) => s.conn)
  const list = useMemo(() => {
    const all = Object.values(atoms)
    const picked = ids
      ? ids.map((id) => atoms[id]).filter((a): a is AtomRec => a != null)
      : all.filter((a) => from != null && to != null && a.id >= from && a.id < to)
    return picked.sort((a, b) => a.id - b.id)
  }, [atoms, from, to, ids])

  const tally = useMemo(() => {
    const t: Record<string, number> = { green: 0, amber: 0, red: 0, grey: 0 }
    for (const a of list) t[a.color ?? 'grey'] = (t[a.color ?? 'grey'] ?? 0) + 1
    return t
  }, [list])

  if (list.length === 0) {
    return (
      <div className="scard">
        <div className="st">{title}</div>
        <div className="ss dim">
          {conn === 'live'
            ? 'ما في ذرّات محمّلة بالنواة من هالقسم الآن.'
            : 'النواة مقطوعة — حالة ذرّات القسم غير معروفة (اللوحة لا تخمّن).'}
        </div>
      </div>
    )
  }

  return (
    <div className="scard">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <div className="st" style={{ fontWeight: 700 }}>{title}</div>
        <span className="ss dim">{list.length} ذرّة بالقسم</span>
        {(['green', 'amber', 'red', 'grey'] as const).map((c) =>
          tally[c] ? <span key={c} className={`pill ${c}`} style={{ fontSize: 12 }}>{COLOR_AR[c]} {tally[c]}</span> : null)}
      </div>
      {note ? <div className="ss dim" style={{ marginTop: 4 }}>{note}</div> : null}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
        {list.map((a) => (
          <span key={a.id} className={`pill ${a.color ?? 'grey'}`} style={{ fontSize: 12.5 }}
            title={a.label_ar ?? ''}>
            <span className="num dim" style={{ fontSize: 10.5 }}>{a.id}</span> {a.name_ar ?? a.name} — {a.label_ar ?? '؟'}
          </span>
        ))}
      </div>
    </div>
  )
}

// ── بند ٩: جدول المعاملات الجماعي ─────────────────────────────────────────

interface Setting { key: string; value: unknown; type: string; min: number | null; max: number | null }
type ConfigMap = Record<number, Setting[]>

const asDraft = (s: Setting): string =>
  s.type === 'array' || s.type === 'object' ? JSON.stringify(s.value) : String(s.value)

export function SectionConfigTable({ from, to, title }: { from: number; to: number; title: string }) {
  const atoms = useStore((s) => s.atoms)
  // مفتاح ثابت لمجموعة ذرّات النطاق — لا نعيد الجلب مع كل نبضة مخزون (كل ٤ث)
  const idsKey = useMemo(() =>
    Object.values(atoms).filter((a) => a.id >= from && a.id < to).map((a) => a.id).sort((x, y) => x - y).join(','),
    [atoms, from, to])
  const ids = useMemo(() => (idsKey ? idsKey.split(',').map(Number) : []), [idsKey])

  const [configs, setConfigs] = useState<ConfigMap | null>(null)
  const [vals, setVals] = useState<Record<string, string>>({})       // `${id}:${key}` → مسودّة
  const [base, setBase] = useState<Record<string, string>>({})       // قيم الخادم كما قُرئت
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const pendingRef = useRef<Record<string, string> | null>(null)     // ما أُرسل وينتظر تحقّقًا

  const load = useCallback(async (signal?: AbortSignal): Promise<Record<string, string> | null> => {
    if (!ids.length) { setConfigs({}); return null }
    try {
      const rows = await Promise.all(ids.map(async (id) => {
        const r = await fetch(`/gov/atoms/${id}/config`, { signal })
        const d = (await r.json()) as { settings?: Setting[] }
        return [id, d.settings ?? []] as const
      }))
      const map: ConfigMap = {}
      const flat: Record<string, string> = {}
      for (const [id, settings] of rows) {
        if (settings.length === 0) continue
        map[id] = settings
        for (const s of settings) flat[`${id}:${s.key}`] = asDraft(s)
      }
      setConfigs(map)
      setBase(flat)
      // المسودّات: تُحفظ تعديلات المالك غير المرسلة، والجديد يأخذ قيمة الخادم
      setVals((v) => {
        const next: Record<string, string> = {}
        for (const k of Object.keys(flat)) next[k] = k in v ? v[k] : flat[k]
        return next
      })
      return flat
    } catch { /* الخادم مطفي أو أُلغي الطلب */ }
    return null
  }, [ids])

  useEffect(() => {
    const controller = new AbortController()
    setConfigs(null); setNote(null); pendingRef.current = null
    setVals({})
    void load(controller.signal)
    return () => controller.abort()
  }, [load])

  const dirtyKeys = useMemo(
    () => Object.keys(vals).filter((k) => k in base && vals[k] !== base[k]),
    [vals, base])
  const dirtyAtomIds = useMemo(
    () => [...new Set(dirtyKeys.map((k) => Number(k.split(':')[0])))].sort((a, b) => a - b),
    [dirtyKeys])

  const nameOf = (id: number) => atoms[id]?.name_ar ?? atoms[id]?.name ?? `#${id}`

  // «حفظ المعدَّل» — دفعة بتأكيد واحد؛ كل ذرّة تمرّ بنفس POST القائم للضبط الفردي
  const saveDirty = async () => {
    if (!configs || dirtyAtomIds.length === 0) return
    const names = dirtyAtomIds.map((id) => `«${nameOf(id)}»`).join(' · ')
    if (!window.confirm(`تعديل معاملات ${dirtyAtomIds.length} ذرّة وإعادة تحميلها حيًّا؟\n${names}`)) return
    setBusy(true); setNote(null)
    const sent: Record<string, string> = {}
    const failed: string[] = []
    for (const id of dirtyAtomIds) {
      const settings = configs[id] ?? []
      const updates: Record<string, unknown> = {}
      try {
        for (const s of settings) {
          const raw = vals[`${id}:${s.key}`]
          updates[s.key] = s.type === 'array' || s.type === 'object' ? JSON.parse(raw) : raw
        }
      } catch { failed.push(`${nameOf(id)} (قيمة JSON غير صالحة)`); continue }
      try {
        const r = await fetch(`/gov/atoms/${id}/config`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(updates),
        })
        const j = (await r.json()) as { message?: string }
        if (!r.ok) { failed.push(`${nameOf(id)}: ${j.message ?? r.status}`); continue }
        for (const s of settings) sent[`${id}:${s.key}`] = vals[`${id}:${s.key}`]
      } catch { failed.push(`${nameOf(id)}: خطأ اتصال`) }
    }
    if (Object.keys(sent).length) pendingRef.current = sent
    setBusy(false)
    setNote(failed.length === 0
      ? { ok: true, text: `أُرسل تعديل ${dirtyAtomIds.length} ذرّة — اضغط «تحديث» للتحقّق أنّها انضبطت فعلًا` }
      : { ok: false, text: `فشل: ${failed.join(' · ')}` })
  }

  // «تحديث» — قراءة مستقلّة من الخادم ومطابقة ما أُرسل قيمة قيمة (لا ثقة بردّ الحفظ)
  const verifyRefresh = async () => {
    setBusy(true)
    const fresh = await load()
    setBusy(false)
    if (!fresh) { setNote({ ok: false, text: 'ما قدرت أقرأ من الخادم — أعد المحاولة' }); return }
    const pending = pendingRef.current
    if (!pending) { setNote({ ok: true, text: 'قُرئت القيم من الخادم — ما في دفعة بانتظار تحقّق' }); return }
    const mismatch = Object.keys(pending).filter((k) => fresh[k] !== pending[k])
    if (mismatch.length === 0) {
      pendingRef.current = null
      setNote({ ok: true, text: `✅ تمّ الضبط — ${Object.keys(pending).length} قيمة تطابق بعد القراءة من الخادم` })
    } else {
      const names = [...new Set(mismatch.map((k) => nameOf(Number(k.split(':')[0]))))].join(' · ')
      setNote({ ok: false, text: `⚠️ لسّا ما انضبط: ${names} — جرّب «تحديث» بعد لحظة` })
    }
  }

  const withCfg = configs ? Object.keys(configs).map(Number).sort((a, b) => a - b) : []

  return (
    <div className="scard" style={{ overflow: 'hidden', padding: 0 }}>
      <div style={{ padding: '11px 14px', borderBottom: '1px solid var(--glassb)', display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10 }}>
        <div style={{ minWidth: 220 }}>
          <div className="st">{title}</div>
          <div className="ss dim">المعاملات الحقيقية من مانيفست كل ذرّة — التعديل يُكتب ويُعاد تحميل الذرّة حيًّا، بنفس آلية الضبط الفردي.</div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginInlineStart: 'auto', flexWrap: 'wrap' }}>
          {dirtyKeys.length ? <span className="amber" style={{ fontSize: 12.5 }}>{dirtyKeys.length} تعديل غير محفوظ</span> : null}
          <button className="btn" disabled={busy || dirtyAtomIds.length === 0} onClick={() => void saveDirty()}>
            {busy ? '⏳ …' : `💾 حفظ المعدَّل${dirtyAtomIds.length ? ` (${dirtyAtomIds.length})` : ''}`}
          </button>
          <button className="btn" disabled={busy} onClick={() => void verifyRefresh()}
            title="يقرأ من الخادم من جديد ويطابق ما أُرسل — تحقّق مستقلّ، لا ثقة بردّ الحفظ">🔄 تحديث</button>
        </div>
        {note ? <div style={{ flexBasis: '100%', fontSize: 13, color: note.ok ? 'var(--green)' : 'var(--amber)' }}>{note.text}</div> : null}
      </div>
      {configs == null ? (
        <div className="dim" style={{ padding: 14 }}>جارِ جلب المعاملات…</div>
      ) : ids.length === 0 ? (
        <div className="dim" style={{ padding: 14 }}>ما في ذرّات محمّلة بالنواة من هالقسم — المعاملات تظهر لمّا تتّصل النواة.</div>
      ) : withCfg.length === 0 ? (
        <div className="dim" style={{ padding: 14 }}>ذرّات القسم الحاضرة ({ids.length}) ما عندها معاملات قابلة للضبط بالمانيفست.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
            <thead><tr className="dim" style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
              <th style={{ padding: 8 }}>الذرّة</th><th style={{ padding: 8 }}>المعامل</th>
              <th style={{ padding: 8 }}>القيمة</th><th style={{ padding: 8 }}>الحدود</th>
            </tr></thead>
            <tbody>
              {withCfg.map((id) => (configs[id] ?? []).map((s, i) => {
                const k = `${id}:${s.key}`
                const dirty = k in base && vals[k] !== base[k]
                return (
                  <tr key={k} style={{ borderBottom: '1px solid var(--glassb)' }}>
                    <td style={{ padding: '7px 8px', whiteSpace: 'nowrap', fontWeight: i === 0 ? 700 : 400, color: i === 0 ? 'var(--ink)' : 'var(--dim)' }}>
                      {i === 0 ? <>{nameOf(id)} <span className="num dim" style={{ fontSize: 11 }}>#{id}</span></> : ''}
                    </td>
                    <td style={{ padding: '7px 8px', whiteSpace: 'nowrap' }}>{settingLabel(s.key)}</td>
                    <td style={{ padding: '5px 8px', minWidth: 130 }}>
                      {s.type === 'boolean' ? (
                        <label className="cfgswitch">
                          <input type="checkbox" checked={vals[k] === 'true'}
                            onChange={(e) => setVals((v) => ({ ...v, [k]: e.target.checked ? 'true' : 'false' }))} />
                          <span className={vals[k] === 'true' ? 'on' : 'off'}>{vals[k] === 'true' ? 'مفتوح' : 'مقفل'}</span>
                        </label>
                      ) : (
                        <input className="cfginput num" style={{ margin: 0, padding: '5px 8px', fontSize: 14, borderColor: dirty ? 'var(--amber)' : undefined }}
                          value={vals[k] ?? ''} inputMode="decimal"
                          onChange={(e) => setVals((v) => ({ ...v, [k]: e.target.value }))} />
                      )}
                    </td>
                    <td className="ss dim" style={{ padding: '7px 8px', whiteSpace: 'nowrap' }}>
                      {s.min != null ? `أدنى ${s.min}` : ''}{s.min != null && s.max != null ? ' · ' : ''}{s.max != null ? `أقصى ${s.max}` : ''}
                    </td>
                  </tr>
                )
              }))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
