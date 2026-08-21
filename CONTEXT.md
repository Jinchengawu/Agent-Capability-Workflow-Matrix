# ACWM Domain Context

ACWM separates an Agent's deep execution ability from the workflow institution in which that
ability participates. A Journey binds the two explicitly at every Node Stage.

- **Capability**: stable identity, labels and policy for an Agent ability. It owns no run state.
- **Adapter**: provider-specific implementation that declares its immutable feature manifest and
  translates native behavior into ACWM turns, signals and events.
- **Feature**: stable string describing observable runtime behavior, such as
  `interaction.multi_turn` or `workspace.cwd_binding`. YAML cannot claim features.
- **Workflow Requirement**: required and optional features declared by a Workflow Mode.
- **ResolvedNode**: immutable snapshot of the Stage's Capability, Adapter, features, Workflow and
  policy/config fingerprints. Recovery uses this snapshot and never silently re-resolves it.
- **Stage Exchange**: one Attempt-scoped runtime context. A Direct workflow makes one turn;
  LangGraph may make implement, review and repair turns in the same exchange.
- **HandoffEnvelope**: immutable, hashed contract between Stages. Native sessions and provider
  state never cross a Stage boundary.
- **Journey**: durable application-level sequence of Node Stages and approval gates.

Control-plane truth lives in the ACWM event log; LangGraph checkpoints own only workflow-internal
cursor state; a Git worktree owns code-product truth.
