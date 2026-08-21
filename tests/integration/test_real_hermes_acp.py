import os
from pathlib import Path

import pytest

from acwm.adapters import HermesACPTransportAdapter
from acwm.domain import CapabilityDescriptor, HermesACPTransport, PermissionPolicy
from acwm.ports import CapabilityInvocation


@pytest.mark.integration
@pytest.mark.skipif(
    os.environ.get("ACWM_REAL_HERMES") != "1",
    reason="set ACWM_REAL_HERMES=1 and configure Hermes model credentials",
)
async def test_real_hermes_acp_smoke(tmp_path: Path) -> None:
    descriptor = CapabilityDescriptor(
        id="hermes-developer",
        version="1.0.0",
        labels=("developer",),
        transport=HermesACPTransport(command=("hermes", "acp")),
        permissions=PermissionPolicy(workspace_edits="deny"),
    )
    adapter = HermesACPTransportAdapter(descriptor)
    try:
        result = await adapter.invoke(
            CapabilityInvocation(
                capability_id=descriptor.id,
                session_id="acwm:smoke:plan:direct",
                cwd=tmp_path,
                purpose="plan",
                prompt="Reply with the single word READY. Do not use tools.",
            )
        )
    finally:
        await adapter.close()

    assert result.output.strip()
