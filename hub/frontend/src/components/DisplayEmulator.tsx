import { useRef, useEffect } from 'react'
import { type PetStatus } from './PetWidget'

/**
 * Pixel-perfect 240x280 emulation of the Cortex Pi ST7789 display.
 * Renders the cyberpunk UI with circuit traces, neon bars, vitals icons,
 * and sprite — matching tamagotchi_display.py on the Pi.
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

// ── Vital icon polygon points (16x20 relative, matching tamagotchi_display.py) ──
const ICON_FORK: [number, number][] = [
  [4, 0], [4, 8], [7, 8], [7, 0], [9, 0], [9, 8], [12, 8],
  [12, 0], [14, 0], [14, 10], [10, 12], [10, 20], [6, 20],
  [6, 12], [2, 10], [2, 0],
]
const ICON_DROP: [number, number][] = [
  [8, 0], [13, 8], [14, 12], [13, 15], [11, 18], [8, 20],
  [5, 18], [3, 15], [2, 12], [3, 8],
]
const ICON_BOLT: [number, number][] = [
  [9, 0], [3, 10], [7, 10], [5, 20], [13, 8], [9, 8], [11, 0],
]
const ICON_HEART: [number, number][] = [
  [8, 4], [5, 0], [2, 0], [0, 2], [0, 6], [2, 10],
  [8, 18], [14, 10], [16, 6], [16, 2], [14, 0], [11, 0],
]

const VITAL_LOW = 0.30
const VITAL_CRITICAL = 0.15

// ── Voxel point cloud (procedural, matching voxel_animator.py aesthetic) ──

interface Voxel {
  x: number; y: number; z: number
  r: number; g: number; b: number; a: number
  size: number
  lora: number  // 0-255, how much this voxel is LoRA-affected
}

// Mood params matching voxel_animator.py
const VOXEL_MOODS: Record<string, { rot: number; bright: number; tint: RGB; pulse: number }> = {
  happy:   { rot: 0.035, bright: 1.1, tint: [0, 40, 0],   pulse: 0.02 },
  content: { rot: 0.028, bright: 1.0, tint: [0, 20, 0],   pulse: 0.015 },
  neutral: { rot: 0.020, bright: 0.9, tint: [0, 0, 0],    pulse: 0.01 },
  uneasy:  { rot: 0.040, bright: 0.85, tint: [0, 20, 30], pulse: 0.03 },
  sad:     { rot: 0.010, bright: 0.6, tint: [0, 0, 30],   pulse: 0.008 },
}

// Seeded pseudo-random for deterministic voxel generation
function seededRandom(seed: number) {
  let s = seed
  return () => {
    s = (s * 1664525 + 1013904223) & 0xFFFFFFFF
    return (s >>> 0) / 0xFFFFFFFF
  }
}

// Generate 350 voxels in a brain-like shape with LoRA clusters
function generateVoxels(): Voxel[] {
  const rng = seededRandom(42)
  const voxels: Voxel[] = []
  const N = 350

  for (let i = 0; i < N; i++) {
    // Spheroid distribution with some organic clustering
    const theta = rng() * Math.PI * 2
    const phi = Math.acos(2 * rng() - 1)
    const baseR = 30 + rng() * 14

    // Elongate slightly on Y axis (brain-like)
    const x = baseR * Math.sin(phi) * Math.cos(theta) + 48
    const y = baseR * 0.85 * Math.cos(phi) + 48
    const z = baseR * Math.sin(phi) * Math.sin(theta) + 48

    // LoRA clusters: ~30% of points are LoRA-affected, clustered together
    const distFromCenter = Math.sqrt((x - 48) ** 2 + (y - 48) ** 2 + (z - 48) ** 2)
    const clusterAngle = Math.atan2(z - 48, x - 48)
    // Two LoRA cluster regions
    const inCluster1 = clusterAngle > 0.5 && clusterAngle < 2.0 && distFromCenter > 20
    const inCluster2 = clusterAngle < -1.5 && distFromCenter > 15 && y > 40
    const isLora = (inCluster1 || inCluster2) ? Math.min(255, Math.round(rng() * 255)) : 0

    // Base color: dim gray for base model, orange→purple for LoRA
    let r: number, g: number, b: number
    if (isLora > 0) {
      // LoRA gradient: orange (high lora) to purple (medium lora)
      const t = isLora / 255
      r = Math.round(200 * t + 120 * (1 - t))
      g = Math.round(80 * t + 20 * (1 - t))
      b = Math.round(40 * (1 - t) + 180 * (1 - t * 0.5))
    } else {
      // Base model: dim gray with slight variation
      const v = 40 + Math.round(rng() * 30)
      r = v; g = v; b = v + 10
    }

    voxels.push({
      x, y, z,
      r, g, b, a: 180 + Math.round(rng() * 75),
      size: 1.5 + rng() * 3,
      lora: isLora,
    })
  }
  return voxels
}

// Singleton: generate once
let _voxelCache: Voxel[] | null = null
function getVoxels(): Voxel[] {
  if (!_voxelCache) _voxelCache = generateVoxels()
  return _voxelCache
}

function drawVoxelCloud(
  ctx: CanvasRenderingContext2D,
  cx: number, cy: number,
  frame: number,
  mood: string,
  opacity = 1.0,
) {
  const voxels = getVoxels()
  const moodParams = VOXEL_MOODS[mood] || VOXEL_MOODS.neutral
  const angle = frame * moodParams.rot
  const cosA = Math.cos(angle)
  const sinA = Math.sin(angle)
  const pulse = 0.5 + 0.5 * Math.sin(frame * moodParams.pulse)
  const bright = moodParams.bright
  const [tintR, tintG, tintB] = moodParams.tint

  // Project and depth-sort
  const projected: { sx: number; sy: number; rz: number; v: Voxel }[] = []
  const scale = SPRITE_SIZE / 110

  for (const v of voxels) {
    const dx = v.x - 48
    const dz = v.z - 48
    const rx = dx * cosA - dz * sinA + 48
    const rz = dx * sinA + dz * cosA + 48

    const sx = (rx - 48) * scale + SPRITE_SIZE / 2
    const sy = (v.y - 48) * scale + SPRITE_SIZE / 2

    if (sx >= -4 && sx <= SPRITE_SIZE + 4 && sy >= -4 && sy <= SPRITE_SIZE + 4) {
      projected.push({ sx, sy, rz, v })
    }
  }

  // Sort back-to-front
  projected.sort((a, b) => a.rz - b.rz)

  const zMin = projected.length > 0 ? projected[0].rz : 0
  const zMax = projected.length > 0 ? projected[projected.length - 1].rz : 1
  const zRange = Math.max(zMax - zMin, 1)

  // Offset to center in the pet zone
  const offX = cx - SPRITE_SIZE / 2
  const offY = cy - SPRITE_SIZE / 2

  for (const { sx, sy, rz, v } of projected) {
    const depthT = (rz - zMin) / zRange
    const depthBright = 0.4 + 0.6 * depthT

    let loraPulse = 1.0
    if (v.lora > 0) {
      loraPulse = 1.0 + 0.3 * pulse * (v.lora / 255)
    }

    const mult = bright * depthBright * loraPulse * opacity
    const cr = Math.min(255, Math.round(v.r * mult + tintR * pulse))
    const cg = Math.min(255, Math.round(v.g * mult + tintG * pulse))
    const cb = Math.min(255, Math.round(v.b * mult + tintB * pulse))
    const ca = Math.round(v.a * opacity)

    const radius = Math.max(1, v.size * scale * 0.5)
    const px = offX + sx
    const py = offY + sy

    ctx.fillStyle = `rgba(${cr},${cg},${cb},${ca / 255})`
    if (radius <= 1.2) {
      ctx.fillRect(px, py, 1, 1)
    } else {
      ctx.beginPath()
      ctx.arc(px, py, radius, 0, Math.PI * 2)
      ctx.fill()
    }
  }
}

interface Props {
  petStatus: PetStatus | null
  piOnline: boolean
  bleConnected?: boolean
  useVoxels?: boolean
}

export function DisplayEmulator({ petStatus, piOnline, bleConnected = false, useVoxels = false }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const frameRef = useRef(0)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    // Run at ~4fps for smoother voxel rotation, ~2fps for sprite
    const fps = useVoxels ? 250 : 500
    const interval = setInterval(() => {
      renderFrame(ctx, petStatus, piOnline, bleConnected, frameRef.current++, useVoxels)
    }, fps)

    renderFrame(ctx, petStatus, piOnline, bleConnected, frameRef.current, useVoxels)

    return () => clearInterval(interval)
  }, [petStatus, piOnline, bleConnected, useVoxels])

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
  ctx.strokeStyle = rgb(C.SEPARATOR)
  ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1)
  fillRect(ctx, x + 1, y + 1, w - 2, h - 2, C.BAR_BG)
  const fw = Math.round((w - 2) * Math.max(0, Math.min(1, pct)))
  if (fw > 0) {
    fillRect(ctx, x + 1, y + 1, fw, h - 2, color)
    const bright: RGB = [
      Math.min(255, Math.round(color[0] * 1.3)),
      Math.min(255, Math.round(color[1] * 1.3)),
      Math.min(255, Math.round(color[2] * 1.3)),
    ]
    fillRect(ctx, x + 1, y + 1, fw, 1, bright)
  }
}

// ── Vital icon drawing (matches _draw_vital_icon in tamagotchi_display.py) ──

function drawVitalIcon(
  ctx: CanvasRenderingContext2D,
  cx: number, cy: number,
  iconPts: [number, number][],
  fillPct: number,
  color: RGB, lowColor: RGB,
  flash: boolean,
) {
  const iw = 16, ih = 20
  const ox = cx - iw / 2
  const oy = cy - ih / 2

  fillPct = Math.max(0, Math.min(1, fillPct))

  // Determine color
  let fillColor: RGB
  let outlineColor: RGB
  if (fillPct < VITAL_CRITICAL && flash) {
    fillColor = C.RED
    outlineColor = C.RED
  } else if (fillPct < VITAL_LOW) {
    fillColor = lowColor
    outlineColor = lowColor
  } else {
    fillColor = color
    outlineColor = color
  }

  const dimOutline = dimColor(outlineColor, 0.33)

  // Create an offscreen canvas to use as a mask for fill-from-bottom
  const maskCanvas = document.createElement('canvas')
  maskCanvas.width = iw + 2
  maskCanvas.height = ih + 2
  const maskCtx = maskCanvas.getContext('2d')!

  // Draw the icon polygon on the mask
  maskCtx.fillStyle = 'white'
  maskCtx.beginPath()
  maskCtx.moveTo(iconPts[0][0] + 1, iconPts[0][1] + 1)
  for (let i = 1; i < iconPts.length; i++) {
    maskCtx.lineTo(iconPts[i][0] + 1, iconPts[i][1] + 1)
  }
  maskCtx.closePath()
  maskCtx.fill()

  // Clear above the fill line (unfilled portion)
  const fillY = Math.round(ih * (1 - fillPct))
  if (fillY > 0) {
    maskCtx.clearRect(0, 0, iw + 2, fillY + 1)
  }

  // Read mask pixels and draw filled portion
  const maskData = maskCtx.getImageData(0, 0, iw + 2, ih + 2)
  ctx.fillStyle = rgb(fillColor)
  for (let py = 0; py < ih + 2; py++) {
    for (let px = 0; px < iw + 2; px++) {
      const idx = (py * (iw + 2) + px) * 4
      if (maskData.data[idx] > 128) {
        ctx.fillRect(ox + px - 1, oy + py - 1, 1, 1)
      }
    }
  }

  // Draw outline on top
  ctx.strokeStyle = rgb(dimOutline)
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(ox + iconPts[0][0], oy + iconPts[0][1])
  for (let i = 1; i < iconPts.length; i++) {
    ctx.lineTo(ox + iconPts[i][0], oy + iconPts[i][1])
  }
  ctx.closePath()
  ctx.stroke()

  // Glow line at fill level
  if (fillPct > 0 && fillPct < 1) {
    const lineY = oy + fillY
    const bright: RGB = [
      Math.min(255, Math.round(fillColor[0] * 1.5)),
      Math.min(255, Math.round(fillColor[1] * 1.5)),
      Math.min(255, Math.round(fillColor[2] * 1.5)),
    ]
    ctx.fillStyle = rgb(bright)
    // Draw glow pixels along the fill line where mask is set
    for (let px = 0; px < iw; px++) {
      const mx = px + 1
      const my = fillY + 1
      if (my < ih + 2) {
        const idx = (my * (iw + 2) + mx) * 4
        const idx2 = my + 1 < ih + 2 ? ((my + 1) * (iw + 2) + mx) * 4 : -1
        if (maskData.data[idx] > 128 || (idx2 >= 0 && maskData.data[idx2] > 128)) {
          ctx.fillRect(ox + px, lineY, 1, 1)
        }
      }
    }
  }
}

function drawVitalsIcons(
  ctx: CanvasRenderingContext2D,
  pet: PetStatus,
  y: number,
  flash: boolean,
) {
  const vitals: [typeof ICON_FORK, number, RGB, RGB][] = [
    [ICON_FORK, pet.hunger ?? 1, C.HUNGER, C.HUNGER_LOW],
    [ICON_DROP, pet.cleanliness ?? 1, C.CLEAN, C.CLEAN_LOW],
    [ICON_BOLT, pet.energy ?? 1, C.ENERGY, C.ENERGY_LOW],
    [ICON_HEART, pet.happiness ?? 1, C.PET_HAPPY, C.PET_SAD],
  ]

  const n = vitals.length
  const spacing = W / (n + 1)

  for (let i = 0; i < n; i++) {
    const [icon, value, color, lowColor] = vitals[i]
    const cx = spacing * (i + 1)
    const cy = y + 12
    drawVitalIcon(ctx, cx, cy, icon, value, color, lowColor, flash)
  }
}

// ── Circuit trace background ──

function drawCircuitBackground(ctx: CanvasRenderingContext2D) {
  for (const y of [25, 65, 105, 145]) {
    hLine(ctx, 0, W, PET_Y + y, C.CIRCUIT_PRIMARY)
  }
  for (const x of [40, 120, 200]) {
    fillRect(ctx, x, PET_Y, 1, PET_H, C.CIRCUIT_PRIMARY)
  }
  for (const y of [25, 65, 105, 145]) {
    for (const x of [40, 120, 200]) {
      fillRect(ctx, x - 1, PET_Y + y - 1, 3, 3, C.CIRCUIT_NODE)
    }
  }
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

// ── Sprite drawing (orange cat) ──

function drawSprite(ctx: CanvasRenderingContext2D, cx: number, cy: number,
                    frame: number, opacity = 1.0) {
  const bob = frame % 2 === 0 ? 0 : -2

  const orangeLight: RGB = [255, 190, 50]
  const orangeMid: RGB = [240, 150, 30]
  const orangeDark: RGB = [200, 100, 20]
  const outlineYellow: RGB = [255, 230, 50]
  const eyeColor: RGB = [50, 30, 40]
  const noseColor: RGB = [80, 50, 60]
  const white: RGB = [255, 255, 255]

  // Apply opacity dimming for sleep/coma
  const dim = (c: RGB): RGB => opacity < 1 ? [
    Math.round(c[0] * opacity + C.BG[0] * (1 - opacity)),
    Math.round(c[1] * opacity + C.BG[1] * (1 - opacity)),
    Math.round(c[2] * opacity + C.BG[2] * (1 - opacity)),
  ] : c

  const headR = 18
  const headCy = cy + bob

  // Glow behind pet
  const glowGrad = ctx.createRadialGradient(cx, headCy + 8, 5, cx, headCy + 8, 40)
  glowGrad.addColorStop(0, `rgba(255, 180, 30, ${0.12 * opacity})`)
  glowGrad.addColorStop(1, 'rgba(255, 180, 30, 0)')
  ctx.fillStyle = glowGrad
  ctx.beginPath()
  ctx.arc(cx, headCy + 8, 40, 0, Math.PI * 2)
  ctx.fill()

  // Body
  const bodyTop = headCy + headR - 6
  const bodyW = 30
  const bodyH = 24
  ctx.fillStyle = rgb(dim(orangeDark))
  ctx.beginPath()
  ctx.ellipse(cx, bodyTop + bodyH / 2, bodyW / 2, bodyH / 2, 0, 0, Math.PI * 2)
  ctx.fill()
  ctx.strokeStyle = rgb(dim(outlineYellow))
  ctx.lineWidth = 1.5
  ctx.stroke()

  // Paws
  ctx.fillStyle = rgb(dim(orangeDark))
  ctx.beginPath()
  ctx.ellipse(cx - 8, bodyTop + bodyH - 2, 5, 4, 0, 0, Math.PI * 2)
  ctx.fill()
  ctx.beginPath()
  ctx.ellipse(cx + 8, bodyTop + bodyH - 2, 5, 4, 0, 0, Math.PI * 2)
  ctx.fill()

  // Head
  const headGrad = ctx.createRadialGradient(cx - 4, headCy - 4, 2, cx, headCy, headR)
  headGrad.addColorStop(0, rgb(dim(orangeLight)))
  headGrad.addColorStop(0.7, rgb(dim(orangeMid)))
  headGrad.addColorStop(1, rgb(dim(orangeDark)))
  ctx.fillStyle = headGrad
  ctx.beginPath()
  ctx.arc(cx, headCy, headR, 0, Math.PI * 2)
  ctx.fill()
  ctx.strokeStyle = rgb(dim(outlineYellow))
  ctx.lineWidth = 1.5
  ctx.stroke()

  // Ears
  const earBaseY = headCy - headR + 4
  const earTipY = earBaseY - 14

  ctx.fillStyle = rgb(dim(orangeMid))
  ctx.beginPath()
  ctx.moveTo(cx - headR + 3, earBaseY + 2)
  ctx.lineTo(cx - headR + 10, earBaseY + 2)
  ctx.lineTo(cx - headR + 1, earTipY)
  ctx.closePath()
  ctx.fill()
  ctx.strokeStyle = rgb(dim(outlineYellow))
  ctx.lineWidth = 1.5
  ctx.stroke()

  ctx.fillStyle = rgb(dim(orangeMid))
  ctx.beginPath()
  ctx.moveTo(cx + headR - 10, earBaseY + 2)
  ctx.lineTo(cx + headR - 3, earBaseY + 2)
  ctx.lineTo(cx + headR - 1, earTipY)
  ctx.closePath()
  ctx.fill()
  ctx.strokeStyle = rgb(dim(outlineYellow))
  ctx.lineWidth = 1.5
  ctx.stroke()

  // Inner ears
  ctx.fillStyle = rgb(dim(orangeLight))
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

  // Eyes
  const eyeY = headCy - 1
  const eyeSpread = 8

  for (const ex of [cx - eyeSpread, cx + eyeSpread]) {
    ctx.fillStyle = rgb(dim(eyeColor))
    ctx.beginPath()
    ctx.ellipse(ex, eyeY, 5, 6, 0, 0, Math.PI * 2)
    ctx.fill()

    ctx.fillStyle = rgb(dim(white))
    ctx.beginPath()
    ctx.arc(ex - 1.5, eyeY - 2, 2, 0, Math.PI * 2)
    ctx.fill()

    ctx.fillStyle = `rgba(255, 255, 255, ${0.6 * opacity})`
    ctx.beginPath()
    ctx.arc(ex + 1.5, eyeY + 1.5, 1, 0, Math.PI * 2)
    ctx.fill()
  }

  // Nose
  ctx.fillStyle = rgb(dim(noseColor))
  ctx.beginPath()
  ctx.moveTo(cx - 2.5, headCy + 5)
  ctx.lineTo(cx + 2.5, headCy + 5)
  ctx.lineTo(cx, headCy + 8)
  ctx.closePath()
  ctx.fill()

  // Mouth
  ctx.strokeStyle = rgb(dim(noseColor))
  ctx.lineWidth = 1
  ctx.beginPath()
  ctx.moveTo(cx - 4, headCy + 10)
  ctx.lineTo(cx - 1, headCy + 8)
  ctx.lineTo(cx, headCy + 9)
  ctx.lineTo(cx + 1, headCy + 8)
  ctx.lineTo(cx + 4, headCy + 10)
  ctx.stroke()

  // Tail
  ctx.strokeStyle = rgb(dim(orangeMid))
  ctx.lineWidth = 3
  ctx.lineCap = 'round'
  ctx.beginPath()
  ctx.moveTo(cx + bodyW / 2 - 2, bodyTop + bodyH / 2)
  ctx.quadraticCurveTo(cx + bodyW / 2 + 12, bodyTop - 2, cx + bodyW / 2 + 8, bodyTop - 10)
  ctx.stroke()
  ctx.strokeStyle = rgb(dim(orangeLight))
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(cx + bodyW / 2 + 8, bodyTop - 10)
  ctx.quadraticCurveTo(cx + bodyW / 2 + 5, bodyTop - 14, cx + bodyW / 2 + 3, bodyTop - 12)
  ctx.stroke()
  ctx.lineCap = 'butt'
}

// ── Pet drawing dispatcher (sprite or voxels) ──

function drawPet(
  ctx: CanvasRenderingContext2D,
  cx: number, cy: number,
  frame: number,
  mood: string,
  useVoxels: boolean,
  opacity = 1.0,
) {
  if (useVoxels) {
    drawVoxelCloud(ctx, cx, cy, frame, mood, opacity)
  } else {
    drawSprite(ctx, cx, cy, frame, opacity)
  }
}

// ── Mood color helpers ──

function moodColor(mood: string): RGB {
  if (mood === 'happy' || mood === 'content') return C.PET_HAPPY
  if (mood === 'uneasy' || mood === 'sad') return C.PET_SAD
  return C.PET_NEUTRAL
}

// ── Sleeping state renderer ──

function renderSleeping(ctx: CanvasRenderingContext2D, _pet: PetStatus, frame: number, useVoxels: boolean) {
  drawCircuitBackground(ctx)

  // Dimmed pet
  const sx = W / 2
  const sy = PET_Y + 40 + SPRITE_SIZE / 2
  drawPet(ctx, sx, sy, frame, 'sad', useVoxels, 0.5)

  // Moon + Zzz
  const moonX = sx + SPRITE_SIZE / 2 - 5
  const moonY = sy - SPRITE_SIZE / 2 - 10
  drawText(ctx, '\u263D', moonX, moonY, C.YELLOW, 14)

  const phase = Math.floor(frame / 2) % 3
  for (let i = 0; i <= phase; i++) {
    const zx = moonX + 15 + i * 8
    const zy = moonY - 5 - i * 8
    const fade = 1 - i * 0.25
    const c: RGB = [
      Math.round(C.CYAN[0] * fade),
      Math.round(C.CYAN[1] * fade),
      Math.round(C.CYAN[2] * fade),
    ]
    drawText(ctx, 'z', zx, zy, c, 11)
  }

  // Label
  const label = 'Sleeping peacefully...'
  const lw = measureText(ctx, label, 11)
  drawText(ctx, label, (W - lw) / 2, sy + SPRITE_SIZE / 2 + 6, C.DIM, 11)
}

// ── Coma state renderer ──

function renderComa(ctx: CanvasRenderingContext2D, pet: PetStatus, frame: number, useVoxels: boolean) {
  drawCircuitBackground(ctx)

  // Very dimmed pet
  const sx = W / 2
  const sy = PET_Y + 30 + SPRITE_SIZE / 2
  drawPet(ctx, sx, sy, frame, 'sad', useVoxels, 0.4)

  // "Zzz" particles
  const phase = Math.floor(frame / 2) % 4
  for (let i = 0; i <= Math.min(phase, 2); i++) {
    const zx = sx + SPRITE_SIZE / 2 + i * 10
    const zy = sy - SPRITE_SIZE / 2 - 5 - i * 10
    const fade = 1 - i * 0.3
    const c: RGB = [
      Math.round(C.MAGENTA[0] * fade),
      Math.round(C.MAGENTA[1] * fade),
      Math.round(C.MAGENTA[2] * fade),
    ]
    drawText(ctx, 'z', zx, zy, c, 11)
  }

  // Label
  const label = 'COMA'
  const lw = measureText(ctx, label, 14)
  const flash = frame % 2 === 0
  drawText(ctx, label, (W - lw) / 2, sy + SPRITE_SIZE / 2 + 6, flash ? C.RED : C.DIM, 14)

  // Revival bars
  const barY = sy + SPRITE_SIZE / 2 + 28
  const vitals: [string, number, RGB][] = [
    ['H', pet.hunger ?? 0, C.HUNGER],
    ['C', pet.cleanliness ?? 0, C.CLEAN],
    ['E', pet.energy ?? 0, C.ENERGY],
    ['\u2665', pet.happiness ?? 0, C.PET_HAPPY],
  ]
  const barW = 40
  for (let i = 0; i < vitals.length; i++) {
    const [lbl, val, col] = vitals[i]
    const bx = 10 + i * (barW + 12)
    drawText(ctx, lbl, bx, barY - 2, dimColor(col, 0.5), 11)
    drawNeonBar(ctx, bx + 12, barY, barW, 6, val, val >= 0.30 ? C.GREEN : col)
  }

  // Footer
  hLine(ctx, 0, W, H - 32, C.SEPARATOR)
  drawText(ctx, 'Feed & clean to revive', 30, H - 28, C.DIM, 11)
}

// ── Home state renderer ──

function renderHome(
  ctx: CanvasRenderingContext2D, pet: PetStatus, frame: number, flash: boolean, useVoxels: boolean,
) {
  drawCircuitBackground(ctx)

  // Pet centered
  const mood = pet.mood || 'neutral'
  const sx = W / 2
  const sy = PET_Y + (PET_H - SPRITE_SIZE) / 2 - 15 + SPRITE_SIZE / 2
  drawPet(ctx, sx, sy, frame, mood, useVoxels)

  // Pet name
  const petName = pet.stage_name ? `${pet.stage_name} Pet` : 'Cortex Pet'
  const nameW = measureText(ctx, petName, 14)
  drawText(ctx, petName, (W - nameW) / 2, sy + SPRITE_SIZE / 2 + 4, C.TEXT, 14)

  // Stage + mood label
  const stageName = pet.stage_name || 'Primordial'
  const mc = moodColor(mood)
  const label = `${stageName} \u00b7 ${mood}`
  const labelW = measureText(ctx, label, 11)
  drawText(ctx, label, (W - labelW) / 2, sy + SPRITE_SIZE / 2 + 22, mc, 11)

  // ── Info Bar (y=200-279) ──
  hLine(ctx, 0, W, INFO_Y, C.SEPARATOR)

  // Vitals icons (4 icons with fill-from-bottom)
  drawVitalsIcons(ctx, pet, INFO_Y + 3, flash)

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
  const hints: [string, string, RGB][] = [
    ['[A]', 'Talk', C.CYAN],
    ['[X]', 'Feed', C.HUNGER],
    ['[Y]', 'Clean', C.CLEAN],
  ]
  let hx = 10
  for (const [key, action, color] of hints) {
    drawText(ctx, key, hx, H - 28, color, 11)
    const kw = measureText(ctx, key, 11)
    drawText(ctx, action, hx + kw + 4, H - 28, C.DIM, 11)
    const aw = measureText(ctx, action, 11)
    hx += kw + aw + 16
  }
}

// ── Main render function ──

function renderFrame(ctx: CanvasRenderingContext2D, pet: PetStatus | null,
                     piOnline: boolean, bleConnected: boolean, frame: number,
                     useVoxels = false) {
  // Clear with BG
  fillRect(ctx, 0, 0, W, H, C.BG)

  if (!piOnline || !pet) {
    // Offline state
    drawCircuitBackground(ctx)
    drawText(ctx, 'Pi Offline', W / 2 - 32, PET_Y + 70, C.DIM, 14)
    drawPet(ctx, W / 2, PET_Y + PET_H / 2, frame, 'neutral', useVoxels, 0.3)
    hLine(ctx, 0, W, 19, C.SEPARATOR)
    const now = new Date()
    const timeStr = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`
    drawText(ctx, timeStr, W - 38, 2, C.DIM)
    return
  }

  const flash = Math.floor(frame / 1) % 2 === 0

  // ── Status Bar (y=0-19) ──

  // BLE dot
  if (bleConnected) {
    fillRect(ctx, 6, 2, 12, 12, C.CYAN_DIM)
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

  // Moon icon when sleeping
  if (pet.is_sleeping) {
    drawText(ctx, '\u263D', 22, 1, C.YELLOW, 11)
  }

  // Mood bar (center)
  const mood = pet.mood || 'neutral'
  const mc = moodColor(mood)
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

  // ── Dispatch to state renderer ──
  if (pet.is_coma) {
    renderComa(ctx, pet, frame, useVoxels)
  } else if (pet.is_sleeping) {
    renderSleeping(ctx, pet, frame, useVoxels)
  } else if (pet.is_thinking) {
    // Thinking screen
    drawCircuitBackground(ctx)
    const sx = W / 2
    const sy = PET_Y + 30 + SPRITE_SIZE / 2
    drawPet(ctx, sx, sy, frame, 'neutral', useVoxels)

    const dots = '.'.repeat(1 + (Math.floor(frame / 2) % 3))
    const thinkText = `Thinking${dots}`
    const tw = measureText(ctx, thinkText, 14)
    drawText(ctx, thinkText, (W - tw) / 2, sy + SPRITE_SIZE / 2 + 6, C.PET_NEUTRAL, 14)

    // Footer
    hLine(ctx, 0, W, H - 32, C.SEPARATOR)
    drawText(ctx, '[B] Cancel', 10, H - 28, C.YELLOW, 11)
  } else {
    renderHome(ctx, pet, frame, flash, useVoxels)
  }
}
