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
- **Journey Graph**: an immutable outer directed acyclic graph of Stages, approval Gates and
  bounded Loop Nodes. It is the durable cross-Workflow execution definition.
- **Journey Edge**: a directed dependency between two outer graph Nodes. An optional condition
  policy selects a branch from committed upstream outcomes.
- **Loop Node**: an explicit control Node whose body is itself acyclic and may be repeated. It owns
  a maximum iteration count, deadline, exit-condition policy and exhaustion action. Arbitrary
  cyclic Journey Edges are invalid.
- **Pipeline**: the product-facing identity and revision lifecycle of a Journey Graph. ACWM owns
  graph semantics; consuming products own catalog, permissions, activation and business inputs.

Control-plane truth lives in the ACWM event log. Node Runs, Attempts and Loop Iterations are
append-only execution facts. Workflow checkpoints own only mode-internal cursor state. Products own
business facts, Artifact bytes, code-product truth and final side effects.

AgentScope messages, sessions, memory and topology never become ACWM Journey state. Only coarse
Stage progress, Artifact references, attention requests and terminal outcomes cross the boundary.
