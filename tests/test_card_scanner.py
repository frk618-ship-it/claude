import re
import shutil
import subprocess
from pathlib import Path

import pytest

from card_scanner.cli import main
from card_scanner.page import SCRIPTS, WEB, render_page, write_site

ROOT = Path(__file__).resolve().parents[1]


def test_render_page_inlines_everything():
    html = render_page()
    assert "/*__CSS__*/" not in html and "/*__JS__*/" not in html
    assert html.count("<script>") == 1  # one self-contained page: survives protect's document.write
    for name in SCRIPTS:
        assert f"/* ---- {name} ---- */" in html
    assert 'id="q"' in html and 'id="rail"' in html and 'id="dlg-edit"' in html
    # only pinned CDN versions are loaded at runtime
    for url in re.findall(r"https://cdn\.jsdelivr\.net/npm/[^'\"]+", html):
        assert re.search(r"@\d+\.\d+\.\d+/", url), url
    assert "claude-opus-5-5" in html


def test_write_site_adds_site_nav(tmp_path):
    page = write_site(tmp_path / "cards").read_text(encoding="utf-8")
    assert 'id="fabnav"' in page and 'href="../news/index.html"' in page
    assert re.search(r'href="\.\./cards/"[^>]*aria-current=page', page)
    bare = write_site(tmp_path / "bare", nav=False).read_text(encoding="utf-8")
    assert 'id="fabnav"' not in bare


def test_cli_build(tmp_path, capsys):
    assert main(["build", "-o", str(tmp_path / "out"), "--no-nav"]) == 0
    assert (tmp_path / "out" / "index.html").exists()
    assert "wrote" in capsys.readouterr().out


def test_site_switcher_lists_cards():
    from target_news_agent.fabnav import fab_nav

    assert 'href="../cards/"' in fab_nav("news", "../")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_parser_and_search_js():
    r = subprocess.run(["node", "--test", str(ROOT / "tests" / "js" / "card_scanner.test.cjs")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_js_files_exist():
    for name in SCRIPTS + ["template.html", "style.css"]:
        assert (WEB / name).is_file()
