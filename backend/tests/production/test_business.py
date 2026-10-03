import importlib.util


def test_production_ticket_service_is_available():
    assert importlib.util.find_spec("app.production.business") is not None


def test_ticket_number_has_no_four_digit_ceiling():
    from app.production.business import format_ticket_number

    assert format_ticket_number(2026, 10000) == "IT-2026-10000"


def test_employee_cannot_resolve_ticket_and_support_can_reopen():
    from app.production.business import validate_transition
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as rejected:
        validate_transition("pending", "resolved", "employee")
    assert rejected.value.status_code == 403
    validate_transition("closed", "in_progress", "support")

