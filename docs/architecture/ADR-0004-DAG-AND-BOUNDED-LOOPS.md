# ADR-0004: Journey Graphs Use DAGs and Explicit Bounded Loops

Status: accepted for v0.4

## Context

The v0.3 Journey contract is an ordered `steps` sequence. Agent-Team-OS requires multiple
independently versioned Pipelines with branches, joins and repeated work. Letting products infer
graph semantics from canvas edges would duplicate ACWM's cross-Stage ownership and make recovery
and evidence provider-specific.

A directed acyclic graph cannot itself contain a loop. Treating arbitrary back edges as loops would
make termination, approval, replay and recovery ambiguous.

## Decision

ACWM owns an immutable Journey Graph contract and compiler.

- The outer Journey Graph is a DAG of Stage, Approval Gate and Loop Nodes.
- Edges express dependencies. Conditional edges reference named condition policies; configuration
  never contains executable expressions.
- A Loop Node contains an acyclic body graph and a mandatory bounded policy: maximum iterations,
  timeout, exit-condition policy and exhaustion action.
- Nested Loop Nodes are excluded from v0.4. They can be reconsidered only with explicit depth and
  total-budget limits.
- Compilation rejects duplicate ids, missing endpoints, self edges, outer cycles, cyclic loop
  bodies, unreachable Nodes, missing entry/exit Nodes and unbounded loops.
- Compilation produces a deterministic topological order, entry/exit sets and immutable graph
  fingerprint. Workflow and Capability resolution then freezes every Stage as before.
- Schema v3 ordered `steps` remain readable and compile to a single-path graph. New graph
  definitions use schema v4.
- Runtime history records Node Run, Attempt and Loop Iteration facts. Rebuildable projections do
  not own scheduling truth.

## Ownership

- ACWM owns graph validation, compilation, cross-Stage scheduling semantics, Loop accounting and
  coarse execution events.
- Workflow adapters such as AgentScope own Stage-internal topology, messages and checkpoints.
- Consuming products own Pipeline catalogs, permissions, business inputs, Artifacts, evidence and
  final side effects.

## Consequences

The ACWM graph compiler becomes the public test surface for orchestration semantics. Agent-Team-OS
can offer a real graph editor without becoming a second scheduler. Existing linear Journeys remain
compatible but are canonicalized before fingerprinting and publication.

The first v0.4 slice covers immutable definition and compilation. Durable scheduling and recovery
are added only after compiler behavior is fixed by public-interface tests.
