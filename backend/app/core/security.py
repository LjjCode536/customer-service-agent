from dataclasses import dataclass

from fastapi import Header


@dataclass
class SecurityContext:
    user_id: str
    tenant_id: str
    role: str

async def get_security_context(
    x_user_id: str = Header('U1001'),
    x_tenant_id: str = Header('TENANT-DEMO'),
    x_user_role: str = Header('customer'),
):
    return SecurityContext(x_user_id, x_tenant_id, x_user_role)
