# -*- coding: utf-8 -*-
# pylint: disable=redefined-outer-name,unused-argument
import asyncio
import hmac
import inspect
import mimetypes
import os
import sys
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..__version__ import __version__
from ..config import load_config  # pylint: disable=no-name-in-module
from ..config.utils import get_config_path, read_last_api, write_last_api
from ..constant import (
    CORS_ORIGINS,
    DOCS_ENABLED,
    LOG_LEVEL_ENV,
    PROJECT_NAME,
    WORKING_DIR,
)
from ..envs import load_envs_into_environ
from ..providers.provider_manager import ProviderManager
from ..utils.io_utils import run_sync_io
from ..utils.logging import (
    LOG_FILE_PATH,
    SuppressPathAccessLogFilter,
    add_project_file_handler,
    setup_logger,
)
from ..utils.startup_display import AgentStartupDisplay
from ..utils.system_info import summarize_python_environment
from .auth import (
    AuthMiddleware,
    RuntimeBoundaryMiddleware,
    auto_register_from_env,
    check_proxy_config_sanity,
    is_auth_enabled,
)
from .exception_handlers import register_exception_handlers
from .migration import (
    ensure_default_agent_exists,
    ensure_qa_agent_exists,
    migrate_legacy_skills_to_skill_pool,
    migrate_legacy_workspace_to_default_agent,
)
from .routers import create_agent_scoped_router
from .routers import router as api_router
from .routers.agent_scoped import AgentContextMiddleware
from .routers.approval import router as approval_router
from .routers.healthz import router as healthz_router
from .routers.loops import router as loops_router
from .routers.tool_calls import router as tool_calls_router

# Apply log level on load so reload child process gets same level as CLI.
logger = setup_logger(os.environ.get(LOG_LEVEL_ENV, "info"))

# Ensure static assets are served with browser-compatible MIME types across
# platforms (notably Windows may miss .js/.mjs mappings).
mimetypes.init()
mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("image/svg+xml", ".svg")

# Load persisted env vars into os.environ at module import time
# so they are available before the lifespan starts.
load_envs_into_environ()


async def _sync_scroll_history_on_startup() -> None:
    """Run the composed legacy-history migration outside the event loop."""
    try:
        from ..agents.context.scroll.sync import sync_all_scroll_agents

        await run_sync_io(sync_all_scroll_agents)
    except Exception:  # noqa: BLE001 - session sync must never block startup
        logger.warning("session-sync: import/launch failed", exc_info=True)


