// ترجمة أسماء المحلّلات (مفتاح المساهِم بحدث analysis.raw.completed اسم إنجليزي، لا رقم ذرة).
// تُطابق ذرات التحليل ١٥١..١٦٥. أي مفتاح جديد يُنظَّف تلقائيًّا (بلا شرطات سفلية).
export const ANALYZER_AR: Record<string, string> = {
  trend: 'الاتجاه',            // 151
  momentum: 'الزخم',           // 152
  volatility: 'التذبذب',       // 153
  volume: 'الحجم',             // 154
  spread: 'السبريد',           // 155
  candle: 'الشموع',            // 156
  gap: 'الفجوات',              // 157
  session: 'الجلسات',          // 158
  time: 'أثر الوقت',           // 159
  correlation: 'الارتباط',     // 160
  relative_strength: 'القوة النسبية', // 161
  velocity: 'السرعة',          // 162
  acceleration: 'التسارع',     // 163
  volume_quality: 'جودة الحجم', // 164
  noise: 'الضوضاء',            // 165
  fusion: 'الدمج',             // 166
}

export const analyzerLabel = (key: string): string =>
  ANALYZER_AR[key] ?? key.replace(/_/g, ' ')
