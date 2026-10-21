import { useEffect, useRef } from 'react'

type Preview = 'drive' | 'battery'
type Mode = 'overview' | 'entering' | 'detail' | 'returning'
const sources: Record<Preview, [string, string]> = {
  drive: ['/media/hood-hover-forward.mp4', '/media/hood-hover-reverse.mp4'],
  battery: ['/media/battery-hover-forward.mp4', '/media/battery-hover-reverse.mp4'],
}

// Complete each short clip before changing direction. The supplied reverse clips
// have their own timing curves: duration-ratio seeking would not preserve pose.
export function nextPreviewClip(current: Preview | null, desired: Preview | null) {
  if (current === desired) return null
  return current ? { id: current, reverse: true } : desired ? { id: desired, reverse: false } : null
}

export default function HoverVideo({ desired, mode, reduced, onNeutral }: {
  desired: Preview | null; mode: Mode; reduced: boolean; onNeutral?: (neutral: boolean) => void
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const state = useRef({ desired, mode, reduced, onNeutral })
  const reconcile = useRef<() => void>(() => {})
  state.current = { desired, mode, reduced, onNeutral }

  useEffect(() => {
    const surface = canvas.current!
    const context = surface.getContext('2d', { alpha: false })!
    const videos = new Map<string, HTMLVideoElement>()
    let disposed = false
    let settled: Preview | null = null
    let active: HTMLVideoElement | null = null
    let running = false
    let frozen = false
    let failed = false
    let generation = 0
    let raf = 0
    let watchdog = 0
    let cancelFrame: (() => void) | undefined

    for (const [id, pair] of Object.entries(sources)) pair.forEach((src, direction) => {
      const video = document.createElement('video')
      video.muted = true
      video.playsInline = true
      video.preload = 'auto'
      video.src = src
      videos.set(`${id}-${direction}`, video)
    })

    function draw(video: HTMLVideoElement) {
      if (video.readyState < 2 || video.seeking || !video.videoWidth) return
      if (surface.width !== video.videoWidth || surface.height !== video.videoHeight) {
        surface.width = video.videoWidth
        surface.height = video.videoHeight
      }
      context.drawImage(video, 0, 0, surface.width, surface.height)
      surface.style.opacity = '1'
    }

    function stop() {
      generation++
      active?.pause()
      if (active) active.onended = active.onerror = active.onloadeddata = active.onseeked = null
      cancelFrame?.()
      cancelFrame = undefined
      cancelAnimationFrame(raf)
      clearTimeout(watchdog)
      running = false
      active = null
    }

    function update() {
      if (disposed) return
      const props = state.current
      // Freeze the exact visible hover frame while the existing detail crossfade
      // covers it. Reset only once the detail is fully opaque.
      if (props.mode === 'entering') {
        if (!frozen) { stop(); frozen = true }
        return
      }
      if (props.mode === 'detail' || props.reduced) {
        stop(); settled = null; frozen = false
        surface.style.opacity = '0'
        props.onNeutral?.(true)
        return
      }
      if (props.mode !== 'overview' || running || failed) return
      frozen = false
      const next = nextPreviewClip(settled, props.desired)
      if (!next) return
      const video = videos.get(`${next.id}-${next.reverse ? 1 : 0}`)!
      active = video
      running = true
      props.onNeutral?.(false)
      const token = ++generation
      const valid = () => !disposed && generation === token

      function fail() {
        if (!valid()) return
        stop(); failed = true
        state.current.onNeutral?.(true)
        // Keep the last decoded canvas frame. Detail navigation still works.
      }
      function complete() {
        if (!valid()) return
        draw(video)
        stop()
        settled = next!.reverse ? null : next!.id
        state.current.onNeutral?.(settled === null)
        update()
      }
      function paint() {
        if (!valid()) return
        draw(video)
        if ('requestVideoFrameCallback' in video) {
          const handle = video.requestVideoFrameCallback(paint)
          cancelFrame = () => video.cancelVideoFrameCallback(handle)
        } else {
          raf = requestAnimationFrame(paint)
        }
      }
      function start() {
        if (!valid()) return
        video.onseeked = video.onloadeddata = null
        // Don't draw the video during load/seek. The canvas retains the previous
        // frame until this video's first decoded playback frame is delivered.
        if ('requestVideoFrameCallback' in video) {
          const handle = video.requestVideoFrameCallback(paint)
          cancelFrame = () => video.cancelVideoFrameCallback(handle)
        } else raf = requestAnimationFrame(paint)
        video.play().catch(fail)
      }
      function prepare() {
        if (!valid()) return
        video.onloadeddata = null
        if (video.currentTime > 0.001) {
          video.onseeked = start
          video.currentTime = 0
        } else start()
      }
      video.onended = complete
      video.onerror = fail
      watchdog = window.setTimeout(fail, 15000)
      if (video.readyState >= 2) prepare()
      else { video.onloadeddata = prepare; video.load() }
    }

    reconcile.current = update
    update()
    return () => {
      disposed = true
      stop()
      reconcile.current = () => {}
      for (const video of videos.values()) { video.removeAttribute('src'); video.load() }
    }
  }, [])

  useEffect(() => { reconcile.current() }, [desired, mode, reduced])
  return <canvas ref={canvas} className="hover-video" aria-hidden="true" />
}
