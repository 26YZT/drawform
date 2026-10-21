// Exercise App handlers without a browser or added dependencies.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
const states = [], refs = [], timers = [], effects = []
let stateIndex = 0, refIndex = 0, tree
const jsx = (type, props) => ({ type, props })
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
  vm.runInNewContext(code, { exports, require: customRequire, window: { setTimeout: fn => { timers.push(fn); return timers.length } },
    clearTimeout() {}, setTimeout: fn => fn(), document: { activeElement: null }, Set })
  return exports
}
const appearance = compile('src/appearance.ts')
const content = compile('src/content.ts')
const App = compile('src/App.tsx', name => {
  if (name === 'react') return react
  if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx }
  if (name === 'lucide-react') return { Plus: 'Plus', X: 'X', ArrowLeft: 'ArrowLeft', ArrowUpRight: 'ArrowUpRight' }
  if (name === './appearance') return appearance
  if (name === './content') return content
  if (name === './HoverVideo') return { default: 'HoverVideo' }
  if (name === './AppearanceMenu') return { default: 'AppearanceMenu' }
  throw Error(name)
}).default
function render() { stateIndex = refIndex = 0; effects.length = 0; tree = App(); return tree }
function find(predicate, node = tree) {
  if (!node || typeof node !== 'object') return null
  if (Array.isArray(node)) { for (const child of node) { const hit = find(predicate, child); if (hit) return hit } return null }
  if (predicate(node)) return node
  return find(predicate, node.props?.children ?? null)
}
const button = label => find(n => n.type === 'button' && n.props['aria-label'] === label)
const menu = () => find(n => n.type === 'AppearanceMenu')
const video = () => find(n => n.type === 'HoverVideo')
render()
states[4] = true // Initial scene images loaded.
states[14] = new Set(Object.values(appearance.appearanceOptions).flat().map(option => option.image))
render()
video().props.onNeutral(false)
button('Body colour').props.onFocus()
render()
assert.equal(menu().props.mode, 'paint')
assert.equal(menu().props.waiting, true)
assert.equal(button('Explore the battery pack').props.disabled, true)
assert.equal(button('Explore the drive unit').props.disabled, true)
assert.equal(button('Wheel design').props.disabled, true)
assert.equal(video().props.desired, null)
menu().props.onSelect('electric'); render()
assert.equal(menu().props.selected, 'silver', 'Ignore selection while hover returns')
video().props.onNeutral(true); render()
menu().props.onSelect('electric'); render()
assert.equal(menu().props.selected, 'electric')
assert.equal(states[12], '/media/config/electric-green.png')
assert.equal(states[9], true, 'Selection pins the menu')
button('Explore the drive unit').props.onClick(); render()
assert.equal(states[0], 'overview', 'Programmatic click must also respect the lock')
menu().props.onClose(); render()
assert.equal(button('Wheel design').props.disabled, true, 'Remain locked while fading out')
timers.splice(0).forEach(fn => fn()); render()
assert.equal(menu(), null)
assert.equal(button('Wheel design').props.disabled, false)
assert.equal(states[12], appearance.baseExterior)
button('Wheel design').props.onFocus(); render()
assert.equal(menu().props.selected, 'original', 'Modes do not combine')
menu().props.onSelect('aero'); render()
assert.equal(states[12], '/media/config/wheels-aero.png')
assert.equal(button('Body colour').props.disabled, true)
button('Wheel design').props.onClick(); render()
timers.splice(0).forEach(fn => fn()); render()
assert.equal(menu(), null, 'Second click closes pinned mode')
button('Body colour').props.onFocus(); render()
assert.equal(menu().props.selected, 'silver', 'Reopening starts from original')
console.log('PASS: neutral gate, exclusive modes, click guards, pin, close fade lock, reset, no combinations')
