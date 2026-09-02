// الشبكة العصبية السينمائية (WebGL) — النسخة الثانية: نقاط متوهّجة (glow) + Additive + ACES +
// ضباب + Bloom + حركة عائمة + أسماء هولوجرامية (المحاور) + tooltip + Vignette/Scanlines +
// كاميرا parallax + دوران. مدفوعة بداتا حقيقية · ضغط = تفاصيل.
// خطوط الوصلات (أمر المالك 2026-08-06): النسيج الهادي خافت، والوصلة بتشتعل + نبضة ضوء
// تمشي ناشر→مشترك **بس لمّا يمر حدث حقيقي** — النبضات العشوائية الزخرفية انشالت (لا وهمي).
import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js'
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js'
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js'
import { useStore } from '../core/store'

const CCOL: Record<string, THREE.Color> = { green: new THREE.Color(0x1fffc3), amber: new THREE.Color(0xffc23d), red: new THREE.Color(0xff4f78), grey: new THREE.Color(0x6f7f9c) }
const colOf = (id: number) => CCOL[useStore.getState().atoms[id]?.color ?? 'grey'] ?? CCOL.grey

// عائلة كل ذرة (نفس تقسيم الأعمدة) — لتلوين الأشرطة حسب العائلة (طلب المالك: مو لون واحد)
const stageOf = (id: number) => (id >= 700 && id < 800) ? -2 : (id < 100 || id >= 800) ? -1
  : id >= 600 ? 0 : id >= 550 ? 10 : id >= 500 ? 9 : id >= 450 ? 8 : id >= 400 ? 7
    : id >= 350 ? 6 : id >= 300 ? 5 : id >= 250 ? 4 : id >= 200 ? 3 : id >= 150 ? 2 : 1
const FAMILY_COL: Record<number, THREE.Color> = {
  0: new THREE.Color(0x38bdf8),   // المنصّة والجسور — سماوي
  1: new THREE.Color(0x22d3ee),   // بيانات السوق — فيروزي
  2: new THREE.Color(0x34d399),   // التحليل — أخضر
  3: new THREE.Color(0xa3e635),   // البنية — ليموني
  4: new THREE.Color(0xfbbf24),   // السيولة — كهرماني
  5: new THREE.Color(0xfb923c),   // الإحصاء — برتقالي
  6: new THREE.Color(0xf472b6),   // الاحتمالات — وردي
  7: new THREE.Color(0xc084fc),   // الاستراتيجيات — بنفسجي
  8: new THREE.Color(0x818cf8),   // القرار — نيلي
  9: new THREE.Color(0xef4444),   // المخاطر — أحمر
  10: new THREE.Color(0xfb7185),  // التنفيذ — وردي محمر
  [-1]: new THREE.Color(0x94a3b8),  // النظام والصيانة — رمادي فضي
  [-2]: new THREE.Color(0x60a5fa),  // المخازن — أزرق
}
const famColOf = (id: number) => FAMILY_COL[stageOf(id)] ?? CCOL.grey

function glowTexture(): THREE.CanvasTexture {
  const c = document.createElement('canvas'); c.width = c.height = 64
  const x = c.getContext('2d')!; const g = x.createRadialGradient(32, 32, 0, 32, 32, 32)
  g.addColorStop(0, 'rgba(255,255,255,1)'); g.addColorStop(0.25, 'rgba(200,220,255,0.85)'); g.addColorStop(1, 'rgba(0,0,0,0)')
  x.fillStyle = g; x.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c)
}
function textSprite(text: string): THREE.Sprite {
  const c = document.createElement('canvas'); c.width = 320; c.height = 72
  const x = c.getContext('2d')!; x.direction = 'rtl'
  x.font = '600 30px "IBM Plex Sans Arabic",Tahoma,sans-serif'; x.textAlign = 'center'; x.textBaseline = 'middle'
  x.fillStyle = '#dff3ff'; x.fillText(text, 160, 38, 312) // بلا ظل ولا توهّج (طلب المالك)
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, opacity: 0.92, depthWrite: false }))
  s.scale.set(106, 23.9, 1); return s
}
function titleSprite(text: string): THREE.Sprite {
  // عنوان عائلة — أكبر وأهدى من أسماء الذرات، بلا توهّج
  const c = document.createElement('canvas'); c.width = 512; c.height = 96
  const x = c.getContext('2d')!; x.direction = 'rtl'
  x.font = '700 52px "IBM Plex Sans Arabic",Tahoma,sans-serif'; x.textAlign = 'center'; x.textBaseline = 'middle'
  x.fillStyle = 'rgba(159,184,220,0.9)'; x.fillText(text, 256, 50, 500)
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, transparent: true, opacity: 0.85, depthWrite: false }))
  s.scale.set(170, 31.9, 1); return s
}

