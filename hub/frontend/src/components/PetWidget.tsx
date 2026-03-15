export interface PetStatus {
  stage: number
  stage_name: string
  mood: string
  mood_score: number
  interaction_count: number
  enabled: boolean
  model_loaded: boolean
  is_thinking: boolean
  // Tamagotchi vitals
  hunger: number
  cleanliness: number
  energy: number
  happiness: number
  intelligence: number
  is_coma: boolean
  is_sleeping: boolean
  total_feeds: number
  total_cleans: number
}

// Mood → eye style for the cat avatar
const moodEyes: Record<string, { shape: string; extra?: string }> = {
  happy: { shape: 'happy' },
  content: { shape: 'happy' },
  curious: { shape: 'wide' },
  playful: { shape: 'wide', extra: 'sparkle' },
  excited: { shape: 'wide', extra: 'sparkle' },
  sleepy: { shape: 'closed' },
  hungry: { shape: 'sad' },
  calm: { shape: 'normal' },
  confused: { shape: 'wide' },
  stubborn: { shape: 'narrow' },
  neutral: { shape: 'normal' },
  uneasy: { shape: 'sad' },
  sad: { shape: 'sad' },
}

function CatAvatar({ mood, size = 32, isComa }: { mood: string; size?: number; isComa?: boolean }) {
  const eyes = isComa ? { shape: 'closed' } : (moodEyes[mood] || moodEyes.neutral)
  const s = size

  // Eye shapes as SVG paths
  const eyeContent = () => {
    const lx = s * 0.36, rx = s * 0.64, ey = s * 0.44
    const r = s * 0.07
    switch (eyes.shape) {
      case 'happy':
        return (
          <>
            <path d={`M${lx-r},${ey} Q${lx},${ey-r*1.5} ${lx+r},${ey}`} fill="none" stroke="#3a2030" strokeWidth={s*0.04} strokeLinecap="round"/>
            <path d={`M${rx-r},${ey} Q${rx},${ey-r*1.5} ${rx+r},${ey}`} fill="none" stroke="#3a2030" strokeWidth={s*0.04} strokeLinecap="round"/>
          </>
        )
      case 'closed':
        return (
          <>
            <line x1={lx-r} y1={ey} x2={lx+r} y2={ey} stroke="#3a2030" strokeWidth={s*0.04} strokeLinecap="round"/>
            <line x1={rx-r} y1={ey} x2={rx+r} y2={ey} stroke="#3a2030" strokeWidth={s*0.04} strokeLinecap="round"/>
          </>
        )
      case 'sad':
        return (
          <>
            <ellipse cx={lx} cy={ey} rx={r} ry={r*1.2} fill="#3a2030"/>
            <circle cx={lx-r*0.3} cy={ey-r*0.5} r={r*0.4} fill="white" opacity={0.9}/>
            <ellipse cx={rx} cy={ey} rx={r} ry={r*1.2} fill="#3a2030"/>
            <circle cx={rx-r*0.3} cy={ey-r*0.5} r={r*0.4} fill="white" opacity={0.9}/>
          </>
        )
      case 'narrow':
        return (
          <>
            <ellipse cx={lx} cy={ey} rx={r} ry={r*0.5} fill="#3a2030"/>
            <ellipse cx={rx} cy={ey} rx={r} ry={r*0.5} fill="#3a2030"/>
          </>
        )
      case 'wide':
        return (
          <>
            <ellipse cx={lx} cy={ey} rx={r*1.1} ry={r*1.4} fill="#3a2030"/>
            <circle cx={lx-r*0.3} cy={ey-r*0.5} r={r*0.45} fill="white" opacity={0.9}/>
            <circle cx={lx+r*0.3} cy={ey+r*0.3} r={r*0.25} fill="white" opacity={0.5}/>
            <ellipse cx={rx} cy={ey} rx={r*1.1} ry={r*1.4} fill="#3a2030"/>
            <circle cx={rx-r*0.3} cy={ey-r*0.5} r={r*0.45} fill="white" opacity={0.9}/>
            <circle cx={rx+r*0.3} cy={ey+r*0.3} r={r*0.25} fill="white" opacity={0.5}/>
          </>
        )
      default: // normal
        return (
          <>
            <ellipse cx={lx} cy={ey} rx={r} ry={r*1.1} fill="#3a2030"/>
            <circle cx={lx-r*0.3} cy={ey-r*0.4} r={r*0.4} fill="white" opacity={0.9}/>
            <ellipse cx={rx} cy={ey} rx={r} ry={r*1.1} fill="#3a2030"/>
            <circle cx={rx-r*0.3} cy={ey-r*0.4} r={r*0.4} fill="white" opacity={0.9}/>
          </>
        )
    }
  }

  return (
    <svg viewBox={`0 0 ${s} ${s}`} width={s} height={s} className="shrink-0">
      {/* Body */}
      <ellipse cx={s*0.5} cy={s*0.78} rx={s*0.28} ry={s*0.18} fill="#c86414"/>
      {/* Paws */}
      <ellipse cx={s*0.38} cy={s*0.92} rx={s*0.08} ry={s*0.05} fill="#c86414"/>
      <ellipse cx={s*0.62} cy={s*0.92} rx={s*0.08} ry={s*0.05} fill="#c86414"/>
      {/* Head */}
      <circle cx={s*0.5} cy={s*0.42} r={s*0.28} fill="url(#catGrad)" stroke="#ffe632" strokeWidth={s*0.02}/>
      {/* Left ear */}
      <polygon points={`${s*0.22},${s*0.28} ${s*0.35},${s*0.22} ${s*0.24},${s*0.08}`} fill="#e09628" stroke="#ffe632" strokeWidth={s*0.015}/>
      <polygon points={`${s*0.245},${s*0.25} ${s*0.33},${s*0.22} ${s*0.26},${s*0.12}`} fill="#ffbe32" opacity={0.7}/>
      {/* Right ear */}
      <polygon points={`${s*0.65},${s*0.22} ${s*0.78},${s*0.28} ${s*0.76},${s*0.08}`} fill="#e09628" stroke="#ffe632" strokeWidth={s*0.015}/>
      <polygon points={`${s*0.67},${s*0.22} ${s*0.755},${s*0.25} ${s*0.74},${s*0.12}`} fill="#ffbe32" opacity={0.7}/>
      {/* Eyes */}
      {eyeContent()}
      {/* Nose */}
      <polygon points={`${s*0.47},${s*0.52} ${s*0.53},${s*0.52} ${s*0.5},${s*0.56}`} fill="#50323c"/>
      {/* Mouth */}
      <path d={`M${s*0.44},${s*0.59} Q${s*0.47},${s*0.56} ${s*0.5},${s*0.58} Q${s*0.53},${s*0.56} ${s*0.56},${s*0.59}`} fill="none" stroke="#50323c" strokeWidth={s*0.02} strokeLinecap="round"/>
      {/* Tail */}
      <path d={`M${s*0.72},${s*0.72} Q${s*0.88},${s*0.55} ${s*0.82},${s*0.4}`} fill="none" stroke="#e09628" strokeWidth={s*0.06} strokeLinecap="round"/>
      {/* Gradient definition */}
      <defs>
        <radialGradient id="catGrad" cx="40%" cy="35%" r="60%">
          <stop offset="0%" stopColor="#ffbe32"/>
          <stop offset="70%" stopColor="#f09620"/>
          <stop offset="100%" stopColor="#c86414"/>
        </radialGradient>
      </defs>
    </svg>
  )
}

