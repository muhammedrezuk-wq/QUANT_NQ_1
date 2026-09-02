// طبقة البيانات (١٤ §٤/§٦): آخر قيمة لكل تدفّق، في الذاكرة فقط.
// الحوكمة مو مصدر الحقيقة — بلا تخزين؛ تموت وتعود تقرأ من جديد (١٤ §١٠).
import { create } from 'zustand'
import type { Color } from './i18n'

export interface AtomRec {
  id: number
  name: string
  name_ar?: string
  label_ar?: string
  color?: Color
  version?: string
  critical?: boolean
  state: string
  health: { state: string; message?: string } | null
  restart_count: number
  last_error: string | null
}

export interface AnalyzerContribution {
  analyzer_id?: string
  signal?: string
  direction?: number
  score?: number
  confidence?: number            // 0..100
  strength?: number
  ratio?: number
  current_depth?: number         // 0..100، عمق الأدلة الحالي
  required_depth?: number        // 0..100، عمق الأدلة المطلوب
  confidence_threshold?: number  // 0..100، عيار الثقة المستقل
  threshold?: number
  weight?: number                // لا يُطبَّق إلا بعد الجاهزية
  weight_applied?: number
  analysis_state?: string
  state?: string
  ready?: boolean
  included?: boolean
  source_timestamp?: number
  timestamp?: number
  sequence?: number
}

// إقفال 150 مرحلة ٢/٣ (أمر المالك 2026-08-23): الروم المتدفّق ولوحة المحلّلين.
// ⛔ الواجهة تنقل ما وصل فقط؛ الغائب مجهول معلن، لا صفر ولا حساب.
export interface RoomSectionRow {
  section_id: string
  state?: string
  direction?: number | null
  strength?: number | null
  confidence?: number | null
  readiness_pct?: number | null
  weight?: number | null
  current_depth?: number | null
  required_depth?: number | null
  timeframe?: string
  period_start?: unknown
  age_s?: number
}

export interface DecisionRoom {
  account_id?: string | null
  broker?: string | null
  symbol: string
  direction?: number | null
  strength?: number | null
  confidence?: number | null
  confidence_defined?: boolean
  readiness_pct?: number | null
  signal?: string
  sections_present?: string[]
  sections_missing?: string[]
  sections?: RoomSectionRow[]
}

export interface AnalystRow {
  id: string
  present: boolean
  mode?: string
  timeframe?: string
  period_start?: unknown
  sequence?: number | null
  direction?: number | null
  strength?: number | null
  confidence?: number | null
  weight?: number | null
  ready?: boolean
  state?: string
  deliveries?: number
  delivered_at?: number | null
  age_s?: number | null
  next_expected_at?: number | null
}

export interface AnalystsPanel {
  account_id?: string | null
  broker?: string | null
  symbol: string
  expected?: number
  present?: number
  missing?: string[]
  analysts?: AnalystRow[]
}

// بند أ١١ — بطاقة مسار (سريع من التكّات / بطيء من الشموع) من ١٦٦ v2.3.1:
// العقد الثماني + unknown_fields. ⛔ القيمة 0.0 مع ورود اسمها في unknown_fields
// = مجهول، وتُعرض «مجهول» لا صفرًا. اللوحة تنقل ما وصل ولا تحسب شيئًا.
export interface PathCard {
  path?: string                  // "fast" | "slow"
  source?: string                // "ticks" | "candles"
  account_id?: string | null
  broker?: string | null
  symbol?: string
  timeframe?: string
  cycle_id?: string
  quality?: string
  warnings?: string[]
  direction?: number             // ±100
  strength?: number
  confidence?: number            // 0..100
  current_depth?: number         // 0..100
  required_depth?: number        // 0..100
  weight?: number                // وزن المسار المعلَن
  ratio?: number
  state?: string                 // READY | ANALYZING | NOT_READY | STALE
  unknown_fields?: string[]
  sequence?: number
  source_timestamp?: number
  timestamp?: number
  active_weight?: number
  available_weight?: number
  missing_weight?: number
  contributors?: Record<string, AnalyzerContribution>
}