export default function Network() {
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const wrap = wrapRef.current
    if (!wrap) return
    let W = wrap.clientWidth || 800, H = wrap.clientHeight || 600, stopped = false, raf = 0

    let renderer: THREE.WebGLRenderer
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' }) }
    catch { wrap.innerHTML = '<div style="height:100%;display:flex;align-items:center;justify-content:center;color:#8a97ad">الرسم الثلاثي غير مدعوم</div>'; return }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2)); renderer.setSize(W, H)
    renderer.setClearColor(0x030307, 1); renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.25
    wrap.appendChild(renderer.domElement)

    const vig = document.createElement('div'); vig.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:2;background:radial-gradient(circle at 50% 50%, rgba(0,0,0,0) 55%, rgba(0,0,0,.82) 100%)'
    const scan = document.createElement('div'); scan.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:3;opacity:.025;background:linear-gradient(rgba(0,0,0,0) 50%, rgba(0,0,0,.5) 50%);background-size:100% 4px'
    wrap.appendChild(vig); wrap.appendChild(scan)

    const scene = new THREE.Scene(); scene.fog = new THREE.FogExp2(0x030409, 0.00035)
    const camera = new THREE.PerspectiveCamera(56, W / H, 1, 8000); camera.position.set(0, 0, 800)
    const group = new THREE.Group(); scene.add(group)
    const composer = new EffectComposer(renderer)
    composer.addPass(new RenderPass(scene, camera))
    composer.addPass(new UnrealBloomPass(new THREE.Vector2(W, H), 0.45, 0.4, 0.12)) // توهّج مخفَّف (طلب المالك)
    const tex = glowTexture()

    const starG = new THREE.BufferGeometry(); const sv = new Float32Array(1200 * 3); let sd = 7
    const rnd = () => { sd = (sd * 16807) % 2147483647; return sd / 2147483647 }
    for (let i = 0; i < sv.length; i++) sv[i] = (rnd() - 0.5) * 3200
    starG.setAttribute('position', new THREE.BufferAttribute(sv, 3))
    group.add(new THREE.Points(starG, new THREE.PointsMaterial({ color: 0x2a3a5a, size: 3, map: tex, transparent: true, opacity: 0.5, blending: THREE.AdditiveBlending, depthWrite: false })))

    interface DN { id: number; name: string; deg: number; x: number; y: number; z: number; vx: number; vy: number; vz: number; hx: number; hy: number; hz: number }
    let dn: DN[] = []
    let pts: THREE.Points | null = null, lines: THREE.LineSegments | null = null
    let edges: Array<[number, number]> = []
    let heat = new Float32Array(0) // اشتعال كل وصلة (١ عند مرور حدث حقيقي → يخبو)
    const topicEdges = new Map<string, number[]>() // اسم الحدث → فهارس وصلاته
    let sprites: Array<{ s: THREE.Sprite; i: number }> = []
    let titles: THREE.Sprite[] = []
    let sig = ''
    const IMAX = 160, impPos = new Float32Array(IMAX * 3)
    const impGeo = new THREE.BufferGeometry(); impGeo.setAttribute('position', new THREE.BufferAttribute(impPos, 3).setUsage(THREE.DynamicDrawUsage))
    const impSys = new THREE.Points(impGeo, new THREE.PointsMaterial({ color: 0xe6ffff, size: 32, map: tex, transparent: true, opacity: 1, blending: THREE.AdditiveBlending, depthWrite: false })); impSys.frustumCulled = false; group.add(impSys)
    const imps: Array<{ a: number; b: number; t: number; sp: number }> = []
    const seenFlow: Record<string, number> = {}, lastSpawn: Record<string, number> = {}

    const clearG = () => {
      if (pts) { group.remove(pts); pts.geometry.dispose(); (pts.material as THREE.Material).dispose(); pts = null }
      if (lines) { group.remove(lines); lines.geometry.dispose(); (lines.material as THREE.Material).dispose(); lines = null }
      for (const { s } of sprites) { group.remove(s); s.material.map?.dispose(); s.material.dispose() }
      for (const s of titles) { group.remove(s); s.material.map?.dispose(); s.material.dispose() }
      sprites = []; titles = []; edges = []; topicEdges.clear()
    }

    const build = (g: { nodes: Array<{ id: number; name: string }>; edges: Array<{ source: number; target: number; topic?: string }> }) => {
      const ns = g.nodes.map((n) => n.id).join(',') + '|' + g.edges.length
      if (ns === sig) return
      sig = ns; clearG()
      const deg: Record<number, number> = {}
      for (const e of g.edges) { deg[e.source] = (deg[e.source] || 0) + 1; deg[e.target] = (deg[e.target] || 0) + 1 }
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const fnodes: any[] = g.nodes.map((n) => ({ id: n.id, name: n.name, deg: deg[n.id] || 0 }))
      const links = g.edges.map((e) => ({ source: e.source, target: e.target, topic: e.topic }))
      // منظور بشري (طلب المالك «شو بفهم منها؟ ولا شي»): بدل كرة الشعر العشوائية —
      // خط إنتاج مقروء من اليمين لليسار: المنصّة والبيانات يمين ← التحليل ← ... ← التنفيذ يسار،
      // «النظام والصيانة» شريط فوق و«المخازن» شريط تحت. مكان الذرة بيحكي دورها.
      const TITLES = ['المنصّة والجسور', 'بيانات السوق', 'التحليل', 'البنية', 'السيولة', 'الإحصاء', 'الاحتمالات', 'الاستراتيجيات', 'القرار', 'المخاطر', 'التنفيذ']
      const visH = 2 * Math.tan((56 / 2) * Math.PI / 180) * 800 // المدى المرئي عند البعد الأساسي
      const halfW = visH * (W / H) * 0.46, halfH = visH * 0.43
      const colW = (2 * halfW) / TITLES.length
      const colX = (i: number) => halfW - colW * (i + 0.5) // العمود ٠ أقصى اليمين (قراءة عربية)
      const groups = new Map<number, typeof fnodes>()
      for (const n of fnodes) { const st = stageOf(n.id); const arr = groups.get(st) ?? []; arr.push(n); groups.set(st, arr) }
      for (const arr of groups.values()) arr.sort((a, b) => a.id - b.id)
      const topY = halfH * 0.6, spanY = halfH * 1.2 // مساحة الأعمدة بين الشريطين
      const place = new Map<number, { x: number; y: number }>()
      for (const [st, arr] of groups) {
        if (st >= 0) {
          const x = colX(st)
          arr.forEach((n, k) => place.set(n.id, { x: x + (k % 2 ? 17 : -17), y: topY - (arr.length < 2 ? spanY / 2 : (spanY * k) / (arr.length - 1)) }))
        } else {
          const y = st === -1 ? halfH * 0.92 : -halfH * 0.92
          arr.forEach((n, k) => place.set(n.id, { x: halfW * 0.85 - (arr.length < 2 ? halfW * 0.85 : (halfW * 1.7 * k) / (arr.length - 1)), y }))
        }
      }
      dn = fnodes.map((n) => {
        const p = place.get(n.id)!
        // عمق حقيقي (طلب المالك: «العمق لازم ينوسع») — توزيع منتظم على ~⅔ الارتفاع المرئي،
        // ثابت لكل ذرة (من رقمها) فما بيتغيّر مكانها بين فتحة وفتحة
        const z = (((n.id * 2654435761) % 1000) / 1000 - 0.5) * (halfH * 2.6)
        return { id: n.id, name: n.name, deg: n.deg, x: p.x, y: p.y, z, vx: 0, vy: 0, vz: 0, hx: p.x, hy: p.y, hz: z }
      })
      // عناوين العائلات فوق أعمدتها + عنوانا الشريطين عاليمين
      for (let i = 0; i < TITLES.length; i++) { const s = titleSprite(TITLES[i]); s.position.set(colX(i), halfH * 0.79, 0); group.add(s); titles.push(s) }
      const tSys = titleSprite('النظام والصيانة'); tSys.position.set(halfW * 0.99, halfH * 0.92, 0); group.add(tSys); titles.push(tSys)
      const tStore = titleSprite('المخازن'); tStore.position.set(halfW * 0.99, -halfH * 0.92, 0); group.add(tStore); titles.push(tStore)
      const idx: Record<number, number> = {}; dn.forEach((n, i) => (idx[n.id] = i))
      const pos = new Float32Array(dn.length * 3), col = new Float32Array(dn.length * 3)
      dn.forEach((n, i) => { pos[i * 3] = n.x; pos[i * 3 + 1] = n.y; pos[i * 3 + 2] = n.z; const c = colOf(n.id); col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b })
      const pg = new THREE.BufferGeometry()
      pg.setAttribute('position', new THREE.BufferAttribute(pos, 3).setUsage(THREE.DynamicDrawUsage))
      pg.setAttribute('color', new THREE.BufferAttribute(col, 3).setUsage(THREE.DynamicDrawUsage))
      pts = new THREE.Points(pg, new THREE.PointsMaterial({ size: 22, map: tex, vertexColors: true, transparent: true, opacity: 0.98, blending: THREE.AdditiveBlending, depthWrite: false })); pts.frustumCulled = false; group.add(pts)
      // ⚠️ d3-forceLink يبدّل source/target من رقم لكائن العقدة بعد المحاكاة — ناخد الـid بالحالتين
      // (هاد كان جذر «ولا خط»: البحث برقم على كائن → قايمة الوصلات تطلع فاضية)
      for (const e of links) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const sid = typeof e.source === 'object' ? (e.source as any).id : e.source
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const tid = typeof e.target === 'object' ? (e.target as any).id : e.target
        const a = idx[sid], b = idx[tid]
        if (a == null || b == null) continue
        const ei = edges.length; edges.push([a, b])
        if (e.topic) { const arr = topicEdges.get(e.topic) ?? []; arr.push(ei); topicEdges.set(e.topic, arr) }
      }
      heat = new Float32Array(edges.length)
      const lp = new Float32Array(edges.length * 6), lc = new Float32Array(edges.length * 6)
      const lg = new THREE.BufferGeometry(); lg.setAttribute('position', new THREE.BufferAttribute(lp, 3).setUsage(THREE.DynamicDrawUsage)); lg.setAttribute('color', new THREE.BufferAttribute(lc, 3).setUsage(THREE.DynamicDrawUsage))
      lines = new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: 0.92, blending: THREE.AdditiveBlending, depthWrite: false })); lines.frustumCulled = false; group.add(lines)
      // كل الأسماء ظاهرة بالوضع الأساسي (طلب المالك) — مش بس المحاور
      for (let i = 0; i < dn.length; i++) { const s = textSprite(dn[i].name); group.add(s); sprites.push({ s, i }) }
    }
    const load = () => fetch('/gov/graph').then((r) => r.json()).then(build).catch(() => {})
    load(); const gpoll = window.setInterval(load, 5000)

    const tip = document.createElement('div')
    tip.style.cssText = 'position:absolute;pointer-events:none;z-index:4;font:600 12px "IBM Plex Sans Arabic",sans-serif;color:#eafaff;background:rgba(8,14,26,.82);border:1px solid rgba(120,180,255,.35);border-radius:7px;padding:3px 10px;transform:translate(-50%,-150%);opacity:0;transition:opacity .1s;white-space:nowrap'
    wrap.appendChild(tip)
    const ray = new THREE.Raycaster(); ray.params.Points = { threshold: 20 }
    // تحكّم بيد المالك (طلبه 2026-08-06): لا دوران ذاتي ولا زحلقة مع الماوس —
    // سحب = تدوير · عجلة = زوم · بتضل ثابتة متل ما تركها
    let dragging = false, lx = 0, ly = 0
    const hitAt = (e: MouseEvent): { i: number; r: DOMRect } | null => { if (!pts) return null; const r = renderer.domElement.getBoundingClientRect(); ray.setFromCamera(new THREE.Vector2(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1), camera); const hit = ray.intersectObject(pts, false)[0]; return hit && hit.index != null && dn[hit.index] ? { i: hit.index, r } : null }
    const onMove = (e: MouseEvent) => {
      if (dragging) {
        group.rotation.y += (e.clientX - lx) * 0.005
        group.rotation.x = Math.max(-0.9, Math.min(0.9, group.rotation.x + (e.clientY - ly) * 0.003))
        lx = e.clientX; ly = e.clientY
        tip.style.opacity = '0'; renderer.domElement.style.cursor = 'grabbing'
        return
      }
      const h = hitAt(e)
      if (h) { tip.textContent = dn[h.i].name; tip.style.left = (e.clientX - h.r.left) + 'px'; tip.style.top = (e.clientY - h.r.top) + 'px'; tip.style.opacity = '1'; renderer.domElement.style.cursor = 'pointer' }
      else { tip.style.opacity = '0'; renderer.domElement.style.cursor = 'grab' }
    }
    let dx0 = 0, dy0 = 0
    const onDown = (e: MouseEvent) => { dx0 = e.clientX; dy0 = e.clientY; lx = e.clientX; ly = e.clientY; dragging = true }
    const onUp = (e: MouseEvent) => { dragging = false; renderer.domElement.style.cursor = 'grab'; if (Math.hypot(e.clientX - dx0, e.clientY - dy0) > 5) return; const h = hitAt(e); if (h) useStore.getState().select(dn[h.i].id) }
    const onWheel = (e: WheelEvent) => { e.preventDefault(); camera.position.z = Math.max(240, Math.min(2400, camera.position.z * (e.deltaY > 0 ? 1.1 : 1 / 1.1))) }
    window.addEventListener('mousemove', onMove); renderer.domElement.addEventListener('mousedown', onDown); window.addEventListener('mouseup', onUp); renderer.domElement.addEventListener('wheel', onWheel, { passive: false })

    let t = 0
    const animate = () => {
      if (stopped) return
      raf = requestAnimationFrame(animate)
      t += 0.016
      camera.lookAt(scene.position) // لا دوران ذاتي ولا parallax — التحكّم كله بإيد المالك
      if (pts && dn.length) {
        const pa = (pts.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array
        const ca = (pts.geometry.attributes.color as THREE.BufferAttribute).array as Float32Array
        for (let i = 0; i < dn.length; i++) {
          const n = dn[i]
          n.vx = (n.vx + (n.hx - n.x) * 0.0009 + Math.sin(t * 0.7 + i) * 0.02) * 0.96
          n.vy = (n.vy + (n.hy - n.y) * 0.0009 + Math.cos(t * 0.6 + i * 1.3) * 0.02) * 0.96
          n.vz = (n.vz + (n.hz - n.z) * 0.0009 + Math.sin(t * 0.5 + i * 0.7) * 0.02) * 0.96
          n.x += n.vx; n.y += n.vy; n.z += n.vz
          pa[i * 3] = n.x; pa[i * 3 + 1] = n.y; pa[i * 3 + 2] = n.z
          if ((((t * 60) | 0) & 7) === 0) { const c = colOf(n.id); ca[i * 3] = c.r; ca[i * 3 + 1] = c.g; ca[i * 3 + 2] = c.b }
        }
        pts.geometry.attributes.position.needsUpdate = true; pts.geometry.attributes.color.needsUpdate = true
        for (const sp of sprites) { const n = dn[sp.i]; sp.s.position.set(n.x, n.y + 22, n.z) }
      }
      if (lines && dn.length) {
        const la = (lines.geometry.attributes.position as THREE.BufferAttribute).array as Float32Array
        const lc = (lines.geometry.attributes.color as THREE.BufferAttribute).array as Float32Array
        for (let e = 0; e < edges.length; e++) {
          heat[e] *= 0.945 // خبوّ الاشتعال (~ثانية ونص)
          const a = dn[edges[e][0]], b = dn[edges[e][1]]
          la[e * 6] = a.x; la[e * 6 + 1] = a.y; la[e * 6 + 2] = a.z; la[e * 6 + 3] = b.x; la[e * 6 + 4] = b.y; la[e * 6 + 5] = b.z
          // الأشرطة بألوان العائلات (متدرّجة من عيلة الناشر لعيلة المستقبِل) — النقاط تبقى بألوان الصحة
          const ca = famColOf(a.id), cb = famColOf(b.id)
          const h = heat[e], k = 0.18 + 0.82 * h, w = h * 0.35
          lc[e * 6] = ca.r * k + w; lc[e * 6 + 1] = ca.g * k + w; lc[e * 6 + 2] = ca.b * k + w
          lc[e * 6 + 3] = cb.r * k + w; lc[e * 6 + 4] = cb.g * k + w; lc[e * 6 + 5] = cb.b * k + w
        }
        lines.geometry.attributes.position.needsUpdate = true; lines.geometry.attributes.color.needsUpdate = true
      }
      // أحداث حقيقية فقط: كل اشتعال/نبضة = حدث فعلًا انطلق بالنواة (بلا أي حركة زخرفية)
      const now = performance.now(), flows = useStore.getState().flows
      for (const topic in flows) {
        if (seenFlow[topic] === flows[topic]) continue
        seenFlow[topic] = flows[topic]
        const es = topicEdges.get(topic)
        if (!es) continue
        for (const ei of es) heat[ei] = 1 // الاشتعال دايمًا (رخيص وصادق)
        if (now - (lastSpawn[topic] || 0) < 110) continue // النبضات وحدها محكومة بمعدّل
        lastSpawn[topic] = now
        for (const ei of es) { if (imps.length >= IMAX) break; const [a, b] = edges[ei]; imps.push({ a, b, t: 0, sp: 0.02 + Math.random() * 0.015 }) }
      }
      for (const im of imps) im.t += im.sp
      for (let i = imps.length - 1; i >= 0; i--) if (imps[i].t >= 1) imps.splice(i, 1)
      for (let i = 0; i < imps.length; i++) { const im = imps[i], a = dn[im.a], b = dn[im.b]; if (!a || !b) { impPos[i * 3] = 1e6; continue } impPos[i * 3] = a.x + (b.x - a.x) * im.t; impPos[i * 3 + 1] = a.y + (b.y - a.y) * im.t; impPos[i * 3 + 2] = a.z + (b.z - a.z) * im.t }
      impGeo.setDrawRange(0, imps.length); impGeo.attributes.position.needsUpdate = true
      composer.render()
    }
    animate()

    const resize = () => { W = wrap.clientWidth || W; H = wrap.clientHeight || H; camera.aspect = W / H; camera.updateProjectionMatrix(); renderer.setSize(W, H); composer.setSize(W, H) }
    const ro = new ResizeObserver(resize); ro.observe(wrap)
    return () => {
      stopped = true; cancelAnimationFrame(raf); clearInterval(gpoll); ro.disconnect()
      window.removeEventListener('mousemove', onMove); renderer.domElement.removeEventListener('mousedown', onDown); window.removeEventListener('mouseup', onUp); renderer.domElement.removeEventListener('wheel', onWheel)
      clearG(); composer.dispose(); renderer.dispose()
      try { wrap.removeChild(renderer.domElement); wrap.removeChild(vig); wrap.removeChild(scan); wrap.removeChild(tip) } catch { /* */ }
    }
  }, [])

  return (
    <div className="network" ref={wrapRef} style={{ position: 'relative', overflow: 'hidden', margin: '26px 0 16px', height: 'calc(100% - 42px)' }}>
      <div className="network-badge">● بيانات حقيقية · الوميض = حدث فعلي</div>
      <div className="network-legend" aria-label="دليل ألوان عائلات الذرات">
        {[['#38bdf8', 'المنصّة'], ['#22d3ee', 'بيانات السوق'], ['#34d399', 'التحليل'], ['#fbbf24', 'السيولة'], ['#fb923c', 'الإحصاء'], ['#f472b6', 'الاحتمالات'], ['#c084fc', 'الاستراتيجيات'], ['#818cf8', 'القرار'], ['#ef4444', 'المخاطر'], ['#fb7185', 'التنفيذ']].map(([color, label]) => (
          <span key={label}><i style={{ background: color }} />{label}</span>
        ))}
      </div>
      <div className="network-hint">اسحب للتدوير · عجلة للزوم · اضغط الذرة لفتح تفاصيلها</div>
    </div>
  )
}
