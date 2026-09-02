// حاكم التغيير (850) — عرض حيّ للحدود والتجارب، بلا أزرار تنفيذ أو اعتماد.
// لا نخترع حالة: كل قيمة تأتي من الحدث الفعلي، والغياب يظهر «—».
import { useMemo, useState } from 'react'
import { useStore } from '../core/store'

type Payload = Record<string, unknown>

function text(v: unknown): string {
  if (v == null || v === '') return '—'
  if (typeof v === 'boolean') return v ? 'نعم' : 'لا'
  if (typeof v === 'number') return String(v)
  return String(v)
}

function pick(p: Payload | undefined, keys: string[]): unknown {
  if (!p) return undefined
  for (const k of keys) if (p[k] != null) return p[k]
  return undefined
}

function Card({ title, value, sub, color = '' }: { title: string; value: string; sub?: string; color?: string }) {
  return <div className="scard"><div className="st">{title}</div><div className={`sv ${color}`}>{value}</div>{sub ? <div className="ss dim">{sub}</div> : null}</div>
}

export default function ChangeGovernor() {
  const [view, setView] = useState<'overview' | 'experiments' | 'limits' | 'kill' | 'events'>('overview')
  const streams = useStore((s) => s.streams) as Record<string, unknown>
  const exp = streams['experiment.state'] as Payload | undefined
  const limits = streams['adaptation.limits.state'] as Payload | undefined
  const kill = streams['adaptation.kill_switch.state'] as Payload | undefined
  const proposed = streams['recalibration.proposed'] as Payload | undefined
  const seen = useMemo(() => [exp, limits, kill, proposed].filter(Boolean).length, [exp, limits, kill, proposed])

  const killState = text(pick(kill, ['state', 'status', 'mode']))
  const expState = text(pick(exp, ['state', 'status', 'phase']))
  const proposedState = text(pick(proposed, ['state', 'status', 'decision']))

  return (
    <div className="section" style={{ display: 'flex', flexDirection: 'column', gap: 14, overflow: 'auto' }}>
      <div className="scard" style={{ borderInlineStart: '4px solid var(--accent)' }}>
        <div className="st" style={{ fontSize: 17 }}>🛡️ حاكم التغيير · الذرة 850</div>
        <div className="ss dim">مراقبة التجارب وحدود التغيير فقط. هذه الصفحة للقراءة والتدقيق؛ لا تعتمد تعديلًا ولا ترسل أمرًا.</div>
      </div>
      <div className="change-nav" role="tablist" aria-label="أقسام حاكم التغيير">
        {([['overview', 'نظرة عامة'], ['experiments', 'التجارب'], ['limits', 'الحدود'], ['kill', 'مفتاح الإيقاف'], ['events', 'الأحداث']] as const).map(([id, label]) => (
          <button key={id} className={`btn ${view === id ? 'active' : ''}`} onClick={() => setView(id)}>{label}</button>
        ))}
      </div>
      {view === 'overview' ? <div className="cards">
        <Card title="حالة التجربة" value={expState} sub={text(pick(exp, ['experiment_id', 'id', 'symbol']))} color={exp ? 'green' : 'grey'} />
        <Card title="حدود التكيّف" value={text(pick(limits, ['state', 'status']))} sub={limits ? `السقف اليومي: ${text(pick(limits, ['max_change_per_day', 'daily_limit']))}` : 'لم يصل حدث الحدود'} color={limits ? 'green' : 'grey'} />
        <Card title="مفتاح الإيقاف" value={killState} sub={text(pick(kill, ['reason', 'message']))} color={kill ? (killState === 'engaged' || killState === 'on' ? 'red' : 'amber') : 'grey'} />
        <Card title="اقتراح إعادة المعايرة" value={proposedState} sub={text(pick(proposed, ['reason', 'symbol']))} color={proposed ? 'amber' : 'grey'} />
        <Card title="أحداث واصلة" value={`${seen} / 4`} sub="من أحداث عقد الذرة 850" color={seen === 4 ? 'green' : seen ? 'amber' : 'grey'} />
      </div> : null}
      {view === 'experiments' ? <div className="scard"><div className="st">التجارب</div><div className="ss">الحالة: {expState} · المعرّف: {text(pick(exp, ['experiment_id', 'id']))} · الرمز: {text(pick(exp, ['symbol']))}</div></div> : null}
      {view === 'limits' ? <div className="scard"><div className="st">حدود التكيّف</div><div className="ss">الحالة: {text(pick(limits, ['state', 'status']))} · خطوة: {text(pick(limits, ['max_change_per_step', 'step_limit']))} · يوم: {text(pick(limits, ['max_change_per_day', 'daily_limit']))} · نافذة: {text(pick(limits, ['max_changes_per_window', 'window_limit']))}</div></div> : null}
      {view === 'kill' ? <div className="scard"><div className="st">مفتاح الإيقاف</div><div className={`sv ${kill ? (killState === 'engaged' || killState === 'on' ? 'red' : 'amber') : 'grey'}`}>{killState}</div><div className="ss dim">السبب: {text(pick(kill, ['reason', 'message']))}</div></div> : null}
      {view === 'events' ? <div className="scard"><div className="st">الأحداث الأربعة</div><div className="ss dim">experiment.state: {exp ? 'واصل' : '—'} · adaptation.limits.state: {limits ? 'واصل' : '—'} · adaptation.kill_switch.state: {kill ? 'واصل' : '—'} · recalibration.proposed: {proposed ? 'واصل' : '—'}</div></div> : null}
      <div className="scard">
        <div className="st">مبدأ التشغيل</div>
        <div className="ss dim" style={{ lineHeight: 1.9 }}>
          لا يظهر «سليم» أو «مسموح» من التخمين. كل بطاقة تعتمد على حدثها الفعلي، وأي حقل غير منشور يبقى «—».
          لا يوجد في هذه اللوحة زر تفعيل أو اعتماد، حفاظًا على الفحص الآمن.
        </div>
      </div>
    </div>
  )
}
