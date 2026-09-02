// المحرّك الحيّ (١٤ §٦): WS → طابور → requestAnimationFrame → تحديث واحد لكل إطار.
// مهما انهمر البثّ، تصريف واحد لكل إطار شاشة — لا سحب دوري، لا رسم لكل حدث.
import { useStore, type AtomRec } from './store'
import { connectWs, govGet, type WsMsg } from './transport'
import { labelOf } from './i18n'

// أحداث مهمّة للمراقبة (بلا ضجيج التكّات/الساعة/الحالات المستمرّة)
// system.alert = تنبيه المُنذِر (831) لحظة صدور، بلا .state (الحالة ليست حدث مراقبة)
// إصلاح م-6 (ورقة ٤١، أمر المالك 2026-08-28): تصعيد المالك واستنفاد زوج التحوّط
// كانا يُنشران ولا يظهران بأي مكان — النظام «يصرخ» واللوحة لا تسمعه.
const IMPORTANT = /crypto\.(universe|feed)|provider_down|provider_recovered|feed_interrupted|feed_recovered|halt|order\.|execution\.|trade\.|\.rejected|hot_loaded|hot_unloaded|reloaded|system\.alert(?!\.state)|atom\.(started|stopped|failed|restarted|unhealthy)|perpetual\.owner\.escalation|perpetual\.pair\.state/i

// حزمة ج (ختم 22): سلسلة القرار + التنفيذ الدائم + تفعيل الأصل — أحداث تحمل `symbol`
// وتحتاج مفتاحًا مستقلًا لكل رمز (لا "آخر قيمة عامّة" مثل streams). عقود الأحداث
// مقروءة من الكود الفعلي وقت التنفيذ (451/452/453/454/455/456/457/458/466/467/450/581/576)
// — راجع سياق/٩٨ حزمة ج §٣ لجدول الحدث↔الناشر↔الحقول.
const SYMBOL_KEYED = new Set([
  'decision.aggregated.state',       // 451 — تجميع
  'decision.evaluated.state',        // 452 — تقييم
  'decision.scored.state',           // 453 — درجة
  'decision.eligibility.buy.state',  // 455 — أهلية الشراء
  'decision.eligibility.sell.state', // 456 — أهلية البيع
  'decision.wait.state',             // 457 — الانتظار
  'decision.resolved.state',         // 458 — حسم
  'decision.filtered.state',         // 454 — حواجز
  'decision.approved.state',         // 466 — موافقة
  'decision.gate.passed',            // 467 — بوابة (مرّت)
  'decision.gate.blocked',           // 467 — بوابة (محجوبة)
  'decision.gate.recorded',          // 467 — بوابة (انتظار مسجَّل)
  'decision.cycle.record',           // 450 — السجل الموحّد لرحلة القرار
  'perpetual.target.state',          // 581 — الهدف/المركز الدائم
  'horizon.profile.state',           // 523 — ظل شخصية الأفق (لا يطبَّق حتى أمر المالك)
  'perpetual.entry.state',           // 576 — حالة تفعيل الأصل
  // ٢٠٢٦-٠٩-٠١ (حكم المالك: «بطاقات إشارة هي وحدة، بدنا إياهم ٣ ٤… لكل
  // العملات اللي عم تدخل فورًا بتطلع بطاقتها لحالها ما ينتظروا»).
  // سلسلة الكريبتو كانت خارج هذه القائمة كلّها، فتُخزَّن في `streams` العامّة
  // بمفتاح اسم الحدث وحده — ورمزٌ يدهس رمزًا: بطاقة BTC تمحو بطاقة ETH ولا
  // يبقى إلّا آخر واصلة. هذا سبب «بطاقة واحدة» حرفيًّا.
  'crypto.decision.signal_card.state',   // 2277 — بطاقة الإشارة لكل رمز
  'crypto.decision.trigger_court.state', // 2273 — محكمة الزناد لكل رمز
  'crypto.decision.sized_entry.state',   // 2276 — الدخول المُحجَّم لكل رمز
  'crypto.decision.entry_candidate.state', // 2274 — مرشّح الدخول لكل رمز
])

// تفصيل موجز لحدث المراقبة من حمولته الحقيقيّة (رمز · جهة · سبب) — بلا حساب،
// نقل ما وصل فقط. الغياب يبقى غيابًا (بند 17 بدفتر 97: سطر الحدث كان بلا أي تفصيل).
function eventDetail(payload: unknown): string | undefined {
  if (!payload || typeof payload !== 'object') return undefined
  const p = payload as Record<string, unknown>
  const parts: string[] = []
  if (typeof p.symbol === 'string' && p.symbol) parts.push(p.symbol)
  if (p.side === 'BUY') parts.push('شراء')
  else if (p.side === 'SELL') parts.push('بيع')
  if (typeof p.reason === 'string' && p.reason) parts.push(String(p.reason))
  return parts.length ? parts.join(' · ') : undefined
}

function enrich(atoms: AtomRec[], names: Record<number, string>): AtomRec[] {
  for (const a of atoms) {
    a.name_ar = names[a.id] ?? a.name
    const [txt, color] = labelOf(a)
    a.label_ar = txt
    a.color = color
  }
  return atoms
}

