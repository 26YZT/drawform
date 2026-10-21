"""Optional, same-origin presentation shell; CAD and workspace APIs stay authoritative."""

from pathlib import Path
import os

from flask import abort, request, send_file


FRONTEND_ENV = "DRAWFORM_FRONTEND_DIST"
PUBLIC_ENDPOINTS = frozenset({"drawform_frontend_asset"})


def configure_frontend(app):
    configured = os.environ.get(FRONTEND_ENV, "")
    if not configured:
        return
    root = Path(configured).expanduser().resolve(strict=True)
    if not root.is_dir() or not (root / "index.html").is_file():
        raise RuntimeError("前端构建目录缺少 index.html，请先完成 frontend build。")
    app.config[FRONTEND_ENV] = root

    @app.get("/assets/<path:name>", endpoint="drawform_frontend_asset")
    @app.get("/media/<path:name>", endpoint="drawform_frontend_asset")
    def asset(name):
        # Only release assets are public. A path prefix never exempts API data.
        category = "media" if request.path.startswith("/media/") else "assets"
        base = root / category
        candidate = (base / name).resolve()
        if not candidate.is_relative_to(base) or not candidate.is_file():
            abort(404)
        return send_file(candidate, conditional=True, max_age=3600)


def presentation_page(app, page, kind):
    if not app.config.get(FRONTEND_ENV):
        return page
    from .workspace_theme import themed_workspace
    view = "projects" if request.args.get("view") == "projects" else request.args.get("mode")
    return themed_workspace(page, kind, view=view,
                            source_open=view != "projects" and not bool(request.args.get("session")))


def home_response(app, legacy_page):
    root = app.config.get(FRONTEND_ENV)
    if root and "job" not in request.args and request.args.get("tool") != "step":
        response = send_file(root / "index.html", max_age=0)
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; img-src 'self' data:; "
            "media-src 'self'; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    return presentation_page(app, legacy_page, "step"), 200, {"Content-Type": "text/html; charset=utf-8"}
