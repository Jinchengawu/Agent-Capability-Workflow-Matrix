# ACWM Domain Context

ACWM separates an Agent's deep execution ability from the workflow institution in which that
ability participates. It is a cross-Workflow Journey control plane, not an Agent framework.

- **Capability**: stable identity, labels and policy for an Agent ability. It owns no run state.
- **Adapter**: provider-specific implementation that declares its immutable feature manifest and
  translates native behavior into ACWM turns, signals and events.
- **Feature**: stable string describing observable runtime behavior, such as
  `interaction.multi_turn` or `workspace.cwd_binding`. YAML cannot claim features.
- **Workflow Requirement**: required and optional features declared by a Workflow Mode.
- **ResolvedNode**: immutable snapshot of the Stage's Capability, Adapter, features, Workflow and
  policy/config fingerprints. Recovery uses this snapshot and never silently re-resolves it.
- **Stage**: one Workflow boundary with one or more named Capability bindings.
- **Node**: one resolved Capability/Workflow matrix cell inside a Stage.
- **ResolvedStage**: immutable Workflow snapshot plus all of its ResolvedNodes.
- **Stage Exchange**: one Attempt-scoped Capability context owned by a Workflow Adapter.
- **HandoffEnvelope**: immutable, hashed contract between Stages. Native sessions and provider
  state never cross a Stage boundary.
- **Journey**: durable application-level sequence of Node Stages and approval gates.

Control-plane truth lives in the ACWM event log. Workflow checkpoints own only mode-internal cursor
state. Products own business facts, Artifact bytes, code-product truth and final side effects.

AgentScope messages, sessions, memory and topology never become ACWM Journey state. Only coarse
Stage progress, Artifact references, attention requests and terminal outcomes cross the boundary.
