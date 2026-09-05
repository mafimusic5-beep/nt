from __future__ import annotations

from fastapi.testclient import TestClient

import api


def test_public_device_gate_authorize_route_is_exposed_and_validated():
    paths = {
        route.path
        for route in api.app.routes
        if "POST" in (getattr(route, "methods", set()) or set())
    }
    assert "/internal/device-gate/authorize" in paths
    assert "/api/device-gate/authorize" in paths

    response = TestClient(api.app).post("/api/device-gate/authorize", json={})
    assert response.status_code == 422