function VitalBar({ label, value, color, lowColor }: {
  label: string
  value: number
  color: string
  lowColor: string
}) {
  const pct = Math.min(100, Math.max(0, value * 100))
  const isLow = value < 0.3
  const isCritical = value < 0.15
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[9px] text-text-muted w-[14px]">{label}</span>
      <div className="flex-1 h-[3px] bg-surface-tertiary rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${isCritical ? 'animate-pulse' : ''}`}
          style={{
            width: `${pct}%`,
            backgroundColor: isLow ? lowColor : color,
          }}
        />
      </div>
      <span className="text-[8px] text-text-muted w-[22px] text-right">{Math.round(pct)}%</span>
    </div>
  )
}

export function PetWidget({ pet }: { pet: PetStatus }) {
  return (
    <div className="p-3 border-t border-border">
      {/* Header: cat avatar + stage + IQ */}
      <div className="flex items-center gap-2 mb-1.5">
        <CatAvatar mood={pet.mood} size={32} isComa={pet.is_coma} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-text-primary">
              {pet.stage_name || `Stage ${pet.stage}`}
            </span>
            <span className="text-[10px] text-purple-400">
              IQ {Math.round(pet.intelligence ?? 0)}
            </span>
          </div>
          <span className="text-[10px] text-text-muted capitalize">{pet.mood}</span>
        </div>
      </div>

      {/* Coma warning */}
      {pet.is_coma && (
        <div className="bg-red-500/15 text-red-400 text-[10px] px-2 py-1 rounded mb-1.5 text-center">
          Pet is in a coma! Feed &amp; clean to revive.
        </div>
      )}

      {/* Vital bars */}
      <div className="space-y-0.5 mb-1">
        <VitalBar label="H" value={pet.hunger ?? 1} color="#ffa000" lowColor="#ff5000" />
        <VitalBar label="C" value={pet.cleanliness ?? 1} color="#00a0ff" lowColor="#785028" />
        <VitalBar label="E" value={pet.energy ?? 1} color="#ffdc00" lowColor="#ff3c00" />
        <VitalBar label="☺" value={pet.happiness ?? 0.5} color="#00c864" lowColor="#b46428" />
      </div>

      {/* Footer stats */}
      <div className="flex justify-between text-[10px] text-text-muted">
        <span>{pet.interaction_count} chats</span>
        {pet.is_thinking && <span className="text-accent">thinking...</span>}
      </div>
    </div>
  )
}
