import { useEffect, useRef, useCallback } from 'react'

// Match Pi LCD dimensions (2x for retina)
const SCALE = 2
const W = 240 * SCALE
const H = 280 * SCALE

// Game constants (match pong.py, scaled)
const PADDLE_W = 6 * SCALE
const PADDLE_H = 40 * SCALE
const PADDLE_MARGIN = 8 * SCALE
const PADDLE_SPEED = 200 * SCALE
const BALL_SIZE = 6 * SCALE
const BALL_SPEED_INITIAL = 120 * SCALE
const BALL_SPEED_MAX = 220 * SCALE
const BALL_SPEED_INCREMENT = 8 * SCALE
const WIN_SCORE = 5

interface GameState {
  paddle1Y: number
  paddle2Y: number
  ballX: number
  ballY: number
  ballVX: number
  ballVY: number
  score1: number
  score2: number
  gameOver: boolean
  winner: number | null
  ballSpeed: number
  servePause: number
}

function createGame(): GameState {
  return {
    paddle1Y: H / 2,
    paddle2Y: H / 2,
    ballX: W / 2,
    ballY: H / 2,
    ballVX: 0,
    ballVY: 0,
    score1: 0,
    score2: 0,
    gameOver: false,
    winner: null,
    ballSpeed: BALL_SPEED_INITIAL,
    servePause: 0,
  }
}

function resetBall(g: GameState, direction: number) {
  g.ballX = W / 2
  g.ballY = H / 2
  const angle = (Math.random() - 0.5)
  g.ballVX = BALL_SPEED_INITIAL * Math.cos(angle) * direction
  g.ballVY = BALL_SPEED_INITIAL * Math.sin(angle)
  g.ballSpeed = BALL_SPEED_INITIAL
  g.servePause = 0.5
}

function resetGame(g: GameState) {
  g.paddle1Y = H / 2
  g.paddle2Y = H / 2
  g.score1 = 0
  g.score2 = 0
  g.gameOver = false
  g.winner = null
  resetBall(g, 1)
}

function bounceOffPaddle(g: GameState, paddleY: number) {
  const offset = Math.max(-1, Math.min(1, (g.ballY - paddleY) / (PADDLE_H / 2)))
  g.ballSpeed = Math.min(g.ballSpeed + BALL_SPEED_INCREMENT, BALL_SPEED_MAX)
  const angle = offset * Math.PI / 4
  const dir = g.ballVX < 0 ? 1 : -1
  g.ballVX = g.ballSpeed * Math.cos(angle) * dir
  g.ballVY = g.ballSpeed * Math.sin(angle)
}

function tick(g: GameState, dt: number) {
  if (g.gameOver) return
  if (g.servePause > 0) {
    g.servePause -= dt
    return
  }

  g.ballX += g.ballVX * dt
  g.ballY += g.ballVY * dt
  const half = BALL_SIZE / 2

  // Wall bounce
  if (g.ballY - half <= 0) {
    g.ballY = half
    g.ballVY = Math.abs(g.ballVY)
  } else if (g.ballY + half >= H) {
    g.ballY = H - half
    g.ballVY = -Math.abs(g.ballVY)
  }

  // Paddle 1 collision
  const p1x = PADDLE_MARGIN + PADDLE_W
  if (g.ballX - half <= p1x && g.ballVX < 0 &&
      g.paddle1Y - PADDLE_H/2 <= g.ballY && g.ballY <= g.paddle1Y + PADDLE_H/2) {
    g.ballX = p1x + half
    bounceOffPaddle(g, g.paddle1Y)
  }

  // Paddle 2 collision
  const p2x = W - PADDLE_MARGIN - PADDLE_W
  if (g.ballX + half >= p2x && g.ballVX > 0 &&
      g.paddle2Y - PADDLE_H/2 <= g.ballY && g.ballY <= g.paddle2Y + PADDLE_H/2) {
    g.ballX = p2x - half
    bounceOffPaddle(g, g.paddle2Y)
  }

  // Scoring
  if (g.ballX < 0) {
    g.score2++
    if (g.score2 >= WIN_SCORE) { g.gameOver = true; g.winner = 2 }
    else resetBall(g, 1)
  } else if (g.ballX > W) {
    g.score1++
    if (g.score1 >= WIN_SCORE) { g.gameOver = true; g.winner = 1 }
    else resetBall(g, -1)
  }
}

function aiAction(g: GameState): number {
  if (g.ballVX <= 0) return 0
  const diff = g.ballY - g.paddle2Y
  if (Math.abs(diff) < 10 * SCALE) return 0
  if (Math.random() > 0.7) return 0
  return diff > 0 ? 1 : -1
}