/** يبدأ المحرّك: يفتح البثّ، يجمّع الرسائل بطابور، يصرّفها مرّة كل إطار. يرجّع دالة إيقاف. */
export function startEngine(): () => void {
  const queue: WsMsg[] = []
  let raf = 0

  const pump = () => {
    if (queue.length) {
      const st = useStore.getState()
      let snapAtoms: AtomRec[] | null = null
      let snapMetrics: unknown = undefined
      const events: Array<[string, unknown]> = []
      for (const m of queue) {
        if (m.type === 'snapshot') { snapAtoms = m.atoms as AtomRec[]; snapMetrics = m.metrics }
        else events.push([m.name, m.payload])
      }
      queue.length = 0
      st.markMsg()
      if (snapAtoms) st.setSnapshot(enrich(snapAtoms, st.namesAr), snapMetrics)
      const fired: string[] = []
      for (const [name, payload] of events) {
        st.setEvent(name, payload)
        fired.push(name)
        if (name === 'market.tick' || name === 'crypto.feed.tick') {
          const p = payload as { symbol?: string; bid?: number; ask?: number; timestamp?: number; received_at?: number }
          if (p.symbol && typeof p.bid === 'number' && typeof p.ask === 'number') {
            st.setMarketTick(p.symbol, p.bid, p.ask, p.timestamp ?? p.received_at ?? 0)
          }
        }
        // تنفيذ ميتاتريدر على خريطة منفصلة عن تحليل سي‑تريدر:
        // feed.mt5.tick (618) هو التِكّ الحيّ؛ market.broker_tick يبقى توافقًا إن وصل.
        // لا يُدمجان مع market.tick ولا يُحوَّل طابعهما.
        if (name === 'feed.mt5.tick' || name === 'market.broker_tick') {
          const p = payload as { symbol?: string; bid?: number; ask?: number; spread?: number; provider?: string; timestamp?: number; received_at?: number }
          if (p.symbol && typeof p.bid === 'number' && typeof p.ask === 'number') {
            st.setBrokerTick(p.symbol, p.bid, p.ask,
              typeof p.spread === 'number' ? p.spread : p.ask - p.bid,
              p.provider ?? (name === 'feed.mt5.tick' ? 'mt5' : 'الوسيط'),
              p.timestamp ?? p.received_at ?? 0)
          }
        }
        if (name === 'analysis.raw.completed') {
          const p = payload as { account_id?: string; symbol?: string; timeframe?: string }
          if (p && p.symbol) {
            const scope = `${p.account_id ?? 'بلا حساب'}::${p.symbol}`
            st.setAnalysis(scope, payload as never)
            // بند أ١١ — جسم القسم من ١٦٦ (timeframe="section"): الثماني المدموجة
            // + بطاقتا المسارين {fast, slow} كما وصلت — يُخزَّن على حدة بلا حساب.
            if (p.timeframe === 'section') st.setSectionFusion(scope, payload as never)
          }
        }
        // §١٨/§١٩ — بطاقة القسم الحيّة تصل كما بناها `section_contract`.
        // ⛔ الواجهة لا تحسب اتجاهًا ولا ثقةً ولا عمقًا ولا وزنًا؛ تعرض ما وصل.
        if (name.endsWith('.section.live')) {
          const p = payload as { account_id?: string; broker?: string; symbol?: string; section_id?: string }
          if (p && p.account_id && p.broker && p.symbol && p.section_id) {
            st.setSectionCard(`${p.account_id}::${p.broker}::${p.symbol}::${p.section_id}`, payload as never)
          }
        }
        // إقفال 150 مرحلة ٢: الروم المتدفّق — قرار حيّ متدرّج بلا انتظار أحد.
        if (name === 'decision.room.state') {
          const p = payload as { account_id?: string; broker?: string; symbol?: string }
          if (p && p.symbol) st.setRoom(`${p.account_id ?? ''}::${p.broker ?? ''}::${p.symbol}`, payload as never)
        }
        // إقفال 150 مرحلة ٣: لوحة المحلّلين — كل محلّل يعلن نفسه.
        if (name === 'analysis.analysts.state') {
          const p = payload as { account_id?: string; broker?: string; symbol?: string }
          if (p && p.symbol) st.setAnalysts(`${p.account_id ?? ''}::${p.broker ?? ''}::${p.symbol}`, payload as never)
        }
        if (name === 'market.structure.updated') {
          const p = payload as { symbol?: string }
          if (p && p.symbol) st.setStructure(p.symbol, payload as never)
        }
        if (name === 'market.liquidity.updated') {
          const p = payload as { symbol?: string }
          if (p && p.symbol) st.setLiquidity(p.symbol, payload as never)
        }
        if (name === 'stats.cycle.collected') {
          const p = payload as { symbol?: string }
          if (p && p.symbol) st.setStats(p.symbol, payload as never)
        }
        if (name === 'probability.cycle.collected') {
          const p = payload as { symbol?: string }
          if (p && p.symbol) st.setProbability(p.symbol, payload as never)
        }
        if (name === 'strategy.cycle.collected') {
          const p = payload as { symbol?: string }
          if (p && p.symbol) st.setStrategy(p.symbol, payload as never)
        }
        if (name === 'decision.cycle.collected') {
          const p = payload as { symbol?: string }
          if (p && p.symbol) st.setDecision(p.symbol, payload as never)
        }
        // حزمة ج — سلسلة القرار/التنفيذ الدائم/تفعيل الأصل، كل حدث يبقى تحت رمزه.
        if (SYMBOL_KEYED.has(name)) {
          const p = payload as { symbol?: string }
          if (p && p.symbol) st.setSymbolStream(name, p.symbol, payload)
        }
        // ج٥ — عدّاد «رفض بلا أصل» (NO_PARENT_AUTHORITY) لكل رمز
        if (name === 'perpetual.entry.rejected') {
          const p = payload as { symbol?: string; reason?: string }
          if (p && p.symbol) {
            st.setSymbolStream(name, p.symbol, payload)
            if (p.reason === 'NO_PARENT_AUTHORITY') st.bumpAssetReject(p.symbol)
          }
        }
        if (name === 'risk.unified.state') st.setRisk(payload as never)
        // بوّابة التنفيذ الحقيقيّة (٥٥٢ — تُنشَر عند كل تغيير، لا صحّة مشتقّة)
        if (name === 'execution.gate.state') st.setGate(payload as never)
        // التنفيذ (عيلة ٥٥٠) — عرض فقط: حالة موحّدة + سجلّات دوّارة (وقت الوصول محلّي)
        if (name === 'execution.unified.state') st.setExecution(payload as never)
        if (name === 'execution.order.built') st.pushExecOrder({ ...(payload as object), kind: 'built', ts: Date.now() } as never)
        if (name === 'execution.order.skipped') st.pushExecOrder({ ...(payload as object), kind: 'skipped', ts: Date.now() } as never)
        if (name === 'trading.final_decision') st.pushExecOrder({ ...(payload as object), kind: 'final', ts: Date.now() } as never)
        if (name === 'execution.order.rejected') st.pushExecOrder({ ...(payload as object), kind: 'rejected', ts: Date.now() } as never)
        if (name === 'execution.manage.intent') st.pushExecManage({ ...(payload as object), stage: 'intent', ts: Date.now() } as never)
        if (name === 'execution.manage.command') st.pushExecManage({ ...(payload as object), stage: 'command', ts: Date.now() } as never)
        if (name === 'execution.manage.written') st.pushExecManage({ ...(payload as object), stage: 'written', ts: Date.now() } as never)
        if (name === 'market.outcome.realized') st.pushExecOutcome({ ...(payload as object), ts: Date.now() } as never)
        if (IMPORTANT.test(name)) st.pushEvent(name, eventDetail(payload))
      }
      if (fired.length) st.bumpFlows(fired)
    }
    raf = requestAnimationFrame(pump)
  }
  raf = requestAnimationFrame(pump)

  // اتصال واحد ببثّ النواة → كل رسالة تُدفع للطابور فقط (التصريف بالإطار).
  // الطابور محدود: لو التبويب مخفي (rAF متوقّف) وتراكمت الرسائل، نُسقط الأقدم — بلا نموّ ذاكرة (١٤ §٧).
  const CAP = 800
  const stopWs = connectWs(
    (m) => { queue.push(m); if (queue.length > CAP) queue.splice(0, queue.length - CAP) },
    (open) => useStore.getState().setConn(open ? 'live' : 'down'),
  )

  // تحميل أوّلي (لحاق فوري قبل أول بثّ): أسماء عربية + لقطة ذرات مُثراة من الحوكمة
  govGet<Record<number, string>>('/gov/names')
    .then((n) => useStore.getState().setNames(n))
    .catch(() => { /* الخادم لسا مطفي */ })
  govGet<{ connected: boolean; atoms: AtomRec[] }>('/gov/atoms')
    .then((d) => { if (d.connected) useStore.getState().setSnapshot(d.atoms) })
    .catch(() => { /* النواة/الخادم مطفي */ })

  // تحديث دوري للأسماء العربية → أي ذرة جديدة تاخد اسمها العربي بكل الأقسام لحالها
  const namesPoll = window.setInterval(() => {
    govGet<Record<number, string>>('/gov/names')
      .then((n) => useStore.getState().setNames(n))
      .catch(() => { /* الخادم مطفي */ })
  }, 15000)

  // نبض REST دوري للذرات (احتياط قوي): يحدّث العدد/الحالة كل ٤ث حتى لو تعثّر WS أو التبويب غير الشبكة
  const atomsPoll = window.setInterval(() => {
    govGet<{ connected: boolean; atoms: AtomRec[] }>('/gov/atoms')
      .then((d) => { if (d.connected) useStore.getState().setSnapshot(d.atoms) })
      .catch(() => { /* الخادم مطفي */ })
  }, 4000)

  return () => { cancelAnimationFrame(raf); stopWs(); clearInterval(namesPoll); clearInterval(atomsPoll) }
}
