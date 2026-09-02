// التشخيص (869) — صحة النواة + تقرير الإقلاع (منافذ حقيقية: /gov/health · /gov/boot-report)
// + محلّل الصمت الحيّ: ليش كل ذرة ساكتة ساكتة — على النظام الحيّ الحقيقي، بلا موك
// (الفكرة من أداة exercise.py القديمة؛ التنفيذ حيّ: مانيفستات /gov/graph + نبض الأحداث الفعلي).
import { useEffect, useMemo, useState } from 'react'
import { useStore } from '../core/store'
import { streamAr } from '../core/streams'

interface Health { status: string; core_version: string; atom_count: number }
interface Boot {
  started_at: number; finished_at: number; success: boolean
  booted: number[]; failed: number[]; excluded: number[]; scan_failures: unknown[]
}
interface Wiring {
  pubs?: Record<string, number[]>; subs?: Record<string, number[]>
  foreign_pubs?: string[]; foreign_market?: string
}

// أحداث النبض/الساعة — وصولها لا يعني «وصلها مدخل شغل»، وإلا انفلَغت كل الذرات الدورية
const PULSE = new Set(['SYS_SECOND', 'SYS_DAY', 'kernel.clock.heartbeat', 'time.utc.synced'])

function Card({ t, v, c, sub }: { t: string; v: string; c?: string; sub?: string }) {
  return (
    <div className="scard">
      <div className="st">{t}</div>
      <div className={`sv ${c ?? ''}`}>{v}</div>
      {sub ? <div className="ss">{sub}</div> : null}
    </div>
  )
}

type Verdict = { kind: string; detail: string; alarm?: boolean }

