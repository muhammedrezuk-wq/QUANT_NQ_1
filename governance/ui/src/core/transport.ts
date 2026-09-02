import { getApiKey } from './auth'
// طبقة النقل (١٤ §٣): اتصال واحد ببثّ النواة (WS) + عميل قراءة (REST عبر خادم الحوكمة).
// إعادة اتصال تلقائية عند الانقطاع؛ لا CORS (الـREST يمرّ بخادم الحوكمة، نفس الأصل).

// المرشِد على أصل الصفحة (يعمل عن بُعد ومعاينة Arena). المسار المباشر :8010
// للجهاز المحلّي فقط — هناك المتصفّح يصل للنواة. عن بُعد، :8010 على اسم الصفحة
// لا يصل أبدًا وكان يترك اللوحة في حالة تجميد سوداء.
const _secure = window.location.protocol === 'https:'
const _host = window.location.host || '127.0.0.1:8090'
const _hostname = window.location.hostname || '127.0.0.1'
const RELAY_WS = `${_secure ? 'wss' : 'ws'}://${_host}/gov/ws/core`

// منفذ النواة يتبع السوق المختار (كوكي QUANT_MARKET الذي يضبطه الهبّ عند التبديل):
// فوركس 8010 · كريبتو 8020.
const CORE_PORTS: Record<string, number> = { forex: 8010, crypto: 8020 }
const UI_PORT_MARKET: Record<string, string> = { '8090': 'forex', '8091': 'crypto' }
const _coreWs = (): string => {
  const uiPort = window.location.port || '8090'
  const re = new RegExp(`(?:^|;\\s*)QUANT_MARKET_${uiPort}=(forex|crypto)`)
  const m = re.exec(document.cookie)
  const market = m ? m[1] : (UI_PORT_MARKET[uiPort] ?? 'forex')
  const port = CORE_PORTS[market] ?? 8010
  return `${_secure ? 'wss' : 'ws'}://${_hostname}:${port}/ws/events`
}

export type WsMsg =
  | { type: 'snapshot'; atoms: unknown[]; metrics: unknown }
  | { type: 'event'; name: string; payload: unknown }

/** يفتح اتصالًا واحدًا ببثّ النواة. عن بُعد: المرشِد فقط. محلّيًا: النواة مباشرة. */
export function connectWs(
  onMsg: (m: WsMsg) => void,
  onStatus: (open: boolean) => void,
): () => void {
  let ws: WebSocket | null = null
  let stopped = false
  let reconnectTimer: number | undefined
  let downTimer: number | undefined
  const localHost = _hostname === '127.0.0.1' || _hostname === 'localhost' || _hostname === '::1'

  // لا نعلن الانقطاع من أول إغلاق عابر: ننتظر 10 ثوانٍ. هذا يمنع وميض
  // الشاشة عند إعادة تشغيل النواة أو عند تبدّل الشبكة للحظات.
  const markDownDebounced = () => {
    if (downTimer !== undefined) return
    downTimer = window.setTimeout(() => { if (!stopped && ws?.readyState !== WebSocket.OPEN) onStatus(false) }, 10000)
  }
  const markUp = () => {
    if (downTimer !== undefined) { window.clearTimeout(downTimer); downTimer = undefined }
    onStatus(true)
  }

  const open = () => {
    if (stopped) return
    let next: WebSocket
    try {
      const key = getApiKey()
      const encoded = key ? btoa(unescape(encodeURIComponent(key))).replace(/=+$/g, '').replace(/\+/g, '-').replace(/\//g, '_') : ''
      const url = localHost ? _coreWs() : RELAY_WS
      next = encoded ? new WebSocket(url, ['quant-nq', `quant-nq-key.${encoded}`]) : new WebSocket(url)
    } catch {
      markDownDebounced()
      reconnectTimer = window.setTimeout(open, 2500)
      return
    }
    ws = next
    ws.onopen = () => markUp()
    ws.onmessage = (e) => {
      try { onMsg(JSON.parse(e.data as string) as WsMsg) } catch { /* رسالة غير صالحة تُتجاهل */ }
    }
    ws.onclose = () => {
      markDownDebounced()
      if (!stopped) reconnectTimer = window.setTimeout(open, 2500)
    }
    ws.onerror = () => { try { ws?.close() } catch { /* تجاهل */ } }
  }
  open()
  return () => {
    stopped = true
    if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer)
    if (downTimer !== undefined) window.clearTimeout(downTimer)
    try { ws?.close() } catch { /* تجاهل */ }
  }
}

/** قراءة عبر خادم الحوكمة (يقرأ النواة ويترجم للعربي). */
export async function govGet<T>(path: string): Promise<T> {
  const r = await fetch(path)
  if (!r.ok) throw new Error(String(r.status))
  return (await r.json()) as T
}

/** كتابة/أمر عبر بوّابة الحوكمة (تمرّر لخُطّاف النواة). */
export function govPost(path: string): Promise<Response> {
  return fetch(path, { method: 'POST' })
}
