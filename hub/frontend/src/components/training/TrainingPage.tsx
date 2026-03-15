import { useCallback, useEffect, useState } from 'react'
import { useTraining } from '../../hooks/useTraining'
import { apiFetch } from '../../lib/api'
import { PipelineStep } from './PipelineStep'
import { LogViewer } from './LogViewer'
import { HyperparamEditor } from './HyperparamEditor'
import { DatasetTab } from './DatasetTab'
import { AutoResearchPanel } from './AutoResearchPanel'
import { LearningTab } from './LearningTab'

interface Availability {
  scripts_available: boolean
  is_bundled: boolean
  has_system_python: boolean
  scripts_dir: string
  training_dir_exists: boolean
  available_steps: Record<string, { exists: boolean; path: string }>
  learned_examples: number
}

export function TrainingPage() {
  const {
    steps,
    logLines,
    isRunning,
    config,
    results,
    viewingStepId,
    fetchSteps,
    fetchConfig,
    fetchResults,
    updateConfig,
    runStep,
    stopJob,
    viewStepLogs,
    resetJobs,
    // Auto-Research
    researchLog,
    researchBest,
    isAutoResearching,
    autoResearchLogLines,
    fetchResearchLog,
    startAutoResearch,
    stopAutoResearch,
    clearResearchLog,
  } = useTraining()

  const [activeTab, setActiveTab] = useState<'learning' | 'pipeline' | 'config' | 'dataset' | 'results' | 'research'>(
    'learning'
  )
  const [availability, setAvailability] = useState<Availability | null>(null)

  const fetchAvailability = useCallback(async () => {
    try {
      const res = await apiFetch<Availability & { ok: boolean }>('/training/availability')
      if (res?.ok) setAvailability(res)
    } catch { /* offline */ }
  }, [])

  useEffect(() => {
    fetchSteps()
    fetchConfig()
    fetchResults()
    fetchAvailability()
  }, [fetchSteps, fetchConfig, fetchResults, fetchAvailability])

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-6 py-3 border-b border-border bg-surface-secondary">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-text-primary">
              Training Pipeline
            </h2>
            <p className="text-xs text-text-muted">
              LoRA fine-tuning for Cortex Pet
            </p>
          </div>
          <div className="flex gap-1 bg-surface-tertiary rounded-lg p-0.5">
            {(['learning', 'pipeline', 'config', 'dataset', 'results', 'research'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-colors capitalize cursor-pointer ${
                  activeTab === tab
                    ? 'bg-accent text-white'
                    : 'text-text-secondary hover:text-text-primary'
                }`}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {activeTab === 'learning' && <LearningTab />}

        {activeTab === 'pipeline' && (
          <div className="flex flex-col h-full">
            {/* Workflow explanation */}
            <div className="p-4 pb-0">
              <div className="bg-surface-secondary rounded-xl p-4 border border-border mb-4">
                <h3 className="text-sm font-semibold text-text-primary mb-2">Training Workflow</h3>
                <p className="text-xs text-text-secondary mb-3">
                  The full training pipeline turns raw data into a personalized on-device model.
                  Most users should start with the <strong>Learning</strong> tab to generate training examples,
                  then come here to prepare the dataset, train, and deploy.
                </p>
                <div className="flex flex-wrap gap-1 text-[10px] text-text-muted">
                  <span className="bg-accent/10 text-accent px-2 py-0.5 rounded">1. Learn</span>
                  <span className="text-text-muted">{'>'}</span>
                  <span className="bg-surface-tertiary px-2 py-0.5 rounded">2. Prepare Dataset</span>
                  <span className="text-text-muted">{'>'}</span>
                  <span className="bg-surface-tertiary px-2 py-0.5 rounded">3. Train LoRA</span>
                  <span className="text-text-muted">{'>'}</span>
                  <span className="bg-surface-tertiary px-2 py-0.5 rounded">4. Evaluate</span>
                  <span className="text-text-muted">{'>'}</span>
                  <span className="bg-surface-tertiary px-2 py-0.5 rounded">5. Export & Deploy</span>
                </div>
              </div>
            </div>

            {/* Availability warning */}
            {availability && !availability.scripts_available && (
              <div className="px-4 pb-2">
                <div className="bg-warning/10 border border-warning/30 rounded-lg p-4">
                  <h4 className="text-sm font-semibold text-warning mb-1">Pipeline Scripts Not Available</h4>
                  <p className="text-xs text-text-secondary mb-2">
                    {availability.is_bundled && !availability.has_system_python
                      ? 'Python not found on PATH. Install Python 3.10+ and ensure it\'s accessible from the command line.'
                      : !availability.training_dir_exists
                      ? 'Training scripts not found. This is unexpected — try reinstalling CortexHub.'
                      : 'The training pipeline requires Python with PyTorch and CUDA (GPU).'}
                  </p>
                  <div className="text-xs text-text-muted space-y-1">
                    <p><strong>Setup (one-time):</strong></p>
                    <ol className="list-decimal list-inside space-y-0.5 ml-2">
                      <li>Install <a href="https://www.python.org/downloads/" target="_blank" rel="noopener" className="text-accent hover:underline">Python 3.10+</a> (make sure "Add to PATH" is checked)</li>
                      <li>Install <a href="https://pytorch.org/get-started/locally/" target="_blank" rel="noopener" className="text-accent hover:underline">PyTorch with CUDA</a>: <code className="bg-surface-tertiary px-1 rounded">pip install torch --index-url https://download.pytorch.org/whl/cu121</code></li>
                      <li>Install ML libraries: <code className="bg-surface-tertiary px-1 rounded">pip install transformers peft trl datasets accelerate</code></li>
                      <li>Restart CortexHub</li>
                    </ol>
                  </div>
                  {availability.learned_examples > 0 && (
                    <p className="text-xs text-accent mt-2">
                      You have {availability.learned_examples} learned examples ready for training.
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* Pipeline steps — 3-column grid */}
            <div className="p-4 pt-2">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-text-muted">Pipeline Steps</span>
                {steps.some((s) => s.latest_job?.status === 'running' || s.latest_job?.status === 'failed') && (
                  <button
                    onClick={resetJobs}
                    className="px-2 py-1 rounded bg-surface-tertiary text-text-muted text-xs hover:text-text-primary hover:bg-surface-secondary transition-colors cursor-pointer"
                    title="Clear all job history and reset pipeline state"
                  >
                    Reset
                  </button>
                )}
              </div>
              <div className="grid grid-cols-3 gap-2">
                {steps.filter((s) => s.id !== 'pong_train').map((step) => (
                  <PipelineStep
                    key={step.id}
                    step={step}
                    onRun={() => runStep(step.id)}
                    onStop={stopJob}
                    onViewLogs={() => viewStepLogs(step.id)}
                    isRunning={
                      isRunning &&
                      (step.latest_job?.status === 'running' ||
                        viewingStepId === step.id)
                    }
                    isViewingLogs={viewingStepId === step.id}
                    compact
                    disabled={availability !== null && !availability.scripts_available &&
                      !(availability.available_steps[step.id]?.exists)}
                  />
                ))}
              </div>
              {steps.length === 0 && (
                <div className="text-center py-8 text-text-muted">
                  <p>Loading pipeline steps...</p>
                </div>
              )}
            </div>

            {/* Log viewer — shown when a step is selected */}
            {viewingStepId && (
              <div className="flex-1 min-h-[200px] border-t border-border">
                <LogViewer
                  lines={logLines}
                  isRunning={isRunning && steps.find(s => s.id === viewingStepId)?.latest_job?.status === 'running'}
                  stepName={steps.find(s => s.id === viewingStepId)?.name}
                />
              </div>
            )}
          </div>
        )}

        {activeTab === 'config' && (
          <HyperparamEditor config={config} onSave={updateConfig} />
        )}

        {activeTab === 'dataset' && <DatasetTab />}

        {activeTab === 'results' && (
          <div className="p-6">
            <ResultsView results={results} />
          </div>
        )}

        {activeTab === 'research' && (
          <AutoResearchPanel
            researchLog={researchLog}
            researchBest={researchBest}
            isAutoResearching={isAutoResearching}
            autoResearchLogLines={autoResearchLogLines}
            fetchResearchLog={fetchResearchLog}
            startAutoResearch={startAutoResearch}
            stopAutoResearch={stopAutoResearch}
            clearResearchLog={clearResearchLog}
          />
        )}
      </div>
    </div>
  )
}

function ResultsView({ results }: { results: Record<string, any> }) {
  const log = results.training_log
  const evalResults = results.eval_results

  if (!log && !evalResults) {
    return (
      <div className="text-center py-8 text-text-muted">
        <p className="text-3xl mb-2">📊</p>
        <p className="text-sm">No training results yet. Run the pipeline first.</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Training log */}
      {log && (
        <div className="bg-surface-secondary rounded-xl p-5 border border-border">
          <h3 className="text-sm font-semibold text-text-primary mb-3">
            Training Log
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <MetricCard label="Final Loss" value={log.final_loss?.toFixed(4)} />
            <MetricCard label="Total Steps" value={log.total_steps} />
            <MetricCard
              label="Training Time"
              value={`${log.training_time_s}s`}
            />
            <MetricCard label="GPU" value={log.gpu || 'CPU'} />
            <MetricCard label="Epochs" value={log.epochs} />
            <MetricCard label="Batch Size" value={log.effective_batch_size} />
            <MetricCard label="LoRA Rank" value={log.lora_rank} />
            <MetricCard
              label="Learning Rate"
              value={log.learning_rate}
            />
          </div>
        </div>
      )}

      {/* Eval results */}
      {evalResults && (
        <div className="bg-surface-secondary rounded-xl p-5 border border-border">
          <h3 className="text-sm font-semibold text-text-primary mb-3">
            Evaluation Results
          </h3>
          {evalResults.base_perplexity && evalResults.finetuned_perplexity && (
            <div className="grid grid-cols-2 gap-4 mb-4">
              <MetricCard
                label="Base Perplexity"
                value={evalResults.base_perplexity.perplexity}
              />
              <MetricCard
                label="Fine-tuned Perplexity"
                value={evalResults.finetuned_perplexity.perplexity}
                highlight
              />
            </div>
          )}
          {evalResults.prompts && (
            <div className="space-y-3 mt-4">
              <h4 className="text-xs font-medium text-text-secondary uppercase">
                Response Comparison
              </h4>
              {evalResults.prompts.slice(0, 5).map((p: any, i: number) => (
                <div
                  key={i}
                  className="bg-surface rounded-lg p-3 border border-border"
                >
                  <p className="text-xs font-medium text-accent mb-2">
                    "{p.prompt}"
                  </p>
                  {p.base && (
                    <p className="text-xs text-text-muted mb-1">
                      <span className="text-text-secondary">Base:</span>{' '}
                      {p.base.response}
                    </p>
                  )}
                  {p.finetuned && (
                    <p className="text-xs text-text-primary">
                      <span className="text-success">Fine-tuned:</span>{' '}
                      {p.finetuned.response}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function MetricCard({
  label,
  value,
  highlight,
}: {
  label: string
  value: any
  highlight?: boolean
}) {
  return (
    <div className="bg-surface rounded-lg p-3 border border-border">
      <p className="text-xs text-text-muted mb-1">{label}</p>
      <p
        className={`text-sm font-semibold ${
          highlight ? 'text-success' : 'text-text-primary'
        }`}
      >
        {value ?? '-'}
      </p>
    </div>
  )
}
