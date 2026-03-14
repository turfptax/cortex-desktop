import { useRef, useEffect } from 'react'
import { type PetStatus } from './PetWidget'

/**
 * Pixel-perfect 240×280 emulation of the Cortex Pi ST7789 display.
 * Renders the cyberpunk UI with circuit traces, neon bars, and sprite placeholder.
 * Scaled to fit the sidebar while maintaining exact pixel proportions.
 */

// ── Cyberpunk color palette (mirrors config.py exactly) ──
const C = {
  BG:              [8, 8, 20] as RGB,
  TEXT:            [220, 230, 240] as RGB,
  DIM:             [60, 70, 90] as RGB,
  RED:             [255, 40, 80] as RGB,
  GREEN:           [0, 255, 140] as RGB,
  YELLOW:          [255, 220, 0] as RGB,
  CYAN:            [0, 255, 255] as RGB,
  CYAN_DIM:        [0, 60, 80] as RGB,
  MAGENTA:         [255, 0, 200] as RGB,
  BAR_BG:          [16, 16, 32] as RGB,
  SEPARATOR:       [20, 30, 50] as RGB,
  CIRCUIT_PRIMARY: [12, 20, 35] as RGB,
  CIRCUIT_NODE:    [16, 28, 45] as RGB,
  SPEECH_BG:       [14, 14, 30] as RGB,
  SPEECH_BORDER:   [0, 255, 255] as RGB,
  XP_BAR:          [0, 200, 255] as RGB,
  XP_BAR_BG:       [16, 16, 32] as RGB,
  PET_HAPPY:       [0, 255, 140] as RGB,
  PET_NEUTRAL:     [0, 200, 255] as RGB,
  PET_SAD:         [255, 60, 100] as RGB,
  HUNGER:          [255, 160, 0] as RGB,
  HUNGER_LOW:      [255, 60, 0] as RGB,
  CLEAN:           [0, 160, 255] as RGB,
  CLEAN_LOW:       [160, 80, 0] as RGB,
  ENERGY:          [255, 255, 0] as RGB,
  ENERGY_LOW:      [255, 40, 40] as RGB,
  IQ:              [200, 0, 255] as RGB,
  HIGHLIGHT:       [20, 30, 60] as RGB,
}

type RGB = [number, number, number]

const W = 240
const H = 280
const PET_Y = 20
const PET_H = 180
const INFO_Y = 200
const SPRITE_SIZE = 80

// ── Stage thresholds ──
const THRESHOLDS = [0, 50, 200, 1000, 5000]

interface Props {
  petStatus: PetStatus | null
  piOnline: boolean
  bleConnected?: boolean
}

export function DisplayEmulator({ petStatus, piOnline, bleConnected = false }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const frameRef = useRef(0)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const draw = () => {
      frameRef.current++
      renderFrame(ctx, petStatus, piOnline, bleConnected, frameRef.current)
      requestAnimationFrame(draw)
    }

    // Run at ~2fps to match Pi display
    const interval = setInterval(() => {
      renderFrame(ctx, petStatus, piOnline, bleConnected, frameRef.current++)
    }, 500)

    // Initial draw
    renderFrame(ctx, petStatus, piOnline, bleConnected, frameRef.current)

    return () => clearInterval(interval)
  }, [petStatus, piOnline, bleConnected])

  return (
    <div className="px-2 pt-2">
      <canvas
        ref={canvasRef}
        width={W}
        height={H}
        className="w-full rounded border border-border"
        style={{ imageRendering: 'pixelated', aspectRatio: `${W}/${H}` }}
      />
    </div>
  )
}

// ── Drawing helpers ──

function rgb(c: RGB): string {
  return `rgb(${c[0]},${c[1]},${c[2]})`
}

function dimColor(c: RGB, factor: number): RGB {
  return [
    Math.round(c[0] * factor),
    Math.round(c[1] * factor),
    Math.round(c[2] * factor),
  ]
}

function fillRect(ctx: CanvasRenderingContext2D, x: number, y: number,
                  w: number, h: number, color: RGB) {
  ctx.fillStyle = rgb(color)
  ctx.fillRect(x, y, w, h)
}

function hLine(ctx: CanvasRenderingContext2D, x1: number, x2: number,
               y: number, color: RGB) {
  fillRect(ctx, x1, y, x2 - x1, 1, color)
}

