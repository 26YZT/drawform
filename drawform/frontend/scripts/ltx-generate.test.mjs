import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, rm, readFile } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { prepare, runJob } from './ltx-generate.mjs'

const input = { image_uri: 'https://example.org/vehicle.png', prompt: 'A stationary car opens its hood.', model: 'ltx-2-5-fast', duration: 6, resolution: '1920x1080', fps: 24, generate_audio: false }
const response = value => new Response(JSON.stringify(value), { status: 200, headers: { 'Content-Type': 'application/json' } })
const mp4 = Buffer.from([0, 0, 0, 20, 102, 116, 121, 112, 105, 115, 111, 109])

test('preflight validates request without a key or network', async () => {
  assert.deepEqual(await prepare(input), input)
  await assert.rejects(prepare({ ...input, duration: 5 }), /6, 8 or 10/)
  await assert.rejects(prepare({ ...input, fps: 60 }), /supported generation frame rate/)
  await assert.rejects(prepare({ ...input, negative_prompt: 'warping' }), /Unsupported request field/)
})

test('one POST, saved ID, independent output download and no overwrite', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'veyra-ltx-'))
  const directory = path.join(root, 'take')
  const calls = []
  try {
    let polls = 0
    const fetcher = async (url, init) => {
      calls.push({ url, init })
      if (init.method === 'POST') return response({ id: 'test-job' })
      if (url === 'https://example.org/result.mp4') { assert.equal(init.headers, undefined); return new Response(mp4) }
      return response(++polls === 1 ? { status: 'processing' } : { status: 'completed', result: { video_url: 'https://example.org/result.mp4' } })
    }
    await runJob({ payload: input, directory, key: 'fake-test-key', fetcher, wait: async () => {}, log() {} })
    assert.equal(calls.filter(c => c.init.method === 'POST').length, 1)
    assert.equal(JSON.parse(await readFile(path.join(directory, 'job.json'), 'utf8')).id, 'test-job')
    assert.deepEqual(await readFile(path.join(directory, 'video.mp4')), mp4)
    await assert.rejects(runJob({ payload: input, directory, key: 'fake-test-key', fetcher, log() {} }), /EEXIST/)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('interrupted polling resumes using GET and never repeats submission', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'veyra-ltx-'))
  const directory = path.join(root, 'take')
  try {
    await assert.rejects(runJob({ payload: input, directory, key: 'fake', polls: 1, wait: async () => {}, log() {}, fetcher: async (_, init) => response(init.method === 'POST' ? { id: 'resume-job' } : { status: 'pending' }) }), /Polling time limit/)
    await runJob({ directory, key: 'fake', resume: true, wait: async () => {}, log() {}, fetcher: async (url, init) => {
      assert.notEqual(init.method, 'POST')
      return url === 'https://example.org/video.mp4' ? new Response(mp4) : response({ status: 'completed', result: { video_url: 'https://example.org/video.mp4' } })
    } })
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('an uncertain POST is not retried and its take directory stays reserved', async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), 'veyra-ltx-'))
  const directory = path.join(root, 'take')
  let calls = 0
  try {
    await assert.rejects(runJob({ payload: input, directory, key: 'fake', log() {}, fetcher: async () => { calls++; throw new Error('network') } }), /connection failed/)
    assert.equal(calls, 1)
    await assert.rejects(runJob({ directory, key: 'fake', resume: true, log() {} }), /No saved job ID/)
    await assert.rejects(runJob({ payload: input, directory, key: 'fake', log() {} }), /EEXIST/)
  } finally { await rm(root, { recursive: true, force: true }) }
})
