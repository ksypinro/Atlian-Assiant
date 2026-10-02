from atlassian_client.core import errors as E


def test_every_class_of_the_design_exists():
    names = ["AtlassianError", "ConfigurationError", "AuthenticationError", "PermissionDeniedError", "NotFoundError", "ConflictError",
             "InvalidRequestError", "RateLimitedError", "ServerError", "UnsupportedOperationError", "BackendUnavailableError",
             "NoBackendAvailableError", "DeploymentMismatchError", "PolicyDeniedError", "ConfirmationDeclinedError",
             "AmbiguousReferenceError", "WebhookVerificationError", "CliExecutionError"]
    for n in names:
        assert issubclass(getattr(E, n), E.AtlassianError), n


def test_status_mapping_and_hints():
    cases = {400: E.InvalidRequestError, 401: E.AuthenticationError, 403: E.PermissionDeniedError, 404: E.NotFoundError,
             409: E.ConflictError, 422: E.InvalidRequestError, 429: E.RateLimitedError, 502: E.ServerError}
    for status, cls in cases.items():
        err = E.error_for_status(status, "boom", operation="op", backend="rest", details={"k": 1})
        assert type(err) is cls and err.status == status and err.hint and err.operation == "op" and err.details == {"k": 1}
    assert "permission" in E.error_for_status(404, "x").hint.lower()
    assert E.error_for_status(429, "x", retry_after=7).retry_after == 7 and E.error_for_status(503, "x").retryable


def test_str_includes_the_hint():
    assert "hint:" in str(E.NotFoundError("missing", hint="try X"))
    assert str(E.NotFoundError("missing")) == "missing"
