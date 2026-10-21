// Exercise the DrawForm scene's workflow adapters without a browser or dependencies.
// Real layout, video playback and navigation are verified separately in the browser.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
const states = [], refs = [], effects = []
const deferredFocusEvents = []
const timers = new Map(), listeners = new Map()
const document = { activeElement: null }
let stateIndex = 0, refIndex = 0, nextTimer = 0, tree
const jsx = (type, props) => ({ type, props })
const schedule = (fn, delay = 0) => { const id = ++nextTimer; timers.set(id, { fn, delay }); return id }
const clear = id => timers.delete(id)
const window = {
  setTimeout: schedule,
  addEventListener: (name, fn) => listeners.set(name, fn),
  removeEventListener: (name, fn) => { if (listeners.get(name) === fn) listeners.delete(name) },
}
const react = {
  useState(initial) {
    const i = stateIndex++
    if (!(i in states)) states[i] = typeof initial === 'function' ? initial() : initial
    return [states[i], value => { states[i] = typeof value === 'function' ? value(states[i]) : value }]
  },
  useRef(value) { return refs[refIndex++] ||= { current: value } },
  useEffect(fn) { effects.push(fn) },
}
function compile(file, customRequire) {
  const exports = {}
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: {
    target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText
  vm.runInNewContext(code, { exports, require: customRequire, window, document,
    setTimeout: schedule, clearTimeout: clear, Set })
  return exports
}
const appearance = compile('src/appearance.ts')
const content = compile('src/content.ts')
const requireForScene = name => {
  if (name === 'react') return react
  if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx }
  if (name === 'lucide-react') return { Plus: 'Plus', X: 'X', ArrowLeft: 'ArrowLeft', ArrowUpRight: 'ArrowUpRight' }
  if (name === './appearance') return appearance
  if (name === './content') return content
  if (name === './HoverVideo') return { default: 'HoverVideo' }
  if (name === './AppearanceMenu') return { default: 'AppearanceMenu' }
  if (name === './components/canvasui/Ripple') return { Ripple: 'Ripple' }
  if (name === './drawform.css') return {}
  throw Error(`Unexpected import: ${name}`)
}
const App = compile('src/App.tsx', requireForScene).default
const AppearanceMenu = compile('src/AppearanceMenu.tsx', requireForScene).default
function nodes(node = tree, result = []) {
  if (!node || typeof node !== 'object') return result
  if (Array.isArray(node)) { node.forEach(child => nodes(child, result)); return result }
  result.push(node)
  nodes(node.props?.children ?? null, result)
  return result
}
const find = (predicate, root = tree) => nodes(root).find(predicate)
const button = label => find(n => n.type === 'button' && n.props['aria-label'] === label)
const menu = () => find(n => n.type === 'AppearanceMenu')
const video = () => find(n => n.type === 'HoverVideo')
const dockOwner = label => find(n => n.type === 'div' && n.props.className?.includes('appearance-hotspot') && find(child => child.type === 'button' && child.props['aria-label'] === label, n))
const links = (root = tree) => nodes(root).filter(n => n.type === 'a')
function render() {
  stateIndex = refIndex = 0
  effects.length = 0
  tree = App()
  // Simulate DOM refs followed by the focus-restoration effect after commit.
  nodes().filter(n => n.type === 'button' && n.props.ref).forEach(n => {
    const element = {
      label: n.props['aria-label'],
      focus() {
        assert.equal(n.props.disabled, false, 'Focus returns only after the button is enabled')
        document.activeElement = element
        // React can replay focus/entry after the closing commit; do not make
        // the test pass solely because the effect's synchronous guard is set.
        if (n.props.onFocus) deferredFocusEvents.push(n.props.onFocus)
      },
    }
    if (typeof n.props.ref === 'function') n.props.ref(element)
    else n.props.ref.current = element
  })
  effects[1]()
  effects[4]() // Keep the latest Escape handler, as React's effect does.
  return tree
}
function flushTimers() {
  let count = 0
  while (timers.size) {
    assert.ok(++count < 20, 'Timers must settle')
    const [id, value] = [...timers].sort((a, b) => a[1].delay - b[1].delay)[0]
    timers.delete(id)
    value.fn()
    render()
  }
}
function flushDeferredFocus() {
  while (deferredFocusEvents.length) {
    deferredFocusEvents.shift()()
    render()
  }
}
const expectedRoutes = ['/?tool=step', '/workbench/?mode=text', '/workbench/?mode=drawing', '/workbench/?view=projects']
render()
states[4] = true // Scene images loaded; image/network behavior belongs to browser QA.
render()
const entries = links().filter(n => n.props.className?.includes('workflow-entry'))
assert.equal(entries.length, 4)
assert.deepEqual(entries.map(n => n.props.href), expectedRoutes)
assert.equal(links().find(n => n.props.className === 'header-workspace-link').props.href, expectedRoutes[0])
assert.equal(content.hotspots.length, 4)
assert.equal(content.hotspots[0].id, 'drive', 'The original STEP workflow stays first')
assert.ok(links().every(n => ['/', ...expectedRoutes].includes(n.props.href)), 'Only known local routes are linked')

