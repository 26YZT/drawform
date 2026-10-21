import { readFile, writeFile, mkdir, access } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

const API = 'https://api.ltx.io/v2/image-to-video'
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms))
const json = value => JSON.stringify(value, null, 2) + '\n'
const exists = async file => { try { await access(file); return true } catch { return false } }

// Local paths are a convenience of this helper, not a field type of the LTX API.
async function imageUri(value, cwd) {
  if (typeof value !== 'string' || !value) throw new Error('Provide an image URI or a local PNG/JPEG/WebP path.')
  if (/^(https:\/\/|ltx:\/\/|data:image\/)/.test(value)) return value
  if (/^[a-z]+:\/\//i.test(value)) throw new Error('Remote images must use HTTPS.')
  const ext = path.extname(value).toLowerCase()
  const mime = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp' }[ext]
  if (!mime) throw new Error('Local input must be PNG, JPEG or WebP.')
  const data = await readFile(path.resolve(cwd, value))
  const uri = `data:${mime};base64,${data.toString('base64')}`
  if (Buffer.byteLength(uri) > 7 * 1024 * 1024) throw new Error('Inline image exceeds 7 MiB. Use an HTTPS or LTX upload URI.')
  return uri
}

export async function prepare(input, cwd = process.cwd()) {
  const allowed = new Set(['image_uri', 'last_frame_uri', 'prompt', 'model', 'duration', 'resolution', 'fps', 'generate_audio', 'camera_motion'])
  for (const key of Object.keys(input)) if (!allowed.has(key)) throw new Error(`Unsupported request field: ${key}`)
  if (typeof input.prompt !== 'string' || !input.prompt.trim() || input.prompt.length > 5000) throw new Error('prompt must contain 1–5000 characters.')
  if (!['ltx-2-5-fast', 'ltx-2-5-pro'].includes(input.model)) throw new Error('This example supports ltx-2-5-fast or ltx-2-5-pro.')
  if (![6, 8, 10].includes(input.duration)) throw new Error('This helper deliberately supports fixed 6, 8 or 10 second requests.')
  if (!['1280x720', '1920x1080'].includes(input.resolution)) throw new Error('This helper supports landscape 720p or 1080p.')
  if (![24, 25, 48, 50].includes(input.fps ?? 24)) throw new Error('Use a supported generation frame rate: 24, 25, 48 or 50.')
  if (input.generate_audio !== undefined && typeof input.generate_audio !== 'boolean') throw new Error('generate_audio must be boolean.')
  const payload = { ...input, fps: input.fps ?? 24, generate_audio: input.generate_audio ?? false, image_uri: await imageUri(input.image_uri, cwd) }
  if (input.last_frame_uri) payload.last_frame_uri = await imageUri(input.last_frame_uri, cwd)
  return payload
}

export function summary(payload) {
  const describe = uri => uri?.startsWith('data:') ? `[inline image: ${Buffer.byteLength(uri)} bytes]` : uri
  return { ...payload, image_uri: describe(payload.image_uri), ...(payload.last_frame_uri ? { last_frame_uri: describe(payload.last_frame_uri) } : {}) }
}

export async function runJob({ payload, directory, key, resume = false, fetcher = fetch, wait = sleep, polls = 180, log = console.log }) {
  if (!key?.trim()) throw new Error('Set LTX_API_KEY in the local environment or .env.ltx.local. Do not paste it into a prompt.')
  const jobPath = path.join(directory, 'job.json')
  const headers = { Authorization: `Bearer ${key}` }
  async function apiJson(url, init, retry = false) {
    for (let attempt = 0; ; attempt++) {
      let response
      try { response = await fetcher(url, { ...init, signal: AbortSignal.timeout(120000), redirect: 'error' }) }
      catch {
        if (retry && attempt < 3) { await wait(5000 * 2 ** attempt); continue }
        throw new Error('LTX connection failed. Check the saved job; do not blindly resubmit a POST.')
      }
      if (retry && (response.status === 429 || response.status >= 500) && attempt < 3) { await wait(5000 * 2 ** attempt); continue }
      if (!response.ok) throw new Error(`LTX HTTP ${response.status}. See docs/LTX_API.md; request details and credentials are not logged.`)
      return response.json()
    }
  }
  let job
  if (resume) {
    if (!await exists(jobPath)) throw new Error('No saved job ID. A previous submission may be uncertain; inspect the console before any new POST.')
    job = JSON.parse(await readFile(jobPath, 'utf8'))
  } else {
    await mkdir(path.dirname(directory), { recursive: true })
    await mkdir(directory) // Exclusive: refuses an existing take, including an uncertain POST.
    await writeFile(path.join(directory, 'request.json'), json(payload), { flag: 'wx' })
    await writeFile(path.join(directory, 'submission-started.json'), json({ at: new Date().toISOString(), endpoint: API }), { flag: 'wx' })
    job = await apiJson(API, { method: 'POST', headers: { ...headers, 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
    if (typeof job.id !== 'string' || !job.id) throw new Error('Submission returned no job ID. Inspect the console before retrying.')
    await writeFile(jobPath, json({ id: job.id, created_at: job.created_at }), { flag: 'wx' })
  }
  if (typeof job.id !== 'string' || !job.id) throw new Error('Invalid saved job ID.')
  log(`Job ${job.id}. Resume directory: ${directory}`)
  for (let i = 0; i < polls; i++) {
    const status = await apiJson(`${API}/${encodeURIComponent(job.id)}`, { headers }, true)
    if (status.status === 'failed') {
      await writeFile(path.join(directory, 'status.json'), json({ id: job.id, status: 'failed' }))
      throw new Error(`Job ${job.id} failed. Review it in the console; no automatic resubmission.`)
    }
    if (status.status === 'completed') {
      const url = new URL(status.result?.video_url)
      if (url.protocol !== 'https:') throw new Error('Expected an HTTPS output URL.')
      let bytes
      for (let attempt = 0; ; attempt++) {
        try {
          // Intentionally no API Authorization on an output storage URL.
          const result = await fetcher(url.href, { signal: AbortSignal.timeout(120000) })
          if (!result.ok) throw new Error('Output download failed.')
          bytes = Buffer.from(await result.arrayBuffer())
          if (bytes.length < 12 || bytes.toString('ascii', 4, 8) !== 'ftyp') throw new Error('Output is not an MP4.')
          break
        } catch {
          if (attempt >= 3) throw new Error('Could not download a valid MP4. Resume this job directory to retry the download.')
          await wait(5000 * 2 ** attempt)
        }
      }
      const output = path.join(directory, 'video.mp4')
      if (await exists(output)) throw new Error('video.mp4 already exists; refusing to overwrite it.')
      await writeFile(output, bytes, { flag: 'wx' })
      await writeFile(path.join(directory, 'status.json'), json({ id: job.id, status: 'completed', bytes: bytes.length }))
      log(`Saved ${output}. Review before copying into public/media.`)
      return output
    }
    if (!['pending', 'processing'].includes(status.status)) throw new Error('Unknown job status. Keep the job ID and check the current LTX docs.')
    await wait(5000 + Math.floor(Math.random() * 1000))
  }
  throw new Error('Polling time limit reached. Resume the same directory; do not submit another generation.')
}

async function main(args) {
  let input, output, resume, submit = false
  for (let i = 0; i < args.length; i++) {
    const arg = args[i]
    if (arg === '--submit') submit = true
    else if (arg === '--out') output = args[++i]
    else if (arg === '--resume') resume = args[++i]
    else if (arg.startsWith('--')) throw new Error(`Unknown option: ${arg}`)
    else if (!input) input = arg
    else throw new Error('Provide only one request JSON file.')
  }
  if (resume && (submit || input || output)) throw new Error('--resume takes only an existing take directory.')
  if (!resume && !input) throw new Error('Usage: node scripts/ltx-generate.mjs examples/ltx/hood-open.json [--submit --out generated/hood-01] OR --resume generated/hood-01')
  const payload = resume ? undefined : await prepare(JSON.parse(await readFile(input, 'utf8')))
  if (!resume && !submit) { console.log('DRY RUN — no HTTP request, API key or charge.'); console.log(json(summary(payload))); return }
  const directory = path.resolve(resume || output || '')
  const generated = path.resolve('generated')
  const relative = path.relative(generated, directory)
  if (!relative || relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) throw new Error('Use a named take directory inside generated/, for example --out generated/hood-01.')
  await runJob({ payload, directory, key: process.env.LTX_API_KEY, resume: Boolean(resume) })
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main(process.argv.slice(2)).catch(error => { console.error(error.message); process.exitCode = 1 })
}