@asynccontextmanager
async def lifespan(  # pylint: disable=too-many-statements,too-many-branches
    app: FastAPI,
):
    startup_start_time = time.time()
    add_project_file_handler(LOG_FILE_PATH)

    # ================================================================
    # Fast synchronous setup (target < 100ms)
    # Everything here must be lightweight so the server starts quickly.
    # ================================================================

    auto_register_from_env()
    check_proxy_config_sanity()

    try:
        from ..utils.telemetry import (
            collect_and_upload_telemetry,
            has_telemetry_been_collected,
            is_telemetry_opted_out,
        )

        if not is_telemetry_opted_out(
            WORKING_DIR,
        ) and not has_telemetry_been_collected(WORKING_DIR):
            collect_and_upload_telemetry(WORKING_DIR)
    except Exception:
        logger.debug(
            "Telemetry collection skipped due to error",
            exc_info=True,
        )

    logger.debug("Checking for legacy config migration...")
    migrate_legacy_workspace_to_default_agent()
    ensure_default_agent_exists()
    migrate_legacy_skills_to_skill_pool()
    ensure_qa_agent_exists()

    from ..config.utils import get_agent_dirs

    # Portability transaction recovery removed
    recovered_transactions = []
    if recovered_transactions:
        logger.warning(
            "Recovered %d interrupted PawPort import transaction(s)",
            len(recovered_transactions),
        )

    # Migrate old conversations from sessions/*.json into each scroll agent's
    # history.db, so chats from before scroll existed stay recallable. This is
    # a one-off backfill, not core startup work: if it fails, we log and keep
    # booting — that agent just won't have its old chats imported (scroll still
    # records new turns normally). The import sits inside the try for the same
    # reason — even a failed import must not block init.
    #
    # Note: being pure backfill, this could later run asynchronously (off the
    # boot path) to speed up startup.
    await _sync_scroll_history_on_startup()

    # Provider initialization scans and may migrate persisted configuration.
    provider_manager = await asyncio.to_thread(ProviderManager.get_instance)

    # --- AppServiceManager + WorkspaceRegistry ---
    app_services = None
    workspace_registry = None
    try:
        from .app_services import AppServiceManager
        from .workspace_registry import WorkspaceRegistry

        app_services = AppServiceManager()
        await app_services.start()
        app.state.app_services = app_services

        workspace_registry = WorkspaceRegistry(
            app_services=app_services,
        )
        app.state.workspace_registry = workspace_registry
        logger.debug("Runtime infrastructure initialized")

        # --- @api_action auto-registration ---
        _api_action_command_specs: list[Any] = []
        try:
            from ..api_action import ManagerRegistry
            from ._api_action_routes import (
                collect_slash_specs_from_api_actions,
                register_http_routes,
            )
            from .crons.manager import CronManager

            manager_registry = ManagerRegistry()

            def _get_default_cron_mgr(app_inst: Any) -> Any:
                mam = getattr(app_inst.state, "multi_agent_manager", None)
                if mam is None:
                    return None
                # pylint: disable-next=protected-access
                ws = mam._workspaces.get("default")
                return getattr(ws, "cron_manager", None) if ws else None

            manager_registry.register(CronManager, _get_default_cron_mgr)
            app.state.manager_registry = manager_registry

            n_routes = register_http_routes(app, manager_registry)
            logger.debug("Auto-registered %d HTTP routes", n_routes)

            _api_action_command_specs.extend(
                collect_slash_specs_from_api_actions(manager_registry),
            )
            logger.debug(
                "Collected %d slash specs from @api_action",
                len(_api_action_command_specs),
            )
        except Exception:
            logger.debug(
                "@api_action auto-registration skipped",
                exc_info=True,
            )

        # --- HITL slash commands ---
        try:
            from .app_services._builtin_tool_commands import (
                build_tool_command_specs,
            )

            _api_action_command_specs.extend(
                build_tool_command_specs(app_services.tool_coordinator),
            )
            logger.debug("HITL tool commands registered")
        except Exception:
            logger.debug(
                "HITL tool command registration skipped",
                exc_info=True,
            )

        # --- Use shared bootstrap factory ---
        from .workspace.bootstrap_factory import WorkspaceBootstrapFactory

        factory_kwargs = WorkspaceBootstrapFactory.build_bootstrap_kwargs(
            app_services,
            extra_command_specs=(
                _api_action_command_specs
                if _api_action_command_specs
                else None
            ),
        )
        # Merge factory output into workspace_registry._bootstrap_kwargs
        for key, value in factory_kwargs.items():
            # pylint: disable-next=protected-access
            workspace_registry._bootstrap_kwargs[key] = value

        # Warm descriptor-driven caches off the event loop so the first
        # /tools or agent-config path does not pay full import cost inline.
        def _warm_descriptor_caches() -> None:
            from ..config.config import _default_builtin_tools
            from ..governance.policy import get_default_user_rules
            from ..governance.tool_registry import DEFAULT_REGISTRY

            DEFAULT_REGISTRY.get_all_tool_names()
            _default_builtin_tools()
            get_default_user_rules()

        try:
            await asyncio.to_thread(_warm_descriptor_caches)
        except Exception:
            logger.debug(
                "Descriptor cache warm-up skipped",
                exc_info=True,
            )

    except Exception:
        logger.debug(
            "Runtime infrastructure init skipped",
            exc_info=True,
        )

    # Start token usage manager background tasks
    logger.debug("Starting TokenUsageManager background tasks...")
    from ..token_usage import get_token_usage_manager

    token_usage_manager = get_token_usage_manager()
    token_usage_manager.start(flush_interval=10)

    # Expose to endpoints (must be set before first request arrives).
    # WorkspaceRegistry IS-A MultiAgentManager — backward compat for
    # routers / agent_context that read app.state.multi_agent_manager.
    app.state.multi_agent_manager = workspace_registry
    app.state.provider_manager = provider_manager
    app.state.plugin_loader = None
    app.state.plugin_registry = None

    async def _get_agent_by_id(agent_id: str = None):
        """Get agent instance by ID, or active agent if not specified."""
        if agent_id is None:
            config = load_config(get_config_path())
            agent_id = config.agents.active_agent or "default"
        return await workspace_registry.get_agent(agent_id)

    app.state.get_agent_by_id = _get_agent_by_id

    app.state.startup_ready = asyncio.Event()
    app.state.startup_time = startup_start_time

    fast_elapsed = time.time() - startup_start_time
    logger.info(
        f"Server ready in {fast_elapsed:.3f}s (agents loading in background)",
    )

    # ================================================================
    # Background heavy initialization
    # Agents, plugins, and services start in a background task so the
    # server can begin accepting HTTP requests immediately.
    # First API requests that need an agent will await its readiness
    # via MultiAgentManager.get_agent() lazy-loading / event wait.
    # ================================================================

    startup_display = AgentStartupDisplay(read_last_api()).start()

    async def _background_startup():  # pylint: disable=too-many-statements
        try:
            # ---- Plugin System (phase 1: startup-critical plugins) ----
            # Channel and memory plugins must register before agents start.
            logger.debug("Initializing plugin system...")

            from ..config.utils import get_plugins_dir
            from ..plugins.loader import PluginLoader
            from ..plugins.runtime import RuntimeHelpers

            # PawApps install into the plugins dir alongside other plugins
            # and load through the same pipeline as 'app'-type plugins
            # (plugin.json carrying meta.pawapp); surfaced only in the App
            # Center, hidden from the sidebar.
            plugin_dirs = [get_plugins_dir()]

            plugin_loader = PluginLoader(plugin_dirs)

            plugin_loader.registry.set_plugin_http_app(app)

            config = load_config(get_config_path())
            plugin_configs = (
                config.plugins if hasattr(config, "plugins") else {}
            )
            logger.debug(
                f"Loading plugins with {len(plugin_configs)} config(s)",
            )

            # Phase 1: load startup-critical plugins before agents start
            await plugin_loader.load_all_plugins(
                configs=plugin_configs,
                types=["channel", "memory"],
            )
            logger.debug("Phase 1: channel and memory plugins loaded")

            def _mark_core_agents_ready(_results: dict[str, bool]) -> None:
                """Publish readiness after the core agent phase."""
                core_elapsed = time.time() - startup_start_time
                startup_display.mark_core_ready(core_elapsed)
                app.state.startup_ready.set()

            startup_results = (
                await workspace_registry.start_all_configured_agents(
                    on_core_ready=_mark_core_agents_ready,
                    startup_display=startup_display,
                )
            )
            if startup_results.get("default") is False:
                startup_display.mark_failed(
                    "Default agent failed to start",
                )
            elif app.state.startup_ready.is_set():
                startup_display.mark_finalizing()

            startup_provider_ids = provider_manager.startup_sync_provider_ids()
            asyncio.create_task(
                provider_manager.sync_startup_provider_models(
                    startup_provider_ids,
                ),
                name="qwenpaw-provider-model-sync",
            )
            asyncio.create_task(
                provider_manager.sync_remote_catalogs(),
                name="qwenpaw-provider-catalog-sync",
            )

            # Phase 2: load remaining plugins (channel plugins already
            # loaded — load_plugin skips them automatically)
            loaded_plugins = await plugin_loader.load_all_plugins(
                configs=plugin_configs,
            )
            logger.debug(f"Loaded {len(loaded_plugins)} plugin(s)")

            runtime_helpers = RuntimeHelpers(
                provider_manager=provider_manager,
            )
            plugin_loader.registry.set_runtime_helpers(runtime_helpers)
            plugin_loader.registry.set_workspace_manager(
                workspace_registry,
            )

            for (
                provider_id,
                provider_reg,
            ) in plugin_loader.registry.get_all_providers().items():
                await provider_manager.register_plugin_provider_async(
                    provider_id=provider_id,
                    provider_class=provider_reg.provider_class,
                    label=provider_reg.label,
                    base_url=provider_reg.base_url,
                    metadata=provider_reg.metadata,
                )
                logger.debug(
                    f"Registered plugin provider: {provider_id}",
                )

            app.state.plugin_loader = plugin_loader
            app.state.plugin_registry = plugin_loader.registry

            # ---- Plugin Control Commands ----
            logger.debug("Registering plugin control commands...")
            from qwenpaw.runtime.commands.control import register_command

            from ..app.channels.command_registry import CommandRegistry

            command_registry = CommandRegistry()

            control_commands = plugin_loader.registry.get_control_commands()
            for cmd_reg in control_commands:
                try:
                    register_command(cmd_reg.handler)

                    command_registry.register_command(
                        f"/{cmd_reg.handler.command_name}",
                        priority_level=cmd_reg.priority_level,
                    )

                    logger.debug(
                        f"Registered plugin control command: "
                        f"/{cmd_reg.handler.command_name} "
                        f"from plugin '{cmd_reg.plugin_id}' (priority"
                        f"={cmd_reg.priority_level})",
                    )
                except Exception as e:
                    logger.error(
                        f"✗ Failed to register control command "
                        f"'{cmd_reg.handler.command_name}' "
                        f"from plugin '{cmd_reg.plugin_id}': {e}",
                        exc_info=True,
                    )

            # ---- Startup Hooks ----
            logger.debug("Executing plugin startup hooks...")
            startup_hooks = plugin_loader.registry.get_startup_hooks()
            for hook in startup_hooks:
                try:
                    logger.debug(
                        f"Executing startup hook '{hook.hook_name}' "
                        f"from plugin '{hook.plugin_id}' "
                        f"(priority={hook.priority})",
                    )

                    result = hook.callback()
                    if inspect.iscoroutine(
                        result,
                    ) or inspect.isawaitable(result):
                        await result

                    logger.debug(
                        f"Completed startup hook '{hook.hook_name}' "
                        f"from plugin '{hook.plugin_id}'",
                    )
                except Exception as e:
                    logger.error(
                        f"✗ Failed to execute startup hook "
                        f"'{hook.hook_name}' "
                        f"from plugin '{hook.plugin_id}': {e}",
                        exc_info=True,
                    )

            # ---- Approval Service ----
            try:
                default_agent = await workspace_registry.get_agent(
                    "default",
                )
                if default_agent.channel_manager:
                    from .approvals import get_approval_service

                    get_approval_service().set_channel_manager(
                        default_agent.channel_manager,
                    )
            except Exception as e:
                logger.warning(f"Approval service setup skipped: {e}")

            # ---- Skill Pool builtin update + workspace auto-sync ----
            try:
                from ..agents.skill_system import run_pool_automation_pipeline
                from .routers.skills import post_pool_automation_inbox

                result = await asyncio.to_thread(
                    run_pool_automation_pipeline,
                )
                await post_pool_automation_inbox(result)
            except Exception:
                logger.warning(
                    "Skill Pool automation skipped on startup",
                    exc_info=True,
                )

            startup_elapsed = time.time() - startup_start_time
            logger.info(
                "Background startup completed in "
                f"{startup_elapsed:.3f} seconds",
            )
            if app.state.startup_ready.is_set():
                startup_display.complete(startup_elapsed)

        except Exception:
            logger.error(
                "Background startup encountered an error",
                exc_info=True,
            )

    _bg_task = asyncio.create_task(_background_startup())

    try:
        yield
    finally:
        # Cancel background startup if still in progress
        if not _bg_task.done():
            _bg_task.cancel()
            with suppress(asyncio.CancelledError):
                await _bg_task

        # Import jobs removed

        # ==================== Execute Shutdown Hooks ====================
        plugin_registry = getattr(app.state, "plugin_registry", None)
        if plugin_registry is not None:
            logger.info("Executing plugin shutdown hooks...")
            shutdown_hooks = plugin_registry.get_shutdown_hooks()
            for hook in shutdown_hooks:
                try:
                    logger.info(
                        f"Executing shutdown hook '{hook.hook_name}' "
                        f"from plugin '{hook.plugin_id}' (priority"
                        f"={hook.priority})",
                    )

                    result = hook.callback()
                    if inspect.iscoroutine(result) or inspect.isawaitable(
                        result,
                    ):
                        await result

                    logger.info(
                        f"✓ Completed shutdown hook '{hook.hook_name}' "
                        f"from plugin '{hook.plugin_id}'",
                    )
                except Exception as e:
                    logger.error(
                        f"✗ Failed to execute shutdown hook "
                        f"'{hook.hook_name}' "
                        f"from plugin '{hook.plugin_id}': {e}",
                        exc_info=True,
                    )

        # Stop AppServiceManager (ToolCoordinator shutdown, etc.)
        _app_svc = getattr(app.state, "app_services", None)
        if _app_svc is not None:
            try:
                await _app_svc.stop()
            except Exception as e:
                logger.error(f"Error stopping AppServiceManager: {e}")

        # Stop multi-agent manager (stops all agents and their components)
        multi_agent_mgr = getattr(app.state, "multi_agent_manager", None)
        if multi_agent_mgr is not None:
            logger.info("Stopping MultiAgentManager...")
            try:
                await multi_agent_mgr.stop_all()
            except Exception as e:
                logger.error(f"Error stopping MultiAgentManager: {e}")

        # These three cleanup tasks are independent; run in parallel.
        from ..agents.skill_system.hub import aclose_hub_client

        async def _stop_token_usage():
            logger.info("Stopping TokenUsageManager...")
            try:
                await token_usage_manager.stop()
            except Exception as e:
                logger.error(
                    f"Error stopping TokenUsageManager: {e}",
                )

        async def _close_hub():
            try:
                await aclose_hub_client()
            except Exception as e:
                logger.error(
                    f"Error closing skills hub HTTP client: {e}",
                )

        await asyncio.gather(
            _stop_token_usage(),
            _close_hub(),
        )

        logger.info("Application shutdown complete")
        startup_display.stop()