// جسم القسم من ١٦٦ (analysis.raw.completed بحقل timeframe="section"):
// الثماني المدموجة (section_contract) + البطاقتان كاملتين أو null للغائب.
export interface SectionFusionState {
  account_id?: string | null
  broker?: string | null
  symbol: string
  status?: string
  signal?: string | null
  ready?: boolean
  warnings?: string[]            // منها "missing_path"
  section_contract?: PathCard
  paths?: { fast?: PathCard | null; slow?: PathCard | null }
  path_weights?: { fast?: number; slow?: number }
  path_missing_weight?: number
  contributors?: Record<string, AnalyzerContribution>
  timestamp?: number
  source_timestamp?: number
  metadata?: { present_paths?: string[]; merge?: string }
}

export interface AnalysisState {
  account_id?: string
  symbol: string
  status: string
  analysis_state?: string
  ready?: boolean
  signal?: string | null
  direction?: number | null
  score?: number | null           // -100..+100
  confidence: number              // 0..100
  strength?: number
  ratio?: number
  current_depth?: number
  quality: string
  agreement: number               // 0..1
  active_weight?: number
  available_weight?: number
  missing_weight?: number
  timestamp?: number
  source_timestamp?: number
  warnings?: string[]
  contributors?: Record<string, AnalyzerContribution>
  metadata?: { expected?: number; present?: number; complete?: boolean; valid?: number; weight_normalization?: string }
}

export interface StructureState {
  symbol: string
  status: string
  signal: string
  score: number
  confidence: number
  quality: string
  structure: {
    trend: string; phase: string; swing: string; swing_price: number | null
    external_high: number | null; external_low: number | null; internal: string
    last_shift: { type: string | null; direction: string | null }
  }
  metadata?: { timeframe?: string; present?: number; expected?: number }
}

export interface LiquidityState {
  symbol: string
  status: string
  signal: string
  confidence: number
  quality: string
  liquidity: {
    pool: string
    buyside_level: number | null; sellside_level: number | null
    sweep: { signal: string; direction: string | null; price: number | null }
    fvg: { signal: string; gap_top: number | null; gap_bottom: number | null }
  }
  metadata?: { timeframe?: string; present?: number; expected?: number }
}

export interface StatsUnit { id?: string; signal?: string; status?: string; metadata?: { value?: number; count?: number } }
export interface StatsState {
  symbol: string
  timeframe?: string
  results: Record<string, StatsUnit>
}

export interface ProbUnit { signal?: string; status?: string; quality?: string; confidence?: number; metadata?: { probability?: number; value?: number; direction?: string; band?: string } }
export interface ProbabilityState { symbol: string; results: Record<string, ProbUnit> }

export interface StratUnit { signal?: string; status?: string; score?: number; confidence?: number }
export interface StrategyState { symbol: string; results: Record<string, StratUnit> }

export interface DecisionUnit { signal?: string; score?: number; confidence?: number; metadata?: { approved?: boolean } }
export interface DecisionState { symbol: string; results: Record<string, DecisionUnit> }
export interface RiskState { daily_loss_pct?: number; consecutive_losses?: number; daily_trade_count?: number; kill_switch_state?: boolean; kill_switch_reason?: string }

// بوّابة التنفيذ (٥٥٢ — execution.gate.state): حالة حقيقيّة تُنشَر عند كل تغيير،
// لا تُشتقّ من رسالة صحّة الذرّة (كانت `health.message` تبدأ بـ`LIVE` افتراضًا
// لم يعد كود ٥٥٢ الحالي ينتجه إطلاقًا — راجع سياق/٩٠).
export interface GateState {
  enabled?: boolean
  halted?: boolean
  halted_accounts?: Record<string, string>
  status?: 'LIVE' | 'STOPPED' | 'HALTED' | 'PARTIAL_HALT' | string
  seen?: number
}

