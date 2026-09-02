// المختبر — تشغيل الذرّات الحقيقية على بيانات تاريخية.
// أمر المالك: غيّر عتبة، اعزل محلّل أو قسم، وشوف ليش عم يفشل. مو باك تست منفصل.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { AtomConfigForm } from './Settings'

interface SectionSpec {
  id: string
  ar: string
  atoms: number[]
  why: string
  names?: Record<string, string>
}
interface Analyst { id: number; key: string; ar: string }
interface Catalog {
  ok: boolean
  running?: boolean
  sections?: Record<string, SectionSpec>
  analysts?: Analyst[]
  data?: { id: string; label: string }[]
  history?: HistoryRow[]
  windows?: Record<string, { first?: string; last?: string; first_ts?: number; last_ts?: number; count?: number }>
}
interface HistoryRow {
  run_id?: string
  isolate?: string
  source?: string
  candles?: number
  atoms_loaded?: number
  fail_count?: number
  duration_s?: number
  at?: number
}
interface UnitRow {
  event: string
  state: string
  ok: boolean
  reason?: string | null
  last?: Record<string, unknown>
}
interface Report {
  ok: boolean
  error?: string | null
  run_id?: string
  isolate?: string
  source?: string
  symbol?: string
  timeframe?: string
  candles?: number
  ticks?: number
  atoms_loaded?: number
  atom_ids?: number[]
  duration_s?: number
  stages?: Record<string, { count: number }>
  units?: UnitRow[]
  failing?: UnitRow[]
  fail_count?: number
  decisions?: { count?: number }
}
interface Compare {
  ok: boolean
  error?: string
  before?: HistoryRow
  after?: HistoryRow
  delta?: { fail_count?: number; duration_s?: number; candles?: number }
}

const num = (n?: number | null, d = 0) =>
  n == null || !Number.isFinite(n) ? '—' : n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: d, minimumFractionDigits: 0 })

function chip(on: boolean) {
  return {
    fontSize: 12, padding: '3px 10px', borderRadius: 8, cursor: 'pointer',
    fontFamily: 'inherit', whiteSpace: 'nowrap',
    background: on ? 'var(--accent)' : 'transparent',
    color: on ? '#06121c' : 'var(--dim)',
    border: `1px solid ${on ? 'var(--accent)' : 'var(--glassb)'}`,
  }
}

