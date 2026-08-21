import sys
from pathlib import Path

import pytest

from acwm.adapters import HermesACPTransportAdapter
from acwm.domain import CapabilityDescriptor, HermesACPTransport, PermissionPolicy
from acwm.ports import CapabilityInvocation


@pytest.mark.asyncio
async def test_acp_stdio_binds_cwd_and_reuses_the_mode_scoped_session(tmp_path: Path) -> None:
    fake_agent = Path(__file__).parents[1] / "fixtures" / "fake_acp_agent.py"
    descriptor = CapabilityDescriptor(
        id="hermes-developer",
        version="1.0.0",
        transport=HermesACPTransport(command=(sys.executable, str(fake_agent))),
        permissions=PermissionPolicy(),
    )
    adapter = HermesACPTransportAdapter(descriptor)
    invocation = CapabilityInvocation(
        capability_id=descriptor.id,
        session_id="logical-session",
        cwd=tmp_path,
        purpose="plan",
        prompt="first",
    )
    try:
        first = await adapter.invoke(invocation)
        second = await adapter.invoke(
            CapabilityInvocation(
                capability_id=descriptor.id,
                session_id="logical-session",
                cwd=tmp_path,
                purpose="plan",
                prompt="second",
            )
        )
    finally:
        await adapter.close()

    assert f"cwd={tmp_path}" in first.output
    assert "count=1" in first.output
    assert "count=2" in second.output
