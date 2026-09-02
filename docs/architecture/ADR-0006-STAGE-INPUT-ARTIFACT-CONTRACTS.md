# ADR-0006: Stage Input Artifact Contracts

Status: accepted for v0.5.1

## Context

Provider manifests already declare Artifact Contracts, but the Journey did not state which
cross-Workflow inputs a concrete Stage requires. A consuming product could therefore claim that a
Stage consumes a frozen Artifact while ACWM compiled no corresponding requirement. Treating a
product-local DTO as the Stage contract would create a second source of execution semantics.

The extension must preserve historical Journey fingerprints and compiled snapshots when no Stage
input contract is declared.

## Decision

- `StageDefinition.input_artifact_contracts` contains immutable ACWM `ArtifactContract` values.
- Contract ids are unique within one Stage; declaring multiple versions under one id is ambiguous
  and rejected.
- `compile_journey_graph` emits `stage_input_artifact_contracts`, keyed by canonical outer or bounded
  Loop Stage path. Each normalized contract includes a SHA-256 over its complete schema, modality,
  integrity, provenance and verification declaration.
- `DefaultWorkflowRuntime` merges every Stage input contract into each named binding's
  `WorkflowRequirements`. Capability and Provider resolution therefore fail before execution when
  a selected Provider cannot accept the Stage input.
- Empty declarations are excluded from canonical serialization. A legacy Journey with no Stage
  input contracts retains its previous graph fingerprint and compiled JSON shape.

## Ownership

- ACWM owns Stage input semantics, contract normalization, canonical path mapping and compatibility
  validation.
- Consuming products own Artifact bytes, content-addressed storage, access policy, Pipeline
  revision lifecycle and selection of the concrete Artifact reference.
- Provider manifests own their truthful accepted input contracts. They do not grant access to an
  Artifact or external source.

## Compatibility

The field is optional and defaults to empty. Existing Journey YAML, graph fingerprints, persisted
GraphRun snapshots and runtime seams remain valid. A product opting into a Stage input contract
must lock an ACWM revision containing this ADR and freeze the compiled contract hash in its own
Published Pipeline evidence.
