"""Tests for schema validation, unknown tools and rate limiting."""

from __future__ import annotations

import pytest

from gateway.models import ToolSpec
from gateway.registry import RateLimiter, ToolRegistry, UnknownToolError


@pytest.fixture()
def registry() -> ToolRegistry:
    spec = ToolSpec(
        name="billing.invoice.get",
        description="Fetch one invoice by id.",
        method="GET",
        url="http://billing.internal/invoices/{invoice_id}",
        input_schema={
            "type": "object",
            "properties": {"invoice_id": {"type": "string", "minLength": 3}},
            "required": ["invoice_id"],
            "additionalProperties": False,
        },
        allowed_callers=["assistant-finance"],
        rate_limit_per_minute=2,
    )
    return ToolRegistry([spec])


def test_validate_accepts_valid_arguments(registry: ToolRegistry) -> None:
    cleaned = registry.validate("billing.invoice.get", {"invoice_id": "INV-1042"})
    assert cleaned == {"invoice_id": "INV-1042"}


def test_validate_rejects_missing_field(registry: ToolRegistry) -> None:
    with pytest.raises(ValueError) as excinfo:
        registry.validate("billing.invoice.get", {})
    assert "invoice_id" in str(excinfo.value)


def test_validate_rejects_extra_field(registry: ToolRegistry) -> None:
    with pytest.raises(ValueError):
        registry.validate("billing.invoice.get", {"invoice_id": "INV-1042", "debug": True})


def test_unknown_tool_raises(registry: ToolRegistry) -> None:
    with pytest.raises(UnknownToolError):
        registry.get("billing.invoice.delete")
    with pytest.raises(UnknownToolError):
        registry.validate("billing.invoice.delete", {})


def test_names_are_sorted(registry: ToolRegistry) -> None:
    assert registry.names() == ["billing.invoice.get"]


def test_rate_limiter_blocks_after_limit() -> None:
    limiter = RateLimiter(limit=2, window_s=60.0)
    assert limiter.take("assistant-finance:billing.invoice.get") is True
    assert limiter.take("assistant-finance:billing.invoice.get") is True
    assert limiter.take("assistant-finance:billing.invoice.get") is False
    assert limiter.take("assistant-ops:billing.invoice.get") is True


def test_registry_allow_checks_caller(registry: ToolRegistry) -> None:
    assert registry.allow("assistant-finance", "billing.invoice.get") is True
    assert registry.allow("assistant-ops", "billing.invoice.get") is False
