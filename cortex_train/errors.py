"""Exception hierarchy for cortex_train pipeline steps."""


class CortexTrainError(Exception):
    """Base exception for all training pipeline errors."""
    pass


class SyncError(CortexTrainError):
    """Failed to sync data from Pi (SCP, SSH, or export)."""
    pass


class SynthesisError(CortexTrainError):
    """Failed during LM Studio teacher synthesis."""
    pass


class DatasetError(CortexTrainError):
    """Failed during dataset preparation or merge."""
    pass


class TrainingError(CortexTrainError):
    """Failed during LoRA fine-tuning."""
    pass


class EvalError(CortexTrainError):
    """Failed during model evaluation."""
    pass


class DeployError(CortexTrainError):
    """Failed during GGUF export or deployment to Pi."""
    pass


class ResearchError(CortexTrainError):
    """Failed during hyperparameter search."""
    pass
