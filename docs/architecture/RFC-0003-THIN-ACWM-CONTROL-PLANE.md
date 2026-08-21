# RFC-0003: ACWM v0.3 Thin Control Plane

Status: implemented on `codex/acwm-v0.3-thin-control-plane`

## Problem

ACWM v0.2 proved that one strong Capability can enter Direct and LangGraph modes, but its
reference server also owns Git worktrees, code-delivery policy, FastAPI, SQLite, permissions and
framework checkpoints. AgentScope 2.x already owns Agent messages, state, memory, workspace and
deployment. Keeping both systems thick would create duplicate state, session and orchestration
owners.

## Decision

ACWM v0.3 is a thin, embeddable long-horizon Journey control plane:

```text
Product (Agent-Team-OS)
  -> ACWM Journey / Stage / Gate / Handoff / Event Log
    -> Workflow Adapter (AgentScope, Direct, LangGraph)
      -> Capability Adapter (Hermes, Codex, HTTP)
```

- A Journey orders Workflow Stages and global approval Gates.
- A Stage selects one Workflow Mode and contains one or more named Capability bindings.
- A Node is one resolved `Capability x Workflow Mode` matrix cell.
- Workflow internals, raw messages, sessions, memory and checkpoints never become Journey Nodes.
- Cross-Stage context moves only through immutable `HandoffEnvelope` and Artifact references.
- Product policies run through `StageOutputValidator`; Core does not know Git paths or secret rules.
- Final merge, push, candidate apply and product business state belong to the consuming product.

## Public contracts

`WorkflowManifest` declares the adapter/version, resumability and named binding slots. Each slot
owns its required and optional Capability features. Configuration cannot claim features.

`DefaultWorkflowRuntime.resolve_journey()` freezes every Stage into a `ResolvedStage`, containing a
`ResolvedWorkflow` and immutable `ResolvedNode` entries. Missing or unknown binding slots fail
before execution.

`DefaultWorkflowRuntime.execute()` calls only the selected Workflow Adapter. When the Stage declares
an output validator, a failed `StageValidationReport` prevents successful completion and therefore
prevents a following Gate from opening.

Global Gates bind decisions to a generic `GateSubject(kind, artifact_id, sha256)` and revision.
Provider permission prompts remain runtime attention requests, not Journey Gates.

## Adapters

- `agentscope.role-turn` uses AgentScope 2.x `UserMsg`/`AssistantMsg` only inside one Stage. The
  Capability still executes through `CapabilityRuntime`.
- `code-delivery` delegates one autonomous workspace-bound turn to the developer Capability.
- `codex.cli` invokes `codex exec --json --ephemeral --sandbox ... -C ... -`, supports cancellation,
  and converts JSONL terminal/tool events to the provider-neutral Capability contract.
- Hermes ACP, HTTP sync and LangGraph remain optional reference adapters.

## Dependency and compatibility policy

Core installs only Pydantic and PyYAML. SQLite, server, AgentScope, LangGraph, ACP and HTTP support
are optional Extras. Importing `acwm.domain` or `acwm.application` must not import any optional
framework or server dependency.

Schema v3 is intentionally incompatible with v0.2 configuration and data. The v0.2 FastAPI code
delivery chain remains an optional reference adapter during migration, but it is not the v0.3
architectural authority.

## Acceptance criteria

- One Journey can resolve multiple Workflow Stages and multiple Capability adapters.
- AgentScope raw messages and provider memory do not enter the ACWM Event Log or Handoff.
- Workflow and Capability manifests are frozen with version/fingerprint evidence.
- Product validation failure blocks Stage success.
- Core imports and tests without AgentScope, LangGraph, FastAPI, ACP or HTTPX.
- Deterministic tests cover AgentScope message mapping, Codex cwd/JSONL handling, generic Gates,
  binding compatibility and legacy reference-server recovery.

## Non-goals

v0.3 does not add Meeting, Debate, dynamic routing, DAG scheduling, shared Agent memory, distributed
queues, a new sandbox, automatic merge/push/PR, or Agent-Team-OS product APIs.

