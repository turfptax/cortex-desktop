"""Games router — train Pong AI, manage game models."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services import process_manager

router = APIRouter()


class PongTrainRequest(BaseModel):
    episodes: int = 50000


@router.post("/train/pong")
async def train_pong(req: PongTrainRequest | None = None):
    """Start Pong Q-learning training."""
    try:
        episodes = req.episodes if req else 50000
        extra_args = ["--episodes", str(episodes)]
        job = await process_manager.start_job("pong_train", extra_args)
        return {"job": job.to_dict()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@router.get("/train/pong/logs/{job_id}")
async def stream_pong_logs(job_id: str):
    """SSE stream of Pong training logs."""
    job = process_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return StreamingResponse(
        process_manager.subscribe_logs(job_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/train/pong/stop/{job_id}")
async def stop_pong_training(job_id: str):
    """Stop a running Pong training job."""
    success = await process_manager.stop_job(job_id)
    if not success:
        raise HTTPException(status_code=400, detail="Cannot stop job")
    return {"stopped": True}
