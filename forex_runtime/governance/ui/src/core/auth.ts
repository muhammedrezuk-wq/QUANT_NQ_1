let apiKey = ''

export function getApiKey(): string { return apiKey }

function installAuthenticatedFetch(key: string): void {
  apiKey = key
  const nativeFetch = window.fetch.bind(window)
  window.fetch = (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === 'string' ? new URL(input, window.location.href)
      : input instanceof URL ? input : new URL(input.url, window.location.href)
    if (url.origin !== window.location.origin) return nativeFetch(input, init)
    const headers = new Headers(input instanceof Request ? input.headers : undefined)
    new Headers(init.headers).forEach((value, name) => headers.set(name, value))
    headers.set('X-API-Key', key)
    return nativeFetch(input, { ...init, headers })
  }
}

/** Local mode succeeds without a key. Remote mode prompts once and keeps the
 * key only in JS memory (never localStorage, URL, or a project file). */
export async function bootstrapAuth(): Promise<boolean> {
  const nativeFetch = window.fetch.bind(window)
  const probe = await nativeFetch('/gov/version', { cache: 'no-store' }).catch(() => null)
  // إذا كانت النواة غير مشغّلة، لا نحجب معاينة الواجهة؛ ستظهر القيم كـ «—».
  // التوثيق يُطلب فقط عندما تردّ النواة صراحةً بـ 401.
  if (!probe || probe.status !== 401) return true
  for (;;) {
    const key = window.prompt('أدخل مفتاح واجهة الحوكمة لهذا الجهاز')
    if (key === null) return false
    const checked = await nativeFetch('/gov/version', {
      cache: 'no-store', headers: { 'X-API-Key': key },
    }).catch(() => null)
    if (checked?.ok) { installAuthenticatedFetch(key); return true }
    window.alert('المفتاح غير صحيح')
  }
}
