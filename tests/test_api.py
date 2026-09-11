"""Tests for the call route with a mocked upstream and the audit route."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway.api import create_app
from gateway.config import Settings
from gateway.models import ToolSpec

CATALOG = [
    ToolSpec(
        name="billing.invoice.get",
        description="Fetch one invoice by id.",
        method="GET",
        url="http://billing.internal/invoices/{invoice_id}",
        input_schema={
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"],
        },
        allowed_callers=["assistant-finance"],
        rate_limit_per_minute=100,
        timeout_s=1.0,
        retries=0,
    )
]


def _upstream(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/invoices/INV-1042":
        return httpx.Response(200, json={"id": "INV-1042", "total_cents": 18400, "status": "open"})
    return httpx.Response(404, json={"detail": "not found"})


@pytest.fixture()
def client(tmp_path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'audit.db'}",
        catalog_path=str(tmp_path / "tools.yaml"),
    )
    app = create_app(settings=settings, specs=CATALOG, transport=httpx.MockTransport(_upstream))
    with TestClient(app) as test_client:
        yield test_client


def test_list_tools(client: TestClient) -> None:
    response = client.get("/tools")
    assert response.status_code == 200
    body = response.json()
    assert [tool["name"] for tool in body["tools"]] == ["billing.invoice.get"]
    assert body["tools"][0]["allowed_callers"] == ["assistant-finance"]


def test_call_tool_success(client: TestClient) -> None:
    response = client.post(
        "/tools/call",
        json={
            "tool": "billing.invoice.get",
            "arguments": {"invoice_id": "INV-1042"},
            "caller": "assistant-finance",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["output"]["total_cents"] == 18400
    assert body["audit_id"] is not None
    assert body["latency_ms"] >= 0


def test_call_tool_dry_run(client: TestClient) -> None:
    response = client.post(
        "/tools/call",
        json={
            "tool": "billing.invoice.get",
            "arguments": {"invoice_id": "INV-1042"},
            "caller": "assistant-finance",
            "dry_run": True,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["output"]["method"] == "GET"
    assert body["output"]["url"] == "http://billing.internal/invoices/INV-1042"


def test_call_tool_denied_caller(client: TestClient) -> None:
    response = client.post(
        "/tools/call",
        json={
            "tool": "billing.invoice.get",
            "arguments": {"invoice_id": "INV-1042"},
            "caller": "assistant-ops",
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["status"] == "denied"


def test_call_tool_invalid_arguments(client: TestClient) -> None:
    response = client.post(
        "/tools/call",
        json={"tool": "billing.invoice.get", "arguments": {}, "caller": "assistant-finance"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["status"] == "invalid"


def test_audit_recent(client: TestClient) -> None:
    client.post(
        "/tools/call",
        json={
            "tool": "billing.invoice.get",
            "arguments": {"invoice_id": "INV-1042"},
            "caller": "assistant-finance",
        },
    )
    response = client.get("/audit/recent?limit=5")
    assert response.status_code == 200
    records = response.json()["records"]
    assert len(records) == 1
    assert records[0]["tool"] == "billing.invoice.get"
    assert records[0]["caller"] == "assistant-finance"
    assert records[0]["status"] == "ok"
