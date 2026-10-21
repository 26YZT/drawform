"""The optional visual shell may expose release media, never workspace files."""
import pytest

from drawform.service.app import PAGE, create_app
from drawform.service.frontend import FRONTEND_ENV


@pytest.fixture
def visual_client(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "media/config").mkdir(parents=True)
    (dist / "index.html").write_text('<html><body><div id="root">visual-shell</div></body></html>')
    (dist / "assets/app.js").write_text('console.log("release")')
    (dist / "media/config/scene.png").write_bytes(b"release-media")
    private = tmp_path / "private.step"
    private.write_text("private engineering data")
    (dist / "assets/escape.step").symlink_to(private)
    monkeypatch.setenv(FRONTEND_ENV, str(dist))
    app = create_app(tmp_path / "work", token="ui-test-token", auto_resume=False)
    app.testing = True
    yield app.test_client()
    service = app.extensions["drawform_modeling"]
    service.stop()
    service.worker.join(timeout=5)
    service.jobs.stop(timeout=5)


def test_home_and_legacy_deep_links_are_separate(visual_client):
    home = visual_client.get("/")
    assert home.status_code == 200 and b"visual-shell" in home.data
    assert "script-src 'self'" in home.headers["Content-Security-Policy"]
    assert home.headers["Cache-Control"] == "no-cache"
    for url in ("/?tool=step", "/?job=historical&version=1", "/?job="):
        review = visual_client.get(url)
        assert review.status_code == 200
        assert 'id="drawform-workspace-theme"' in review.text
        assert 'id="step-file"' in review.text
        assert "visual-shell" not in review.text
    model = visual_client.get("/workbench/?mode=drawing")
    assert model.status_code == 200 and 'id="drawing-file"' in model.text
    assert 'id="drawform-workspace-theme"' in model.text


def test_only_release_assets_are_public_and_symlinks_cannot_escape(visual_client):
    for url, expected in (("/assets/app.js", b"release"),
                          ("/media/config/scene.png", b"release-media")):
        response = visual_client.get(url)
        assert response.status_code == 200 and expected in response.data
    for url in ("/assets/escape.step", "/assets/../private.step",
                "/assets/%2e%2e/private.step", "/media/config/missing.png"):
        response = visual_client.get(url)
        assert response.status_code == 404
        assert b"private engineering data" not in response.data
    for url in ("/jobs", "/jobs/private/files/step", "/api/workbench/v1/projects",
                "/api/workbench/v1/capabilities", "/static/private.step"):
        assert visual_client.get(url).status_code == 401
    assert visual_client.post("/jobs").status_code == 401
    assert visual_client.post("/assets/app.js").status_code == 401


def test_legacy_profile_remains_available_without_frontend(tmp_path, monkeypatch):
    monkeypatch.delenv(FRONTEND_ENV, raising=False)
    app = create_app(tmp_path / "legacy", token="ui-test-token", auto_resume=False)
    try:
        client = app.test_client()
        assert client.get("/").text == PAGE
        assert client.get("/assets/app.js").status_code == 401
    finally:
        service = app.extensions["drawform_modeling"]
        service.stop()
        service.worker.join(timeout=5)
        service.jobs.stop(timeout=5)


def test_missing_frontend_build_fails_before_workers_start(tmp_path, monkeypatch):
    monkeypatch.setenv(FRONTEND_ENV, str(tmp_path))
    with pytest.raises(RuntimeError, match="index.html"):
        create_app(tmp_path / "unused", token="ui-test-token")
    assert not (tmp_path / "unused").exists()
