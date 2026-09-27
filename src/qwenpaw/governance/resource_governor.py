# -*- coding: utf-8 -*-
"""Resource Governor — Policy evaluation + audit logging.

Core responsibilities: policy evaluation, audit recording, dynamic rule
addition.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

from ..constant import WORKING_DIR
from ..utils.io_utils import get_sync_path_lock, run_sync_io
from .audit import AuditLog
from .policy import (
    GovernanceAction,
    GovernanceDecision,
    GovernancePolicy,
    GovernanceRule,
    ToolCallSpec,
    load_governance_policy,
    save_governance_policy,
)

logger = logging.getLogger(__name__)


class ResourceGovernor:
    """ResourceGovernor — core of policy and audit.

    Responsibilities:
        1. Policy evaluation: assert_policy(tool_call) → GovernanceDecision
        2. Audit logging: audit(tool_call, decision) records an audit log entry
        3. Dynamic rule addition: add_rule(...) after user approval
    """

    def __init__(
        self,
        workspace_dir: str,
        governance_dir: Optional[str] = None,
        coding_project_dir: Optional[str] = None,
        extra_project_dirs: Optional[list[str]] = None,
    ):
        self.workspace_dir = Path(workspace_dir)
        # Primary project directory. Falls back to the workspace when
        # unset so the CODING_PROJECT_DIR policy placeholder always
        # resolves to a concrete path.
        self.coding_project_dir = Path(
            coding_project_dir or workspace_dir,
        )
        # Remaining bound project directories (the list minus the
        # primary). They get their own ALLOW rules and sandbox mounts;
        # entries duplicating the primary/workspace are dropped. Keys come
        # from the shared ``dir_key`` so the fold follows the platform:
        # folding unconditionally would make ``/repo`` and ``/Repo`` look
        # identical on Linux and leave one of two bound roots ungranted.
        from ..services.project_directory import dir_key

        seen = {dir_key(self.workspace_dir), dir_key(self.coding_project_dir)}
        self.extra_project_dirs: list[Path] = []
        for raw in extra_project_dirs or []:
            try:
                path = Path(raw).expanduser()
            except (OSError, TypeError, ValueError):
                continue
            key = dir_key(path)
            if key in seen:
                continue
            seen.add(key)
            self.extra_project_dirs.append(path)
        # Policy is stored outside the workspace to prevent agent tampering.
        # Use ``<basename>_<hash>`` so two workspaces with the same basename
        # but different absolute paths (e.g. ``/Users/a/project`` vs
        # ``/Users/b/project``) do not share the same policy directory.
        if governance_dir is not None:
            self._governance_dir = Path(governance_dir)
        else:
            self._governance_dir = WORKING_DIR / "governance"
        ws_resolved = str(self.workspace_dir.resolve())
        ws_hash = hashlib.sha256(
            ws_resolved.encode("utf-8"),
        ).hexdigest()[:12]
        self._policy_dir = (
            self._governance_dir / f"{self.workspace_dir.name}_{ws_hash}"
        )
        self._policy_path = self._policy_dir / "policy.yaml"
        self._policy: Optional[GovernancePolicy] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Load policy."""
        with get_sync_path_lock(self._policy_path):
            self._policy_dir.mkdir(parents=True, exist_ok=True)
            self._policy = load_governance_policy(
                str(self._policy_dir),
                str(self.workspace_dir),
                str(self.coding_project_dir),
                extra_project_dirs=[
                    str(path) for path in self.extra_project_dirs
                ],
            )

            # Persist migrations/defaults while holding the same lock used by
            # approval transactions in other governor instances.
            try:
                save_governance_policy(
                    self._policy,
                    str(self._policy_dir),
                    str(self.workspace_dir),
                    str(self.coding_project_dir),
                )
            except Exception:
                logger.exception(
                    "ResourceGovernor.start: failed to persist policy.yaml",
                )

    def stop(self) -> None:
        """Finish this governor without closing process-wide resources.

        Policy mutation methods persist their transactions immediately.
        Saving this instance's snapshot here could overwrite rules committed
        by another request. The shared AuditLog is closed at process exit,
        not when an individual request-scoped governor stops.
        """

    # ------------------------------------------------------------------
    # Core interface 1: Policy evaluation
    # ------------------------------------------------------------------

    def assert_policy(self, tc_spec: ToolCallSpec) -> GovernanceDecision:
        """Evaluate policy for a tool call.

        Flow:
            1. policy.evaluate(tc_spec) → GovernanceDecision
            2. Log the governance decision (observability)
            3. Return decision (does NOT record audit)

        Returns GovernanceDecision:
            ALLOW → execute directly
            DENY  → rejected
            ASK   → ask user
        """
        decision = self.policy.evaluate(tc_spec)

        # Observability: log every governance decision so operators can
        # trace policy evaluation results without querying audit.db.
        target_repr = (tc_spec.target or "")[:120]
        logger.info(
            "governance decision: tool=%s target=%r action=%s source=%s "
            "reason=%s",
            tc_spec.tool_name,
            target_repr,
            decision.action.value,
            decision.source,
            decision.reason,
        )
        return decision

    # ------------------------------------------------------------------
    # Core interface 2: Audit logging
    # ------------------------------------------------------------------

    def audit(
        self,
        tc_spec: ToolCallSpec,
        decision: GovernanceDecision,
    ) -> None:
        """Record a governance decision to the audit log.

        Callers should invoke this after ``assert_policy()`` to persist
        the decision for compliance / forensics:

            decision = governor.assert_policy(tc_spec)
            governor.audit(tc_spec, decision)
        """
        if self._policy is not None and self._policy.audit_level == "none":
            return
        self.audit_log.record(
            str(self.workspace_dir),
            tc_spec,
            decision,
        )

    # ------------------------------------------------------------------
    # Core interface 3: Dynamic rule addition
    # ------------------------------------------------------------------

    def add_rule(self, rule: GovernanceRule) -> None:
        """Dynamically append a rule to the policy after user approval.

        Approved rules carry a duration (session / permanent).
        The rule is also persisted to policy.yaml.
        Note: rules are only appended to user_rules; builtin_rules are
        immutable.
        """
        with get_sync_path_lock(self._policy_path):
            policy = load_governance_policy(
                str(self._policy_dir),
                str(self.workspace_dir),
                str(self.coding_project_dir),
                extra_project_dirs=[
                    str(path) for path in self.extra_project_dirs
                ],
            )
            policy.add_rule(rule)
            save_governance_policy(
                policy,
                str(self._policy_dir),
                str(self.workspace_dir),
                str(self.coding_project_dir),
            )
            self._policy = policy

    async def add_approved_rule(
        self,
        tc_spec: ToolCallSpec,
        *,
        generalized_target: str,
    ) -> bool:
        """Add an ALLOW rule for a user-approved tool call.

        Args:
            generalized_target: the generalized target/pattern (e.g.
                ``"git *"``), already computed upstream by
                ``generalize_target_for_approval``.

        Returns True if a rule was actually added, False if skipped
        (e.g. builtin ask, empty target pattern).
        """
        if self.is_builtin_ask(tc_spec):
            return False

        try:
            if not generalized_target:
                logger.debug(
                    "ResourceGovernor: empty pattern, skipping rule "
                    "for tool=%s target=%s",
                    tc_spec.tool_name,
                    tc_spec.target,
                )
                return False

            match = f"{tc_spec.tool_name}({generalized_target})"
            rule = GovernanceRule(
                match=match,
                action=GovernanceAction.ALLOW,
                reason="user approved",
                grantee=tc_spec.agent_id or "*",
                duration="session",
                session_id=tc_spec.session_id,
            )
            await run_sync_io(self.add_rule, rule)
            logger.info(
                "ResourceGovernor: added approved rule: %s",
                rule.match,
            )
            return True
        except Exception:
            logger.debug(
                "ResourceGovernor: failed to persist approved rule",
                exc_info=True,
            )
            return False

    def is_builtin_ask(self, tc_spec: ToolCallSpec) -> bool:
        """Determine whether a tool call's ASK comes from builtin_rules.

        builtin ask → no rule recorded on approval (asks every time)
        user ask   → rule recorded on approval (won't ask next time)

        Called by tool_adapter's approval flow to decide whether to
        persist a new rule.
        """
        if not self._policy:
            return False
        source = self._policy.evaluate_source(tc_spec)
        return source == "builtin_rules"

    # ------------------------------------------------------------------
    # Property access
    # ------------------------------------------------------------------

    @property
    def policy(self) -> GovernancePolicy:
        if self._policy is None:
            raise RuntimeError("ResourceGovernor not started")
        return self._policy

    @property
    def audit_log(self) -> AuditLog:
        """Get the global AuditLog singleton."""
        return AuditLog.get_instance(
            db_dir=self._governance_dir,
        )
