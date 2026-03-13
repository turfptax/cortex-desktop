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
  total_feeds: number
  total_cleans: number
}

const moodEmoji: Record<string, string> = {
  happy: '\u{1F60A}',
  content: '\u{1F60C}',
  curious: '\u{1F9D0}',
  playful: '\u{1F60E}',
  excited: '\u{1F929}',
  sleepy: '\u{1F634}',
  hungry: '\u{1F62B}',
  calm: '\u{1F60C}',
  confused: '\u{1F615}',
  stubborn: '\u{1F624}',
  neutral: '\u{1F610}',
  uneasy: '\u{1F61F}',
  sad: '\u{1F622}',
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
  const emoji = pet.is_coma ? '\u{1F634}' : moodEmoji[pet.mood] || '\u{1F914}'

  return (
    <div className="p-3 border-t border-border">
      {/* Header: emoji + stage + IQ */}
      <div className="flex items-center gap-2 mb-1.5">
        <span className="text-xl leading-none">{emoji}</span>
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