function render(ctx: CanvasRenderingContext2D, g: GameState) {
  // Background
  ctx.fillStyle = '#000'
  ctx.fillRect(0, 0, W, H)

  // Center dashed line
  ctx.fillStyle = '#282828'
  for (let y = 0; y < H; y += 14 * SCALE) {
    ctx.fillRect(W / 2 - SCALE, y, 2 * SCALE, 7 * SCALE)
  }

  // Scores
  ctx.fillStyle = '#00cccc'
  ctx.font = `bold ${20 * SCALE}px monospace`
  ctx.textAlign = 'center'
  ctx.fillText(String(g.score1), W / 2 - 40 * SCALE, 30 * SCALE)
  ctx.fillText(String(g.score2), W / 2 + 40 * SCALE, 30 * SCALE)

  // Paddles
  ctx.fillStyle = '#fff'
  ctx.fillRect(PADDLE_MARGIN, g.paddle1Y - PADDLE_H / 2, PADDLE_W, PADDLE_H)
  ctx.fillRect(W - PADDLE_MARGIN - PADDLE_W, g.paddle2Y - PADDLE_H / 2, PADDLE_W, PADDLE_H)

  // Ball
  const bh = BALL_SIZE / 2
  ctx.fillRect(g.ballX - bh, g.ballY - bh, BALL_SIZE, BALL_SIZE)

  // HUD
  ctx.fillStyle = '#282828'
  ctx.font = `${8 * SCALE}px monospace`
  ctx.textAlign = 'left'
  ctx.fillText('Arrow keys / W,S', 4 * SCALE, H - 6 * SCALE)
  ctx.textAlign = 'right'
  ctx.fillText('AI: rule', W - 4 * SCALE, H - 6 * SCALE)

  // Game over overlay
  if (g.gameOver) {
    ctx.fillStyle = 'rgba(0,0,0,0.6)'
    ctx.fillRect(0, H / 2 - 40 * SCALE, W, 80 * SCALE)
    ctx.fillStyle = '#ffb400'
    ctx.font = `bold ${18 * SCALE}px monospace`
    ctx.textAlign = 'center'
    ctx.fillText(g.winner === 1 ? 'You win!' : 'AI wins!', W / 2, H / 2 - 5 * SCALE)
    ctx.fillStyle = '#fff'
    ctx.font = `${10 * SCALE}px monospace`
    ctx.fillText('Press Space to restart', W / 2, H / 2 + 20 * SCALE)
  }
}

export function PongCanvas() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const gameRef = useRef<GameState>(createGame())
  const keysRef = useRef<Set<string>>(new Set())
  const rafRef = useRef<number>(0)
  const lastTimeRef = useRef<number>(0)

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (['ArrowUp', 'ArrowDown', 'w', 's', ' '].includes(e.key)) {
      e.preventDefault()
      keysRef.current.add(e.key)
      if (e.key === ' ' && gameRef.current.gameOver) {
        resetGame(gameRef.current)
      }
    }
  }, [])

  const handleKeyUp = useCallback((e: KeyboardEvent) => {
    keysRef.current.delete(e.key)
  }, [])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    resetGame(gameRef.current)

    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('keyup', handleKeyUp)

    const loop = (timestamp: number) => {
      const dt = lastTimeRef.current ? Math.min((timestamp - lastTimeRef.current) / 1000, 0.05) : 1 / 60
      lastTimeRef.current = timestamp

      const g = gameRef.current
      const keys = keysRef.current

      if (!g.gameOver) {
        // Player paddle
        let playerDir = 0
        if (keys.has('ArrowUp') || keys.has('w')) playerDir = -1
        if (keys.has('ArrowDown') || keys.has('s')) playerDir = 1
        g.paddle1Y = Math.max(PADDLE_H / 2, Math.min(H - PADDLE_H / 2, g.paddle1Y + playerDir * PADDLE_SPEED * dt))

        // AI paddle
        const ai = aiAction(g)
        g.paddle2Y = Math.max(PADDLE_H / 2, Math.min(H - PADDLE_H / 2, g.paddle2Y + ai * PADDLE_SPEED * dt))

        tick(g, dt)
      }

      render(ctx, g)
      rafRef.current = requestAnimationFrame(loop)
    }

    rafRef.current = requestAnimationFrame(loop)

    return () => {
      cancelAnimationFrame(rafRef.current)
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('keyup', handleKeyUp)
    }
  }, [handleKeyDown, handleKeyUp])

  return (
    <div className="flex flex-col items-center gap-4">
      <canvas
        ref={canvasRef}
        width={W}
        height={H}
        className="border border-border rounded-lg"
        style={{ width: W / 2, height: H / 2 }}
        tabIndex={0}
      />
      <p className="text-xs text-text-muted">
        Arrow keys or W/S to move paddle &middot; Space to restart
      </p>
    </div>
  )
}