app = FastAPI(
    lifespan=lifespan,
    docs_url="/docs" if DOCS_ENABLED else None,
    redoc_url="/redoc" if DOCS_ENABLED else None,
    openapi_url="/openapi.json" if DOCS_ENABLED else None,
)
register_exception_handlers(app)

# Add agent context middleware for agent-scoped routes
app.add_middleware(AgentContextMiddleware)

app.add_middleware(AuthMiddleware)
app.add_middleware(RuntimeBoundaryMiddleware)

# Apply CORS middleware if CORS_ORIGINS is set
if CORS_ORIGINS:
    origins = [o.strip() for o in CORS_ORIGINS.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )


_CONSOLE_STATIC_ENV = "QWENPAW_CONSOLE_STATIC_DIR"


def _resolve_console_static_dir() -> str:
    from ..constant import EnvVarLoader

    static_dir = EnvVarLoader.get_str(_CONSOLE_STATIC_ENV)
    if static_dir:
        return static_dir
    # Shipped dist lives in the package as static data
    pkg_dir = Path(__file__).resolve().parent.parent
    candidate = pkg_dir / "console"
    if candidate.is_dir() and (candidate / "index.html").exists():
        return str(candidate)

    # Fallback to repo data
    repo_dir = pkg_dir.parent.parent
    candidate = repo_dir / "console" / "dist"
    if candidate.is_dir() and (candidate / "index.html").exists():
        return str(candidate)

    # Fallback to cwd data
    cwd = Path(os.getcwd())
    for subdir in ("console/dist", "console_dist"):
        candidate = cwd / subdir
        if candidate.is_dir() and (candidate / "index.html").exists():
            return str(candidate)

    fallback = cwd / "console" / "dist"
    logger.warning(
        f"Console static directory not found. Falling back to '{fallback}'.",
    )
    return str(fallback)


