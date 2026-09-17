"""Stable fingerprints for configuration baselines and runtime snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Mapping

from app.core.admin_config.models import ConfigValue
from app.core.admin_config.registry import ADMIN_CONFIG_REGISTRY_VERSION, current_config_values
from app.core.config import Settings
from app.version import __version__


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def baseline_fingerprint(
    settings: Settings,
    *,
    values: Mapping[str, ConfigValue] | None = None,
) -> str:
    """Return a content-addressed version for one complete registered snapshot."""

    payload = {
        "registry_version": ADMIN_CONFIG_REGISTRY_VERSION,
        "service_version": __version__,
        "release_commit": settings.service_release_commit,
        "values": dict(values) if values is not None else current_config_values(settings),
    }
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def format_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )
