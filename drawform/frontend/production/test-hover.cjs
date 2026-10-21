// Non-browser lifecycle regression tests. No additional dependencies.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('typescript')
const refs = [], effects = [], cleanups = [], videos = []
let hook = 0, effectIndex = 0, draws = 0
const neutralEvents = []
const surface = { style: {}, width: 300, height: 150, getContext: () => ({ drawImage: () => draws++ }) }
const react = {
  useRef(value) { const i = hook++; return refs[i] ||= { current: value } },
  useEffect(fn, deps) {
    const i = effectIndex++, old = effects[i]
    if (!old || deps.some((d, n) => old.deps[n] !== d)) effects[i] = { fn, deps, pending: true }
  },
}
class Video {
  readyState = 2; videoWidth = 1916; videoHeight = 1080; currentTime = 0
  seeking = false; plays = 0; callbacks = new Map(); next = 0
  play() { this.plays++; return Promise.resolve() }
  pause() {}
  load() {}
  removeAttribute() {}
  requestVideoFrameCallback(fn) { this.callbacks.set(++this.next, fn); return this.next }
  cancelVideoFrameCallback(id) { this.callbacks.delete(id) }
  frame() { const callbacks = [...this.callbacks.values()]; this.callbacks.clear(); callbacks.forEach(fn => fn()) }
  end() { this.onended?.() }
}
const exportsObject = {}
const code = ts.transpileModule(fs.readFileSync('src/HoverVideo.tsx', 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText
vm.runInNewContext(code, {
  exports: exportsObject,
  require(name) {
    if (name === 'react') return react
    if (name === 'react/jsx-runtime') return { jsx: (_, props) => { props.ref.current = surface } }
    throw Error(name)
  },
  document: { createElement: () => { const v = new Video(); videos.push(v); return v } },
  window: { setTimeout: () => 1 }, clearTimeout() {},
  requestAnimationFrame: () => 1, cancelAnimationFrame() {},
})
function render(desired, mode = 'overview', reduced = false) {
  hook = effectIndex = 0
  exportsObject.default({ desired, mode, reduced, onNeutral: value => neutralEvents.push(value) })
  effects.forEach((effect, i) => {
    if (effect.pending) { effect.pending = false; cleanups[i]?.(); cleanups[i] = effect.fn() }
  })
}
render(null)
const [hoodForward, hoodReverse, batteryForward, batteryReverse] = videos
render('drive')
assert.equal(hoodForward.plays, 1)
assert.equal(neutralEvents.at(-1), false, 'Appearance mode must wait for moving vehicle')
assert.notEqual(surface.style.opacity, '1', 'Do not expose an undecoded frame')
hoodForward.frame()
assert.equal(surface.style.opacity, '1')
render(null)
assert.equal(hoodReverse.plays, 0, 'Do not ratio-seek independently retimed reverse')
hoodForward.end()
assert.equal(hoodReverse.plays, 1)
assert.equal(surface.style.opacity, '1', 'Keep canvas visible at the seam')
render('battery')
hoodReverse.end()
assert.equal(batteryForward.plays, 1, 'Latest desired hover wins after neutral endpoint')
batteryForward.frame()
batteryForward.end()
render(null)
assert.equal(batteryReverse.plays, 1)
batteryReverse.frame()
batteryReverse.end()
assert.equal(neutralEvents.at(-1), true, 'Appearance mode may start at the neutral endpoint')
assert.equal(surface.style.opacity, '1', 'Hold reverse final frame instead of clearing')
render('drive')
hoodForward.frame()
render(null, 'entering')
const frozenDraws = draws
hoodForward.frame()
assert.equal(draws, frozenDraws, 'Freeze hover during detail crossfade')
assert.equal(surface.style.opacity, '1')
render(null, 'detail')
assert.equal(surface.style.opacity, '0', 'Only reset once detail is opaque')
render(null, 'returning')
render(null)
render('battery', 'overview', true)
assert.equal(surface.style.opacity, '0', 'Respect reduced motion')
cleanups.forEach(fn => fn?.())
console.log('PASS: decode gate, endpoint queue, rapid hover switching, reverse hold, detail freeze, reduced motion, cleanup')
