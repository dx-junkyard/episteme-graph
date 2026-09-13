"""core/llm_worker/ ガードレールの自動テスト。

正本: docs/architecture/consolidation_survey_2026-07.md Tier2 提案6「LLM worker 共通基盤」。
tension / structure_anchor / reconstruction / doubt.scope_candidates /
doubt.assumption_mining / doubt.falsification_conditions /
deliberation.standardization の7系統に個別実装されていた LLM クライアント・修復ループ・
コスト上限カウンタ・デーモンスレッド起動の骨格を core/llm_worker/ に集約した
（骨格の「組み方」の宣言は ``core/llm_worker/system.py`` の WorkerSystem、
系統ごとのドメイン語彙は ``core/<system>/system.py``）。この集約先自身が
既存の設計原則から逸脱しないことを構造的に守る。

- core/llm_worker/ は FastAPI を import しない（開発ルール2、テスタビリティ確保）
- core/llm_worker/ はベンダ SDK（openai / google.generativeai 等）を直接 import しない
  （LLM 呼び出しは必ず core/llm.py の公開 API 経由 — 開発ルール3）
- core/llm_worker/ は os.environ / os.getenv を直接使わない（開発ルール1: 環境変数は
  core/config.py の Settings に集約。各系統の env var 名はこのモジュールへ持ち込まない）
- 各系統固有の環境変数プレフィックス（TENSION_ / ANCHOR_ / RECON_ / DOUBT_SCOPE_ /
  DOUBT_ASSUMPTION_）がここに漏れ出ていない（差分注入だけで系統を分離する設計の裏付け）
- core/llm_worker/ に DELETE FROM を書かない（他レイヤーと同型の定型チェック）
"""

from __future__ import annotations

from pathlib import Path

from tests.guardrail_helpers import (
    assert_module_tree_does_not_import,
    assert_module_tree_forbids,
)

BACKEND = Path(__file__).resolve().parents[1]
_CORE_DIR = BACKEND / "core" / "llm_worker"


class TestCoreIsFrameworkFree:
    """core/llm_worker/ は FastAPI を import しない（テスタビリティ確保、開発ルール2）。"""

    def test_no_fastapi_import(self):
        assert_module_tree_does_not_import(_CORE_DIR, ["fastapi"])


class TestNoDirectVendorSdk:
    """LLM 呼び出しは必ず core/llm.py 経由（開発ルール3）。ベンダ SDK を直接 import しない。"""

    def test_no_vendor_sdk_import(self):
        assert_module_tree_does_not_import(
            _CORE_DIR,
            ["openai", "google.generativeai", "google.cloud.aiplatform", "vertexai"],
        )

    def test_client_uses_core_llm_public_api(self):
        src = (_CORE_DIR / "client.py").read_text(encoding="utf-8")
        assert "from core.llm import generate_text" in src


class TestNoDirectEnvAccess:
    """環境変数は core/config.py の Settings に集約（開発ルール1）。os.environ 直読み禁止。"""

    def test_no_os_environ_or_getenv(self):
        assert_module_tree_forbids(_CORE_DIR, ["os.environ", "os.getenv", "import os"])


class TestNoEnvVarNameLeakage:
    """各系統の環境変数名はこのモジュールに持ち込まない（差分注入だけで系統を分離する設計）。

    実際の env var 名解決は core/config.py の Settings フィールド名（例:
    tension_llm_model）経由で行い、呼び出し側（各 worker.py）が settings から解決した
    値・キーを渡す。llm_worker 自身は「どの系統か」を知らない。
    """

    def test_no_domain_specific_prefixes(self):
        assert_module_tree_forbids(
            _CORE_DIR,
            [
                "TENSION_",
                "ANCHOR_",
                "RECON_",
                "DOUBT_SCOPE_",
                "DOUBT_ASSUMPTION_",
                "tension_llm_model",
                "anchor_llm_model",
                "recon_llm_model",
                "doubt_scope_llm_model",
                "doubt_assumption_llm_model",
            ],
        )


