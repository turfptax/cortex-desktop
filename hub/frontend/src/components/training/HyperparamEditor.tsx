import { useState, useEffect, useCallback } from 'react'
import { apiFetch } from '../../lib/api'

interface Props {
  config: Record<string, any>
  onSave: (updates: Record<string, any>) => Promise<void>
}

interface FieldDef {
  section: string
  key: string
  label: string
  type: 'number' | 'text' | 'boolean'
  step?: number
  fullWidth?: boolean
}

interface ModelPreset {
  name: string
  params: string
  lora_target_modules: string[]
  max_seq_length: number
  recommended_lora_r: number
  recommended_lora_alpha: number
  dtype: string
  recommended_gguf_type?: string
  notes: string
}

const fields: FieldDef[] = [
  { section: 'training', key: 'epochs', label: 'Epochs', type: 'number' },
  { section: 'training', key: 'batch_size', label: 'Batch Size', type: 'number' },
  {
    section: 'training',
    key: 'learning_rate',
    label: 'Learning Rate',
    type: 'number',
    step: 0.0001,
  },
  {
    section: 'training',
    key: 'gradient_accumulation_steps',
    label: 'Grad Accumulation',
    type: 'number',
  },
  { section: 'training', key: 'warmup_steps', label: 'Warmup Steps', type: 'number' },
  { section: 'training', key: 'weight_decay', label: 'Weight Decay', type: 'number', step: 0.01 },
  { section: 'lora', key: 'r', label: 'LoRA Rank', type: 'number' },
  { section: 'lora', key: 'alpha', label: 'LoRA Alpha', type: 'number' },
  { section: 'lora', key: 'dropout', label: 'LoRA Dropout', type: 'number', step: 0.01 },
  { section: 'lora', key: 'target_modules', label: 'Target Modules', type: 'text', fullWidth: true },
  { section: 'model', key: 'max_seq_length', label: 'Max Seq Length', type: 'number' },
  { section: 'model', key: 'gguf_quantization', label: 'GGUF Quant', type: 'text' },
  { section: 'data', key: 'test_split', label: 'Test Split', type: 'number', step: 0.05 },
]

