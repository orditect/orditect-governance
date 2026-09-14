"""GovernanceStore: the five store protocols of the cold path.

A governance store bundles five independent surfaces (reference
implementation: orditect.adapter.local.LocalFileStore):

  snapshot    - task execution snapshots (every generation of every node)
  audit       - append-only governed-call event log
  dependency  - pure-edge dependency facts (T12)
  content     - content-addressed pointer store (T5)
  result      - stream/result manifest store

These protocols pin the MINIMUM surface observed from the reference
application's usage; they are deliberately loose where the orditect-side
record types live (DependencyEdge, AuditEvent, ...). Conformance target:
LocalFileStore. Review standard: could a pg/minio-backed implementation
satisfy this naturally?
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SnapshotStore(Protocol):
    """Read surface used by viewers. The write side is driven through
    orditect.flow.snapshot.ProtocolSnapshotSink, not called directly."""

    async def get_tree(self, root_id: str, *, latest_only: bool = True) -> list: ...
    async def aggregate(self, *, group_by: str) -> dict: ...


@runtime_checkable
class AuditStore(Protocol):
    """Append-only audit log; queried by viewers and rule validation."""

    async def query(self, *, task_id: str | None = None) -> list: ...


@runtime_checkable
class DependencyStore(Protocol):
    """Pure-edge dependency facts (child depends on parent, T12)."""

    async def write_dependency(self, edge: Any) -> None: ...
    async def read_graph(self, root_id: str) -> Any: ...


@runtime_checkable
class ContentStore(Protocol):
    """Content-addressed store for pointer-ized payloads (T5).

    Write surface is exercised by the call-level governance plane
    (content_writer); read surface resolves pointers for viewers.
    Method names are pinned by the orditect-side client contracts —
    see LocalFileStore.
    """
    ...


@runtime_checkable
class ResultStore(Protocol):
    """Result/manifest store consumed by the stream runner
    (get_protocol_store). Pinned by the orditect-stream contract."""
    ...


@runtime_checkable
class GovernanceStore(Protocol):
    """The five-surface bundle every run context wires once."""

    @property
    def snapshot(self) -> SnapshotStore: ...
    @property
    def audit(self) -> AuditStore: ...
    @property
    def dependency(self) -> DependencyStore: ...
    @property
    def content(self) -> ContentStore: ...
    @property
    def result(self) -> ResultStore: ...