// التنفيذ (عيلة ٥٥٠) — عرض فقط: الحالة الموحّدة من ٥٥٠ + سجلّات دوّارة للأوامر/الإدارة/النتائج
export interface ExecutionState {
  counts?: Record<string, number>
  halted?: boolean
  last_order?: { stage?: string; symbol?: string; side?: string; volume?: number | null; stop_loss?: number | null; take_profit?: number | null } | null
  last_outcome?: { symbol?: string; profit?: number | null } | null
  reject_reasons?: Record<string, number>  // من ٥٥٢ execution.order.rejected — لماذا رُفض كل أمر
  skip_reasons?: Record<string, number>    // من ٥٥١ execution.order.skipped — لماذا ما بُني الأمر أصلًا
}
export interface ExecOrderRec {
  kind: 'built' | 'final' | 'rejected' | 'skipped'
  symbol?: string; side?: string; volume?: number
  reference_price?: number; stop_loss?: number; take_profit?: number
  reward_risk?: number; gate?: string; reason?: string
  ts: number
}
export interface ExecManageRec {
  stage: 'intent' | 'command' | 'written'
  action?: string; ticket?: number | string; symbol?: string; side?: string
  stop_loss?: number; volume?: number; reason?: string; r_multiple?: number
  ts: number
}
export interface ExecOutcomeRec {
  symbol?: string; side?: string; profit?: number; volume?: number
  entry_price?: number; exit_price?: number; event_type?: string; reason?: string
  ticket?: number | string
  ts: number
}

export type Conn = 'connecting' | 'live' | 'down'

interface StoreState {
  atoms: Record<number, AtomRec>
  metrics: unknown
  streams: Record<string, unknown> // آخر قيمة لكل حدث أعمال (باسمه الكامل)
  // حزمة ج (ختم 22): آخر حمولة لكل (اسم حدث × رمز) — لأحداث سلسلة القرار/التنفيذ/تفعيل
  // الأصل التي `streams` لا تكفيها لأنها عامّة (رمز واحد يدهس رمزًا آخر). المفتاح
  // الخارجي اسم الحدث الكامل، الداخلي الرمز — بلا حساب، نقل ما وصل فقط.
  symbolStreams: Record<string, Record<string, unknown>>
  // عدّاد «رفض بلا أصل» (perpetual.entry.rejected بسبب NO_PARENT_AUTHORITY) لكل رمز — ج٥
  assetRejectCounts: Record<string, number>
  market: Record<string, { bid: number; ask: number; ts: number }> // أسعار حسب الرمز
  // ٢٠٢٦-٠٨-٣١ (ختم NQ): سعر الوسيط (ميتاتريدر) منفصل تمامًا عن سعر المرجع.
  // `market` أعلاه هو المرجع (سي‑تريدر) الذي يقود التحليل؛ وهذا سعر التنفيذ
  // الفعليّ بسبريده — للعرض على الشارت واللوحة فقط، لا يدخل حكمًا ولا تحليلًا.
  brokerMarket: Record<string, { bid: number; ask: number; spread: number; provider: string; ts: number }>
  // §١٨ — بطاقة القسم كما بناها section_contract؛ المفتاح حساب::وسيط::أصل::قسم
  sectionCards: Record<string, Record<string, unknown>>
  // بند أ١١ — جسم قسم التحليل المدموج من ١٦٦ (timeframe="section")؛ المفتاح حساب::رمز
  sectionFusion: Record<string, SectionFusionState>
  room: Record<string, DecisionRoom>
  analystsPanels: Record<string, AnalystsPanel>
  analysis: Record<string, AnalysisState> // آخر تحليل مُجمَّع حسب الرمز (من ١٦٦ دمج التحليل)
  structure: Record<string, StructureState> // آخر بنية حسب الرمز (من ٢١٠ ناشر البنية)
  liquidity: Record<string, LiquidityState> // آخر سيولة حسب الرمز (من ٢٦٠ ناشر السيولة)
  stats: Record<string, StatsState> // آخر إحصاء مُجمَّع حسب الرمز (من ٣٠٠ مدير الإحصاء)
  probability: Record<string, ProbabilityState> // آخر احتمالات حسب الرمز (من ٣٥٠ مدير الاحتمالات)
  strategy: Record<string, StrategyState> // آخر استراتيجيات حسب الرمز (من ٤٠٠ مدير الاستراتيجيات)
  decision: Record<string, DecisionState> // آخر قرار حسب الرمز (من ٤٥٠ مدير القرار)
  risk: RiskState | null // حالة المخاطر الموحّدة العالمية (من ٥٠٠ مدير المخاطر)
  gate: GateState | null // حالة بوّابة التنفيذ الحقيقيّة (من ٥٥٢ — execution.gate.state)
  execution: ExecutionState | null // حالة التنفيذ الموحّدة (من ٥٥٠ مدير التنفيذ — execution.unified.state)
  execOrders: ExecOrderRec[]   // سجلّ دوّار: أوامر (انبنى/مقفول/نهائي/مرفوض) — أحدث أولًا
  execManage: ExecManageRec[]  // سجلّ دوّار: الإدارة الذكية (نيّة/أمر/انكتب للجسر)
  execOutcomes: ExecOutcomeRec[] // سجلّ دوّار: نتائج محقَّقة (من ٥٦٣ تأكيد التنفيذ)
  // أحداث مهمّة (سجل دوّار للمراقبة). `detail` من حمولة الحدث (رمز · جهة · سبب)،
  // و`n` يدمج التكرار المتتالي المتطابق بعدّاد بدل رشق أسطر متطابقة (بند 17 بدفتر 97).
  events: Array<{ id: number; name: string; detail?: string; ts: number; n: number }>
  flows: Record<string, number>    // آخر وقت (performance.now) انطلق فيه كل حدث — لتدفّق الشبكة
  flowStats: Record<string, { n: number; first: number; last: number }> // نبض كل تيار — لإنذار الصمت
  conn: Conn
  lastMsgAt: number                // performance.now() لآخر رسالة (للطزاجة)
  namesAr: Record<number, string>
  selectedId: number | null        // الذرة المفتوحة تفاصيلها (من الشبكة أو القائمة)
  setSnapshot: (atoms: AtomRec[], metrics?: unknown) => void
  resetLive: () => void
  setEvent: (name: string, payload: unknown) => void
  setSymbolStream: (event: string, symbol: string, payload: unknown) => void
  bumpAssetReject: (symbol: string) => void
  setConn: (c: Conn) => void
  setNames: (n: Record<number, string>) => void
  markMsg: () => void
  select: (id: number | null) => void
  setMarketTick: (symbol: string, bid: number, ask: number, ts: number) => void
  setBrokerTick: (symbol: string, bid: number, ask: number, spread: number, provider: string, ts: number) => void
  setSectionCard: (key: string, card: Record<string, unknown>) => void
  setSectionFusion: (key: string, body: SectionFusionState) => void
  setRoom: (key: string, room: DecisionRoom) => void
  setAnalysts: (key: string, panel: AnalystsPanel) => void
  setAnalysis: (symbol: string, s: AnalysisState) => void
  setStructure: (symbol: string, s: StructureState) => void
  setLiquidity: (symbol: string, s: LiquidityState) => void
  setStats: (symbol: string, s: StatsState) => void
  setProbability: (symbol: string, s: ProbabilityState) => void
  setStrategy: (symbol: string, s: StrategyState) => void
  setDecision: (symbol: string, s: DecisionState) => void
  setRisk: (s: RiskState) => void
  setGate: (s: GateState) => void
  setExecution: (s: ExecutionState) => void
  pushExecOrder: (r: ExecOrderRec) => void
  pushExecManage: (r: ExecManageRec) => void
  pushExecOutcome: (r: ExecOutcomeRec) => void
  pushEvent: (name: string, detail?: string) => void
  bumpFlows: (names: string[]) => void
}