_CONSOLE_STATIC_DIR = _resolve_console_static_dir()
_CONSOLE_INDEX = (
    Path(_CONSOLE_STATIC_DIR) / "index.html" if _CONSOLE_STATIC_DIR else None
)
logger.info(f"STATIC_DIR: {_CONSOLE_STATIC_DIR}")

# The SPA entry (index.html) must never be cached: it references content-hashed
# JS/CSS bundles, so a stale cached index.html would keep pointing the WebView
# at old asset hashes after a rebuild (see desktop dev cache issue). The hashed
# assets under /assets remain safely cacheable because their name changes with
# their content.
_INDEX_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    # Pragma/Expires cover legacy proxies and older WebView caches that do not
    # honor Cache-Control on their own.
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/")
def read_root():
    if _CONSOLE_INDEX and _CONSOLE_INDEX.exists():
        return FileResponse(_CONSOLE_INDEX, headers=_INDEX_NO_CACHE_HEADERS)
    return {
        "message": (
            f"{PROJECT_NAME} web console is not available. "
            "If you installed the project from source code, please run "
            "`npm ci && npm run build` in the `console/` "
            f"directory, and restart {PROJECT_NAME} to enable the "
            "web console."
        ),
    }


@app.get("/api/version")
def get_version():
    """Return the current application version (public-safe payload)."""
    return {
        "version": __version__,
    }


