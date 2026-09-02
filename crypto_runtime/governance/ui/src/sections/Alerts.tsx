// التنبيهات (863) — دماغ الحوكمة: يميّز الخلل الحقيقي عن «لسا ما جهّز» (من حالة الذرات الحيّة)
// + إنذار الصمت: تيار كان ينبض ووقف (فكرة مسبار الناقل القديم — محسوبة من النبض الحيّ، بلا موك).
// + المُنذِر (831): إخفاقات نشطة من تيارات كانت بلا مستمع — يعرض حالة الذرة كما وصلت
// (system.alert.state يُعاد للمتأخّر تلقائيًا من الناقل)، بلا حساب ولا تقدير.
// أسماء التيارات من القاموس المشترك core/streams (بند 10 بدفتر 97: كان 13 اسمًا
// والنظام يراقب 20+، فطلع «تيار غير معروف») — الغريب يظهر باسمه الخام، صدق أوضح من إخفائه.
import { useMemo } from 'react'
import { useStore } from '../core/store'
import { streamAr } from '../core/streams'
const MIN_PULSES = 6            // ما منحكم على تيار قبل ما نشوف نبضه مرّات كافية
const MIN_SILENCE_MS = 60000    // وأقل صمت يُعتبر إنذارًا = دقيقة

interface AlertEntry {
  severity?: string
  source_atom?: number | null
  count?: number
  last_at?: number
  detail?: string
}
interface AlertState {
  total?: number
  alerts?: Record<string, AlertEntry>
  updated_at?: number
}

export default function Alerts() {
  const atoms = useStore((s) => s.atoms)
  const flowStats = useStore((s) => s.flowStats)
  const alertState = useStore((s) => s.streams['system.alert.state']) as AlertState | undefined

  const atomName = (id?: number | null): string | undefined =>
    id != null ? atoms[id]?.name_ar : undefined

  const alerterAlerts = useMemo<Array<[string, AlertEntry]>>(() => {
    const rows = alertState?.alerts
    if (!rows || typeof rows !== 'object') return []
    return Object.entries(rows).sort((a, b) => {
      const s = (x: AlertEntry) => (x.severity === 'critical' ? 0 : x.severity === 'warning' ? 1 : 2)
      return s(a[1]) - s(b[1]) || (b[1].last_at ?? 0) - (a[1].last_at ?? 0)
    })
  }, [alertState])

  const silences = useMemo(() => {
    const now = performance.now()
    const out: Array<{ ev: string; ageS: number; avgS: number }> = []
    for (const [ev, st] of Object.entries(flowStats)) {
      if (st.n < MIN_PULSES) continue
      const avg = (st.last - st.first) / (st.n - 1)
      const age = now - st.last
      if (age > Math.max(MIN_SILENCE_MS, avg * 5)) {
        out.push({ ev, ageS: Math.round(age / 1000), avgS: Math.max(1, Math.round(avg / 1000)) })
      }
    }
    return out.sort((a, b) => b.ageS - a.ageS)
  }, [flowStats, atoms])
  const { real, waiting, stopped } = useMemo(() => {
    const all = Object.values(atoms)
    return {
      real: all.filter((a) => a.color === 'red'),
      waiting: all.filter((a) => a.color === 'amber'),
      stopped: all.filter((a) => a.color === 'grey' && a.state === 'stopped'),
    }
  }, [atoms])

  const nothing = real.length === 0 && stopped.length === 0
  return (
    <div className="section" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div className="cards">
        <div className="scard"><div className="st">خلل حقيقي</div><div className={`sv num ${real.length ? 'red' : 'green'}`}>{real.length}</div></div>
        <div className="scard"><div className="st">المُنذِر (831) نشط</div><div className={`sv num ${alerterAlerts.length ? 'red' : 'green'}`}>{alerterAlerts.length}</div><div className="ss dim">إخفاقات موثّقة من الناقل</div></div>
        <div className="scard"><div className="st">تيارات وقفت عن النبض</div><div className={`sv num ${silences.length ? 'red' : 'green'}`}>{silences.length}</div><div className="ss dim">كان ينبض ووقف — منذ فتح اللوحة</div></div>
        <div className="scard"><div className="st">واقفة</div><div className={`sv num ${stopped.length ? 'amber' : 'grey'}`}>{stopped.length}</div></div>
        <div className="scard"><div className="st">بانتظار مُنتِجها</div><div className="sv num grey">{waiting.length}</div><div className="ss">خطّافات مستقبل — مو مشكلة</div></div>
      </div>
      {alerterAlerts.length ? (
        <div className="scard" style={{ borderColor: 'var(--red)' }}>
          <div className="st">🚨 المُنذِر (831) — إخفاقات نشطة</div>
          {alerterAlerts.map(([ev, a]) => (
            <div key={ev} className="ss" style={{ color: a.severity === 'critical' ? 'var(--red)' : 'var(--amber)' }}>
              ● {streamAr(ev)}
              {a.source_atom != null && atomName(a.source_atom) ? <span> — {atomName(a.source_atom)}</span> : null}
              {a.count != null ? <span> — {a.count}×</span> : null}
              {a.detail ? <span className="dim"> — {a.detail}</span> : null}
            </div>
          ))}
        </div>
      ) : null}
      {silences.length ? (
        <div className="scard" style={{ borderColor: 'var(--red)' }}>
          <div className="st">⏸ تيارات كانت تنبض ووقفت</div>
          {silences.map(({ ev, ageS, avgS }) => (
            <div key={ev} className="ss" style={{ color: 'var(--red)' }}>
              ● {streamAr(ev)} — ساكت من {ageS >= 120 ? `${Math.round(ageS / 60)} دقيقة` : `${ageS} ثانية`} (كان ينبض كل ~{avgS}ث)
            </div>
          ))}
        </div>
      ) : null}
      <div className="loglist" style={{ flex: 1 }}>
        {nothing ? <div className="empty">كل شي تمام — لا خلل حقيقي 🟢</div> : null}
        {real.map((a) => (
          <div className="logrow" key={a.id}><span className="red">● خلل</span><span className="ln">{a.name_ar}</span><span className="dim" style={{ marginInlineStart: 'auto' }}>{a.last_error ? 'تفصيل العطل متاح في السجل' : ''}</span></div>
        ))}
        {stopped.map((a) => (
          <div className="logrow" key={a.id}><span className="grey">● واقفة</span><span className="ln">{a.name_ar}</span></div>
        ))}
      </div>
    </div>
  )
}
