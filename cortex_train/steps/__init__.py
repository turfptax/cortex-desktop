"""Training pipeline step functions.

Each step provides a run_<name>() function with consistent signatures:
    - settings: TrainSettings
    - paths: TrainPaths
    - on_progress: ProgressCallback (optional)
    - **step_specific_kwargs

All return a dict with at least {ok: bool, ...step_results}.
"""

# Step function registry for Hub backend integration
STEP_MAP = {
    "sync": "cortex_train.steps.sync",
    "synthesize": "cortex_train.steps.synthesize",
    "prepare": "cortex_train.steps.prepare",
    "train": "cortex_train.steps.train",
    "evaluate": "cortex_train.steps.evaluate",
    "research": "cortex_train.steps.research",
    "deploy": "cortex_train.steps.deploy",
    "dream": "cortex_train.steps.dream",
}

# Map old numbered step IDs to new names (for Hub backward compatibility)
STEP_ID_MAP = {
    "00": "sync",
    "01": "synthesize",
    "02": "prepare",
    "03": "train",
    "04": "evaluate",
    "05": "research",
    "06": "deploy",
}
