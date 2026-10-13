"""Small server-owned identity boundary for the local interview demo.

The browser never supplies an email or role to approval code.  A deployment
adapter may later replace this module with an OIDC/JWT principal without
changing the governed workflow.
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass

from approval_policy import ROLE_LEVELS, approver_bindings


@dataclass(frozen=True)
class ActorContext:
    email: str
    role: str
    source: str = "server_config"

    def to_dict(self) -> dict:
        return asdict(self)


def get_current_actor() -> ActorContext:
    """Resolve the current demo principal exclusively from server settings."""
    email = os.getenv("HITL_CURRENT_ACTOR_EMAIL", "").strip().lower()
    bindings = approver_bindings()
    if not email:
        candidates = [
            (max((ROLE_LEVELS.get(role, -1) for role in roles), default=-1), candidate)
            for candidate, roles in bindings.items()
        ]
        if not candidates:
            raise PermissionError(
                "服务器没有可用审批主体，请配置 HITL_APPROVER_BINDINGS"
            )
        email = max(candidates, key=lambda item: (item[0], item[1]))[1]
        source = "server_default"
    else:
        source = "server_config"
    roles = bindings.get(email, set())
    authorized = [role for role in roles if role in ROLE_LEVELS]
    if not authorized:
        raise PermissionError(
            "当前服务端审批主体不在 HITL_APPROVER_BINDINGS 授权范围内"
        )
    role = max(authorized, key=lambda item: ROLE_LEVELS[item])
    return ActorContext(email=email, role=role, source=source)
