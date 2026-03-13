import { useEffect, useState } from 'react'
import { useTraining } from '../../hooks/useTraining'
import { PipelineStep } from './PipelineStep'
import { LogViewer } from './LogViewer'
import { HyperparamEditor } from './HyperparamEditor'
import { DatasetTab } from './DatasetTab'
import { AutoResearchPanel } from './AutoResearchPanel'

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

  const [activeTab, setActiveTab] = useState<'pipeline' | 'config' | 'dataset' | 'results' | 'research'>(
    'pipeline'
  )

  useEffect(() => {
    fetchSteps()
    fetchConfig()
    fetchResults()
  }, [fetchSteps, fetchConfig, fetchResults])

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
            {(['pipeline', 'config', 'dataset', 'results', 'research'] as const).map((tab) => (
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
        {activeTab === 'pipeline' && (
          <div className="flex flex-col h-full">
            {/* Pipeline steps — 3-column grid */}
            <div className="p-4">
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
