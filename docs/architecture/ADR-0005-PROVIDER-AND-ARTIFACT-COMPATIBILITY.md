# ADR-0005: Resolve Providers at Workflow Binding Sites

Status: accepted for v0.5

## Context

A Capability identity does not identify which logical Agent role or deployment should satisfy a
particular Stage Slot. A product may deploy several frontend, testing or planning roles through one
runtime instance, and the same Capability may be assigned to different nodes in one Journey.
Global `Capability -> Instance` bindings therefore cannot produce an auditable execution snapshot.

Artifact compatibility was also implicit. A provider could appear feature-compatible while
returning the wrong schema or content modality.

## Decision

ACWM adds four provider-neutral semantic contracts:

- `ArtifactContract` defines a versioned schema, allowed content modalities and integrity,
  provenance and verification requirements. `ContentPart` is a discriminated text, structured,
  file, resource, image or audio value.
- `CapabilityProviderManifest` is immutable and content-addressed. It declares provided
  Capabilities, supported Workflow Modes, required/optional runtime features, input/output Artifact
  Contracts and permission requirements.
- `ProviderBindingSite` identifies one Stage path and named binding Slot.
- `ResolvedProviderBinding` freezes the site, resolved Capability, Provider Manifest, Workflow and
  Artifact compatibility into one verifiable hash.

`ProviderResolver` is called from `DefaultWorkflowRuntime.resolve`. Missing assignments, invalid
manifest hashes, Capability/version mismatches, unsupported Workflow Modes, unavailable required
features and missing Artifact Contracts fail before Stage execution.

The runtime seam remains `resolve -> stage -> signal`. This ADR does not introduce an `invoke` or
`cancel` protocol. Provider manifests contain no runtime endpoint, credentials, product workspace
path, prompt text or self-reported Adapter features.

## Ownership

- ACWM owns Capability, Workflow, Provider and Artifact compatibility and the immutable resolution
  snapshot.
- Adapters own truthful runtime feature manifests and native protocol translation.
- Consuming products own Agent profiles, deployments, runtime instances, credentials, permission
  grants, workspace policy and Pipeline assignment.
- Artifact stores own bytes; ACWM only carries schemas, content parts and content-addressed refs.

## Compatibility

Existing Capability and Journey YAML remain readable. Provider assignment is an optional extension
to resolution so historical Journey snapshots without a provider binding remain valid. New product
Pipeline revisions that opt into provider governance must supply every required binding site and
freeze the resulting `ResolvedProviderBinding` hash.
