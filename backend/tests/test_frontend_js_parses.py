"""frontend/public/js/*.js が JavaScript として構文解析できることの検査（node 不在環境の代替）。

第 25 周（2026-10-23）: 並行する是正で admin.js に余分な ``});`` が混入し、砂場へ配った後にハーネス側の
目視で見つかった（管理画面全体が読み込めない状態になる）。node が無い開発機でも落とせるよう、
pure Python の esprima で全ファイルを parse する。esprima が無い環境では skip（CI は node で別途検査）。
"""
from __future__ import annotations

import pathlib

import pytest

esprima = pytest.importorskip("esprima")

JS_DIR = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "public" / "js"


@pytest.mark.parametrize("path", sorted(JS_DIR.glob("*.js")), ids=lambda p: p.name)
def test_js_file_parses(path: pathlib.Path) -> None:
    source = path.read_text(encoding="utf-8")
    try:
        esprima.parseScript(source, tolerant=False)
    except Exception as exc:  # noqa: BLE001 — esprima.Error は Exception 派生
        pytest.fail(f"{path.name}: {exc}")
