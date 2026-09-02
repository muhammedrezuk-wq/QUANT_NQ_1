// تعريب رسائل صحّة الذرّات — حكم المالك ٢٠٢٦-٠٨-١٦: «ما بدّي أشوف ولا شي أجنبيّ».
//
// الذرّات تكتب رسائلها بالإنجليزيّة لأنّ دستور الذرّة يمنع العربيّة داخل الكود
// (القاعدة م٣). فالترجمة مكانها هنا — في اللوحة — لا في الذرّة.
//
// قاعدة العرض: ما نعرفه يُترجم، والرمز التقني المجهول يُستبدل بوصف عربي
// واضح بدل تسريب الإنجليزية. الأرقام تبقى أرقامًا؛ نترجم المعنى لا القيمة.

const REASONS: Record<string, string> = {
  NOT_STARTED: 'لم تُشغَّل',
  NO_INPUT_YET: 'لم يصلها مدخل بعد',
  NO_TRADES_YET: 'لا صفقات بعد',
  NO_DATA_YET: 'لا بيانات بعد',
  NO_SCORE_YET: 'لا درجة بعد',
  NO_PLAN_YET: 'لا خطّة بعد',
  NO_CANDLES_YET: 'لا شموع بعد',
  NO_LEDGER_YET: 'لا دفتر بعد',
  NO_TARGET_OR_PAIR_YET: 'لا هدف ولا زوج بعد',
  NO_REFERENCE_CAPITAL: 'لا رأس مال مرجعيّ',
  NO_READ_YET: 'لم تُقرأ بعد',
  DISABLED: 'موقوف',
  KILL_SWITCH_ACTIVE: 'قاطع الأمان مفتوح',
  MISSING_ACCOUNT_ID: 'رقم الحساب مفقود',
  NO_REQUESTED_PRICE: 'لا سعر مطلوب — القياس ممنوع',
  ACCOUNT_STATE_STALE: 'بيانات الحساب متقادمة',
  SPREAD_TOO_WIDE: 'الفرق السعريّ واسع',
  PREVIEW_ONLY: 'مقفول',
  LIVE: 'مفتوحة',
  STOPPED: 'متوقّفة',
  HALTED: 'موقوفة بالطوارئ',
  READY: 'جاهزة',
  ACTIVE: 'نشطة',
  UNAVAILABLE: 'غير متاح من الوسيط',
  BRIDGE_UNREADABLE: 'الجسر غير مقروء',
  RESTORE_FAILED_FAIL_CLOSED: 'فشل الاسترجاع — أُقفلت احتياطًا',
  RETRIES_EXHAUSTED: 'نفدت المحاولات',
  ledger_active: 'الدفتر نشط',
  stop_targets_ready: 'أهداف الوقف جاهزة',
  account_state: 'حالة الحساب',
  bridge_writable: 'الجسر قابل للكتابة',
  // رموز «جاهزة بلا عمل بعد» — حملة تنظيف مادة اللغة 2026-08-20:
  // الذرّات صارت تتكلم رموزًا إنكليزية (دستور الذرة م٣) والترجمة هنا.
  READY_AWAITING_FIRST_CLOSED_TRADE_OUTCOME: 'جاهزة — بانتظار أول نتيجة صفقة مغلقة',
  READY_AWAITING_FIRST_LEARNING_SAMPLE: 'جاهزة — بانتظار أول عيّنة تعلّم',
  READY_AWAITING_FIRST_SAMPLE_FOR_FEATURES: 'جاهزة — بانتظار أول عيّنة للميّزات',
  READY_AWAITING_ENOUGH_TRAINING_SAMPLES: 'جاهزة — بانتظار عيّنات كافية للتدريب',
  READY_AWAITING_FIRST_CANDIDATE_MODEL: 'جاهزة — بانتظار أول نموذج مرشّح',
  READY_AWAITING_FIRST_VALIDATED_MODEL: 'جاهزة — بانتظار أول نموذج متحقَّق',
  READY_AWAITING_FIRST_APPROVED_MODEL: 'جاهزة — بانتظار أول نموذج معتمَد',
  SUSPECTED_FAULT_MODEL_SELECTED_BUT_NOT_ACTIVE: 'شكّ عطل: نموذج مُختار بلا تفعيل',
  READY_AWAITING_FIRST_APPROVED_MODEL_TO_REGISTER: 'جاهزة — بانتظار أول نموذج معتمَد للتسجيل',
  READY_AWAITING_FIRST_OUTCOME_FOR_DRIFT: 'جاهزة — بانتظار أول نتيجة لقياس الانجراف',
  READY_AWAITING_FIRST_ORDER_OR_POSITION: 'جاهزة — بانتظار أول أمر أو مركز',
  READY_AWAITING_FIRST_ASSET_LEDGER: 'جاهزة — بانتظار أول دفتر أصول',
  READY_AWAITING_FIRST_FINANCIAL_CANDLE: 'جاهزة — بانتظار أول شمعة بهويّة مالية',
  READY_AWAITING_FIRST_TRADE_OUTCOME: 'جاهزة — بانتظار أول نتيجة صفقة',
  READY_LEDGER_EMPTY_AWAITING_FIRST_POSITION_OR_TRADE: 'جاهزة — الدفتر فاضٍ، بانتظار أول مركز أو صفقة',
  READY_AWAITING_FIRST_FINAL_DECISION_OR_ORDER: 'جاهزة — بانتظار أول قرار نهائي أو أمر',
  READY_AWAITING_FIRST_RISK_VALIDATION: 'جاهزة — بانتظار أول تحقّق مخاطر',
  READY_AWAITING_FIRST_LEGAL_ORDER: 'جاهزة — بانتظار أول أمر شرعي',
  READY_AWAITING_FIRST_MEASURED_TRADE: 'جاهزة — بانتظار أول صفقة تُقاس جودتها',
  READY_AWAITING_FIRST_PLATFORM_TRADE_EVENT: 'جاهزة — بانتظار أول حدث صفقة من المنصّة',
  READY_AWAITING_FIRST_MANAGE_INTENT: 'جاهزة — بانتظار أول نيّة إدارة',
  READY_AWAITING_FIRST_TARGET_OR_PAIR: 'جاهزة — بانتظار أول هدف أو زوج',
  READY_AWAITING_FIRST_TARGET: 'جاهزة — بانتظار أول هدف',
  READY_AWAITING_FIRST_ORDER_STOP_CHECK: 'جاهزة — بانتظار أول فحص وقف لأمر',
  READY_AWAITING_FIRST_ORDER_MARGIN_CHECK: 'جاهزة — بانتظار أول فحص هامش لأمر',
  READY_AWAITING_FIRST_ORDER_SYMBOL_RESOLVE: 'جاهزة — بانتظار أول ترجمة رمز لأمر',
  READY_AWAITING_FIRST_MT5_TRADE_EVENT: 'جاهزة — بانتظار أول حدث صفقة من الوسيط',
  READY_AWAITING_FIRST_CTRADER_REFERENCE_TICK: 'جاهزة — بانتظار أول تكّة مرجعية من سي-تريدر',
  READY_AWAITING_FIRST_OPEN_POSITION: 'جاهزة — بانتظار أول مركز مفتوح',
  READY_AWAITING_FIRST_TRADE_STORE: 'جاهزة — بانتظار أول تخزين صفقة',
  READY_AWAITING_FIRST_ORDER_STORE: 'جاهزة — بانتظار أول تخزين أمر',
  READY_AWAITING_FIRST_MODEL_SAVE: 'جاهزة — بانتظار أول حفظ نموذج',
  READY_AWAITING_FIRST_ROTATED_ARCHIVE_714: 'جاهزة — بانتظار أول أرشيف مدوَّر من 714',
  READY_AWAITING_FIRST_ARCHIVE_EVENT: 'جاهزة — بانتظار أول حدث أرشفة',
  READY_AWAITING_FIRST_CLEANUP_EVENT: 'جاهزة — بانتظار أول حدث تنظيف',
  READY_ATTACHED_AWAITING_FIRST_ERROR: 'جاهزة ومتّصلة — بانتظار أول خطأ',
  READY_AWAITING_FIRST_REAL_TRADE_OR_ORDER_EVENT: 'جاهزة — بانتظار أول حدث صفقة أو أمر حقيقي',
  READY_AWAITING_FIRST_BACKUP_RUN: 'جاهزة — بانتظار أول جولة نسخ',
  READY_AWAITING_FIRST_DASHBOARD_COMMAND: 'جاهزة — بانتظار أول أمر من اللوحة',
  AWAITING_FIRST_PULSE: 'بانتظار أول نبضة',
  SUSPECTED_FAULT: 'شكّ عطل',
  ERROR_LOG_DAILY_CAP_REACHED: 'بلغ سقف سجل الأخطاء اليومي',
  TRADE_LOG_DAILY_CAP_REACHED: 'بلغ سقف سجل الصفقات اليومي',
  // بند أ١١ — حالات بطاقات المسارين/القسم (166) وتحذيراتها المقيسة من الكود:
  ANALYZING: 'قيد التحليل',
  NOT_READY: 'غير جاهز',
  STALE: 'متقادم',
  missing_path: 'مسار غائب',
  missing_weight: 'وزن غائب',
  no_valid_analysis: 'لا تحليل صالحًا',
}

