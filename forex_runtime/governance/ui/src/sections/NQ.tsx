// صفحة NQ (الذرّة 001) — ختم المالك ٢٠٢٦-٠٨-٢٠، خيار «أ».
// ⛔ لا تعرض هذه الصفحة إلا ما يصل فعلًا على السلك من /gov/atoms:
//    state · health.state · health.message · restart_count · last_error
//    (ومنها نسخة النواة والبصمة المقصوصة الظاهرتان داخل نصّ الرسالة).
//    الحقول التالية موجودة داخل health.details ولا تُرسَل إطلاقًا (core/api/app.py):
//    events_seen_total · distinct_event_names · eyes_active · last_event_name ·
//    sealed_at · alerts_raised · last_alert — فلا تُعرض ولا يُدَّعى وجودها.
// تنظيف ٢٠٢٦-٠٨-٢٠ بأمر المالك: أُزيل كل ما لم يُختم (بطاقة نسخة الذرّة ·
//    آخر قراءة وصلت · آخر تغيّر في الحالة · سطر التعريف · سطر النبض · السطر
//    التفسيري · كلمة «سليمة» المشتقّة). لا ختم بأثر رجعي.
import { useStore } from '../core/store'

const NQ_ID = 1
const num = (n: number) => n.toLocaleString('ar-EG-u-nu-latn')

// رسالة الحراسة تصل كنصّ واحد: "watching: 1.17.0 (1ba8cc64a81c…)".
// نقرأ منها النسخة والبصمة المقصوصة كما ظهرتا — وإن لم يطابق الشكل، لا نخمّن.
function parseWatching(message?: string): { version: string | null; digest: string | null } {
  if (!message) return { version: null, digest: null }
  const m = /^watching:\s*(\S+)\s*\((\S+)\)\s*$/.exec(message)
  if (!m) return { version: null, digest: null }
  return { version: m[1], digest: m[2] }
}

export default function NQ() {
  const atom = useStore((s) => s.atoms[NQ_ID])
  const conn = useStore((s) => s.conn)

  const live = conn === 'live' && atom != null
  const { version: coreVersion, digest } = parseWatching(atom?.health?.message)

  return (
    <div className="section">
      <style>{`
        @keyframes nqbeat {
          0%   { transform: scale(1);    }
          1.2% { transform: scale(1.14); }
          2.4% { transform: scale(1);    }
          3.6% { transform: scale(1.08); }
          5%   { transform: scale(1);    }
          100% { transform: scale(1);    }
        }
        .nq-heart { animation: nqbeat 60s linear infinite; }
        .nq-heart.off { animation: none; opacity: .35; }
        .nq-wrap { display:flex; flex-direction:column; align-items:center;
                   justify-content:center; padding: 28px 0 18px; }
        .nq-core { width:150px; height:150px; border-radius:50%;
                   display:flex; align-items:center; justify-content:center;
                   background: var(--glass); border: 2px solid var(--glassb); }
        .nq-core .big { font-size: 30px; font-weight: 800; letter-spacing: 1px; }
        .nq-raw { direction:ltr; text-align:left; font-family: ui-monospace, Consolas, monospace; }
      `}</style>

      <div className="nq-wrap">
        <div className={live ? 'nq-heart nq-core' : 'nq-heart nq-core off'}>
          <div className="big">NQ</div>
        </div>
      </div>

      <div className="cards">
        <div className="scard"><div className="st">حالة الذرّة</div>
          <div className="sv">{atom?.state ?? '—'}</div></div>
        <div className="scard"><div className="st">الصحّة</div>
          <div className="sv">{atom?.health?.state ?? '—'}</div></div>
        <div className="scard"><div className="st">نسخة النواة</div>
          <div className="sv num">{coreVersion ?? '—'}</div></div>
        <div className="scard"><div className="st">بصمة النواة (مقصوصة)</div>
          <div className="sv num nq-raw">{digest ?? '—'}</div></div>
        <div className="scard"><div className="st">إعادات التشغيل</div>
          <div className="sv num">{atom ? num(atom.restart_count) : '—'}</div></div>
        <div className="scard"><div className="st">آخر خطأ</div>
          <div className="sv">{atom?.last_error ?? '—'}</div></div>
      </div>

      <div className="ss" style={{ marginTop: 14 }}>
        <div className="st">رسالة الحراسة</div>
        <div className="nq-raw">{atom?.health?.message ?? '—'}</div>
      </div>
    </div>
  )
}
