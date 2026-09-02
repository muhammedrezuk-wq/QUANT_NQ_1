// السوق الجاري — مقروءًا من الخادم نفسه (`/gov/market`)، لا من تخمين بالمنفذ.
// أُفرِد هنا لأنّ أكثر من قسم صار يحتاجه: App للتبويبات والألوان، والإعدادات
// لإخفاء بطاقات محكومة بسوق واحد (أمر المالك ٢٠٢٦-٠٨-٢٩: «ما بدي شي فوركسي
// بقسم كريبتو»). طلبٌ واحد يُشارَك بين كل المنادين — لا طلب لكل بطاقة.
import { useEffect, useState } from 'react'

let cached: string | null = null
let inflight: Promise<string> | null = null

function fetchMarket(): Promise<string> {
  if (cached) return Promise.resolve(cached)
  if (!inflight) {
    inflight = fetch('/gov/market', { cache: 'no-store' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d: { market?: string } | null) => {
        cached = d?.market === 'crypto' ? 'crypto' : 'forex'
        return cached
      })
      .catch(() => 'forex')
  }
  return inflight
}

/** السوق الجاري — `null` حتى يردّ الخادم (فلا تُرسم بطاقة على تخمين). */
export function useMarket(): string | null {
  const [m, setM] = useState<string | null>(cached)
  useEffect(() => {
    let alive = true
    fetchMarket().then((v) => { if (alive) setM(v) })
    return () => { alive = false }
  }, [])
  return m
}
