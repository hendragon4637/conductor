"""Per-service environment config — single source of truth for env vars."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ServiceConfig:
    """Resolved environment configuration for any Conductor service.

    Every service reads the same env vars; the ``SERVICE`` env distinguishes
    which role this container plays.
    """

    service: str = field(default_factory=lambda: os.environ.get("SERVICE", "unknown"))
    env: str = field(default_factory=lambda: os.environ.get("ENV", "development"))
    database_url: str = field(default_factory=lambda: os.environ["DATABASE_URL"])
    rabbit_url: str = field(default_factory=lambda: os.environ.get("RABBIT_URL", ""))
    rabbit_vhost: str = field(default_factory=lambda: os.environ.get("RABBIT_VHOST", "/"))
    workspace_root: str = field(
        default_factory=lambda: os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace")
    )
    contracts_version: str = ""

    # LiteLLM gateway
    litellm_url: str = field(
        default_factory=lambda: os.environ.get("LITELLM_URL", "http://litellm:4000")
    )
    litellm_key: str = field(
        default_factory=lambda: os.environ.get("LITELLM_MASTER_KEY", "")
    )

    @classmethod
    def from_env(cls) -> ServiceConfig:
        return cls()
