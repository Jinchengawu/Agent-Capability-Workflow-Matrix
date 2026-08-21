# ADR-0003: Keep ACWM Above Workflow Frameworks

Status: accepted for v0.3

## Decision

ACWM owns durable cross-Stage Journey control. Workflow frameworks own Stage-internal topology,
messages, sessions, memory and checkpoints. Capability providers own their Agent loop, tools and
private state. Consuming products own business facts, candidate policy and final side effects.

The primary Workflow seam is `WorkflowManifest + DefaultWorkflowRuntime`. A Stage may bind multiple
named Capabilities, producing one immutable `ResolvedNode` per matrix cell. Only coarse Stage,
Artifact, attention and terminal events cross the Workflow boundary.

## Consequences

ACWM can embed AgentScope without recreating AgentScope. The Core package stays small and importable
without framework dependencies. Existing v0.2 server behavior becomes a reference integration and
Configuration schema v3 and SQLite storage schema 4 are breaking changes. ACWM remains justified only when a Journey crosses meaningful
Workflow boundaries; a product using one AgentScope application end to end may not need ACWM.
