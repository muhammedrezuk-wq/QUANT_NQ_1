// نظام الـWidgets (١٤ §٥) — العقد والسجلّ.
// التطبيق الفعلي: كل Widget = مكوّن React لقسمه، يقرأ شريحته من المتجر (اشتراك دقيق).
//   • Mount/Dispose  = تركيب/إزالة المكوّن.
//   • Activate/Deactivate = القسم الفعّال وحده مركَّب؛ المخفيّ غير مركَّب (١٤ §٧):
//     الاشتراك بالبيانات في المتجر (اتصال واحد بالبثّ) يبقى حيًّا، فحالة كل الأقسام
//     تبقى طازجة؛ والعودة لقسم تقرأ حالته الحاليّة **فورًا** من المتجر — بلا إعادة جلب.
//   • كلفة رسم القسم المخفيّ = صفر (غير مركَّب)، والبيانات لا تضيع.

export interface WidgetContract {
  id: string
  section: string
  /** التدفّقات التي يقرأها (بادئات أسماء أحداث / مفاتيح المتجر) — للتوثيق والتتبّع. */
  streams: string[]
  /** عنوان عربي (للعرض). */
  title: string
}

const registry = new Map<string, WidgetContract[]>()

export function registerWidget(c: WidgetContract): void {
  const arr = registry.get(c.section) ?? []
  arr.push(c)
  registry.set(c.section, arr)
}

export function widgetsOf(section: string): WidgetContract[] {
  return registry.get(section) ?? []
}
