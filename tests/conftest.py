from __future__ import annotations

import pytest

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
