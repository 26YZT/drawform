// Deliberate DrawForm adaptation: do not regenerate the upstream manifest.
import { readFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const raw = await readFile('reference-manifest.json')
if (hash(raw) !== 'f9b4c85f3a5394a9019ef86198ebef5e30e49c35aa557964438426bcf9ff17d8') throw Error('Pinned upstream manifest changed')
const manifest = JSON.parse(raw)
const adapted = new Set(['index.html', 'src/App.tsx', 'src/content.ts', 'src/AppearanceMenu.tsx'])
let count = 0
for (const entry of manifest.files) {
  if (adapted.has(entry.path)) continue
  let bytes = await readFile(entry.path)
  if (entry.text) bytes = Buffer.from(bytes.toString('utf8').replace(/\r\n/g, '\n'))
  if (entry.path === 'src/index.css') {
    // The requested Chinese font replaces exactly the import and root stack.
    // Continue checking every upstream layout/motion rule against its hash.
    let css = bytes.toString('utf8')
    css = css.replace("font-family:'PingFang SC','Microsoft YaHei',system-ui,sans-serif;", "font-family:'Space Grotesk',sans-serif;")
    bytes = Buffer.from("@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400&display=swap');\n" + css)
  }
  if (hash(bytes) !== entry.sha256) throw Error(`Protected release file changed: ${entry.path}`)
  count++
}
console.log(`PASS: ${count - 1} unchanged release files + original layout CSS with the declared font-only replacement; four presentation files intentionally adapted.`)
