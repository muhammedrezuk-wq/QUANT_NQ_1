// تخصيص الشكل (بند ١٥ بورقة ٩٩ — «لازم يكون هون أزرار مو كل ما بدي شي أقلك اعمله»):
// (أ) لوح ألوان حرّ يضبط متغيّرات CSS الرئيسية فورًا، (ب) تكبير عام،
// (ج) ترتيب التبويبات — كلّه محفوظ محليًّا (localStorage) بلا أي مكتبة خارجية،
// ويُطبَّق لحظة الإقلاع قبل الرسم. «رجوع للافتراضي» يمسح التخصيص فيرجع حكم الإطلالة.
// ⛔ لا يلمس صفحة «الشبكة» WebGL: قاعدة التكبير في styles.css تستثني .network صراحة.

const KEY_COLORS = 'nq_appearance_colors_v1'
const KEY_ZOOM = 'nq_appearance_zoom_v1'
const KEY_TABS = 'nq_tab_order_v1'
export const TAB_ORDER_EVENT = 'nq-tab-order-changed'

// المتغيّرات المعروضة للمالك — كل واحد متغيّر CSS حقيقي مستعمل باللوحة فعلًا.
// «glass» خاص: لون واحد يشتقّ منه زجاج البطاقات وحدودها وهوفرها بنفس شفافياتها القائمة.
export interface ColorVarDef { id: string; label: string; hint?: string }
export const COLOR_VARS: ColorVarDef[] = [
  { id: '--bg1', label: 'الخلفية (أعلى)', hint: 'أعلى تدرّج خلفية الشاشة' },
  { id: '--bg2', label: 'الخلفية (أسفل)', hint: 'أسفل التدرّج' },
  { id: 'glass', label: 'لون البطاقات (الزجاج)', hint: 'يشتقّ منه جسم البطاقة وحدودها بنفس الشفافية' },
  { id: '--ink', label: 'النصّ الأساسي' },
  { id: '--dim', label: 'النصّ الخافت' },
  { id: '--accent', label: 'اللون المميّز', hint: 'الأزرار الفعّالة والتبويب المختار' },
  { id: '--green', label: 'لون السليم', hint: 'سليمة · مفتوحة · ربح' },
  { id: '--amber', label: 'لون المتعثّر', hint: 'متعثّرة · تحذير' },
  { id: '--red', label: 'لون الخلل', hint: 'خلل · رفض · خسارة' },
]

export type ColorsMap = Record<string, string> // id ← hex مثل #0d1119

const hexToRgb = (hex: string): [number, number, number] | null => {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim())
  if (!m) return null
  const n = parseInt(m[1], 16)
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255]
}

function readJson<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : null
  } catch { return null }
}

export const loadColors = (): ColorsMap => readJson<ColorsMap>(KEY_COLORS) ?? {}

export function applyColors(colors: ColorsMap): void {
  const root = document.documentElement
  // نظّف أولًا كل ما نملكه، ثم طبّق المحفوظ — حتى يشتغل «رجوع للافتراضي» بلا إعادة تحميل
  for (const def of COLOR_VARS) {
    if (def.id === 'glass') {
      root.style.removeProperty('--glass'); root.style.removeProperty('--glassb'); root.style.removeProperty('--glassh')
    } else root.style.removeProperty(def.id)
  }
  for (const [id, hex] of Object.entries(colors)) {
    if (id === 'glass') {
      const rgb = hexToRgb(hex)
      if (!rgb) continue
      const [r, g, b] = rgb
      // نفس شفافيات الثيم التقني القائمة (styles.css) — يتبدّل اللون لا البنية
      root.style.setProperty('--glass', `rgba(${r},${g},${b},.08)`)
      root.style.setProperty('--glassb', `rgba(${r},${g},${b},.22)`)
      root.style.setProperty('--glassh', `rgba(${r},${g},${b},.13)`)
    } else if (COLOR_VARS.some((d) => d.id === id) && hexToRgb(hex)) {
      root.style.setProperty(id, hex)
    }
  }
}

export function saveColor(id: string, hex: string): ColorsMap {
  const next = { ...loadColors(), [id]: hex }
  try { localStorage.setItem(KEY_COLORS, JSON.stringify(next)) } catch { /* تخزين ممتلئ — يبقى التطبيق الحي */ }
  applyColors(next)
  return next
}

export function resetColors(): void {
  try { localStorage.removeItem(KEY_COLORS) } catch { /* لا شيء */ }
  applyColors({})
}

// ── التكبير العام ──
// يضبط حجم الخطّ الجذري (نسبة مئوية) + متغيّر --uizoom الذي تستعمله قاعدة
// zoom في styles.css لكل أقسام اللوحة (عدا «الشبكة» WebGL — مستثناة صراحة).
export const ZOOM_MIN = 80
export const ZOOM_MAX = 160