export function HyperparamEditor({ config, onSave }: Props) {
  const [local, setLocal] = useState<Record<string, any>>({})
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [presets, setPresets] = useState<Record<string, ModelPreset>>({})

  useEffect(() => {
    setLocal(JSON.parse(JSON.stringify(config)))
  }, [config])

  // Fetch model presets on mount
  const fetchPresets = useCallback(async () => {
    try {
      const data = await apiFetch<{ presets: Record<string, ModelPreset> }>('/training/model-presets')
      setPresets(data.presets || {})
    } catch (err) {
      console.error('Failed to fetch model presets:', err)
    }
  }, [])

  useEffect(() => {
    fetchPresets()
  }, [fetchPresets])

  const currentModel = local?.model?.base_model || ''

  const getValue = (section: string, key: string) => {
    const val = local?.[section]?.[key]
    // Display arrays as comma-separated strings
    if (Array.isArray(val)) return val.join(', ')
    return val ?? ''
  }

  const setValue = (section: string, key: string, value: any) => {
    setLocal((prev) => ({
      ...prev,
      [section]: {
        ...prev[section],
        [key]: value,
      },
    }))
    setSaved(false)
  }

  const applyPreset = (modelId: string) => {
    const preset = presets[modelId]
    if (!preset) return

    setLocal((prev) => ({
      ...prev,
      model: {
        ...prev.model,
        base_model: modelId,
        max_seq_length: preset.max_seq_length,
        dtype: preset.dtype,
        gguf_quantization: preset.recommended_gguf_type || 'q8_0',
      },
      lora: {
        ...prev.lora,
        r: preset.recommended_lora_r,
        alpha: preset.recommended_lora_alpha,
        target_modules: preset.lora_target_modules,
      },
    }))
    setSaved(false)
  }

  const handleSave = async () => {
    setSaving(true)
    // Convert target_modules back to array if it's a string
    const toSave = JSON.parse(JSON.stringify(local))
    if (typeof toSave?.lora?.target_modules === 'string') {
      toSave.lora.target_modules = toSave.lora.target_modules
        .split(',')
        .map((s: string) => s.trim())
        .filter(Boolean)
    }
    await onSave(toSave)
    setSaving(false)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  // Group fields by section
  const sections = fields.reduce(
    (acc, f) => {
      if (!acc[f.section]) acc[f.section] = []
      acc[f.section].push(f)
      return acc
    },
    {} as Record<string, FieldDef[]>
  )

  const presetEntries = Object.entries(presets)

  return (
    <div className="p-6 max-w-2xl">
      {/* Model Selector */}
      <div className="mb-6 p-4 rounded-xl bg-surface-alt border border-border">
        <h4 className="text-xs font-medium text-text-muted uppercase mb-3">
          Base Model
        </h4>

        {presetEntries.length > 0 && (
          <div className="grid grid-cols-1 gap-2 mb-3">
            {presetEntries.map(([modelId, preset]) => {
              const isSelected = currentModel === modelId
              return (
                <button
                  key={modelId}
                  onClick={() => applyPreset(modelId)}
                  className={`flex items-center justify-between px-3 py-2.5 rounded-lg text-left transition-colors cursor-pointer ${
                    isSelected
                      ? 'bg-accent/15 border border-accent text-text-primary'
                      : 'bg-surface border border-border text-text-secondary hover:border-text-muted'
                  }`}
                >
                  <div className="flex items-center gap-3">
                    <div
                      className={`w-2 h-2 rounded-full ${
                        isSelected ? 'bg-accent' : 'bg-border'
                      }`}
                    />
                    <div>
                      <div className="text-sm font-medium">{preset.name}</div>
                      <div className="text-xs text-text-muted">{preset.notes}</div>
                    </div>
                  </div>
                  <span className="text-xs font-mono text-text-muted">
                    {preset.params}
                  </span>
                </button>
              )
            })}
          </div>
        )}

        {/* Custom model ID input */}
        <div>
          <label className="block text-xs text-text-secondary mb-1">
            HuggingFace Model ID
          </label>
          <input
            type="text"
            value={currentModel}
            onChange={(e) => setValue('model', 'base_model', e.target.value)}
            placeholder="e.g. Qwen/Qwen2.5-0.5B-Instruct"
            className="w-full bg-surface text-text-primary text-sm rounded-lg px-3 py-2 border border-border focus:border-accent focus:outline-none font-mono"
          />
          {currentModel && !presets[currentModel] && (
            <p className="text-xs text-warning mt-1">
              Custom model — set LoRA target modules manually below.
            </p>
          )}
        </div>
      </div>

      {/* Hyperparameters */}
      <div className="flex items-center justify-between mb-6">
        <h3 className="text-sm font-semibold text-text-primary">
          Hyperparameters
        </h3>
        <button
          onClick={handleSave}
          disabled={saving}
          className={`px-4 py-2 rounded-lg text-xs font-medium transition-colors cursor-pointer ${
            saved
              ? 'bg-success/20 text-success'
              : 'bg-accent text-white hover:bg-accent-hover'
          }`}
        >
          {saving ? 'Saving...' : saved ? 'Saved!' : 'Save Config'}
        </button>
      </div>

      {Object.entries(sections).map(([section, sectionFields]) => (
        <div key={section} className="mb-6">
          <h4 className="text-xs font-medium text-text-muted uppercase mb-3">
            {section}
          </h4>
          <div className="grid grid-cols-2 gap-3">
            {sectionFields.map((field) => (
              <div
                key={`${field.section}-${field.key}`}
                className={field.fullWidth ? 'col-span-2' : ''}
              >
                <label className="block text-xs text-text-secondary mb-1">
                  {field.label}
                </label>
                <input
                  type={field.type === 'number' ? 'number' : 'text'}
                  value={getValue(field.section, field.key)}
                  onChange={(e) =>
                    setValue(
                      field.section,
                      field.key,
                      field.type === 'number'
                        ? parseFloat(e.target.value)
                        : e.target.value
                    )
                  }
                  step={field.step}
                  className={`w-full bg-surface text-text-primary text-sm rounded-lg px-3 py-2 border border-border focus:border-accent focus:outline-none ${
                    field.key === 'target_modules' ? 'font-mono' : ''
                  }`}
                />
              </div>
            ))}
          </div>
        </div>
      ))}

      <p className="text-xs text-text-muted mt-4">
        Changes are saved to cortex-pet-training/config/settings.json and used
        by the next training run.
      </p>
    </div>
  )
}
