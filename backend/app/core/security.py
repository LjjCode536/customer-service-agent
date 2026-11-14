from dataclasses import dataclass

from fastapi import Header, HTTPException

# End-user roles.  They are the only roles a browser may claim and the only
# values accepted in ``allowed_roles``.  ``service`` is an internal Workflow
# identity and must never be forgeable from the public API.
USER_ROLES = frozenset({'customer', 'agent', 'manager'})
SERVICE_ROLE = 'service'
STAFF_ROLES = frozenset({'agent', 'manager', SERVICE_ROLE})
WRITE_ROLES = frozenset({'manager'})
VALID_VISIBILITIES = frozenset({'tenant', 'staff_only', 'restricted'})


@dataclass
class SecurityContext:
    user_id: str
    tenant_id: str
    role: str


def service_context(tenant_id: str) -> SecurityContext:
    """Return the non-user identity Workflow uses for in-tenant policy recall."""
    return SecurityContext('workflow-service', tenant_id, SERVICE_ROLE)


def can_read_visibility(
    ctx: SecurityContext,
    visibility: str | None,
    allowed_roles: list[str] | None,
) -> bool:
    """Apply the role × visibility read matrix for one document.

    ``tenant`` is readable by every role in the tenant, ``staff_only`` by staff
    roles, and ``restricted`` only by the explicitly allowed roles.  The
    internal ``service`` identity always sees ``restricted`` documents because
    Workflow retrieval must not be blocked by end-user grants.
    """
    if visibility == 'tenant':
        return True
    if visibility == 'staff_only':
        return ctx.role in STAFF_ROLES
    if visibility == 'restricted':
        if ctx.role == SERVICE_ROLE:
            return True
        return ctx.role in set(allowed_roles or ())
    return False


async def get_security_context(
    x_user_id: str = Header('U1001'),
    x_tenant_id: str = Header('TENANT-DEMO'),
    x_user_role: str = Header('customer'),
):
    role = (x_user_role or '').strip().lower()
    if role not in USER_ROLES:
        raise HTTPException(
            status_code=403,
            detail=f'Role {x_user_role!r} is not allowed on the HTTP API.',
        )
    return SecurityContext(x_user_id, x_tenant_id, role)