function drawText(ctx: CanvasRenderingContext2D, text: string, x: number,
                  y: number, color: RGB, size = 11) {
  ctx.fillStyle = rgb(color)
  ctx.font = `${size}px "JetBrains Mono", "DejaVu Sans Mono", monospace`
  ctx.fillText(text, x, y + size)
}

function measureText(ctx: CanvasRenderingContext2D, text: string, size = 11): number {
  ctx.font = `${size}px "JetBrains Mono", "DejaVu Sans Mono", monospace`
  return ctx.measureText(text).width
}

function drawNeonBar(ctx: CanvasRenderingContext2D, x: number, y: number,
                     w: number, h: number, pct: number, color: RGB) {
  // Outline
  ctx.strokeStyle = rgb(C.SEPARATOR)
  ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1)
  // Background
  fillRect(ctx, x + 1, y + 1, w - 2, h - 2, C.BAR_BG)
  // Fill
  const fw = Math.round((w - 2) * Math.max(0, Math.min(1, pct)))
  if (fw > 0) {
    fillRect(ctx, x + 1, y + 1, fw, h - 2, color)
    // Glow highlight (bright top line)
    const bright: RGB = [
      Math.min(255, Math.round(color[0] * 1.3)),
      Math.min(255, Math.round(color[1] * 1.3)),
      Math.min(255, Math.round(color[2] * 1.3)),
    ]
    fillRect(ctx, x + 1, y + 1, fw, 1, bright)
  }
}

// ── Circuit trace background ──

function drawCircuitBackground(ctx: CanvasRenderingContext2D) {
  // Horizontal traces
  for (const y of [25, 65, 105, 145]) {
    hLine(ctx, 0, W, PET_Y + y, C.CIRCUIT_PRIMARY)
  }
  // Vertical traces
  for (const x of [40, 120, 200]) {
    fillRect(ctx, x, PET_Y, 1, PET_H, C.CIRCUIT_PRIMARY)
  }
  // Junction nodes
  for (const y of [25, 65, 105, 145]) {
    for (const x of [40, 120, 200]) {
      fillRect(ctx, x - 1, PET_Y + y - 1, 3, 3, C.CIRCUIT_NODE)
    }
  }
  // Right-angle traces
  const traces = [
    [10, 25, 10, 50, 40, 50],
    [200, 105, 220, 105, 220, 130],
    [40, 145, 40, 165, 75, 165],
    [170, 25, 170, 45, 200, 45],
  ]
  for (const [sx, sy, tx, ty, ex, ey] of traces) {
    ctx.strokeStyle = rgb(C.CIRCUIT_PRIMARY)
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(sx, PET_Y + sy)
    ctx.lineTo(tx, PET_Y + ty)
    ctx.lineTo(ex, PET_Y + ey)
    ctx.stroke()
    fillRect(ctx, tx - 1, PET_Y + ty - 1, 3, 3, C.CIRCUIT_NODE)
  }
}

// ── Sprite drawing (simplified pixel art cat-robot) ──