export const loadZoom = (): number => {
  const n = Number(localStorage.getItem(KEY_ZOOM))
  return Number.isFinite(n) && n >= ZOOM_MIN && n <= ZOOM_MAX ? n : 100
}

export function applyZoom(pct: number): void {
  const clamped = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.round(pct)))
  const root = document.documentElement
  if (clamped === 100) {
    root.style.removeProperty('font-size')
    root.style.removeProperty('--uizoom')
  } else {
    root.style.fontSize = `${clamped}%`
    root.style.setProperty('--uizoom', String(clamped / 100))
  }
}

export function saveZoom(pct: number): void {
  try {
    if (Math.round(pct) === 100) localStorage.removeItem(KEY_ZOOM)
    else localStorage.setItem(KEY_ZOOM, String(Math.round(pct)))
  } catch { /* لا شيء */ }
  applyZoom(pct)
}

// ── ترتيب التبويبات ──
// يُحفظ ترتيب المعرّفات فقط؛ أي تبويب جديد بالكود يظهر بمكانه الافتراضي آخر
// المعروف — فلا يختفي قسم أبدًا بسبب ترتيب قديم محفوظ.
export function getTabOrder(defaultIds: string[]): string[] {
  const saved = readJson<string[]>(KEY_TABS)
  const known = Array.isArray(saved) && saved.length
    ? saved.filter((id) => defaultIds.includes(id))
    : []
  const missing = defaultIds.filter((id) => !known.includes(id))
  const ordered = [...known, ...missing]

  // أقسام التحليل تُعرض بترتيب أرقامها، والاحتمالات 350 قبل الاستراتيجيات 400
  // والقرار 450 — لا نترك ترتيبًا قديمًا يخلطها بين أقسام أخرى.
  const numbered = ['analysis', 'structure', 'liquidity', 'statistics', 'probability', 'strategies', 'decision']
    .filter((id) => ordered.includes(id))
  if (numbered.length > 1) {
    const first = Math.min(...numbered.map((id) => ordered.indexOf(id)))
    for (const id of numbered) {
      const i = ordered.indexOf(id)
      if (i >= 0) ordered.splice(i, 1)
    }
    ordered.splice(first, 0, ...numbered)
  }

  // التبويب الخامس «الذرات» ثابت مكانه — لا يتأثر بترتيب التخصيص المحفوظ.
  const atomsIndex = ordered.indexOf('atoms')
  if (atomsIndex >= 0) {
    ordered.splice(atomsIndex, 1)
    ordered.splice(Math.min(4, ordered.length), 0, 'atoms')
  }

  // طلب المالك: لوحة NQ، وهي آخر لوحة فوركس، تكون أول تبويب دائمًا.
  // لا ينطبق هذا على قائمة الكريبتو المستقلة.
  const nqIndex = ordered.indexOf('nq')
  if (nqIndex >= 0) {
    ordered.splice(nqIndex, 1)
    ordered.unshift('nq')
  }
  return ordered
}

export function saveTabOrder(ids: string[]): void {
  try { localStorage.setItem(KEY_TABS, JSON.stringify(ids)) } catch { /* لا شيء */ }
  window.dispatchEvent(new Event(TAB_ORDER_EVENT))
}

export function resetTabOrder(): void {
  try { localStorage.removeItem(KEY_TABS) } catch { /* لا شيء */ }
  window.dispatchEvent(new Event(TAB_ORDER_EVENT))
}

/** يُنادى مرّة عند الإقلاع (main.tsx) — يطبّق المحفوظ قبل أوّل رسم. */
export function initAppearance(): void {
  applyColors(loadColors())
  applyZoom(loadZoom())
}

// قسم أسمر (كريبتو) — «نفس اللوحة بلمسة تميّز» (أمر المالك 2026-08-28):
// يُطبَّق تلقائيًا في وضع الكريبتو ما لم يكن للمالك تخصيص ألوان محفوظ.
//
// ٢٠٢٦-٠٩-٠١ (أمر المالك: «اعمله الون مثل منصّة MEXC»): اللوحة أخذت طراز
// المنصّة — خلفيّة شبه سوداء محايدة بدل الأخضر المزرقّ، وأخضر MEXC المزرقّ
// `#00b897` للصعود، وأحمرها `#f6465d` للهبوط، وذهبيّها `#f0b90b` للتحذير.
// الأسماء والمفاتيح كما هي، فالتخصيص المحفوظ للمالك يظلّ يطغى عليها.
export const CRYPTO_PRESET: ColorsMap = {
  '--bg1': '#0b0e11', '--bg2': '#12161c', 'glass': '#1e2329',
  '--ink': '#eaecef', '--dim': '#848e9c', '--accent': '#00b897',
  '--green': '#00b897', '--amber': '#f0b90b', '--red': '#f6465d',
}
