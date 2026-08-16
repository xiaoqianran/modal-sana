from pathlib import Path

from fastapi.testclient import TestClient

from modal_sana.web.server import app

STATIC = Path("src/modal_sana/web/static")


def test_index_is_chinese_semantic_shell() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'lang="zh-CN"' in html
    assert "<nav" in html
    assert "<header" in html
    assert 'href="#main"' in html
    assert "<dialog" in html
    assert "fonts.googleapis.com" not in html
    assert "Noto+Serif+SC" not in html


def test_styles_are_viewing_booth() -> None:
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert "#215a78" in css
    assert "#e8edf2" in css
    assert "#b56a3a" in css
    assert "color-scheme:" in css
    assert "margin-inline" in css or "padding-inline" in css
    assert "prefers-reduced-motion" in css
    assert "#1e1e2e" not in css
    assert "#cba6f7" not in css
    assert "Microsoft YaHei" in css
    assert "letter-spacing: -0.04em" not in css
    assert 'font-feature-settings: "ss06"' not in css
    assert ".table-wrap" in css
    assert "input:not([type=\"checkbox\"]" in css or "input:not([type=checkbox]" in css
    assert ".event-head" in css
    assert ".cap-prompt" in css
    assert ".lightbox-stage" in css


def test_static_files_are_served() -> None:
    client = TestClient(app)
    index = client.get("/")
    assert index.status_code == 200
    assert "zh-CN" in index.text
    css = client.get("/static/styles.css")
    assert css.status_code == 200
    assert "#215a78" in css.text


def test_job_table_shows_vram() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "function formatVram" in js
    assert "vram_reserved_mb" in js
    assert ">显存<" in js or "显存" in js


def test_four_k_defaults_to_rtx_pro_6000() -> None:
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "function isFourK" in js
    assert "RTX-PRO-6000" in js
    assert "recommended_gpu" in js
    assert "native_width) >= 4096" in js


def test_cost_page_is_first_class() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'data-page="cost"' in html
    assert 'href="/cost"' in html
    assert "费用" in html
    assert "async function costPage" in js
    assert "function paintCost" in js
    assert "function go" in js
    assert "function formatRate" in js
    assert "include_ledger: \"false\"" in js
    assert "renderChain" in js
    assert 'go(`/cost' in js or 'go("/cost' in js
    assert "event-head" in js
    assert "function shortId" in js
    assert "cap-prompt" in js
    assert "lightbox-stage" in js
    assert "tableWrap" in js


def test_spa_pages_are_real_http_routes() -> None:
    client = TestClient(app)
    for path in ("/", "/generate", "/batch", "/gallery", "/jobs", "/cost", "/benchmark", "/settings"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "zh-CN" in response.text
        assert 'href="/cost"' in response.text
    job = client.get("/job/job_example")
    assert job.status_code == 200
    assert "zh-CN" in job.text
    missing = client.get("/not-a-page")
    assert missing.status_code == 404


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