function drawSprite(ctx: CanvasRenderingContext2D, cx: number, cy: number,
                    _accent: RGB, frame: number) {
  // Orange cat sprite — warm gradient body with big cute eyes
  const bob = frame % 2 === 0 ? 0 : -2

  // Color palette
  const orangeLight: RGB = [255, 190, 50]
  const orangeMid: RGB = [240, 150, 30]
  const orangeDark: RGB = [200, 100, 20]
  const outlineYellow: RGB = [255, 230, 50]
  const eyeColor: RGB = [50, 30, 40]
  const noseColor: RGB = [80, 50, 60]
  const white: RGB = [255, 255, 255]

  const headR = 18
  const headCy = cy + bob

  // Glow behind pet (warm aura)
  const glowGrad = ctx.createRadialGradient(cx, headCy + 8, 5, cx, headCy + 8, 40)
  glowGrad.addColorStop(0, 'rgba(255, 180, 30, 0.12)')
  glowGrad.addColorStop(1, 'rgba(255, 180, 30, 0)')
  ctx.fillStyle = glowGrad
  ctx.beginPath()
  ctx.arc(cx, headCy + 8, 40, 0, Math.PI * 2)
  ctx.fill()

  // Body (egg shape below head)
  const bodyTop = headCy + headR - 6
  const bodyW = 30
  const bodyH = 24
  ctx.fillStyle = rgb(orangeDark)
  ctx.beginPath()
  ctx.ellipse(cx, bodyTop + bodyH / 2, bodyW / 2, bodyH / 2, 0, 0, Math.PI * 2)
  ctx.fill()
  ctx.strokeStyle = rgb(outlineYellow)
  ctx.lineWidth = 1.5
  ctx.stroke()

  // Front paws
  ctx.fillStyle = rgb(orangeDark)
  ctx.beginPath()
  ctx.ellipse(cx - 8, bodyTop + bodyH - 2, 5, 4, 0, 0, Math.PI * 2)
  ctx.fill()
  ctx.beginPath()
  ctx.ellipse(cx + 8, bodyTop + bodyH - 2, 5, 4, 0, 0, Math.PI * 2)
  ctx.fill()

  // Head (large circle)
  const headGrad = ctx.createRadialGradient(cx - 4, headCy - 4, 2, cx, headCy, headR)
  headGrad.addColorStop(0, rgb(orangeLight))
  headGrad.addColorStop(0.7, rgb(orangeMid))
  headGrad.addColorStop(1, rgb(orangeDark))
  ctx.fillStyle = headGrad
  ctx.beginPath()
  ctx.arc(cx, headCy, headR, 0, Math.PI * 2)
  ctx.fill()
  ctx.strokeStyle = rgb(outlineYellow)
  ctx.lineWidth = 1.5
  ctx.stroke()

  // Ears
  const earBaseY = headCy - headR + 4
  const earTipY = earBaseY - 14

  // Left ear
  ctx.fillStyle = rgb(orangeMid)
  ctx.beginPath()
  ctx.moveTo(cx - headR + 3, earBaseY + 2)
  ctx.lineTo(cx - headR + 10, earBaseY + 2)
  ctx.lineTo(cx - headR + 1, earTipY)
  ctx.closePath()
  ctx.fill()
  ctx.strokeStyle = rgb(outlineYellow)
  ctx.lineWidth = 1.5
  ctx.stroke()

  // Right ear
  ctx.fillStyle = rgb(orangeMid)
  ctx.beginPath()
  ctx.moveTo(cx + headR - 10, earBaseY + 2)
  ctx.lineTo(cx + headR - 3, earBaseY + 2)
  ctx.lineTo(cx + headR - 1, earTipY)
  ctx.closePath()
  ctx.fill()
  ctx.strokeStyle = rgb(outlineYellow)
  ctx.lineWidth = 1.5
  ctx.stroke()

  // Inner ears (lighter)
  ctx.fillStyle = rgb(orangeLight)
  ctx.beginPath()
  ctx.moveTo(cx - headR + 5, earBaseY + 2)
  ctx.lineTo(cx - headR + 9, earBaseY + 2)
  ctx.lineTo(cx - headR + 3, earTipY + 4)
  ctx.closePath()
  ctx.fill()
  ctx.beginPath()
  ctx.moveTo(cx + headR - 9, earBaseY + 2)
  ctx.lineTo(cx + headR - 5, earBaseY + 2)
  ctx.lineTo(cx + headR - 3, earTipY + 4)
  ctx.closePath()
  ctx.fill()

  // Eyes (large, dark, cute)
  const eyeY = headCy - 1
  const eyeSpread = 8

  for (const ex of [cx - eyeSpread, cx + eyeSpread]) {
    // Eye white
    ctx.fillStyle = rgb(eyeColor)
    ctx.beginPath()
    ctx.ellipse(ex, eyeY, 5, 6, 0, 0, Math.PI * 2)
    ctx.fill()

    // White glint (top-left)
    ctx.fillStyle = rgb(white)
    ctx.beginPath()
    ctx.arc(ex - 1.5, eyeY - 2, 2, 0, Math.PI * 2)
    ctx.fill()

    // Smaller glint (bottom-right)
    ctx.fillStyle = 'rgba(255, 255, 255, 0.6)'
    ctx.beginPath()
    ctx.arc(ex + 1.5, eyeY + 1.5, 1, 0, Math.PI * 2)
    ctx.fill()
  }

  // Nose (small triangle)
  ctx.fillStyle = rgb(noseColor)
  ctx.beginPath()
  ctx.moveTo(cx - 2.5, headCy + 5)
  ctx.lineTo(cx + 2.5, headCy + 5)
  ctx.lineTo(cx, headCy + 8)
  ctx.closePath()
  ctx.fill()

  // Mouth (small W shape)
  ctx.strokeStyle = rgb(noseColor)
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(cx - 4, headCy + 10)
  ctx.lineTo(cx - 1, headCy + 8)
  ctx.lineTo(cx, headCy + 9)
  ctx.lineTo(cx + 1, headCy + 8)
  ctx.lineTo(cx + 4, headCy + 10)
  ctx.stroke()

  // Tail (curved, right side)
  ctx.strokeStyle = rgb(orangeMid)
  ctx.lineWidth = 3
  ctx.lineCap = 'round'
  ctx.beginPath()
  ctx.moveTo(cx + bodyW / 2 - 2, bodyTop + bodyH / 2)
  ctx.quadraticCurveTo(cx + bodyW / 2 + 12, bodyTop - 2, cx + bodyW / 2 + 8, bodyTop - 10)
  ctx.stroke()
  // Tail tip
  ctx.strokeStyle = rgb(orangeLight)
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(cx + bodyW / 2 + 8, bodyTop - 10)
  ctx.quadraticCurveTo(cx + bodyW / 2 + 5, bodyTop - 14, cx + bodyW / 2 + 3, bodyTop - 12)
  ctx.stroke()
  ctx.lineCap = 'butt'
}

