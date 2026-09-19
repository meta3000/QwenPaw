# -*- coding: utf-8 -*-
"""Built-in tool functions for QwenPaw agents.

Every tool function decorated with ``@tool_descriptor`` is automatically
collected into a global registry at import time.  Adding a new built-in
tool requires only two things:

1. Decorate the function with ``@tool_descriptor(...)`` (include
   governance / UI / default_policy metadata as needed).
2. Import the module here so that the decorator executes.
   ``__all__`` is generated automatically from collected descriptors.

:func:`discover_builtin_tool_funcs` returns all auto-collected built-in
tools — no manual list maintenance or filesystem scanning required.

Note: ``execute_python_code`` / ``view_text_file`` / ``write_text_file``
are intentionally not re-exported — qwenpaw's react_agent does not
register them. The literal names still appear in
``security/tool_guard`` guardians for backward compatibility with
pre-existing allowlists.
"""

# pylint: disable=wrong-import-position

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Callable

from ...config import load_config
from ...config.config import _invalidate_builtin_tools_cache

logger = logging.getLogger(__name__)

# Each import triggers the @tool_descriptor decorator, which auto-
# collects the function into the global registry.
from .file_io import (  # noqa: E402
    read_file,
    write_file,
    edit_file,
    append_file,
)
from .file_search import grep_search, glob_search  # noqa: E402
from .shell import execute_shell_command  # noqa: E402
from .web_search import web_search, web_fetch  # noqa: E402
from .get_current_time import get_current_time, set_user_timezone  # noqa: E402
from .get_token_usage import get_token_usage  # noqa: E402
from .run_tool_batch import run_tool_batch  # noqa: E402

# ``load_config()`` above may build ``ToolsConfig`` while this package is
# still importing. Refresh the descriptor-derived cache.
_invalidate_builtin_tools_cache()


def discover_builtin_tool_funcs() -> list[Callable]:
    """Return all built-in tool functions auto-collected by
    ``@tool_descriptor``.

    The decorator registers each function at import time.  This function
    simply returns the collected built-in tools — no ``pkgutil`` /
    ``importlib`` scanning, no manual list.
    """
    from ...runtime.tool_registry import get_builtin_tool_funcs

    return get_builtin_tool_funcs()


def _build_all() -> list[str]:
    """Build ``__all__`` from auto-collected ``@tool_descriptor`` tools."""
    from ...runtime.tool_registry import get_builtin_tool_funcs

    return ["discover_builtin_tool_funcs"] + [
        fn.__name__ for fn in get_builtin_tool_funcs()
    ]


# Must stay below all side-effect imports above so every @tool_descriptor
# has already appended to the global registry before __all__ is built.
__all__ = _build_all()