class TestNoDeleteFrom:
    def test_no_delete_from(self):
        assert_module_tree_forbids(_CORE_DIR, ["DELETE FROM"])


class TestAllSystemsDelegateToCommonImplementation:
    """7系統の llm_client.py / repair.py / worker.py が共通実装へ委譲していること
    （重複コードが再発していないことの軽量な回帰チェック）。

    系統を追加したら ``_SYSTEM_DIRS`` に1行足す（llm_client / repair / worker /
    system の4ファイルを持つのが標準形）。
    """

    _SYSTEM_DIRS = [
        BACKEND / "core" / "tension",
        BACKEND / "core" / "structure_anchor",
        BACKEND / "core" / "reconstruction",
        BACKEND / "core" / "doubt" / "scope_candidates",
        BACKEND / "core" / "doubt" / "assumption_mining",
        BACKEND / "core" / "doubt" / "falsification_conditions",
        BACKEND / "core" / "deliberation" / "standardization",
    ]

    def test_llm_clients_delegate_to_base_client(self):
        for directory in self._SYSTEM_DIRS:
            path = directory / "llm_client.py"
            src = path.read_text(encoding="utf-8")
            assert "core.llm_worker.client import" in src, f"{path} does not delegate to core.llm_worker.client"
            assert "BaseJSONLLMClient" in src, f"{path} does not subclass BaseJSONLLMClient"

    def test_repairs_delegate_to_common_loop(self):
        for directory in self._SYSTEM_DIRS:
            path = directory / "repair.py"
            src = path.read_text(encoding="utf-8")
            assert "core.llm_worker.repair import" in src, f"{path} does not delegate to core.llm_worker.repair"
            assert "SYSTEM.run(" in src, f"{path} does not delegate to WorkerSystem.run"

    def test_workers_delegate_to_cost_gate(self):
        for directory in self._SYSTEM_DIRS:
            path = directory / "worker.py"
            src = path.read_text(encoding="utf-8")
            assert "core.llm_worker.cost_gate import" in src, f"{path} does not delegate to core.llm_worker.cost_gate"
            assert "SYSTEM.gate" in src, f"{path} does not share the WorkerSystem cost gate"

    def test_each_system_declares_exactly_one_worker_system(self):
        """系統ごとの設定キー・feature・上限既定は system.py の1宣言が正本。"""
        for directory in self._SYSTEM_DIRS:
            path = directory / "system.py"
            src = path.read_text(encoding="utf-8")
            assert src.count("SYSTEM = WorkerSystem(") == 1, f"{path} must declare exactly one WorkerSystem"
            assert "from core.llm_worker.system import" in src

    def test_workers_start_threads_through_the_shared_spawn(self):
        """デーモンスレッド起動は WorkerSystem.spawn に一本化（try/except・daemon・
        命名の扱いを系統ごとに書き分けない）。thread_factory は各 worker 自身の
        ``threading.Thread`` を渡す（テストの monkeypatch の継ぎ目）。"""
        for directory in self._SYSTEM_DIRS:
            src = (directory / "worker.py").read_text(encoding="utf-8")
            if "threading.Thread" not in src:
                continue
            assert "SYSTEM.spawn(" in src, f"{directory}/worker.py starts a thread without WorkerSystem.spawn"
            assert "thread_factory=threading.Thread" in src

    def test_no_system_reimplements_the_repair_loop_or_gate(self):
        """各系統が共通実装を迂回して独自ループ・独自 CostGate を持たないこと。"""
        for directory in self._SYSTEM_DIRS:
            for name in ("llm_client.py", "repair.py", "worker.py"):
                src = (directory / name).read_text(encoding="utf-8")
                assert "CostGate()" not in src, f"{directory}/{name} instantiates its own CostGate"
                assert "from core.llm import" not in src, f"{directory}/{name} bypasses BaseJSONLLMClient"