// ── Mood color helper ──

function moodColor(mood: string): RGB {
  if (mood === 'happy' || mood === 'content') return C.PET_HAPPY
  if (mood === 'uneasy' || mood === 'sad') return C.PET_SAD
  return C.PET_NEUTRAL
}

function moodAccent(mood: string): RGB {
  switch (mood) {
    case 'happy':   return [0, 255, 140]
    case 'content': return [0, 220, 255]
    case 'uneasy':  return [255, 180, 0]
    case 'sad':     return [180, 0, 120]
    default:        return [0, 180, 220]
  }
}

// ── Main render function ──

function renderFrame(ctx: CanvasRenderingContext2D, pet: PetStatus | null,
                     piOnline: boolean, bleConnected: boolean, frame: number) {
  // Clear with BG
  fillRect(ctx, 0, 0, W, H, C.BG)

  if (!piOnline || !pet) {
    // Offline state
    drawCircuitBackground(ctx)
    drawText(ctx, 'Pi Offline', W / 2 - 32, PET_Y + 70, C.DIM, 14)
    drawSprite(ctx, W / 2, PET_Y + PET_H / 2, dimColor(C.CYAN, 0.3), frame)
    hLine(ctx, 0, W, 19, C.SEPARATOR)
    const now = new Date()
    const timeStr = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`
    drawText(ctx, timeStr, W - 38, 2, C.DIM)
    return
  }

  const mood = pet.mood || 'neutral'
  const mc = moodColor(mood)
  const accent = moodAccent(mood)
  const flash = Math.floor(frame / 1) % 2 === 0

  // ── Status Bar (y=0-19) ──

  // BLE dot
  if (bleConnected) {
    fillRect(ctx, 6, 2, 12, 12, C.CYAN_DIM) // glow
    ctx.fillStyle = rgb(C.CYAN)
    ctx.beginPath()
    ctx.arc(12, 8, 4, 0, Math.PI * 2)
    ctx.fill()
  } else {
    ctx.fillStyle = rgb(C.CIRCUIT_NODE)
    ctx.beginPath()
    ctx.arc(12, 8, 4, 0, Math.PI * 2)
    ctx.fill()
  }

  // Mood bar (center)
  const moodBarW = 50
  const moodBarX = (W - moodBarW) / 2
  fillRect(ctx, moodBarX, 3, moodBarW, 4, C.BAR_BG)
  const moodFillW = Math.round(((pet.mood_score || 0) + 1) / 2 * moodBarW)
  fillRect(ctx, moodBarX, 3, Math.max(2, Math.min(moodFillW, moodBarW)), 4, mc)

  // Clock
  const now = new Date()
  const timeStr = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`
  drawText(ctx, timeStr, W - 38, 2, C.DIM)

  // Separator
  hLine(ctx, 0, W, 19, C.SEPARATOR)

  // ── Pet Zone (y=20-199) ──
  drawCircuitBackground(ctx)

  // Sprite centered
  const sx = W / 2
  const sy = PET_Y + (PET_H - SPRITE_SIZE) / 2 - 15 + SPRITE_SIZE / 2
  const bob = frame % 2 === 0 ? 0 : -2
  drawSprite(ctx, sx, sy + bob, accent, frame)

  // Pet name
  const petName = 'Cortex Pet'
  const nameW = measureText(ctx, petName, 14)
  drawText(ctx, petName, (W - nameW) / 2, sy + SPRITE_SIZE / 2 + 4 + bob, C.TEXT, 14)

  // Stage · mood label
  const stageName = pet.stage_name || 'Primordial'
  const label = `${stageName} · ${mood}`
  const labelW = measureText(ctx, label, 11)
  drawText(ctx, label, (W - labelW) / 2, sy + SPRITE_SIZE / 2 + 22 + bob, mc, 11)

  // ── Info Bar (y=200-279) ──
  hLine(ctx, 0, W, INFO_Y, C.SEPARATOR)

  // Vitals bars
  const vitals: [string, number, RGB, RGB][] = [
    ['H', pet.hunger ?? 1, C.HUNGER, C.HUNGER_LOW],
    ['C', pet.cleanliness ?? 1, C.CLEAN, C.CLEAN_LOW],
    ['E', pet.energy ?? 1, C.ENERGY, C.ENERGY_LOW],
  ]

  const barW = 88
  const barH = 6
  const positions = [
    [8, INFO_Y + 3],
    [8 + barW + 14 + 16, INFO_Y + 3],
    [8, INFO_Y + 17],
  ]

  for (let i = 0; i < vitals.length; i++) {
    const [lbl, val, colFull, colLow] = vitals[i]
    const [px, py] = positions[i]
    const dimC = dimColor(colFull, 0.5)
    drawText(ctx, lbl, px, py - 1, dimC, 11)
    const bx = px + 14
    let barColor = colFull
    if (val < 0.15 && flash) barColor = C.RED
    else if (val < 0.30) barColor = colLow
    drawNeonBar(ctx, bx, py, barW, barH, val, barColor)
  }

  // XP bar
  const interactions = pet.interaction_count || 0
  let currentThresh = 0
  let nextThresh = THRESHOLDS[THRESHOLDS.length - 1]
  for (let i = 0; i < THRESHOLDS.length; i++) {
    if (interactions >= THRESHOLDS[i]) {
      currentThresh = THRESHOLDS[i]
      if (i + 1 < THRESHOLDS.length) nextThresh = THRESHOLDS[i + 1]
      else nextThresh = THRESHOLDS[i]
    }
  }
  const rangeSize = Math.max(1, nextThresh - currentThresh)
  const xpPct = Math.min((interactions - currentThresh) / rangeSize, 1.0)
  drawNeonBar(ctx, 10, INFO_Y + 30, W - 20, 8, xpPct, C.XP_BAR)
  drawText(ctx, `XP: ${interactions}/${nextThresh}`, 10, INFO_Y + 40, C.DIM, 11)

  // IQ score
  const iq = Math.round(pet.intelligence ?? 0)
  const iqStr = `IQ:${iq}`
  const iqW = measureText(ctx, iqStr, 11)
  drawText(ctx, iqStr, W - iqW - 10, INFO_Y + 30, C.IQ, 11)

  // Footer hints
  hLine(ctx, 0, W, H - 32, C.SEPARATOR)
  const hints = [
    ['[A]', 'Talk', C.CYAN],
    ['[X]', 'Feed', C.HUNGER],
    ['[Y]', 'Clean', C.CLEAN],
  ] as [string, string, RGB][]
  let hx = 10
  for (const [key, action, color] of hints) {
    drawText(ctx, key, hx, H - 28, color, 11)
    const kw = measureText(ctx, key, 11)
    drawText(ctx, action, hx + kw + 4, H - 28, C.DIM, 11)
    const aw = measureText(ctx, action, 11)
    hx += kw + aw + 16
  }
}