@app.get("/api/doctor/runtime")
def get_doctor_runtime():
    """Return server runtime diagnostics for authenticated troubleshooting."""
    return {
        "python_executable": sys.executable,
        "python_environment": summarize_python_environment(),
    }


app.include_router(api_router, prefix="/api")

app.include_router(healthz_router, prefix="/api")

app.include_router(tool_calls_router, prefix="/api")

# Approval router: /api/approval/approve, /api/approval/deny, etc.
app.include_router(approval_router, prefix="/api")

# Loops router: /api/loops
app.include_router(loops_router, prefix="/api")

# Agent-scoped router: /api/agents/{agentId}/chats, etc.
agent_scoped_router = create_agent_scoped_router()
app.include_router(agent_scoped_router, prefix="/api")


# Console static files and SPA fallback
# Register these AFTER API routes to ensure proper routing priority
if os.path.isdir(_CONSOLE_STATIC_DIR):
    _console_path = Path(_CONSOLE_STATIC_DIR)

    def _serve_console_index():
        if _CONSOLE_INDEX and _CONSOLE_INDEX.exists():
            return FileResponse(
                _CONSOLE_INDEX,
                headers=_INDEX_NO_CACHE_HEADERS,
            )

        raise HTTPException(status_code=404, detail="Not Found")

    _assets_dir = _console_path / "assets"
    if _assets_dir.is_dir():
        app.mount(
            "/assets",
            StaticFiles(directory=str(_assets_dir)),
            name="assets",
        )

    @app.get("/console")
    @app.get("/console/")
    @app.get("/console/{full_path:path}")
    def _console_spa_alias(full_path: str = ""):
        _ = full_path
        return _serve_console_index()


