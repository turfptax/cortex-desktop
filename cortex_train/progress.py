"""Progress reporting for pipeline steps.

Each step function accepts an optional on_progress callback.
The CLI uses cli_progress() to print to stdout.
The Hub backend creates callbacks that push to SSE subscriber queues.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Optional


@dataclass
class ProgressEvent:
    """A progress update from a pipeline step."""
    step: str                              # e.g. "sync", "train", "deploy"
    message: str                           # Human-readable status line
    pct: Optional[float] = None            # 0.0-100.0, None if indeterminate
    metrics: Dict[str, Any] = field(default_factory=dict)  # Step-specific data
    timestamp: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))


# Type alias for progress callbacks
ProgressCallback = Callable[[ProgressEvent], None]


def null_progress(event: ProgressEvent) -> None:
    """No-op progress callback (default when caller doesn't need updates)."""
    pass


def cli_progress(event: ProgressEvent) -> None:
    """Print progress to stdout (for CLI usage)."""
    pct_str = f" ({event.pct:.0f}%)" if event.pct is not None else ""
    print(f"[{event.timestamp}] [{event.step}]{pct_str} {event.message}", flush=True)


def make_step_progress(step: str, callback: ProgressCallback) -> Callable[[str, Optional[float], Dict], None]:
    """Create a convenience emitter that pre-fills the step name.

    Usage in a step function:
        emit = make_step_progress("train", on_progress)
        emit("Loading model...")
        emit("Epoch 1/3", pct=33.3, metrics={"loss": 0.42})
    """
    def emit(message: str, pct: Optional[float] = None, metrics: Optional[Dict] = None):
        callback(ProgressEvent(
            step=step,
            message=message,
            pct=pct,
            metrics=metrics or {},
        ))
    return emit