export default function Diag() {
  const [h, setH] = useState<Health | null>(null)
  const [b, setB] = useState<Boot | null>(null)
  const [wiring, setWiring] = useState<Wiring | null>(null)
  const atoms = useStore((s) => s.atoms)
  const flows = useStore((s) => s.flows)
  const namesAr = useStore((s) => s.namesAr)
  const telemetry = atoms[810]

  useEffect(() => {
    const load = () => {
      fetch('/gov/health').then((r) => r.json()).then(setH).catch(() => {})
      fetch('/gov/boot-report').then((r) => (r.ok ? r.json() : null)).then(setB).catch(() => {})
    }
    const loadWiring = () =>
      fetch('/gov/graph').then((r) => r.json()).then(setWiring).catch(() => {})
    load(); loadWiring()
    const t = setInterval(load, 4000)
    const t2 = setInterval(loadWiring, 15000)
    return () => { clearInterval(t); clearInterval(t2) }
  }, [])

  const nameOf = (id: number) => namesAr[id] ?? `#${id}`

  const analysis = useMemo(() => {
    if (!wiring?.pubs || !wiring?.subs) return null
    const pubs = wiring.pubs, subs = wiring.subs
    const atomPub: Record<number, string[]> = {}
    const atomSub: Record<number, string[]> = {}
    for (const [ev, ids] of Object.entries(pubs)) for (const id of ids) (atomPub[id] ??= []).push(ev)
    for (const [ev, ids] of Object.entries(subs)) for (const id of ids) (atomSub[id] ??= []).push(ev)

    const fired = new Set(Object.keys(flows))
    const spoke = new Set<number>()
    for (const [idStr, evs] of Object.entries(atomPub)) {
      if (evs.some((e) => fired.has(e))) spoke.add(Number(idStr))
    }

    const groups: Record<string, Array<{ id: number; detail: string }>> = {}
    const push = (kind: string, id: number, detail: string) => (groups[kind] ??= []).push({ id, detail })

    for (const a of Object.values(atoms)) {
      if (a.state === 'stopped') { push('واقفة (بيد المالك)', a.id, ''); continue }
      if (spoke.has(a.id)) continue
      const myPubs = atomPub[a.id] ?? []
      const mySubs = (atomSub[a.id] ?? []).filter((e) => !e.startsWith('core.'))
      if (!myPubs.length) { push('مستهلِكة فقط — ما بتنشر أصلًا (طبيعي)', a.id, ''); continue }
      const work = mySubs.filter((e) => !PULSE.has(e))
      if (!work.length) { push('دورية — بتنشر على دورتها (طبيعي)', a.id, ''); continue }
      const orphan = work.filter((e) => !(pubs[e]?.length))
      if (orphan.length === work.length) { push('يتيمة — محدا بينشر مدخلها', a.id, streamAr(orphan[0])); continue }
      const firedWork = work.filter((e) => fired.has(e))
      if (!firedWork.length) {
        const upstream = new Set<number>()
        for (const e of work) for (const p of pubs[e] ?? []) if (!spoke.has(p)) upstream.add(p)
        push('مجوّعة — ناشر مدخلها ساكت', a.id,
          upstream.size ? 'تستنى: ' + [...upstream].slice(0, 3).map(nameOf).join(' · ') : '')
        continue
      }
      push('⚠ وصلها مدخلها وما نطقت — تستاهل تحقيق', a.id, 'وصلها: ' + streamAr(firedWork[0]))
    }

    // الوصلات المكسورة على مستوى النظام كله
    // ٢٠٢٦-٠٨-٢٩ (ختم NQ): ذرّات الكريبتو مَنقولة عن الفوركس، فحملت معها
    // اشتراكات بأحداث منصّة/تنفيذ فوركسيّة لا ناشر لها هنا ولن يكون
    // (لا MT5 ولا cTrader ولا تنفيذ آليّ — تنفيذ الكريبتو بشريّ على MEXC).
    // كانت تُعرض كـ«وصلات مكسورة» فيبدو قسم أسمر مليئًا بالفوركس.
    // تُفرَز الآن في مجموعة مُعلَنة: غيابها صحيح بالتصميم، لا عطل — ولا تُخفى.
    const foreign = new Set(wiring.foreign_pubs ?? [])
    const foreignMarket = wiring.foreign_market === 'crypto' ? 'الكريبتو' : 'الفوركس'
    const noPublisher: Array<{ ev: string; who: number[] }> = []
    const inheritedForex: Array<{ ev: string; who: number[] }> = []
    for (const [ev, ids] of Object.entries(subs)) {
      if ((pubs[ev]?.length) || PULSE.has(ev) || ev.startsWith('core.')) continue
      if (foreign.has(ev)) inheritedForex.push({ ev, who: ids })
      else noPublisher.push({ ev, who: ids })
    }
    const noListener: string[] = []
    for (const [ev, ids] of Object.entries(pubs)) {
      if (ids.length && !(subs[ev]?.length)) noListener.push(ev)
    }
    const forDashboard = (e: string) => /\.state$|\.snapshot$|\.synced$|\.collected$|\.updated$|\.completed$/.test(e)
    return { groups, spokeCount: spoke.size, noPublisher, inheritedForex, foreignMarket, noListener: noListener.filter((e) => !forDashboard(e)) }
  }, [wiring, atoms, flows, namesAr])

  const bootMs = b ? Math.round((b.finished_at - b.started_at) * 1000) : null
  const ORDER = ['⚠ وصلها مدخلها وما نطقت — تستاهل تحقيق', 'يتيمة — محدا بينشر مدخلها',
    'مجوّعة — ناشر مدخلها ساكت', 'واقفة (بيد المالك)', 'دورية — بتنشر على دورتها (طبيعي)',
    'مستهلِكة فقط — ما بتنشر أصلًا (طبيعي)']

  return (
    <div className="section" style={{ display: 'flex', flexDirection: 'column', gap: 14, overflow: 'auto' }}>
      <div className="cards">
        <Card t="النواة" v={h?.status === 'ok' ? 'سليمة' : '—'} c="green" sub={h ? `إصدار ${h.core_version}` : ''} />
        <Card t="عدد الذرات" v={h ? String(h.atom_count) : '—'} />
        <Card t="الإقلاع" v={b ? (b.success ? 'نجح' : 'فشل') : '—'} c={b?.success ? 'green' : 'red'} sub={bootMs != null ? `خلال ${bootMs} جزء من الثانية` : ''} />
        <Card t="أقلعت" v={b ? String(b.booted.length) : '—'} c="green" />
        <Card t="فشلت بالإقلاع" v={b ? String(b.failed.length) : '—'} c={b && b.failed.length ? 'red' : 'grey'} />
        <Card t="نطقت منذ فتح اللوحة" v={analysis ? String(analysis.spokeCount) : '—'} c="green" />
        <Card
          t="ناقل التلمترية (810)"
          v={!telemetry ? 'غير محمّل' : telemetry.health?.state === 'healthy' ? 'يعمل' : telemetry.health?.state === 'degraded' ? 'ينتظر بيانات' : 'متوقّف'}
          c={!telemetry ? 'grey' : telemetry.health?.state === 'healthy' ? 'green' : telemetry.health?.state === 'degraded' ? 'amber' : 'red'}
          sub={telemetry?.health?.message ?? 'لم تصل حالة الناقل بعد'}
        />
      </div>

      <div className="scard">
        <div className="st">لمحة المخازن (عيلة 700) — مين عم يحفظ ومين متعثّر</div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px 18px', marginTop: 8 }}>
          {Object.values(atoms).filter((a) => a.id >= 700 && a.id < 800).sort((a, b) => a.id - b.id).map((a) => (
            <span key={a.id} style={{ whiteSpace: 'nowrap', fontSize: 13 }}>
              <span style={{ color: a.color === 'green' ? 'var(--green)' : a.color === 'red' ? 'var(--red)' : a.color === 'amber' ? 'var(--amber)' : 'var(--dim)' }}>●</span>{' '}
              {a.name_ar ?? nameOf(a.id)} <span className="dim" style={{ fontSize: 11.5 }}>{a.label_ar ?? ''}</span>
            </span>
          ))}
          {Object.values(atoms).filter((a) => a.id >= 700 && a.id < 800).length === 0 ? <span className="dim">ما في ذرات مخازن محمّلة</span> : null}
        </div>
      </div>

      {analysis == null ? (
        <div className="scard"><div className="ss dim">محلّل الصمت بدو النسخة الجديدة من الخادم — أعد فتح «غرفة القيادة» مرّة وحدة.</div></div>
      ) : (
        <>
          <div className="scard">
            <div className="st">وصلات مكسورة (من عقود الذرات الحقيقية)</div>
            {analysis.noPublisher.length === 0 ? (
              <div className="ss" style={{ color: 'var(--green)' }}>ما في مدخل بلا ناشر — كل ذرة مدخلها موصول 🟢</div>
            ) : analysis.noPublisher.map(({ ev, who }) => (
              <div key={ev} className="ss" style={{ color: 'var(--red)' }}>
                {/* الاسم العربي من القاموس المشترك + الخام للتشخيص (بند 13 بدفتر 97) */}
                ● {streamAr(ev) === ev ? <>«{ev}»</> : <>{streamAr(ev)} <span className="dim">(«{ev}»)</span></>} محدا بينشره — وبيستنّاه: {who.slice(0, 4).map(nameOf).join(' · ')}
              </div>
            ))}
            {analysis.noListener.length ? (
              <div className="ss dim" style={{ marginTop: 6 }}>
                مخرجات بلا مستمع (ممكن مقصودة): {analysis.noListener.length} تيار
              </div>
            ) : null}
          </div>

          {/* اشتراكات فوركس موروثة — تُعلَن ولا تُخفى، ولا تُحسب عطلًا (٢٠٢٦-٠٨-٢٩) */}
          {analysis.inheritedForex.length ? (
            <div className="scard">
              <div className="st">اشتراكات موروثة عن {analysis.foreignMarket} — خارج عمل هذا السوق ({analysis.inheritedForex.length})</div>
              <div className="ss dim">
                هذه الذرّات منقولة عن {analysis.foreignMarket}، فحملت معها اشتراكات بأحداث
                <b> لا يَنشرها إلا ذاك السوق</b> (قِيس بالاسم من شجرة ذرّاته، لا بتخمين).
                لا ناشر لها هنا ولن يكون. <b>غيابها صحيح بالتصميم، لا عطل:</b>
                اشتراك بحدث لا يُنشَر لا يُستدعى أبدًا — صفر كلفة وصفر أثر.
              </div>
              <details style={{ marginTop: 6 }}>
                <summary className="ss dim" style={{ cursor: 'pointer' }}>عرض التفصيل</summary>
                {analysis.inheritedForex.map(({ ev, who }) => (
                  <div key={ev} className="ss dim" style={{ fontSize: 12 }}>
                    ● {streamAr(ev) === ev ? <>«{ev}»</> : <>{streamAr(ev)} <span className="dim">(«{ev}»)</span></>}
                    {' '}← {who.slice(0, 4).map(nameOf).join(' · ')}
                  </div>
                ))}
              </details>
            </div>
          ) : null}

          <div className="scard">
            <div className="st">لماذا الصمت؟ — تصنيف كل ذرة ساكتة بسببها (القياس منذ فتح اللوحة)</div>
            <div className="ss dim">ذرة بطيئة الدورة (نسخ احتياطي، أرشفة…) ممكن تبين ساكتة وهي سليمة — التصنيف بيوضّح.</div>
            {ORDER.filter((k) => analysis.groups[k]?.length).map((kind) => (
              <div key={kind} style={{ marginTop: 10 }}>
                <div style={{ fontWeight: 700, color: kind.startsWith('⚠') ? 'var(--amber)' : kind.includes('يتيمة') ? 'var(--red)' : 'var(--dim)' }}>
                  {kind} — {analysis.groups[kind].length}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px 14px', marginTop: 4 }}>
                  {analysis.groups[kind].map(({ id, detail }) => (
                    <span key={id} style={{ fontSize: 12.5 }}>
                      {nameOf(id)}{detail ? <span className="dim"> ({detail})</span> : null}
                    </span>
                  ))}
                </div>
              </div>
            ))}
            {Object.keys(analysis.groups).length === 0 ? (
              <div className="ss" style={{ color: 'var(--green)' }}>كل الذرات نطقت منذ فتح اللوحة 🟢</div>
            ) : null}
          </div>
        </>
      )}
    </div>
  )
}
