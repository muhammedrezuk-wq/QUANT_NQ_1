// المخاطر (856) — الحالة الموحّدة العالمية من مدير المخاطر (500: risk.unified.state):
// قاطع الأمان · الخسارة اليومية · الخسائر المتتالية · عدد صفقات اليوم. يُنتَج عند حدث خسارة/إيقاف/تصفير.
// + تصفير القاطع عبر بوّابة الأوامر (901) بتأكيد — وسجلّ البوّابة (system.commands.state).
import { useStore } from '../core/store'
import { dangerCommand } from '../core/commands'
import { SectionConfigTable } from '../components/SectionAtoms'
import { DecisionDialsCard, RISK_DIAL_NAMES } from './Settings'

const money = (n?: number) => (n == null ? '—' : n.toLocaleString('ar-EG-u-nu-latn', { maximumFractionDigits: 2 }))

interface GatewayState { seen?: number; executed_halt?: number; executed_reset?: number; expired?: number; rejected?: number }

export default function Risk() {
  const risk = useStore((s) => s.risk)
  const gw = useStore((s) => s.streams['system.commands.state']) as GatewayState | undefined

  const resetBtn = (
    <button
      className="btn"
      style={{ borderColor: 'var(--amber)', color: 'var(--amber)', marginTop: 10 }}
      title="يرفع الإيقاف — عبر بوّابة الأوامر (901) بتأكيد"
      onClick={async () => { const r = await dangerCommand('kill_switch_reset'); if (r.message) window.alert(r.message) }}
    >تصفير قاطع الأمان</button>
  )

  if (!risk) {
    return (
      <div className="section" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div className="empty">بانتظار أوّل حدث مخاطر من النواة… (لا صفقات بعد — تُنتَج عند أوّل نتيجة/إيقاف)</div>
        <div className="scard">
          <div className="st">بوّابة الأوامر (901)</div>
          <div className="ss dim">{gw ? `نُفِّذ: إيقاف ${gw.executed_halt ?? 0} · تصفير ${gw.executed_reset ?? 0} · منتهي الصلاحية ${gw.expired ?? 0} · مرفوض ${gw.rejected ?? 0}` : 'ما وصل أمر بعد'}</div>
          {resetBtn}
        </div>
      </div>
    )
  }

  const kill = risk.kill_switch_state === true
  return (
    <div className="section" style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div className={`scard`} style={{ borderColor: kill ? 'var(--red)' : 'var(--glassb)' }}>
        <div className="st">قاطع الأمان</div>
        <div className={`sv ${kill ? 'red' : 'green'}`}>{kill ? '🛑 مُفعَّل — التداول موقوف' : '🟢 سليم — التداول مسموح'}</div>
        {kill && risk.kill_switch_reason ? <div className="ss red">السبب: {risk.kill_switch_reason}</div> : null}
        {kill ? <div className="ss dim">التصفير يدويّ فقط (لا يُصفَّر تلقائيًّا مع اليوم).</div> : null}
        {resetBtn}
        <div className="ss dim" style={{ marginTop: 6 }}>{gw ? `بوّابة الأوامر: إيقاف ${gw.executed_halt ?? 0} · تصفير ${gw.executed_reset ?? 0}` : 'بوّابة الأوامر: ما وصلها أمر بعد'}</div>
      </div>

      <DecisionDialsCard onlyNames={RISK_DIAL_NAMES} includeExtras={false} />

      <div className="cards">
        <div className="scard"><div className="st">الخسارة اليومية</div><div className={`sv num ${(risk.daily_loss_pct ?? 0) < 0 ? 'red' : ''}`}>{money(risk.daily_loss_pct)}%</div></div>
        <div className="scard"><div className="st">الخسائر المتتالية</div><div className={`sv num ${(risk.consecutive_losses ?? 0) >= 3 ? 'red' : ''}`}>{risk.consecutive_losses ?? 0}</div></div>
        <div className="scard"><div className="st">صفقات اليوم</div><div className="sv num">{risk.daily_trade_count ?? 0}</div></div>
      </div>

      <SectionConfigTable from={500} to={530} title="معاملات ذرّات المخاطر (500-529) — ضبط جماعي (قاطع 516 · حدود ربح 507 · حدود جلسة 506)" />
    </div>
  )
}
