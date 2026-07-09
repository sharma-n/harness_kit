"""Fixtures and helpers for live integration tests.

Tests in this package require a real LLM endpoint. They are skipped unless
``LIVE_TESTS_ENABLED=1`` is set. Configure the provider once in
``config_live.yaml`` (project root); the fixtures load it automatically.

The embedder is always ``FakeEmbedder`` — the embed endpoint is never called.
All stores are in-memory (no external infra needed beyond the LLM key).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from harness_kit.config import HarnessKitConfig
from harness_kit.service import AgentService
from harness_kit.agent.events import AgentEvent
from tests.conftest import FakeEmbedder

_LIVE_CONFIG_PATH = Path(__file__).parent.parent.parent / "config_live.yaml"

requires_live = pytest.mark.skipif(
    not os.environ.get("LIVE_TESTS_ENABLED"),
    reason="LIVE_TESTS_ENABLED not set — live integration tests skipped",
)


def _load_live_cfg(**overrides: dict) -> HarnessKitConfig:
    """Load config_live.yaml, apply shallow per-section overrides, return config."""
    import yaml

    with open(_LIVE_CONFIG_PATH) as f:
        data = yaml.safe_load(f)
    for section, values in overrides.items():
        if section in data and isinstance(data[section], dict):
            data[section].update(values)
        else:
            data[section] = values
    return HarnessKitConfig.from_dict(data)


@pytest.fixture
def conv_id() -> str:
    return f"live-{uuid.uuid4().hex[:12]}"


@pytest.fixture
async def live_service():
    """Real LLM (config_live.yaml) + FakeEmbedder + all in-memory stores."""
    service = AgentService.build(_load_live_cfg(), embedder=FakeEmbedder(dim=8))
    await service.astart()
    yield service
    await service.aclose()


async def run_turn(
    service: AgentService, user_id: str, conv_id: str, message: str
) -> list[AgentEvent]:
    return [e async for e in service.agent.run_turn(user_id, conv_id, message)]
