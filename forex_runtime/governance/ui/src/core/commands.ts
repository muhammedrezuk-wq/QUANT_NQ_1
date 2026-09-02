// بوّابة الأوامر (١٤ §٨): كل كتابة/أمر تمرّ من مكان واحد، بدرجة صلاحية وتأكيد،
// ثم تنادي خادم الحوكمة الذي يمرّرها لخُطّاف النواة ويُسجّلها بجورنال النواة.
// الدرجات: قراءة (بلا تأكيد) · كتابة (تأكيد) · أوامر خطرة (تأكيد صريح + تمييز).
import { govPost } from './transport'

export type Tier = 'read' | 'write' | 'danger'

export interface Command {
  tier: Tier
  label: string // بالعربي — نصّ التأكيد
  run: () => Promise<Response>
}

export interface CommandResult { ok: boolean; message?: string }

export async function execute(cmd: Command): Promise<CommandResult> {
  if (cmd.tier !== 'read') {
    const prefix = cmd.tier === 'danger' ? '⚠️ أمر خطر — ' : ''
    if (!window.confirm(prefix + cmd.label + '؟')) return { ok: false, message: 'أُلغي' }
  }
  try {
    const r = await cmd.run()
    if (!r.ok) {
      const j = (await r.json().catch(() => ({}))) as { message?: string; detail?: { message?: string }; error?: string }
      return { ok: false, message: j.message ?? j.detail?.message ?? j.error ?? String(r.status) }
    }
    return { ok: true }
  } catch (e) {
    return { ok: false, message: 'خطأ اتصال: ' + String(e) }
  }
}

// الأوامر الخطرة عبر بوّابة الأوامر (٩٠١) — خطوتان مع الخادم:
// طلب → رمز تأكيد + ملخّص عربي → تأكيد المالك → تُكتب بالجسر → الذرة تنشر الحدث خلال ≤ ثانية
export async function confirmedCommand(action: string, payload: Record<string, unknown> = {}, operator = 'لوحة التحكم'): Promise<CommandResult> {
  const post = (body: object) =>
    fetch('/gov/command', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  try {
    const r1 = await post({ action, payload, operator })
    const j1 = (await r1.json()) as { token?: string; summary?: string; ttl_s?: number; error?: string }
    if (!r1.ok || !j1.token) return { ok: false, message: j1.error ?? String(r1.status) }
    if (!window.confirm(`⚠️ أمر خطر — ${j1.summary ?? action}؟\n(التأكيد صالح ${j1.ttl_s ?? 60} ثانية)`)) {
      return { ok: false, message: 'أُلغي' }
    }
    const r2 = await post({ action, payload, operator, confirm: j1.token })
    const j2 = (await r2.json()) as { message?: string; error?: string }
    if (!r2.ok) return { ok: false, message: j2.error ?? String(r2.status) }
    return { ok: true, message: j2.message }
  } catch (e) {
    return { ok: false, message: 'خطأ اتصال: ' + String(e) }
  }
}

export const dangerCommand = (action: 'halt' | 'kill_switch_reset'): Promise<CommandResult> =>
  confirmedCommand(action)

// دفعة أوامر بتأكيد واحد (بند 2ب بدفتر 97 — «حفظ إعدادات الكل»):
// المالك يؤكّد مرّة واحدة على ملخّص الدفعة، وكل أمر يمرّ بعدها بخطوتي الخادم
// نفسهما (طلب رمز → تنفيذ بالرمز) — عقد بوّابة الأوامر ما تغيّر ولا انلفّ عليه.
export async function confirmedCommandMany(
  items: Array<{ action: string; payload: Record<string, unknown> }>,
  batchLabel: string,
  operator = 'لوحة التحكم',
): Promise<{ ok: boolean; done: number; failed: Array<{ index: number; message: string }> }> {
  if (!items.length) return { ok: true, done: 0, failed: [] }
  if (!window.confirm(`⚠️ ${batchLabel}؟\n(${items.length} أمرًا — كل أمر يمرّ ببوّابة الأوامر)`)) {
    return { ok: false, done: 0, failed: [{ index: -1, message: 'أُلغي' }] }
  }
  const post = (body: object) =>
    fetch('/gov/command', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  let done = 0
  const failed: Array<{ index: number; message: string }> = []
  for (let i = 0; i < items.length; i++) {
    const { action, payload } = items[i]
    try {
      const r1 = await post({ action, payload, operator })
      const j1 = (await r1.json()) as { token?: string; error?: string }
      if (!r1.ok || !j1.token) { failed.push({ index: i, message: j1.error ?? String(r1.status) }); continue }
      const r2 = await post({ action, payload, operator, confirm: j1.token })
      const j2 = (await r2.json()) as { error?: string }
      if (!r2.ok) { failed.push({ index: i, message: j2.error ?? String(r2.status) }); continue }
      done++
    } catch (e) {
      failed.push({ index: i, message: 'خطأ اتصال: ' + String(e) })
    }
  }
  return { ok: failed.length === 0, done, failed }
}

// أوامر جاهزة (تستعملها الشاشات لاحقًا) — إيقاف = خطر، تشغيل = كتابة
export const atomStop = (id: number, name: string): Command => ({
  tier: 'danger',
  label: `إيقاف الذرة «${name}» (#${id})`,
  run: () => govPost(`/gov/atoms/${id}/stop`),
})
export const atomStart = (id: number, name: string): Command => ({
  tier: 'write',
  label: `تشغيل الذرة «${name}» (#${id})`,
  run: () => govPost(`/gov/atoms/${id}/start`),
})