export const useStore = create<StoreState>((set) => ({
  atoms: {},
  metrics: null,
  streams: {},
  symbolStreams: {},
  assetRejectCounts: {},
  market: {},
  brokerMarket: {},
  sectionCards: {},
  sectionFusion: {},
  room: {},
  analystsPanels: {},
  analysis: {},
  structure: {},
  liquidity: {},
  stats: {},
  probability: {},
  strategy: {},
  decision: {},
  risk: null,
  gate: null,
  execution: null,
  execOrders: [],
  execManage: [],
  execOutcomes: [],
  events: [],
  flows: {},
  flowStats: {},
  conn: 'connecting',
  lastMsgAt: 0,
  namesAr: {},
  selectedId: null,
  setSnapshot: (atoms, metrics) =>
    set((s) => {
      const map: Record<number, AtomRec> = {}
      for (const a of atoms) map[a.id] = a
      return { atoms: map, metrics: metrics ?? s.metrics }
    }),
  resetLive: () => set({
    atoms: {}, metrics: null, streams: {}, symbolStreams: {}, assetRejectCounts: {},
    market: {}, brokerMarket: {}, sectionCards: {}, sectionFusion: {}, room: {}, analystsPanels: {},
    analysis: {}, structure: {}, liquidity: {}, stats: {}, probability: {},
    strategy: {}, decision: {}, risk: null, gate: null, execution: null,
    execOrders: [], execManage: [], execOutcomes: [], events: [], flows: {},
    flowStats: {}, conn: 'connecting', lastMsgAt: 0,
  }),
  setEvent: (name, payload) => set((s) => ({ streams: { ...s.streams, [name]: payload } })),

  setSymbolStream: (event, symbol, payload) => set((s) => ({
    symbolStreams: { ...s.symbolStreams, [event]: { ...(s.symbolStreams[event] ?? {}), [symbol]: payload } },
  })),
  bumpAssetReject: (symbol) => set((s) => ({
    assetRejectCounts: { ...s.assetRejectCounts, [symbol]: (s.assetRejectCounts[symbol] ?? 0) + 1 },
  })),
  setConn: (conn) => set({ conn }),
  setNames: (namesAr) => set({ namesAr }),
  markMsg: () => set({ lastMsgAt: performance.now() }),
  select: (selectedId) => set({ selectedId }),
  setMarketTick: (symbol, bid, ask, ts) => set((s) => ({ market: { ...s.market, [symbol]: { bid, ask, ts } } })),
  setBrokerTick: (symbol, bid, ask, spread, provider, ts) =>
    set((s) => ({ brokerMarket: { ...s.brokerMarket, [symbol]: { bid, ask, spread, provider, ts } } })),
  setSectionCard: (key, card) => set((s) => ({ sectionCards: { ...s.sectionCards, [key]: card } })),
  setSectionFusion: (key, body) => set((s) => ({ sectionFusion: { ...s.sectionFusion, [key]: body } })),
  setRoom: (key, room) => set((s) => ({ room: { ...s.room, [key]: room } })),
  setAnalysts: (key, panel) => set((s) => ({ analystsPanels: { ...s.analystsPanels, [key]: panel } })),
  setAnalysis: (symbol, a) => set((s) => ({ analysis: { ...s.analysis, [symbol]: a } })),
  setStructure: (symbol, a) => set((s) => ({ structure: { ...s.structure, [symbol]: a } })),
  setLiquidity: (symbol, a) => set((s) => ({ liquidity: { ...s.liquidity, [symbol]: a } })),
  setStats: (symbol, a) => set((s) => ({ stats: { ...s.stats, [symbol]: a } })),
  setProbability: (symbol, a) => set((s) => ({ probability: { ...s.probability, [symbol]: a } })),
  setStrategy: (symbol, a) => set((s) => ({ strategy: { ...s.strategy, [symbol]: a } })),
  setDecision: (symbol, a) => set((s) => ({ decision: { ...s.decision, [symbol]: a } })),
  setRisk: (a) => set({ risk: a }),
  setGate: (a) => set({ gate: a }),
  setExecution: (a) => set({ execution: a }),
  pushExecOrder: (r) => set((s) => ({ execOrders: [r, ...s.execOrders].slice(0, 40) })),
  pushExecManage: (r) => set((s) => ({ execManage: [r, ...s.execManage].slice(0, 40) })),
  pushExecOutcome: (r) => set((s) => ({ execOutcomes: [r, ...s.execOutcomes].slice(0, 40) })),
  pushEvent: (name, detail) => set((s) => {
    const head = s.events[0]
    // نفس الحدث بنفس التفصيل يتكرّر مباشرة ⇒ عدّاد على السطر نفسه، لا سطر جديد
    if (head && head.name === name && (head.detail ?? '') === (detail ?? '')) {
      return { events: [{ ...head, ts: Date.now(), n: head.n + 1 }, ...s.events.slice(1)] }
    }
    const e = { id: (head?.id ?? 0) + 1, name, detail, ts: Date.now(), n: 1 }
    const arr = [e, ...s.events]
    return { events: arr.length > 60 ? arr.slice(0, 60) : arr }
  }),
  bumpFlows: (names) => set((s) => {
    if (!names.length) return {} as Partial<StoreState>
    const t = performance.now()
    const f = { ...s.flows }
    const fs = { ...s.flowStats }
    for (const n of names) {
      f[n] = t
      const cur = fs[n]
      fs[n] = cur ? { n: cur.n + 1, first: cur.first, last: t } : { n: 1, first: t, last: t }
    }
    return { flows: f, flowStats: fs }
  }),
}))
