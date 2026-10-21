import { readFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'

const manifest = JSON.parse(await readFile('reference-manifest.json', 'utf8'))
const failed = []
for (const entry of manifest.files) {
  try {
    let bytes = await readFile(entry.path)
    if (entry.text) bytes = Buffer.from(bytes.toString('utf8').replace(/\r\n/g, '\n'))
    const hash = createHash('sha256').update(bytes).digest('hex')
    if (hash !== entry.sha256) failed.push(entry.path)
  } catch { failed.push(entry.path) }
}
if (failed.length) {
  console.error('Reference differs: ' + failed.join(', '))
  console.error('For exact restoration, recover files from v1.0.0. For intentional work, report the change; do not conceal it by regenerating the manifest.')
  process.exitCode = 1
} else console.log(`PASS: ${manifest.files.length} approved source/media/reference files match (text line endings normalized).`)
