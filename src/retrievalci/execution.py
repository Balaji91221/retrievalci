"""Killable subprocess retrieval, bounded retries, and immutable checkpoint journals."""
from __future__ import annotations

import math
import multiprocessing as mp
import os
import pickle
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import digest, require
from .store import SnapshotStore


class TransientRetrievalError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExecutionPolicy:
    timeout_s: float = 5.0
    max_retries: int = 1
    backoff_s: float = 0.05

    def validate(self) -> None:
        require(type(self.timeout_s) in {int, float} and math.isfinite(self.timeout_s)
                and 0 < self.timeout_s <= 3600, "timeout_s must be in (0, 3600]")
        require(type(self.max_retries) is int and 0 <= self.max_retries <= 10, "max_retries must be 0..10")
        require(type(self.backoff_s) in {int, float} and math.isfinite(self.backoff_s)
                and 0 <= self.backoff_s <= 10, "backoff_s must be 0..10")


def _child(connection, adapter, query, k, snapshot):
    if os.name == "posix":
        os.setsid()
    try:
        result = {"ok": True, "value": adapter.retrieve(query, k, snapshot)}
    except BaseException as exc:
        result = {"ok": False, "error_type": type(exc).__name__, "message": str(exc),
                  "retryable": isinstance(exc, (TransientRetrievalError, TimeoutError, ConnectionError))}
    try:
        encoded = pickle.dumps(result)
        if len(encoded) > 8_000_000:
            encoded = pickle.dumps({"ok": False, "error_type": "ResponseTooLarge",
                                    "message": "Adapter response exceeds 8 MB", "retryable": False})
        connection.send_bytes(encoded)
    finally:
        connection.close()


def _stop(process) -> None:
    # Clean a timed-out worker and its process group; never leave a hanging thread.
    if process.pid is None:
        return
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            if process.is_alive():
                process.terminate()
    elif process.is_alive():
        process.terminate()
    process.join(0.3)
    if process.is_alive():
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                process.kill()
        else:
            process.kill()
        process.join(1)
    require(not process.is_alive(), "Could not terminate retrieval subprocess")


def execute(adapter, query, k, snapshot, policy: ExecutionPolicy) -> tuple[Any, list[dict[str, Any]]]:
    policy.validate()
    attempts = []
    for index in range(policy.max_retries + 1):
        # spawn isolates adapter state and works on macOS/Linux/Windows. Adapters
        # must be top-level, pickleable instances. Every attempt starts fresh.
        context = mp.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_child, args=(sender, adapter, query, k, snapshot), daemon=True)
        started = time.perf_counter()
        outcome = {"ok": False, "error_type": "WorkerStartError", "message": "Worker did not start", "retryable": False}
        try:
            process.start()
            sender.close()
            if receiver.poll(policy.timeout_s):
                try:
                    outcome = pickle.loads(receiver.recv_bytes(8_000_000))
                except (EOFError, OSError) as exc:
                    outcome = {"ok": False, "error_type": "WorkerExit", "message": str(exc), "retryable": False}
            else:
                outcome = {"ok": False, "error_type": "TimeoutError", "message": "Retrieval deadline exceeded",
                           "retryable": True}
        except (TypeError, AttributeError, RuntimeError) as exc:
            outcome = {"ok": False, "error_type": type(exc).__name__, "message": str(exc), "retryable": False}
        finally:
            sender.close()
            receiver.close()
            _stop(process)
        attempts.append({"attempt": index + 1, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                         "outcome": "success" if outcome["ok"] else "error",
                         **({} if outcome["ok"] else {key: outcome[key] for key in ("error_type", "message", "retryable")})})
        if outcome["ok"]:
            return outcome["value"], attempts
        if not outcome["retryable"] or index == policy.max_retries:
            return None, attempts
        time.sleep(min(policy.backoff_s * (2 ** index), 10))
    raise AssertionError("Unreachable")


class ResumeJournal:
    """One directory per immutable evaluation input; each result is content addressed."""
    def __init__(self, root: str | Path, inputs: dict[str, Any], resume: bool):
        self.root = Path(root)
        self.store = SnapshotStore(self.root)
        self.input_id = digest(inputs)
        manifests = list((self.root / "inputs").glob("*.json"))
        require(not resume or bool(manifests), "No checkpoint found to resume")
        require(not manifests or all(p.stem == self.input_id for p in manifests),
                "Checkpoint inputs differ; use a new checkpoint directory")
        if manifests:
            require(resume, "Checkpoint already exists; use resume=True or a new directory")
            require(self.store._load("inputs", self.input_id) == inputs, "Checkpoint input mismatch")
        self.store._save("inputs", inputs)

    def completed(self) -> dict[str, dict[str, Any]]:
        results = {}
        for path in sorted((self.root / "results").glob("*.json")):
            record = self.store._load("results", path.stem)
            require(record.get("input_id") == self.input_id, "Checkpoint result belongs to other inputs")
            result = record["result"]
            if result["status"] not in {"error", "pending"}:
                query_id = result["query_id"]
                require(query_id not in results or results[query_id] == result, "Conflicting successful checkpoint results")
                results[query_id] = result
        return results

    def save(self, result: dict[str, Any]) -> None:
        self.store._save("results", {"input_id": self.input_id, "result": result})
