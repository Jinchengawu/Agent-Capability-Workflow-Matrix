# ADR-0002: Agent-Agnostic Capability Runtime

Status: accepted for v0.2

## Decision

ACWM exposes one deep runtime seam: `resolve`, `stage`, and `signal`. Journey Nodes bind a
Capability explicitly. Workflows depend only on a Stage Exchange and send provider-neutral turns.
Adapters own feature declarations; configuration cannot override them.

The reference adapters are Hermes ACP and synchronous HTTP. Hermes supports the complete v0.2
feature set. HTTP supports only `io.text.final`, therefore it can enter Direct but is rejected for
LangGraph Code Delivery before a Journey or worktree is created.

Stage boundaries use `HandoffEnvelope`; they never share native Sessions. Resolution snapshots
include Adapter/Workflow versions, resolved features, configuration and policy fingerprints.

## Consequences

Hermes is a reference Adapter rather than part of ACWM's definition. New Agent implementations can
participate by implementing the Adapter contract and truthfully declaring features. v0.2 Capability
YAML, Journey YAML and SQLite schema intentionally reject v0.1 formats and data directories.
