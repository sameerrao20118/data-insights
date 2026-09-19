"""
Identity + entitlement -- R21 (docs/refactor_plan.md §6j). "My RM code"
used to be a dropdown: any user could pick any RM. That filters; it does
not authorise. Now a Principal is resolved from an identity provider
(local_dev today; the bank's IdP is a declared, NOT RUN contract) and
the worklist is scoped SERVER-SIDE from it. The dashboard shows who is
signed in; it offers no control to become someone else.

Rules, deny by default:
  - role rm         -> only rows whose relationship_manager_id is in the
                       principal's rm_ids; no rm_ids -> nothing.
  - role supervisor -> whole book (a team lead reading across RMs).
  - role admin      -> whole book.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: str
    rm_ids: frozenset[str]
    provider: str

    def describe(self) -> str:
        scope = "whole book" if self.role in ("supervisor", "admin") else \
            (", ".join(sorted(self.rm_ids)) if self.rm_ids else "no RM codes -> nothing visible")
        return f"{self.user_id} ({self.role}; {scope}; identity: {self.provider})"


class IdentityProviderNotRun(NotImplementedError):
    pass


def resolve_principal(profile) -> Principal:
    """profile.identity decides the provider. local_dev reads the profile
    block with env overrides (DATAINSIGHTS_USER, DATAINSIGHTS_ROLE,
    DATAINSIGHTS_RM_IDS=RM001,RM002) so one engineer can look at the
    book as a given RM without editing config. idp raises: Stage 3."""
    # read every field by its full path so the config-enforcement guard
    # (tests/test_config_fields_are_enforced.py) can see each has a reader
    provider, user_default, role_default, rm_default = (profile.identity.provider, profile.identity.user,
                                                        profile.identity.role, list(profile.identity.rm_ids))
    if provider == "idp":
        raise IdentityProviderNotRun(
            "identity.provider='idp' is contract-only, NOT RUN -- the bank's identity provider is "
            "wired at Stage 3 (docs/generalization_plan.md Phase 3). Use provider: local_dev locally.")
    user = os.environ.get("DATAINSIGHTS_USER") or user_default
    role = os.environ.get("DATAINSIGHTS_ROLE") or role_default
    if role not in ("rm", "supervisor", "admin"):
        raise ValueError(f"DATAINSIGHTS_ROLE={role!r} must be rm | supervisor | admin")
    env_rms = os.environ.get("DATAINSIGHTS_RM_IDS")
    rm_ids = [r.strip() for r in env_rms.split(",") if r.strip()] if env_rms is not None else rm_default
    return Principal(user_id=user, role=role, rm_ids=frozenset(rm_ids), provider=provider)


def scope_worklist(df: pd.DataFrame, principal: Principal) -> pd.DataFrame:
    """The server-side filter. A worklist with no relationship_manager_id
    column (a schema whose binding has none) is visible only to
    supervisor/admin -- an RM cannot be entitled to rows that carry no
    RM, so they see nothing rather than everything."""
    if principal.role in ("supervisor", "admin"):
        return df
    if "relationship_manager_id" not in df.columns or not principal.rm_ids:
        return df.iloc[0:0]
    return df[df["relationship_manager_id"].isin(principal.rm_ids)]
