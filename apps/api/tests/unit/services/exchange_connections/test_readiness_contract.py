from app.services.exchange_connections.readiness import supports_autonomous_preview


def test_supports_autonomous_preview_accepts_operator_review() -> None:
    assert supports_autonomous_preview("READY_FOR_OPERATOR_REVIEW") is True


def test_supports_autonomous_preview_accepts_legacy_preview_states() -> None:
    assert supports_autonomous_preview("READY_FOR_PREVIEW") is True
    assert supports_autonomous_preview("READY_FOR_ORDER_SUBMISSION") is True
    assert supports_autonomous_preview("NOT_READY_SUBMISSION_DISABLED") is True


def test_supports_autonomous_preview_rejects_non_preview_states() -> None:
    assert supports_autonomous_preview("PERMISSION_BLOCKED") is False
    assert supports_autonomous_preview("AUTHENTICATION_FAILED") is False
    assert supports_autonomous_preview(None) is False