const FIELDS: Record<string, string> = {
  sent: 'أُرسل', rejected: 'مرفوض', seen: 'وصلها', built: 'بُني',
  legal: 'قانونيّ', approved: 'موافَق', released: 'أُفرج', validations: 'فحوص',
  rejections: 'رفوض', resolved: 'مُترجَم', blocked: 'محجوب', allowed: 'مسموح',
  written: 'كُتب', skipped: 'تُخطّي', failed: 'فشل', dropped: 'أُسقط',
  emitted: 'صدر', scored: 'حُسب', cycles: 'دورات', symbols: 'رموز',
  evidence_sources: 'مصادر الأدلّة', complete: 'مكتملة', deadline: 'بالمهلة',
  stale: 'متأخّر', targets: 'أهداف', snapshots: 'لقطات', published: 'نُشر',
  active: 'نشط', pending: 'معلّق', opened: 'فُتح', dials: 'أقراص',
  emits: 'إصدارات', plans: 'خطط', tilts: 'ترجيحات', trails: 'تتبّعات',
  breakevens: 'تعادلات', partials: 'إغلاقات جزئيّة', tracked: 'مُتابَع',
  pairs: 'أزواج', delta_sent: 'فروق أُرسلت', delta_failed: 'فروق فشلت',
  confirmed: 'مؤكَّد', realized: 'محقَّق', outcomes: 'نتائج',
  passed: 'مرّ', finalized: 'قرارات نهائية', bridge: 'الجسر', writable: 'قابل للكتابة',
  reported: 'مُبلَّغ', components: 'مكوّنات', accounts: 'حسابات',
  open: 'مفتوح', floating: 'عائم', slip: 'انزلاق', buy: 'شراء',
  sell: 'بيع', neutral: 'محايد', wait: 'انتظار', conflicts: 'تعارضات',
  updates: 'تحديثات', modify_sent: 'تعديلات أُرسلت', specs: 'مواصفات',
  balance_updates: 'تحديثات الرصيد', equity_updates: 'تحديثات الملكيّة',
  pnl_updates: 'تحديثات الربح', margin_updates: 'تحديثات الهامش',
  overview_parts: 'أجزاء النظرة', stored: 'مخزَّن', unmeasured: 'غير مقيس',
  computable: 'قابل للحساب', stops: 'وقوف', halt: 'إيقاف', reset: 'تصفير',
  activate: 'تفعيل', expired: 'منتهٍ', heartbeat: 'نبضة', minute: 'دقيقة',
  max_drift: 'أقصى انحراف', sys_tick: 'نبضة النظام',
  // بند أ١١ — العقد الثماني لبطاقات المسارين/القسم (166) وأسماء المسارات:
  direction: 'الاتجاه', strength: 'القوّة', confidence: 'الثقة',
  current_depth: 'العمق الحالي', required_depth: 'العمق المطلوب',
  weight: 'الوزن', ratio: 'النسبة', state: 'الحالة',
  fast: 'المسار السريع', slow: 'المسار البطيء', section: 'القسم',
  fused: 'مدموج',
  // حزمة ج (ختم 22) — الهوية الست وحقول سلسلة القرار (451..467) المشتركة عبر
  // أكثر من صفحة: بطاقة القرار · جدول الحواجز · بطاقات الأهلية · رحلة القرار.
  account_id: 'الحساب', broker: 'الوسيط', symbol: 'الرمز',
  timeframe: 'الإطار الزمني', period_start: 'بداية الدورة',
  decision_id: 'معرّف القرار', gate_request_id: 'معرّف طلب البوابة',
  request_id: 'معرّف الطلب', cycle_id: 'معرّف الدورة',
  reason: 'السبب', value: 'القيمة', threshold: 'العتبة',
  measured_at: 'وقت القياس', name: 'الاسم', checks: 'الفحوص',
  origin: 'المصدر', resolution: 'الحسم', stage: 'المرحلة',
  gated_at: 'وقت البوابة', side: 'الجانب', decision_side: 'الجانب',
  direction_buy: 'اتجاه الشراء', direction_sell: 'اتجاه البيع',
  target: 'الهدف', position: 'المركز', budget: 'الميزانية', action: 'الإجراء',
}

