from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from orca_api.main import create_app


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c