function clock(epoch?: number) {
  if (!epoch) return '—'
  const ms = epoch > 10_000_000_000 ? epoch : epoch * 1000
  return new Date(ms).toLocaleTimeString('ar-EG-u-nu-latn', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export default function Lab() {
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [section, setSection] = useState('full')
  const [atomId, setAtomId] = useState<number | ''>('')
  const [source, setSource] = useState('okx')
  const [candles, setCandles] = useState(120)
  const [fromDate, setFromDate] = useState('')
  const [toDate, setToDate] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [report, setReport] = useState<Report | null>(null)
  const [cmp, setCmp] = useState<Compare | null>(null)
  const [openEvent, setOpenEvent] = useState<string | null>(null)
  const [cfgAtom, setCfgAtom] = useState<number | ''>('')

  const loadCatalog = useCallback(() => {
    fetch('/gov/lab/catalog', { cache: 'no-store' })
      .then((r) => r.json() as Promise<Catalog>)
      .then((d) => setCatalog(d))
      .catch(() => setCatalog({ ok: false }))
  }, [])

  useEffect(() => { loadCatalog() }, [loadCatalog])

  const sections = useMemo(() => Object.values(catalog?.sections ?? {}), [catalog])
  const analysts = catalog?.analysts ?? []
  const dataSrc = catalog?.data ?? []
  const names = useMemo(() => {
    const out: Record<number, string> = {}
    for (const spec of sections) {
      for (const [k, v] of Object.entries(spec.names ?? {})) out[Number(k)] = v
    }
    return out
  }, [sections])

  const currentAtoms = useMemo(() => {
    if (section === 'full') return sections.flatMap((s) => s.atoms)
    return catalog?.sections?.[section]?.atoms ?? []
  }, [section, catalog, sections])

  useEffect(() => {
    if (atomId !== '' && !currentAtoms.includes(atomId)) setAtomId('')
  }, [currentAtoms, atomId])

  const run = async () => {
    setBusy(true); setNote(null); setCmp(null); setOpenEvent(null)
    const body: Record<string, unknown> = {
      source,
      max_candles: Math.max(20, Math.min(500, candles)),
    }
    if (fromDate) body.from_date = fromDate
    if (toDate) body.to_date = toDate
    if (atomId !== '') body.atom_id = atomId
    else body.section = section
    try {
      const r = await fetch('/gov/lab/run', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const j = await r.json() as Report & { error?: string }
      if (!j.ok) {
        setNote({ ok: false, text: j.error ?? 'تعذّر التشغيل' })
        setReport(null)
      } else {
        setReport(j)
        const fails = j.fail_count ?? 0
        setNote({
          ok: fails === 0,
          text: fails === 0
            ? `خلصت الجولة — ما في فشل معلَن (${num(j.duration_s, 2)} ث)`
            : `خلصت — ${num(fails)} وحدة عم تفشل. السبب تحت.`,
        })
        loadCatalog()
      }
    } catch {
      setNote({ ok: false, text: 'خطأ اتصال — تأكّد أن خادم الحوكمة شغّال' })
    }
    setBusy(false)
  }

  const doCompare = async () => {
    try {
      const j = await (await fetch('/gov/lab/compare', { cache: 'no-store' })).json() as Compare
      setCmp(j)
    } catch {
      setCmp({ ok: false, error: 'تعذّر المقارنة' })
    }
  }

  const resetLab = async () => {
    setBusy(true)
    try {
      const j = await (await fetch('/gov/lab/reset-overrides', { method: 'POST' })).json() as { ok?: boolean; message?: string }
      setNote({ ok: !!j.ok, text: j.message ?? 'رجعت عتبات المختبر لأصل التداول' })
    } catch {
      setNote({ ok: false, text: 'تعذّر مسح طبقات المختبر' })
    }
    setBusy(false)
  }

  const failing = report?.failing ?? []
  const units = report?.units ?? []
  const openUnit = units.find((u) => u.event === openEvent)

  return (
    <div className="section" style={{ display: 'flex', flexDirection: 'column', gap: 10, minHeight: 0 }}>
      <div className="scard">
        <div className="st" style={{ fontWeight: 700, fontSize: 16 }}>المختبر — معزول عن التداول</div>
        <div className="ss dim" style={{ marginTop: 4 }}>
          النظام تيكات. الشموع يبنيها ١٠٣ بعدد محدود من الفريمات (١٠ث…يوم).
          هون معايرة وعزل: ليش محلّل/قسم عم يفشل. الصفقات والـ PnL بتبويب «باك تست».
          عتبة هون بنسخة المختبر فقط — الديمو والحقيقي ما بيتلمسوا.
        </div>
      </div>

      <div className="scard" style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
        <span className="st" style={{ fontSize: 12 }}>البيانات</span>
        {dataSrc.map((d) => (
          <button key={d.id} style={chip(d.id === source)} onClick={() => setSource(d.id)}>{d.label}</button>
        ))}
        <label className="dim" style={{ fontSize: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          شموع المصدر
          <input className="cfginput num" type="number" min={20} max={500} step={10}
            style={{ width: 72, margin: 0, padding: '4px 7px' }}
            value={candles} onChange={(e) => setCandles(Number(e.target.value))} />
        </label>
        <span className="dim" style={{ fontSize: 11 }}>٢٠–٥٠٠ بار OHLC → حتى ٤ تيكات لكل بار · كل ما زاد، الجولة أبطأ</span>
      </div>

      <div className="scard" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        <span className="st" style={{ fontSize: 12 }}>العزل</span>
        <button style={chip(section === 'full' && atomId === '')} onClick={() => { setSection('full'); setAtomId('') }}>
          النظام الكامل
        </button>
        {sections.map((s) => (
          <button key={s.id} style={chip(section === s.id && atomId === '')}
            title={s.why}
            onClick={() => { setSection(s.id); setAtomId('') }}>
            {s.ar}
          </button>
        ))}
      </div>

      {section === 'analysis' ? (
        <div className="scard" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          <span className="st" style={{ fontSize: 12 }}>محلّل منفرد</span>
          {analysts.map((a) => (
            <button key={a.id} style={chip(atomId === a.id)}
              onClick={() => setAtomId(atomId === a.id ? '' : a.id)}>
              {a.ar} · {a.id}
            </button>
          ))}
        </div>
      ) : (
        <div className="scard" style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
          <span className="st" style={{ fontSize: 12 }}>ذرّة منفردة</span>
          <select className="cfginput" style={{ minWidth: 220, margin: 0 }}
            value={atomId === '' ? '' : String(atomId)}
            onChange={(e) => setAtomId(e.target.value === '' ? '' : Number(e.target.value))}>
            <option value="">كل ذرّات القسم</option>
            {currentAtoms.map((id) => (
              <option key={id} value={id}>{id} · {names[id] ?? id}</option>
            ))}
          </select>
          {section !== 'full' && catalog?.sections?.[section] ? (
            <span className="dim" style={{ fontSize: 12 }}>{catalog.sections[section].why}</span>
          ) : null}
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        <button className="btn start" disabled={busy} onClick={() => void run()}>
          {busy || catalog?.running ? 'عم تشغّل…' : atomId !== '' ? `شغّل الذرّة ${atomId}` : section === 'full' ? 'شغّل النظام الكامل' : `شغّل قسم ${catalog?.sections?.[section]?.ar ?? section}`}
        </button>
        <button className="btn" disabled={busy || (catalog?.history?.length ?? 0) < 2} onClick={() => void doCompare()}>
          قارن قبل / بعد
        </button>
        <button className="btn" disabled={busy} onClick={() => void resetLab()} title="يمسح عتبات المختبر فقط — التداول الحي ما بيتلمس">
          ارجع عتبات المختبر للأصل
        </button>
        {note ? <span style={{ fontSize: 13, color: note.ok ? 'var(--green)' : 'var(--amber)' }}>{note.text}</span> : null}
      </div>

      {cmp ? (
        <div className="scard">
          <div className="st">المقارنة</div>
          {!cmp.ok ? <div className="ss" style={{ color: 'var(--amber)' }}>{cmp.error}</div> : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, marginTop: 6, fontSize: 13.5 }}>
              <span>فشل: <b className="num">{num(cmp.before?.fail_count)}</b> → <b className="num">{num(cmp.after?.fail_count)}</b>
                <span style={{ color: (cmp.delta?.fail_count ?? 0) <= 0 ? 'var(--green)' : 'var(--red)', marginInlineStart: 6 }}>
                  {((cmp.delta?.fail_count ?? 0) > 0 ? '+' : '') + num(cmp.delta?.fail_count)}
                </span>
              </span>
              <span>المدّة: <b className="num">{num(cmp.before?.duration_s, 2)}</b> → <b className="num">{num(cmp.after?.duration_s, 2)}</b> ث</span>
              <span>عزل بعد: <b>{cmp.after?.isolate}</b></span>
            </div>
          )}
        </div>
      ) : null}

      {report ? (
        <div className="cards" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))' }}>
          <div className="scard"><div className="st">العزل</div><div className="sv" style={{ fontSize: 16 }}>{report.isolate}</div><div className="ss">{report.symbol} · {report.timeframe}</div></div>
          <div className="scard"><div className="st">شموع / تيكات</div><div className="sv num">{num(report.candles)} / {num(report.ticks)}</div><div className="ss">{num(report.duration_s, 2)} ث</div></div>
          <div className="scard"><div className="st">ذرّات محمّلة</div><div className="sv num">{num(report.atoms_loaded)}</div><div className="ss">{(report.atom_ids ?? []).slice(0, 8).join(' · ')}{(report.atom_ids ?? []).length > 8 ? '…' : ''}</div></div>
          <div className="scard"><div className="st">فشل معلَن</div><div className={`sv num ${(report.fail_count ?? 0) > 0 ? 'amber' : 'green'}`}>{num(report.fail_count)}</div><div className="ss">من {num(units.length)} حدث</div></div>
          <div className="scard"><div className="st">قرارات</div><div className="sv num">{num(report.decisions?.count)}</div><div className="ss">{report.run_id}</div></div>
        </div>
      ) : null}

      {report?.error ? (
        <div className="scard" style={{ color: 'var(--amber)' }}>خطأ الجولة: {report.error}</div>
      ) : null}

      {failing.length ? (
        <div className="scard" style={{ overflow: 'hidden', padding: 0 }}>
          <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--glassb)' }}>
            <div className="st">ليش عم يفشل</div>
            <div className="ss dim">آخر سبب لكل حدث — من حمولة الذرّة نفسها، مو تخمين لوحة.</div>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table className="tbl" style={{ width: '100%', fontSize: 12.5, borderCollapse: 'collapse' }}>
              <thead><tr className="dim" style={{ textAlign: 'right' }}>
                <th style={{ padding: '6px 10px' }}>الحدث</th>
                <th style={{ padding: '6px 10px' }}>الحالة</th>
                <th style={{ padding: '6px 10px' }}>السبب</th>
              </tr></thead>
              <tbody>
                {failing.map((u) => (
                  <tr key={u.event} style={{ borderTop: '1px solid var(--line)', cursor: 'pointer' }}
                    onClick={() => setOpenEvent(openEvent === u.event ? null : u.event)}>
                    <td style={{ padding: '6px 10px', fontFamily: 'ui-monospace, monospace', fontSize: 12 }}>{u.event}</td>
                    <td style={{ padding: '6px 10px' }}><span className="pill amber">{u.state}</span></td>
                    <td style={{ padding: '6px 10px', color: 'var(--amber)' }}>{u.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {units.length ? (
        <div className="scard" style={{ overflow: 'hidden', padding: 0 }}>
          <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--glassb)' }}>
            <div className="st">آخر حمولة لكل حدث</div>
            <div className="ss dim">اضغط صف لفتح السجل التفصيلي.</div>
          </div>
          <div style={{ overflowX: 'auto', maxHeight: 320 }}>
            <table className="tbl" style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
              <thead><tr className="dim" style={{ textAlign: 'right' }}>
                <th style={{ padding: '5px 10px' }}>الحدث</th>
                <th style={{ padding: '5px 10px' }}>الحالة</th>
                <th style={{ padding: '5px 10px' }}>اتجاه</th>
                <th style={{ padding: '5px 10px' }}>ثقة</th>
                <th style={{ padding: '5px 10px' }}>عمق</th>
              </tr></thead>
              <tbody>
                {units.map((u) => {
                  const last = u.last ?? {}
                  const dir = last.direction ?? last.score
                  return (
                    <tr key={u.event} style={{ borderTop: '1px solid var(--line)', cursor: 'pointer' }}
                      onClick={() => setOpenEvent(openEvent === u.event ? null : u.event)}>
                      <td style={{ padding: '5px 10px', fontFamily: 'ui-monospace, monospace' }}>{u.event}</td>
                      <td style={{ padding: '5px 10px' }}>
                        <span className={`pill ${u.reason ? 'amber' : 'green'}`}>{u.state}</span>
                      </td>
                      <td className="num" style={{ padding: '5px 10px' }}>{dir == null ? '—' : String(dir)}</td>
                      <td className="num" style={{ padding: '5px 10px' }}>{last.confidence == null ? '—' : String(last.confidence)}</td>
                      <td className="num" style={{ padding: '5px 10px' }}>
                        {last.current_depth == null ? '—' : `${last.current_depth} / ${last.required_depth ?? '—'}`}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {openUnit ? (
        <div className="scard">
          <div className="st">سجل · {openUnit.event}</div>
          {openUnit.reason ? <div className="ss" style={{ color: 'var(--amber)', marginTop: 4 }}>{openUnit.reason}</div> : null}
          <pre style={{ margin: '8px 0 0', fontSize: 12, direction: 'ltr', textAlign: 'left', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {JSON.stringify(openUnit.last ?? {}, null, 2)}
          </pre>
        </div>
      ) : null}

      {report?.stages ? (
        <div className="scard" style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 14px', fontSize: 12.5 }}>
          {Object.entries(report.stages).map(([k, v]) => (
            <span key={k}><span className="dim">{k}</span> <b className="num">{num(v.count)}</b></span>
          ))}
        </div>
      ) : null}

      {(catalog?.history?.length ?? 0) > 0 ? (
        <div className="scard">
          <div className="st">آخر الجولات</div>
          <div style={{ display: 'grid', gap: 4, marginTop: 6, fontSize: 12.5 }}>
            {(catalog?.history ?? []).slice().reverse().map((h) => (
              <div key={h.run_id} style={{ display: 'flex', gap: 10, flexWrap: 'wrap' }}>
                <span className="num dim">{clock(h.at)}</span>
                <span>{h.isolate}</span>
                <span className="num">فشل {num(h.fail_count)}</span>
                <span className="num dim">{num(h.duration_s, 2)} ث · {num(h.candles)} شمعة · {num(h.atoms_loaded)} ذرّة</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <div className="scard">
        <div className="st">تعديل عتبات ذرّة — نسخة المختبر</div>
        <div className="ss dim" style={{ marginBottom: 8 }}>
          الحفظ يكتب var/lab فقط. التداول الحي و«الإعدادات» و«التحليل» ما بيتغيّروا. بعد الحفظ شغّل جولة وقارن.
        </div>
        <select className="cfginput" style={{ minWidth: 260 }}
          value={cfgAtom === '' ? '' : String(cfgAtom)}
          onChange={(e) => setCfgAtom(e.target.value === '' ? '' : Number(e.target.value))}>
          <option value="">اختر ذرّة للمعايرة…</option>
          {(section === 'news' ? [615, 616] : currentAtoms).map((id) => (
            <option key={id} value={id}>{id} · {names[id] ?? id}</option>
          ))}
        </select>
        {cfgAtom !== '' ? <div style={{ marginTop: 10 }}><AtomConfigForm atomId={cfgAtom} sandbox /></div> : null}
      </div>
    </div>
  )
}
