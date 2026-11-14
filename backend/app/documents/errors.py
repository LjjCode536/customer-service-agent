"""Shared write-side errors and permission checks for document management."""

from app.core.security import WRITE_ROLES, SecurityContext


class InvalidState(ValueError):
    """Raised when a document lifecycle transition is not allowed (HTTP 400)."""


def require_manager(ctx: SecurityContext) -> None:
    if ctx.role not in WRITE_ROLES:
        raise PermissionError(
            'Only managers can upload, publish, archive, restore, or retry documents.'
        )
