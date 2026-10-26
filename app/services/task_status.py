"""
Global task status and progress tracker for Pream.
Provides real-time activity and progress logging for the UI footer status banner,
including multi-worker tracking for parallel source scanning.
"""
import threading
import time
from typing import Any, Dict, List

_status_lock = threading.Lock()

_current_state: Dict[str, Any] = {
    "status": "idle",  # "idle", "running", "completed", "error"
    "task": "Idle",
    "message": "System ready",
    "progress": 0,  # 0 to 100
    "updated_at": time.time(),
}

_active_workers: Dict[str, Dict[str, Any]] = {}


def set_task_status(
    status: str,
    task: str,
    message: str,
    progress: int = 0,
) -> None:
    """Update global background task status."""
    global _current_state
    with _status_lock:
        _current_state = {
            "status": status,
            "task": task,
            "message": message,
            "progress": max(0, min(100, progress)),
            "updated_at": time.time(),
        }


def update_worker_progress(
    worker_id: str,
    label: str,
    message: str,
    progress: int = 0,
) -> None:
    """Update or register an individual worker in a multi-threaded parallel task."""
    global _current_state, _active_workers
    with _status_lock:
        _active_workers[worker_id] = {
            "id": worker_id,
            "label": label,
            "message": message,
            "progress": max(0, min(100, progress)),
            "updated_at": time.time(),
        }

        # Calculate combined progress across active workers
        if _active_workers:
            avg_progress = int(sum(w["progress"] for w in _active_workers.values()) / len(_active_workers))
            active_labels = [f"{w['label']}: {w['progress']}%" for w in _active_workers.values()]
            worker_summary = " | ".join(active_labels[:3])
            if len(active_labels) > 3:
                worker_summary += f" (+{len(active_labels) - 3} more)"

            _current_state = {
                "status": "running",
                "task": f"Scanning Sources ({len(_active_workers)} active)",
                "message": worker_summary,
                "progress": avg_progress,
                "updated_at": time.time(),
            }


def remove_worker(worker_id: str) -> None:
    """Remove a worker when its task finishes."""
    global _active_workers
    with _status_lock:
        _active_workers.pop(worker_id, None)


def clear_all_workers() -> None:
    """Clear all active workers."""
    global _active_workers
    with _status_lock:
        _active_workers.clear()


def get_task_status() -> Dict[str, Any]:
    """Get current background task status, reverting to idle after 15s of completion."""
    global _current_state, _active_workers
    with _status_lock:
        now = time.time()
        # Prune stale workers (> 180s without update)
        stale_keys = [k for k, v in _active_workers.items() if (now - v.get("updated_at", 0)) > 180]
        for k in stale_keys:
            _active_workers.pop(k, None)

        if _current_state.get("status") == "completed" and (now - _current_state.get("updated_at", 0)) > 15:
            _current_state = {
                "status": "idle",
                "task": "Idle",
                "message": "Idle — all tasks complete",
                "progress": 0,
                "updated_at": now,
            }
            _active_workers.clear()

        res = dict(_current_state)
        res["workers"] = list(_active_workers.values())
        return res


def set_idle(message: str = "System ready") -> None:
    """Reset status to idle."""
    clear_all_workers()
    set_task_status(
        status="idle",
        task="Idle",
        message=message,
        progress=0,
    )

