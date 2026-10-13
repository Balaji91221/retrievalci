"""Local content-addressed storage. Artifacts are atomically created, never replaced."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from .models import EvaluationRun, Snapshot, canonical, digest, is_hash, read_json, require


class SnapshotStore:
    def __init__(self, root: str | Path = ".retrievalci") -> None:
        self.root = Path(root)

    def _save(self, kind: str, payload: dict[str, Any]) -> str:
        artifact_id = digest(payload)
        folder = self.root / kind
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{artifact_id}.json"
        content = canonical(payload) + b"\n"
        if target.exists():
            require(target.read_bytes() == content, f"Existing {kind} artifact was modified: {artifact_id}")
            return artifact_id
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=folder)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                require(target.read_bytes() == content, "Concurrent artifact differs from expected hash")
        finally:
            Path(temporary).unlink(missing_ok=True)
        return artifact_id

    def _load(self, kind: str, artifact_id: str) -> dict[str, Any]:
        require(is_hash(artifact_id), "Artifact ID must be a lowercase SHA-256 digest")
        value = read_json(self.root / kind / f"{artifact_id}.json")
        require(isinstance(value, dict) and digest(value) == artifact_id, "Artifact hash mismatch; content was modified")
        return value

    def create(self, corpus: dict[str, Any], config: dict[str, Any] | None = None) -> Snapshot:
        snapshot = Snapshot.from_corpus(corpus, config)
        self._save("snapshots", snapshot.payload)
        return snapshot

    def save_snapshot(self, snapshot: Snapshot) -> str:
        validated = Snapshot.from_corpus(snapshot.payload, snapshot.config)
        return self._save("snapshots", validated.payload)

    def load(self, snapshot_id: str) -> Snapshot:
        raw = self._load("snapshots", snapshot_id)
        require(raw.get("kind") == "snapshot", "Artifact is not a snapshot")
        snapshot = Snapshot.from_corpus(raw, raw.get("config"))
        require(snapshot.payload == raw and snapshot.id == snapshot_id, "Snapshot manifest mismatch")
        return snapshot

    def save_run(self, run: EvaluationRun) -> str:
        return self._save("runs", EvaluationRun.from_dict(run.payload).payload)

    def load_run(self, run_id: str) -> EvaluationRun:
        return EvaluationRun.from_dict(self._load("runs", run_id))
