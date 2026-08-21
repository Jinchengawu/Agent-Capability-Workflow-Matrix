"""Content-addressed artifact storage."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import uuid4

from acwm.domain import ArtifactRef


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, kind: str, media_type: str, content: bytes) -> ArtifactRef:
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / digest[:2] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(content)
        artifact_id = f"artifact-{uuid4()}"
        return ArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            media_type=media_type,
            sha256=digest,
            uri=f"artifact://{digest}",
        )

    def read(self, artifact: ArtifactRef) -> bytes:
        digest = artifact.uri.removeprefix("artifact://")
        return (self.root / digest[:2] / digest).read_bytes()