# ── Server entry point (replaces cli/app_cmd.py) ──────────────────────


def _format_bind_address(host: str, port: int) -> str:
    """Return a readable bind address for startup logs."""
    normalized_host = host.strip()
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    return f"{normalized_host}:{port}"


def _warn_if_auth_off_non_loopback_bind(host: str, port: int) -> None:
    """Warn when QwenPaw is reachable beyond loopback without auth."""
    from .auth import is_auth_enabled
    from ..utils.http import is_loopback_host

    if is_auth_enabled() or is_loopback_host(host):
        return

    bind_address = _format_bind_address(host, port)
    warning = (
        "\n============================================================\n"
        f"SECURITY NOTICE: QwenPaw is bound to {bind_address} without "
        "authentication.\n\n"
        "Anyone who can reach this address may access QwenPaw APIs "
        "without login.\n\n"
        "Recommended:\n"
        "  - Restrict access to a trusted network interface or protected "
        "environment.\n"
        "  - Enable authentication with QWENPAW_AUTH_ENABLED=true if "
        "untrusted users or\n"
        "    processes may reach this address.\n"
        "============================================================"
    )
    if logger.isEnabledFor(logging.WARNING):
        logger.warning("%s", warning)
    else:
        print(warning, file=sys.stderr)


def run_server(
    host: str = "127.0.0.1",
    port: int = 8088,
    *,
    reload: bool = False,
    log_level: str = "info",
    hide_access_paths: tuple[str, ...] = (
        "/console/push-messages",
        "/console/inbox/events",
    ),
) -> None:
    """Configure the server process and start uvicorn."""
    import logging as _logging

    from ..config.utils import write_last_api
    from ..constant import LOG_LEVEL_ENV
    from ..utils.http import probe_host_for_bind_host
    from ..utils.logging import SuppressPathAccessLogFilter, setup_logger

    write_last_api(probe_host_for_bind_host(host), port)
    os.environ[LOG_LEVEL_ENV] = log_level
    if reload:
        os.environ["QWENPAW_RELOAD_MODE"] = "1"

    setup_logger(log_level)

    paths = [p for p in hide_access_paths if p]
    if paths:
        _logging.getLogger("uvicorn.access").addFilter(
            SuppressPathAccessLogFilter(paths),
        )

    _warn_if_auth_off_non_loopback_bind(host, port)

    import uvicorn

    uvicorn.run(
        "qwenpaw.app._app:app",
        host=host,
        port=port,
        reload=reload,
        workers=1,
        log_level=log_level,
        timeout_graceful_shutdown=5,
        ws_max_size=64 * 1024 * 1024,
    )


def main() -> None:
    """CLI entry point: parse args and start the server."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="qwenpaw",
        description="QwenPaw API Server",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8088,
        help="Bind port (default: 8088)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload (dev only)",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Log level (default: info)",
    )
    parser.add_argument(
        "--hide-access-paths",
        action="append",
        default=None,
        help="Path substrings to hide from access log (repeatable)",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"QwenPaw {__version__}",
    )

    args = parser.parse_args()

    hide_paths = tuple(args.hide_access_paths) if args.hide_access_paths else (
        "/console/push-messages",
        "/console/inbox/events",
    )

    run_server(
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
        hide_access_paths=hide_paths,
    )
