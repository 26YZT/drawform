import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { ArrowLeft, ArrowUpRight, Plus, X } from 'lucide-react'
import { systems, hotspots, workflowEntries, type HotspotId, type SystemId } from './content'
import HoverVideo from './HoverVideo'
import AppearanceMenu from './AppearanceMenu'
import { Ripple } from './components/canvasui/Ripple'
import { canUseHotspot, type AppearanceMode } from './appearance'
import './drawform.css'

type Phase = 'overview' | 'entering' | 'detail' | 'returning'
const ROOT_IMAGE = '/media/exterior-polished.png'

export default function App() {
  const [phase, setPhase] = useState<Phase>('overview')
  const [selected, setSelected] = useState<SystemId>('battery')
  const [hovered, setHovered] = useState<HotspotId | null>(null)
  const [expandedStep, setExpandedStep] = useState<number | null>(null)
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [reduced, setReduced] = useState(false)
  const [detailVisible, setDetailVisible] = useState(false)
  const [appearance, setAppearance] = useState<AppearanceMode | null>(null)
  const [appearancePinned, setAppearancePinned] = useState(false)
  const [appearanceClosing, setAppearanceClosing] = useState(false)
  const [neutral, setNeutral] = useState(true)
  const appearanceTimer = useRef<number>()
  const leaveTimer = useRef<number>()
  const appearanceButtons = useRef<Partial<Record<AppearanceMode, HTMLButtonElement | null>>>({})
  const suppressFocus = useRef(false)
  const pendingAppearanceFocus = useRef<AppearanceMode | null>(null)
  const passiveReopenBlocked = useRef<AppearanceMode | null>(null)
  const appearanceMode = useRef<AppearanceMode | null>(appearance)
  const appearanceCloseLock = useRef(false)
  appearanceMode.current = appearance
  const lock = useRef(false)
  const request = useRef(0)
  const timer = useRef<number>()
  const revealTimer = useRef<number>()
  const stage = useRef<HTMLElement>(null)
  const [plane, setPlane] = useState<CSSProperties>({})
  const closeButton = useRef<HTMLButtonElement>(null)
  const entryButtons = useRef<Partial<Record<SystemId, HTMLButtonElement | null>>>({})
  const current = systems[selected]
  const busy = phase === 'entering' || phase === 'returning'

  useEffect(() => {
    const media = matchMedia('(prefers-reduced-motion: reduce)')
    const update = () => setReduced(media.matches)
    update(); media.addEventListener('change', update)
    return () => { media.removeEventListener('change', update); clearTimeout(timer.current); clearTimeout(revealTimer.current); clearTimeout(appearanceTimer.current); clearTimeout(leaveTimer.current); request.current++ }
  }, [])

  useEffect(() => {
    if (appearance || appearanceClosing) return
    const previousMode = pendingAppearanceFocus.current
    pendingAppearanceFocus.current = null
    if (previousMode) {
      suppressFocus.current = true
      appearanceButtons.current[previousMode]?.focus({ preventScroll: true })
      suppressFocus.current = false
    }
    // Keep the synchronous close lock through removal and the focus commit.
    appearanceCloseLock.current = false
  }, [appearance, appearanceClosing])

  function openAppearance(mode: AppearanceMode, explicit = false) {
    if (phase !== 'overview' || appearanceCloseLock.current || !canUseHotspot(appearanceMode.current, mode, appearanceClosing)) return
    if (passiveReopenBlocked.current === mode && !explicit) return
    if (explicit) passiveReopenBlocked.current = null
    clearTimeout(leaveTimer.current)
    setHovered(null)
    if (appearanceMode.current === mode) return
    appearanceMode.current = mode
    setAppearance(mode); setAppearancePinned(false)
  }

  function closeAppearance(restoreFocus = true, expectedMode = appearanceMode.current) {
    const previousMode = appearanceMode.current
    if (!previousMode || previousMode !== expectedMode || appearanceCloseLock.current) return
    appearanceCloseLock.current = true
    // Restoring focus or uncovering a stationary pointer can replay passive
    // entry events after commit. Keep this guard until a real leave/blur or click.
    if (restoreFocus) passiveReopenBlocked.current = previousMode
    clearTimeout(leaveTimer.current)
    setAppearanceClosing(true); setHovered(null)
    appearanceTimer.current = window.setTimeout(() => {
      pendingAppearanceFocus.current = restoreFocus ? previousMode : null
      appearanceMode.current = null
      setAppearance(null); setAppearancePinned(false); setAppearanceClosing(false)
    }, reduced ? 0 : 260)
  }

  useEffect(() => {
    const element = stage.current!
    const updatePlane = () => {
      const { width } = element.getBoundingClientRect()
      const mobile = width <= 900
      // Keep media on its original plane; controls use a separate row below it.
      // The shared width also aligns the masthead, title and supporting copy.
      // Reserve space for the functional panels; short screens scroll instead
      // of shrinking the model or clipping its controls.
      const mediaHeight = Math.max(430, window.innerHeight - (window.innerHeight <= 780 ? 64 : 76) - 260 - 88)
      const w = mobile ? width - 40 : Math.min(width - 2 * Math.min(72, Math.max(24, width * .04)), mediaHeight * 1672 / 941)
      const h = w * 941 / 1672
      setPlane({ width: w, height: h, left: (width - w) / 2, top: mobile ? 132 : 88 })
    }
    const observer = new ResizeObserver(updatePlane)
    observer.observe(element)
    window.addEventListener('resize', updatePlane)
    return () => { observer.disconnect(); window.removeEventListener('resize', updatePlane) }
  }, [])

  useEffect(() => {
    let cancelled = false
    Promise.all([ROOT_IMAGE, systems.battery.image, systems.drive.image].map(source => new Promise<void>((resolve, reject) => {
      const image = new Image()
      image.onload = () => { image.decode().then(resolve, resolve) }
      image.onerror = reject
      image.src = source
    }))).then(() => { if (!cancelled) setReady(true) }).catch(() => { if (!cancelled) setError('场景图片未能加载，请刷新重试。工作区入口仍可使用。') })
    return () => { cancelled = true }
  }, [])

  function finish(returning: boolean, id = selected) {
    clearTimeout(timer.current)
    setPhase(returning ? 'overview' : 'detail')
    setDetailVisible(!returning)
    lock.current = false
    setTimeout(() => returning ? entryButtons.current[id]?.focus({ preventScroll: true }) : closeButton.current?.focus({ preventScroll: true }), 0)
  }

  function transition(id: SystemId, returning = false) {
    if (id === 'optics' || appearance || lock.current || !ready || (!returning && phase !== 'overview')) return
    lock.current = true
    const token = ++request.current
    setSelected(id); setHovered(null); setExpandedStep(null); setError('')
    setPhase(returning ? 'returning' : 'entering')

    // Hover films stay separate from the existing click-to-detail crossfade.
    revealTimer.current = window.setTimeout(() => { if (request.current === token) setDetailVisible(!returning) }, reduced ? 0 : 350)
    timer.current = window.setTimeout(() => { if (request.current === token) finish(returning, id) }, reduced ? 0 : 1250)
  }

  function returnToCar() { if (phase === 'detail') void transition(selected, true) }
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { if (appearance) closeAppearance(); else returnToCar() } }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  })

  return <main className={`experience drawform-experience phase-${phase} ${reduced ? 'reduced' : ''}`} style={{ '--scene-width': typeof plane.width === 'number' ? `${plane.width}px` : undefined, '--scene-height': typeof plane.height === 'number' ? `${plane.height}px` : undefined } as CSSProperties} aria-busy={busy}>
    <header className="masthead">
      <a className="wordmark product-brand" href="/" aria-label="返回汽绘首页">
        <span className="group-brand">
          <span className="group-logo-plate"><img src="/assets/branding/saic-group-blue.png" width="32" height="32" alt=""/></span>
          <span className="group-brand-name">上汽集团</span>
        </span>
        <span className="brand-divider" aria-hidden="true"/>
        <span className="product-brand-copy"><span className="product-brand-title">汽绘</span><span className="brand-caption">工程设计工作台</span></span>
      </a>
      <span className="header-middle">从输入，到工程图</span>
      <a className="header-workspace-link" href="/?tool=step">模型转工程图<ArrowUpRight size={16}/></a>
    </header>

    <section ref={stage} className="stage" aria-label="探索汽绘工作流程">
      <div className="image-plane" style={{ ...plane, '--focus-x': `${current.anchor.x}%`, '--focus-y': `${current.anchor.y}%` } as CSSProperties}>
        <div className="media-envelope"><div className="exterior-envelope">
        <div className="car-visual"><img className="car-image" src={ROOT_IMAGE} alt="银色概念车，用于演示交互；实际三维模型在工作区查看" draggable={false}/><HoverVideo desired={!appearance && (hovered === 'drive' || hovered === 'battery') ? hovered : null} mode={phase} reduced={reduced} onNeutral={setNeutral}/></div>
        </div><div className={`detail-visual ${detailVisible ? 'shown' : ''}`}><img className="detail-image" src={current.image} alt={`${current.label}流程的概念交互示意，并非生成的三维模型`} draggable={false}/></div>
        </div>
      </div>

      {phase === 'overview' && ready && <nav className="scene-controls" aria-label="查看功能说明">
        <p className="scene-controls-heading">查看功能说明</p>
        {hotspots.map(item => {
          const { id, target } = item
          const enabled = canUseHotspot(appearance, id, appearanceClosing)
          if (id === 'paint' || id === 'wheels') return <div key={id} className={`appearance-hotspot ${appearance === id ? 'expanded' : ''}`} onPointerEnter={() => openAppearance(id)} onPointerLeave={event => {
            if (appearanceCloseLock.current) return
            if (passiveReopenBlocked.current === id) passiveReopenBlocked.current = null
            if (!appearancePinned && !event.currentTarget.contains(document.activeElement)) leaveTimer.current = window.setTimeout(() => closeAppearance(false, id), 140)
          }} onBlur={event => {
            if (appearanceCloseLock.current || !event.relatedTarget) return
            if (!event.currentTarget.contains(event.relatedTarget)) {
              if (passiveReopenBlocked.current === id) passiveReopenBlocked.current = null
              if (!appearancePinned) closeAppearance(false, id)
            }
          }}>
            <button ref={node => { appearanceButtons.current[id] = node }} className={`hotspot ${appearance === id ? 'active' : ''}`} disabled={!enabled} onFocus={() => { if (!suppressFocus.current) openAppearance(id) }} onClick={() => {
              if (appearance === id && appearancePinned) closeAppearance()
              else { openAppearance(id, true); setAppearancePinned(true) }
            }} aria-expanded={appearance === id && !appearanceClosing} aria-controls={`appearance-${id}`} aria-label={item.label}>
              <span className="hotspot-label">{item.label}</span>
            </button>
            {appearance === id && !appearanceClosing && <span className="menu-bridge" aria-hidden="true"/>}
            {appearance === id && !appearanceClosing && <AppearanceMenu mode={id} waiting={!neutral} onClose={() => closeAppearance()}/>}
          </div>
          return <button key={id} ref={node => { if (target) entryButtons.current[target] = node }} className={`hotspot ${hovered === id ? 'active' : ''}`} disabled={!enabled} onPointerEnter={() => { if (enabled) setHovered(id) }} onPointerLeave={() => setHovered(null)} onFocus={() => { if (enabled) setHovered(id) }} onBlur={() => setHovered(null)} onClick={target ? () => void transition(target) : undefined} aria-label={target ? systems[target].short : item.label}>
            <span className="hotspot-label">{item.label}{target && <ArrowUpRight size={15}/>}</span>
          </button>
        })}
      </nav>}

      <div className={`intro ${phase !== 'overview' ? 'hide' : ''}`} aria-hidden={phase !== 'overview'}>
        <h1>让想法，成为图纸。</h1><p className="intro-description"><span>导入模型，或从文字与图纸开始。</span><span>选择一个入口，开始你的设计。</span></p>
      </div>

      {phase !== 'overview' && <button ref={closeButton} className="back" onClick={returnToCar} disabled={busy} aria-label="返回功能总览"><ArrowLeft size={18}/><span>{phase === 'returning' ? '正在返回总览' : '返回总览'}</span><kbd>退出键</kbd></button>}

      <aside className={`detail-copy ${phase === 'detail' ? 'shown' : ''}`} aria-hidden={phase !== 'detail'}>
        <div className="detail-introduction">
        <p className="eyebrow">{current.category}</p><h2>{current.title.split('\n').map(line => <span key={line}>{line}</span>)}</h2>
        <p className="system-description">{current.description}</p>
        <a className="workflow-cta detail-cta" href={current.href} tabIndex={phase === 'detail' ? 0 : -1}>{current.cta}<ArrowUpRight size={17}/></a>
        </div>
        <div className="detail-components">
        <h3 className="detail-guide-title">工作流程说明</h3>
        <div className="part-list">{current.points.map((point, i) => <div key={point.label} className="workflow-guide-item">
          <button type="button" tabIndex={phase === 'detail' ? 0 : -1} className={expandedStep === i ? 'selected' : ''} onClick={() => setExpandedStep(expandedStep === i ? null : i)} aria-expanded={expandedStep === i} aria-controls={`workflow-description-${selected}-${i}`}>
            <span className="part-row">{point.label}<Plus size={14}/></span>
          </button>
          <p id={`workflow-description-${selected}-${i}`} className="part-description" hidden={expandedStep !== i}>{point.text}</p>
        </div>)}</div>
        <div className="benefit"><span>在工作区继续</span><p>{current.benefit}</p></div>
        </div>
      </aside>
      {(busy || !ready) && <div className="transition-status"><span className="small-dot"/>{!ready ? '正在准备交互场景' : phase === 'returning' ? '正在返回总览' : `正在打开${current.label}说明`}</div>}
    </section>

    <footer className="scene-footer">
      <div className="workflow-section-head"><h2>选择你的起点</h2><p>从输入到交付，每一步都有迹可循。</p></div>
      <nav className="feature-notes" aria-label="直接打开工作区">
        {workflowEntries.map(entry => <Ripple key={entry.id} className="canvas-feature" forceOverlay trigger="hover" amplitude={0.12} refraction={0} dispersion={0} shine={0.3} decay={2.5} interval={0}>
        <a className={`feature-note workflow-entry ${entry.id === 'drive' ? 'workflow-entry-primary' : ''}`} href={entry.href}>
          <div className="workflow-entry-top"><span className="workflow-number" aria-hidden="true">{entry.index}</span><span className="workflow-input">{entry.input}</span></div>
          <h3>{entry.label}</h3><p>{entry.description}</p>
          <span className="workflow-entry-action">{entry.action}<ArrowUpRight size={17}/></span>
        </a></Ripple>)}
      </nav>
      <div className="footer-baseline"><span>汽绘 · 本地设计工作台</span><span>画面为交互示意，实际模型与工程图在工作区查看。</span></div>
    </footer>
    <p className="sr-only" role="status" aria-live="polite">{busy ? `${phase === 'entering' ? '正在打开' : '正在关闭'}${current.label}说明` : phase === 'detail' ? `${current.label}说明。按退出键返回总览。` : '汽绘功能总览。选择一个工作区入口。'}</p>
    {error && <div className="error-message" role="alert">{error}<button onClick={() => setError('')} aria-label="关闭提示"><X size={16}/></button></div>}
  </main>
}
