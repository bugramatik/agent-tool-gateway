"""In-memory tool registry with JSON Schema validation and rate limiting."""

from __future__ import annotations

import time
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from gateway.models import ToolSpec


class UnknownToolError(KeyError):
    """Raised when a caller asks for a tool that is not in the catalog."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        return f"unknown tool: {self.name}"


class InvalidArgumentsError(ValueError):
    """Raised when arguments do not match the tool's input schema."""

    def __init__(self, name: str, errors: list[str]) -> None:
        super().__init__(f"invalid arguments for {name}: {'; '.join(errors)}")
        self.name = name
        self.errors = errors


class RateLimiter:
    """Token bucket keyed by an arbitrary string, refilled at a fixed rate."""

    def __init__(self, limit: int, window_s: float = 60.0) -> None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        self.limit = limit
        self.window_s = window_s
        self._buckets: dict[str, tuple[float, float]] = {}

    def _refill(self, key: str, now: float) -> tuple[float, float]:
        tokens, last = self._buckets.get(key, (float(self.limit), now))
        rate = self.limit / self.window_s
        tokens = min(float(self.limit), tokens + (now - last) * rate)
        return tokens, now

    def take(self, key: str) -> bool:
        """Consume one token for key. Returns False when the bucket is empty."""
        now = time.monotonic()
        tokens, last = self._refill(key, now)
        if tokens < 1.0:
            self._buckets[key] = (tokens, last)
            return False
        self._buckets[key] = (tokens - 1.0, last)
        return True

    def remaining(self, key: str) -> int:
        now = time.monotonic()
        tokens, _ = self._refill(key, now)
        return int(tokens)


class ToolRegistry:
    """Holds the tool specs, validates arguments and enforces per-caller limits."""

    def __init__(self, specs: list[ToolSpec]) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._validators: dict[str, Draft202012Validator] = {}
        self._limiters: dict[str, RateLimiter] = {}
        for spec in specs:
            if spec.name in self._specs:
                raise ValueError(f"duplicate tool name: {spec.name}")
            self._specs[spec.name] = spec
            self._validators[spec.name] = Draft202012Validator(spec.input_schema)
            self._limiters[spec.name] = RateLimiter(spec.rate_limit_per_minute)

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise UnknownToolError(name) from None

    def names(self) -> list[str]:
        return sorted(self._specs)

    def specs(self) -> list[ToolSpec]:
        return [self._specs[name] for name in self.names()]

    def validate(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Validate arguments against the tool's schema and return them unchanged."""
        spec = self.get(name)
        validator = self._validators[name]
        errors = sorted(validator.iter_errors(arguments), key=lambda e: list(e.path))
        if errors:
            raise InvalidArgumentsError(name, [self._describe(e) for e in errors])
        return arguments

    @staticmethod
    def _describe(error: ValidationError) -> str:
        location = ".".join(str(part) for part in error.path) or "arguments"
        return f"{location}: {error.message}"

    def allow(self, caller: str, name: str) -> bool:
        """Check the caller allow-list and then the rate limit for this caller."""
        spec = self.get(name)
        if spec.allowed_callers and caller not in spec.allowed_callers:
            return False
        return self._limiters[name].take(f"{caller}:{name}")

    def remaining(self, caller: str, name: str) -> int:
        self.get(name)
        return self._limiters[name].remaining(f"{caller}:{name}")
