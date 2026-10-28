from __future__ import annotations

import os

import pytest

from atlian_assiant.config import AtlassianConfig
from atlian_assiant.events.config import EventPlaneConfig

# Unit tests must never see a developer's real credentials: ignore .env and any ATLASSIAN_* environment.
# Live checks opt in explicitly (scripts/live_smoke.py), never through the test suite.
for _settings in (AtlassianConfig, EventPlaneConfig):
    _settings.model_config["env_file"] = None
for _name in [n for n in os.environ if n.startswith("ATLASSIAN_")]:
    del os.environ[_name]

LAYERS = {
    "layer_01": "protocol contract suites", "layer_02": "FakeBackend", "layer_03": "HTTP (respx / mock transport)",
    "layer_07": "pagination", "layer_08": "formats", "layer_09": "router",
}


def pytest_configure(config: pytest.Config) -> None:
    for name, desc in LAYERS.items():
        config.addinivalue_line("markers", f"{name}: {desc}")
    for i in range(1, 18):
        config.addinivalue_line("markers", f"layer_{i:02d}: test layer {i}")


@pytest.fixture(params=["asyncio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    return str(request.param)
