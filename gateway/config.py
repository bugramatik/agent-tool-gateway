"""Settings from the environment and the tool catalog loader."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from gateway.models import ToolSpec

DEFAULT_CATALOG = Path(__file__).resolve().parent.parent / "catalog" / "tools.yaml"


class Settings(BaseModel):
    """Runtime configuration, read once at start-up."""

    database_url: str = "sqlite:///./gateway.db"
    catalog_path: str = str(DEFAULT_CATALOG)
    request_timeout_s: float = Field(default=2.0, gt=0)
    log_level: str = "info"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("GATEWAY_DATABASE_URL", cls.model_fields["database_url"].default),
            catalog_path=os.getenv("GATEWAY_CATALOG_PATH", cls.model_fields["catalog_path"].default),
            request_timeout_s=float(
                os.getenv("GATEWAY_REQUEST_TIMEOUT_S", cls.model_fields["request_timeout_s"].default)
            ),
            log_level=os.getenv("GATEWAY_LOG_LEVEL", cls.model_fields["log_level"].default),
        )


class CatalogError(ValueError):
    """Raised when the catalog file is missing or does not match ToolSpec."""


def load_catalog(path: str | os.PathLike[str]) -> list[ToolSpec]:
    """Read a YAML catalog and return its tools, failing loudly on bad entries."""

    catalog_path = Path(path)
    if not catalog_path.is_file():
        raise CatalogError(f"catalog not found: {catalog_path}")

    raw = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    entries = raw.get("tools") if isinstance(raw, dict) else raw
    if not isinstance(entries, list) or not entries:
        raise CatalogError(f"catalog {catalog_path} holds no tools")

    specs: list[ToolSpec] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        try:
            spec = ToolSpec.model_validate(entry)
        except ValidationError as exc:
            raise CatalogError(f"tool #{index} in {catalog_path} is invalid: {exc}") from exc
        if spec.name in seen:
            raise CatalogError(f"duplicate tool name in catalog: {spec.name}")
        seen.add(spec.name)
        specs.append(spec)
    return specs


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process settings, cached for the lifetime of the process."""

    return Settings.from_env()
