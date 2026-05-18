"""FastAPI application: tool listing, tool calls, audit queries and metrics."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from gateway.audit import AuditStore
from gateway.config import Settings, get_settings, load_catalog
from gateway.executor import ToolExecutor
from gateway.models import ToolCallRequest, ToolCallResult
from gateway.registry import ToolRegistry

logger = logging.getLogger(__name__)

CALLS = Counter(
    "gateway_tool_calls_total",
    "Tool calls handled by the gateway.",
    ["tool", "status"],
)
DENIALS = Counter(
    "gateway_policy_denials_total",
    "Tool calls refused by policy.",
    ["tool", "reason"],
)
LATENCY = Histogram(
    "gateway_tool_latency_seconds",
    "End to end latency of a tool call.",
    ["tool"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0),
)
TOOLS = Gauge("gateway_tools_registered", "Tools in the loaded catalog.")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application. Settings are read once at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        cfg = settings or get_settings()
        specs = load_catalog(cfg.catalog_path)
        registry = ToolRegistry(specs)
        store = AuditStore.from_url(cfg.database_url)
        client = httpx.AsyncClient(timeout=cfg.request_timeout_s)
        app.state.settings = cfg
        app.state.registry = registry
        app.state.audit = store
        app.state.executor = ToolExecutor(registry, cfg, client)
        TOOLS.set(len(specs))
        logger.info("loaded %d tools from %s", len(specs), cfg.catalog_path)
        try:
            yield
        finally:
            await client.aclose()
            store.close()

    app = FastAPI(
        title="Agent Tool Gateway",
        version="0.3.0",
        description="Typed, audited tool calls for LLM agents.",
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, object]:
        registry: ToolRegistry = request.app.state.registry
        return {"status": "ok", "tools": len(registry.names())}

    @app.get("/tools")
    async def list_tools(request: Request) -> list[dict[str, object]]:
        registry: ToolRegistry = request.app.state.registry
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "method": spec.method,
                "input_schema": spec.input_schema,
                "timeout_s": spec.timeout_s,
            }
            for spec in (registry.get(name) for name in registry.names())
        ]

    @app.post("/tools/call", response_model=ToolCallResult)
    async def call_tool(payload: ToolCallRequest, request: Request) -> JSONResponse:
        registry: ToolRegistry = request.app.state.registry
        executor: ToolExecutor = request.app.state.executor
        store: AuditStore = request.app.state.audit

        if payload.tool not in registry.names():
            DENIALS.labels(tool=payload.tool, reason="unknown_tool").inc()
            raise HTTPException(status_code=404, detail=f"unknown tool: {payload.tool}")

        if not registry.allow(payload.caller, payload.tool):
            DENIALS.labels(tool=payload.tool, reason="caller_not_allowed").inc()
            result = ToolCallResult(
                tool=payload.tool,
                status="denied",
                latency_ms=0.0,
                output=None,
                error=f"caller {payload.caller} is not allowed to call {payload.tool}",
                audit_id=0,
            )
            result.audit_id = store.record(result, payload.caller)
            CALLS.labels(tool=payload.tool, status="denied").inc()
            return JSONResponse(status_code=403, content=result.model_dump())

        try:
            registry.validate(payload.tool, payload.arguments)
        except ValueError as exc:
            DENIALS.labels(tool=payload.tool, reason="invalid_arguments").inc()
            result = ToolCallResult(
                tool=payload.tool,
                status="invalid",
                latency_ms=0.0,
                output=None,
                error=str(exc),
                audit_id=0,
            )
            result.audit_id = store.record(result, payload.caller)
            CALLS.labels(tool=payload.tool, status="invalid").inc()
            return JSONResponse(status_code=422, content=result.model_dump())

        result = await executor.call(payload)
        result.audit_id = store.record(result, payload.caller)
        CALLS.labels(tool=payload.tool, status=result.status).inc()
        LATENCY.labels(tool=payload.tool).observe(result.latency_ms / 1000.0)
        code = 200 if result.status in {"ok", "dry_run"} else 502
        return JSONResponse(status_code=code, content=result.model_dump())

    @app.get("/audit/recent")
    async def audit_recent(
        request: Request, limit: int = Query(default=50, ge=1, le=500)
    ) -> list[dict[str, object]]:
        store: AuditStore = request.app.state.audit
        return store.recent(limit=limit)

    @app.get("/audit/summary")
    async def audit_summary(request: Request) -> dict[str, object]:
        store: AuditStore = request.app.state.audit
        return store.summary()

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
