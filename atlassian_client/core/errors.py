"""Error hierarchy. Every error carries a ``hint`` written for an LLM: what went wrong and what to try next."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AtlassianError(Exception):
    message: str
    hint: str | None = None
    status: int | None = None
    operation: str | None = None
    backend: str | None = None
    request_id: str | None = None         # from response headers when present
    retryable: bool = False
    details: dict[str, Any] = field(default_factory=dict)  # Atlassian errorMessages / errors

    def __str__(self) -> str:
        return f"{self.message}" + (f" — hint: {self.hint}" if self.hint else "")


class ConfigurationError(AtlassianError): ...
class AuthenticationError(AtlassianError): ...          # 401
class PermissionDeniedError(AtlassianError): ...        # 403, or MCP scope/permission-group disabled
class NotFoundError(AtlassianError): ...                # 404 (Atlassian also returns 404 when you lack permission)
class ConflictError(AtlassianError): ...                # 409 (e.g. Confluence version conflict)
class InvalidRequestError(AtlassianError): ...          # 400 / 422 — includes field errors
@dataclass
class RateLimitedError(AtlassianError):                  # 429 after retries are exhausted
    retry_after: float | None = None
class ServerError(AtlassianError): ...                  # 5xx after retries
class UnsupportedOperationError(AtlassianError): ...    # this backend cannot do this op → router falls back
class BackendUnavailableError(AtlassianError): ...      # deps/creds/health missing; nothing was sent → fallback
class NoBackendAvailableError(AtlassianError): ...      # aggregate of all attempts
class DeploymentMismatchError(AtlassianError): ...      # cloud-only op on Data Center
class PolicyDeniedError(AtlassianError): ...            # AccessPolicy forbids it
class ConfirmationDeclinedError(AtlassianError): ...
class AmbiguousReferenceError(AtlassianError): ...      # "Story Points" matches 2 fields; candidates in details
class WebhookVerificationError(AtlassianError): ...
class CliExecutionError(AtlassianError): ...


class UnsupportedDeliveryPathError(AtlassianError): ...  # a delivery path the research rules out (see events guards)
class IngressPolicyError(AtlassianError): ...            # a method would violate the stated ingress policy


_STATUS_HINTS: dict[int, tuple[type[AtlassianError], str]] = {
    400: (InvalidRequestError, "The request was rejected as invalid; read `details` for field/JQL errors, "
                               "then use jira_validate_jql or jira_list_fields."),
    401: (AuthenticationError, "Credentials are missing, expired or wrong. Check ATLASSIAN_EMAIL/ATLASSIAN_API_TOKEN "
                               "or refresh the OAuth token."),
    403: (PermissionDeniedError, "The credential lacks permission or scope for this operation (or an MCP permission "
                                 "group such as delete_jira is disabled by the admin)."),
    404: (NotFoundError, "Not found OR you lack permission to see it (Atlassian returns 404 for both). "
                         "Verify the key/id by searching."),
    409: (ConflictError, "The object changed since you read it. Re-fetch it and retry with the current version."),
    422: (InvalidRequestError, "The request was semantically invalid; read `details`."),
}


def error_for_status(status: int, message: str, *, operation: str | None = None, backend: str | None = None,
                     details: dict[str, Any] | None = None, retry_after: float | None = None) -> AtlassianError:
    """Map an HTTP status to the right error class with a hint."""
    if status == 429:
        err: AtlassianError = RateLimitedError(message, hint="Rate limited; slow down or reduce concurrency.",
                                               status=status, retryable=True)
        err.retry_after = retry_after  # type: ignore[attr-defined]
    elif status in _STATUS_HINTS:
        cls, hint = _STATUS_HINTS[status]
        err = cls(message, hint=hint, status=status)
    elif status >= 500:
        err = ServerError(message, hint="Atlassian returned a server error; retry later.", status=status, retryable=True)
    else:
        err = AtlassianError(message, status=status)
    err.operation, err.backend, err.details = operation, backend, details or {}
    return err
