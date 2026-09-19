# -*- coding: utf-8 -*-
"""Migration Provider registry."""

from __future__ import annotations

from typing import Any

from .base import MigrationProvider
from .locator import canonical_provider_id, resolve_source_location


def provider_names() -> tuple[str, ...]:
    """Return canonical source names currently supported for migration."""
    return ("qoder",)


def create_migration_provider(
    source: str,
    workspace: Any,
) -> MigrationProvider:
    """Create one read-only provider or raise a user-actionable error."""
    # Migration providers removed in this build
    raise RuntimeError(
        f"Migration provider '{source}' is not supported in this build"
    )


__all__ = [
    "MigrationProvider",
    "canonical_provider_id",
    "create_migration_provider",
    "provider_names",
    "resolve_source_location",
]