// ——— محرّك الترجيح (580) — ث٣ (ق١٠ §١٨–٢١): القاموس المركزي للحدثين والحقول ———
// الحقول الستّة القابلة للترجيح بمفردات ق١٠ §٣ نفسها. state وweight ليسا هنا
// عمدًا: الحالة حاجز والوزن عامل مساهمة — لا سلّما نقاط، فلا منحنى لهما.
export const TILT_FIELD_AR: Record<string, string> = {
  direction: 'القيمة الاتجاهية',
  strength: 'القوة',
  confidence: 'الثقة',
  current_depth: 'العمق الحالي',
  required_depth: 'العمق المطلوب',
  ratio: 'النسبة',
}
export const TILT_SIDE_AR: Record<string, string> = {
  up: 'صعودًا',
  down: 'هبوطًا',
  abs: 'بالقيمة المطلقة',
}
// أسماء أحداث الترجيح بالعربي (تقرأها الشاشات من قاموس التيارات في streams.ts أيضًا)
export const TILT_EVENT_AR: Record<string, string> = {
  'tilt.rule.command': 'أمر قاعدة ترجيح',
  'tilt.rules.state': 'قواعد محرّك الترجيح',
  'tilt.state': 'حالة محرّك الترجيح',
}

export function arabicVisible(value?: unknown, fallback = 'تفصيل تقني غير مترجم'): string {
  const text = String(value ?? '').trim()
  if (!text) return '—'
  return /[A-Za-z]/.test(text) ? fallback : text
}

