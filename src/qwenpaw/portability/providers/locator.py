# -*- coding: utf-8 -*-
"""Cross-user and cross-platform source data location resolution.

Migration providers have been removed in this build.
"""

from __future__ import annotations

from ..models import SourceLocation


def canonical_provider_id(source: str) -> str:
    """Return the canonical provider id or raise a useful error."""
    raise ValueError(
        f"Migration provider '{source}' is not supported in this build"
    )


def resolve_source_location(*args, **kwargs) -> SourceLocation:
    """Resolve provider roots - not supported in this build."""
    raise RuntimeError(
        "Migration providers have been removed from this build"
    )


__all__ = [
    "canonical_provider_id",
    "resolve_source_location",
]