video().props.onNeutral(false)
button('图纸建模').props.onFocus()
render()
assert.equal(menu().props.mode, 'paint')
assert.equal(menu().props.waiting, true)
assert.equal(video().props.desired, null)
for (const label of ['了解模型转工程图流程', '了解文字建模流程', '项目与交付']) assert.equal(button(label).props.disabled, true)
button('了解模型转工程图流程').props.onClick()
render()
assert.equal(states[0], 'overview', 'Programmatic detail entry respects the menu lock')
const waitingDock = AppearanceMenu(menu().props)
const waitingLink = links(waitingDock)[0]
assert.equal(waitingLink.props['aria-disabled'], true)
assert.equal(waitingLink.props.tabIndex, -1)
let prevented = false
waitingLink.props.onClick({ preventDefault() { prevented = true } })
assert.equal(prevented, true, 'Dock navigation waits for neutral')
assert.equal(waitingLink.props.href, expectedRoutes[2])

video().props.onNeutral(true)
render()
const readyDock = AppearanceMenu(menu().props)
assert.equal(links(readyDock)[0].props['aria-disabled'], undefined)
assert.equal(links(readyDock)[0].props.tabIndex, 0)
button('图纸建模').props.onClick()
render()
assert.equal(states[9], true, 'A click pins the menu for pointer travel')
listeners.get('keydown')({ key: 'Escape' })
// Real browser sequence: keydown -> removed close button blur (null target)
// -> disabled hotspot/removed bridge pointerleave -> timer -> restored focus.
// Also exercise events before React has committed the closing state.
dockOwner('图纸建模').props.onBlur({ currentTarget: { contains: () => false }, relatedTarget: null })
dockOwner('图纸建模').props.onPointerLeave({ currentTarget: { contains: () => false } })
render()
dockOwner('图纸建模').props.onBlur({ currentTarget: { contains: () => false }, relatedTarget: null })
dockOwner('图纸建模').props.onPointerLeave({ currentTarget: { contains: () => false } })
assert.equal(menu(), undefined, 'Closing hides dock content while retaining its lock')
assert.equal(button('项目与交付').props.disabled, true)
flushTimers()
assert.equal(button('项目与交付').props.disabled, false)
assert.equal(document.activeElement.label, '图纸建模', 'Escape restores focus after React commit')
flushDeferredFocus()
assert.equal(menu(), undefined, 'Restored focus must not reopen the dock')
dockOwner('图纸建模').props.onBlur({ currentTarget: { contains: () => false }, relatedTarget: null })
button('图纸建模').props.onFocus()
render()
dockOwner('图纸建模').props.onPointerEnter()
render()
assert.equal(menu(), undefined, 'Delayed focus and stationary-pointer re-entry must not reopen the closed dock')
button('图纸建模').props.onClick()
render()
assert.equal(menu().props.mode, 'paint', 'An explicit click reopens the same dock')
assert.equal(states[9], true)
menu().props.onClose()
render()
flushTimers()
flushDeferredFocus()
dockOwner('图纸建模').props.onBlur({ currentTarget: { contains: () => true }, relatedTarget: {} })
button('图纸建模').props.onFocus()
render()
assert.equal(menu(), undefined, 'Focus moving within the owner must not release passive suppression')
dockOwner('图纸建模').props.onBlur({ currentTarget: { contains: () => false }, relatedTarget: {} })
button('图纸建模').props.onFocus()
render()
assert.equal(menu().props.mode, 'paint', 'A true blur permits a later keyboard entry')
menu().props.onClose()
render()
flushTimers()
flushDeferredFocus()
dockOwner('图纸建模').props.onPointerLeave({ currentTarget: { contains: () => false } })
dockOwner('图纸建模').props.onPointerEnter()
render()
assert.equal(menu().props.mode, 'paint', 'A true pointer leave permits a later pointer entry')
dockOwner('图纸建模').props.onPointerLeave({ currentTarget: { contains: () => false } })
const staleLeave = [...timers.values()].find(value => value.delay === 140).fn
menu().props.onClose()
render()
flushTimers()
flushDeferredFocus()
staleLeave()
render()
assert.equal(states[10], false, 'A stale leave callback cannot lock an already closed scene')

button('项目与交付').props.onFocus()
render()
staleLeave()
render()
assert.equal(menu().props.mode, 'wheels')
assert.equal(states[10], false, 'An old paint leave callback cannot close a different active dock')
assert.equal(button('图纸建模').props.disabled, true)
assert.equal(links(AppearanceMenu(menu().props))[0].props.href, expectedRoutes[3])
button('项目与交付').props.onClick()
render()
button('项目与交付').props.onClick()
render()
flushTimers()
flushDeferredFocus()
assert.equal(menu(), undefined, 'Second click closes the pinned dock')
assert.equal(document.activeElement.label, '项目与交付')

button('了解模型转工程图流程').props.onClick()
render()
assert.equal(video().props.mode, 'entering')
flushTimers()
assert.equal(video().props.mode, 'detail')
const cta = links().find(n => n.props.className === 'workflow-cta detail-cta')
assert.equal(cta.props.href, expectedRoutes[0])
assert.equal(cta.props.tabIndex, 0)
listeners.get('keydown')({ key: 'Escape' })
render()
flushTimers()
assert.equal(video().props.mode, 'overview')
assert.equal(document.activeElement.label, '了解模型转工程图流程')
assert.equal(links().find(n => n.props.className === 'workflow-cta detail-cta').props.tabIndex, -1)
console.log('PASS: four workflow entries, CTA route whitelist, neutral gate, exclusive docks, synchronous closing lock, DOM-removal blur/leave then deferred focus replay, stale leave callbacks, genuine leave/blur and explicit click reopen, original detail transitions')
