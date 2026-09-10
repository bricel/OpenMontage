"""Unit tests for the Circuit-video MCP helpers (no live Cypress / S3)."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "mcp_servers"))

from circuit_video import s3 as s3mod  # noqa: E402
from circuit_video.config import (  # noqa: E402
    validate_base_url,
    validate_render_id,
    validate_tutorial_name,
)
from circuit_video.server import TOOLS, _call, _handle  # noqa: E402
from circuit_video.service import list_tutorials, render_argv  # noqa: E402


def test_validate_inputs():
    assert validate_tutorial_name("sales-tour") == "sales-tour"
    with pytest.raises(ValueError):
        validate_tutorial_name("../etc/passwd")
    assert validate_base_url("https://demo.example.com/app/") == "https://demo.example.com/app"
    with pytest.raises(ValueError):
        validate_base_url("file:///tmp")
    assert validate_render_id("sales-tour-ab12cd34") == "sales-tour-ab12cd34"
    with pytest.raises(ValueError):
        validate_render_id("bad id")


def test_list_tutorials_from_temp_client(tmp_path):
    spec_dir = tmp_path / "cypress" / "e2e-tutorials" / "sales"
    spec_dir.mkdir(parents=True)
    spec = spec_dir / "sales-tour.tutorial.cy.js"
    spec.write_text("describe('sales', () => {});")
    (spec_dir / "sales-tour.tutorial.json").write_text('{"title":"Sales"}')
    out = list_tutorials({"client_dir": str(tmp_path)})
    assert out["tutorials"] == [{
        "name": "sales-tour",
        "spec": "cypress/e2e-tutorials/sales/sales-tour.tutorial.cy.js",
        "has_recipe": True,
        "has_timings": False,
    }]


def test_render_argv_includes_base_url():
    cfg = {
        "client_dir": "/tmp/client",
        "narration_url": "http://127.0.0.1:5557",
        "render_runtime": "ffmpeg",
    }
    argv = render_argv(
        cfg, tutorial="sales-tour", project_id="sales-tour-test",
        base_url="https://demo.example.com", offline=True, music="bed.mp3",
    )
    assert "--tutorial" in argv and "sales-tour" in argv
    assert argv[argv.index("--base-url") + 1] == "https://demo.example.com"
    assert "--offline-narration" in argv
    assert argv[argv.index("--music") + 1] == "bed.mp3"
    assert argv[0].endswith("python3") or "python" in Path(argv[0]).name
    assert argv[1].endswith("render_tutorial.py")


def test_s3_key_and_public_url():
    key = s3mod.object_key("openmontage/tutorials/", "sales-tour-ab12", "final.mp4")
    assert key == "openmontage/tutorials/sales-tour-ab12/final.mp4"
    url = s3mod.public_url("circuit-kubernetes", "eu-central-1", key)
    assert url.startswith("https://circuit-kubernetes.s3.eu-central-1.amazonaws.com/")
    assert url.endswith("/openmontage/tutorials/sales-tour-ab12/final.mp4")
    cdn = s3mod.public_url("circuit-kubernetes", "eu-central-1", key, "https://cdn.example.com")
    assert cdn == "https://cdn.example.com/openmontage/tutorials/sales-tour-ab12/final.mp4"


def test_presign_get_is_stable_for_frozen_time():
    now = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
    url = s3mod.presign_get(
        bucket="circuit-kubernetes",
        key="openmontage/tutorials/demo/final.mp4",
        region="eu-central-1",
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        expires=3600,
        now=now,
    )
    again = s3mod.presign_get(
        bucket="circuit-kubernetes",
        key="openmontage/tutorials/demo/final.mp4",
        region="eu-central-1",
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        expires=3600,
        now=now,
    )
    assert url == again
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
    assert "X-Amz-Date=20260830T120000Z" in url
    assert "X-Amz-Expires=3600" in url
    sig = url.split("X-Amz-Signature=")[1]
    assert len(sig) == 64 and all(c in "0123456789abcdef" for c in sig)


def test_mcp_initialize_and_tools_list():
    init = _handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "0"},
        },
    })
    assert init["result"]["serverInfo"]["name"] == "circuit-video"
    assert "tools" in init["result"]["capabilities"]
    listed = _handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == {t["name"] for t in TOOLS}
    assert names == {"list_tutorials", "doctor", "render_tutorial", "get_render", "upload_video"}
    render = next(t for t in listed["result"]["tools"] if t["name"] == "render_tutorial")
    assert "base_url" in render["inputSchema"]["required"]


def test_render_tutorial_rejects_bad_url():
    result = _call("render_tutorial", {"base_url": "not-a-url", "tutorial": "sales-tour"})
    assert result.get("isError") is True
    assert "http" in result["content"][0]["text"].lower()


def test_unknown_method():
    reply = _handle({"jsonrpc": "2.0", "id": 9, "method": "nope", "params": {}})
    assert reply["error"]["code"] == -32601