/** اسم حقل من قاموس الحقول المركزي (العقد الثماني · المسارات) — لا يسرّب إنكليزيًّا. */
export const fieldAr = (key: string): string => FIELDS[key] ?? arabicVisible(key, 'حقل تقني')

/** يترجم رسالة صحّة ذرّة إلى العربيّة، ولا يسرّب رمزًا إنجليزيًا مجهولًا للواجهة. */
export function arabicHealth(message?: string | null): string {
  if (!message) return '—'
  const direct = REASONS[message.trim()]
  if (direct) return direct
  return message
    .split(/\s+/)
    .map((raw) => {
      // فاصلة/فاصلة منقوطة/نقطتان لاصقة بآخر الكلمة تمنع مطابقة القاموس — تُقصّ وتُعاد
      const tail = /[،,؛;:]+$/.exec(raw)?.[0] ?? ''
      const token = tail ? raw.slice(0, raw.length - tail.length) : raw
      const put = (t: string) => t + tail
      if (REASONS[token]) return put(REASONS[token])
      const pair = token.match(/^([A-Za-z_]+)=(.*)$/)
      if (pair) {
        const label = FIELDS[pair[1]] ?? 'حقل تقني'
        const value = REASONS[pair[2]] ?? arabicVisible(pair[2], 'قيمة تقنية')
        return put(`${label}=${value}`)
      }
      // كلمة مفردة معروفة، أو سلسلة تحمل سببًا داخلها.
      for (const key of Object.keys(REASONS)) {
        if (token.startsWith(key + '_')) {
          const rest = token.slice(key.length + 1)
          return put(`${REASONS[key]} · ${REASONS[rest] ?? arabicVisible(rest, 'تفصيل تقني')}`)
        }
      }
      return put(FIELDS[token] ?? arabicVisible(token))
    })
    .join(' ')
}

/** حالة الذرّة نفسها (تعمل / متوقّفة). */
export function arabicState(state?: string | null): string {
  if (state === 'running') return 'تعمل'
  if (state === 'stopped') return 'متوقّفة'
  if (state === 'failed') return 'فاشلة'
  return arabicVisible(state, 'حالة غير معروفة')
}
