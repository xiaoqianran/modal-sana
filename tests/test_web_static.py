from pathlib import Path

from fastapi.testclient import TestClient

from modal_sana.web.server import app

STATIC = Path("src/modal_sana/web/static")


def test_index_is_chinese_semantic_shell() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'lang="zh-CN"' in html
    assert "<nav" in html
    assert 'href="#main"' in html
    assert "<dialog" in html
    assert "Noto+Serif+SC" in html
    assert "Noto+Sans+SC" in html


def test_styles_are_catppuccin_mocha() -> None:
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert "#1e1e2e" in css
    assert "#cba6f7" in css
    assert "#fab387" in css
    assert "color-scheme:" in css
    assert "margin-inline" in css or "padding-inline" in css
    assert "prefers-reduced-motion" in css


def test_static_files_are_served() -> None:
    client = TestClient(app)
    index = client.get("/")
    assert index.status_code == 200
    assert "zh-CN" in index.text
    css = client.get("/static/styles.css")
    assert css.status_code == 200
    assert "#1e1e2e" in css.text


def test_workers_default_is_one_gpu() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "workers: meta?.defaults?.workers || 1" in js
    assert "Number(data.get(\"workers\") || 1)" in js


def test_frontend_skills_were_vendored() -> None:
    root = Path(".claude/skills")
    for name in ("html", "css", "tiny-css", "frontend-design"):
        skill = root / name / "SKILL.md"
        assert skill.is_file(), skill
        text = skill.read_text(encoding="utf-8")
        assert text.startswith("---")
        assert len(text) > 200
