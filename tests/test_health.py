"""Smoke test for the FastAPI app — confirms /api/health responds."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health(client: TestClient) -> None:
    """``/api/health`` returns ``{"status": "ok"}``."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
