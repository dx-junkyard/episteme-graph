"""制度指標カタログ（AdminIndicators）の DI 注入順に関する静的回帰テスト。

回帰の対象（2026-09-06 の初期化停止バグ）:
  ログイン後 admin.html の初期化が途中で止まり、教材一覧が「読み込み中...」の
  ままになった。原因は admin.js `initApp()` 内で
  `window.AdminIndicators.init({apiFetch})` が `initInterestDashboard()` より
  **後ろ**に置かれていたこと。`initInterestDashboard()` は同期的に
  `AdminIndicators.mount()` を叩き、admin-indicators.js の内部ラッパー
  `apiFetch()` が未注入の `fn`（undefined）を呼んで **同期例外**を投げ、
  その例外が `initApp()` の残り（教材一覧の読み込みなど）ごと巻き添えにした。

したがって本ファイルが固定する契約は2本立てである（どちらか片方だけでは
同種のバグが再発する）:
  A. 注入順 — `AdminIndicators.init(` は admin.js に1回だけ現れ、`initApp()`
     本体の中で `initInterestDashboard();` より前に位置し、`apiFetch` を注入する。
  B. fail-soft — admin-indicators.js の内部 `apiFetch` ラッパーは、注入前に
     呼ばれても同期例外を投げず rejected Promise を返す。`mount()` も例外を
     呼び出し元へ伝播させない（描画されないだけで初期化は続く）。

加えて、タブ活性化時に mount() を呼ぶ遅延経路（admin-llm-usage.js /
admin-discuss-observation.js）が `if (window.AdminIndicators)` で守られている
ことも確認する。

すべて ES5 ソースの静的解析（部分文字列・正規表現）。実 DOM / 外部 API は使わない。
キャッシュバスティングの `?v=` 文字列は運用のたびに変わるため、**値は検証しない**。
"""

from __future__ import annotations

import re
from pathlib import Path

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public"
ADMIN_JS = FRONTEND_DIR / "js" / "admin.js"
ADMIN_HTML = FRONTEND_DIR / "admin.html"
INDICATORS_JS = FRONTEND_DIR / "js" / "admin-indicators.js"
LLM_USAGE_JS = FRONTEND_DIR / "js" / "admin-llm-usage.js"
DISCUSS_OBSERVATION_JS = FRONTEND_DIR / "js" / "admin-discuss-observation.js"

INIT_CALL = "AdminIndicators.init("
MOUNT_CALL = "AdminIndicators.mount("


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _extract_js_function(src: str, name: str, *, indent: str = "  ") -> str:
    """`{indent}function {name}(` の開始位置から、同じインデントの次の関数宣言の
    直前（無ければ末尾）までを切り出す。

    ブレース数え上げではなく「次の同階層 function 宣言まで」で切るため、文字列や
    コメントの中の `{` `}` に影響されない。admin.js / admin-indicators.js とも
    トップレベル関数は2スペースインデント（IIFE 直下）で揃っているため成立する。
    """
    # 名前の直後に "(" を要求する（`initApp` が `initApproveActions` に前方一致して
    # 別関数を切り出す事故を防ぐ）。
    marker = "\n" + indent + "function " + name + "("
    assert marker in src, f"{name}() が見つかりません"
    start = src.index(marker) + 1
    tail = src[start + 1 :]
    rel_end = tail.find("\n" + indent + "function ")
    return tail[:rel_end] if rel_end != -1 else tail


# ---------------------------------------------------------------------------
# A. admin.js — 注入順（このバグの一次原因）
# ---------------------------------------------------------------------------


class TestInitInjectionOrder:
    def setup_method(self):
        self.src = _read(ADMIN_JS)
        self.init_app = _extract_js_function(self.src, "initApp")

    def test_init_is_called_exactly_once_in_admin_js(self):
        """DI 注入は1箇所だけ。2箇所に増えると「どちらが先か」が壊れやすくなる。"""
        assert self.src.count(INIT_CALL) == 1, (
            f"admin.js の {INIT_CALL} は1回だけであるべき"
            f"（実際: {self.src.count(INIT_CALL)}回）"
        )

    def test_init_is_inside_init_app_body(self):
        """注入は initApp() の中で行う（別関数へ逃がすと呼ばれない経路ができる）。"""
        assert INIT_CALL in self.init_app, (
            "AdminIndicators.init() が initApp() 本体に見つかりません"
        )

    def test_init_precedes_init_interest_dashboard_within_init_app(self):
        """バグの本体: initInterestDashboard() は同期的に mount() を叩くので、
        注入はそれより前でなければならない。"""
        assert "initInterestDashboard();" in self.init_app, (
            "initApp() から initInterestDashboard() が呼ばれていません"
        )
        init_idx = self.init_app.index(INIT_CALL)
        dashboard_idx = self.init_app.index("initInterestDashboard();")
        assert init_idx < dashboard_idx, (
            "AdminIndicators.init() は initApp() 内で initInterestDashboard() より"
            "前に呼ぶこと（後ろに置くと未注入のまま mount() され初期化が止まる）"
        )

    def test_init_call_injects_apifetch(self):
        """注入内容の契約: apiFetch を渡す（渡さないと fail-soft 経路に落ちるだけ）。"""
        m = re.search(
            r"AdminIndicators\.init\(\s*\{[^}]*apiFetch\s*:\s*apiFetch[^}]*\}\s*\)",
            self.src,
        )
        assert m, "AdminIndicators.init({ apiFetch: apiFetch }) の形で注入すること"

    def test_interest_dashboard_mount_site_guards_on_module_presence(self):
        """initApp() 経路の mount 呼び出しもモジュール未ロードに耐えること。"""
        dashboard = _extract_js_function(self.src, "initInterestDashboard")
        assert MOUNT_CALL in dashboard
        before = dashboard[: dashboard.index(MOUNT_CALL)]
        assert "if (window.AdminIndicators)" in before, (
            "initInterestDashboard() の mount() は if (window.AdminIndicators) で"
            "守ること"
        )


# ---------------------------------------------------------------------------
# B. admin-indicators.js — fail-soft（同期例外を投げない）
# ---------------------------------------------------------------------------


class TestIndicatorsFailSoft:
    def setup_method(self):
        self.src = _read(INDICATORS_JS)

    def test_api_fetch_wrapper_rejects_instead_of_throwing(self):
        """未注入時に同期 throw せず rejected Promise を返す（bug の二次原因）。"""
        wrapper = _extract_js_function(self.src, "apiFetch")
        assert "typeof fn" in wrapper, (
            "内部 apiFetch ラッパーは typeof fn を検査すること"
        )
        assert "Promise.reject(" in wrapper, (
            "未注入時は Promise.reject(...) を返すこと（同期 throw しない）"
        )

    def test_no_unguarded_direct_call_of_the_injected_fn(self):
        """typeof 検査を通らずに fn(...) を直接呼ぶ経路が残っていないこと。"""
        wrapper = _extract_js_function(self.src, "apiFetch")
        guard_idx = wrapper.index("typeof fn")
        for m in re.finditer(r"\bfn\s*\(", wrapper):
            assert m.start() > guard_idx, (
                "typeof fn の検査より前に fn(...) を呼び出しています"
                "（未注入時に同期例外になる）"
            )

    def test_mount_swallows_failures(self):
        """mount() は呼び出し元へ例外を伝播させない（try/catch もしくは .catch）。"""
        mount = _extract_js_function(self.src, "mount")
        assert "try {" in mount or ".catch(" in mount, (
            "mount() は try/catch か .catch() で失敗を握り潰すこと"
        )

    def test_mount_does_not_rethrow(self):
        mount = _extract_js_function(self.src, "mount")
        assert "throw " not in mount, "mount() から例外を投げ直さないこと"


# ---------------------------------------------------------------------------
# 遅延経路（タブ活性化時に mount する側）
# ---------------------------------------------------------------------------


class TestDeferredMountCallers:
    def test_tab_activated_callers_guard_on_module_presence(self):
        """initApp() より後（タブ活性化時）に走る mount 呼び出し元のガード。"""
        for path in (LLM_USAGE_JS, DISCUSS_OBSERVATION_JS):
            src = _read(path)
            assert MOUNT_CALL in src, f"{path.name}: mount 呼び出しが見つかりません"
            for m in re.finditer(re.escape(MOUNT_CALL), src):
                head = src[: m.start()]
                assert "if (window.AdminIndicators)" in head, (
                    f"{path.name}: mount() は if (window.AdminIndicators) で守ること"
                )


# ---------------------------------------------------------------------------
# admin.html — 読み込み順（値は検証しない）
# ---------------------------------------------------------------------------


class TestAdminHtmlLoadOrder:
    def setup_method(self):
        self.html = _read(ADMIN_HTML)

    def test_indicators_module_is_loaded_before_admin_js(self):
        idx_mod = self.html.index("/js/admin-indicators.js")
        idx_admin = self.html.index("/js/admin.js?")
        assert idx_mod < idx_admin

    def test_both_scripts_are_cache_busted(self):
        """`?v=` の**値**は運用のたびに変わるため、存在だけを固定する。"""
        for needle in ("/js/admin-indicators.js?v=", "/js/admin.js?v="):
            assert needle in self.html, f"{needle} が admin.html にありません"
