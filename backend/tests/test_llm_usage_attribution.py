"""LLM 使用量計測（U層）の帰属（attribution）配線テスト — Task C。

正本ドキュメント: docs/features/llm_usage_metering_design.md §6「帰属（attribution）—
contextvars による feature 伝搬」。既存ガードレールテストの流儀
（test_image_library_guardrails.py 等）を踏襲し、ソースコードを読み込んで構造的に検査する。

検査項目:
1. 各配線箇所（ファイル×feature 文字列）に usage_context / bind_usage_context /
   set_current_feature の呼び出しが実在すること（チェックリスト形式）。
2. リクエストハンドラを持つ routes/*.py に ``bind_usage_context`` が現れないこと
   （スレッド再利用による文脈漏れの構造的防止, §6 注記）。ただし lecture_studio.py は
   同一ファイルに daemon thread の worker 関数（``_batch_*_worker``）を同居させている
   ため、``bind_usage_context`` はその thread-target 関数の中に限って許可する。
3. 配線済みの feature 文字列がすべて ``core/llm_usage/schema.py`` の ``KNOWN_FEATURES``
   に収載されていること（pipeline stage は orchestrator.py の ``report_start`` 呼び出し
   から動的に導出し、schema.py への転記漏れを検出する）。
4. 機能テスト: tension worker（``run_tension_mining``）を DB 不要のフェイクで実行し、
   ``agent.run`` 呼び出し時点の ``current_usage_context()`` が
   ``feature='learning:tension'`` になっていることを確認する（bind_usage_context が
   実際に contextvars 経由で下流へ伝搬することの実地検証）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
SRC = ROOT / "src"
for _p in (str(BACKEND), str(BACKEND / "api"), str(SRC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _read(rel_path: str) -> str:
    return (BACKEND / rel_path).read_text(encoding="utf-8")


_ORCHESTRATOR = "core/document_pipeline/orchestrator.py"
_LEARNING = "api/routes/learning.py"
_ADMIN = "api/routes/admin.py"
_LECTURE_STUDIO = "api/routes/lecture_studio.py"
_ADMIN_ASSISTANT = "api/routes/admin_assistant.py"
_THEORY_COMPONENTS = "api/routes/theory_components.py"
_ATLAS_GENERATOR = "core/atlas_generator.py"
_TENSION_WORKER = "core/tension/worker.py"
_ANCHOR_WORKER = "core/structure_anchor/worker.py"
_RECON_WORKER = "core/reconstruction/worker.py"
_SCOPE_WORKER = "core/doubt/scope_candidates/worker.py"
_ASSUMPTION_WORKER = "core/doubt/assumption_mining/worker.py"
_SCHEMA = "core/llm_usage/schema.py"

_ROUTE_FILES_WITH_HANDLERS_ONLY = (_LEARNING, _ADMIN, _ADMIN_ASSISTANT, _THEORY_COMPONENTS)


# ===========================================================================
# 1. 配線チェックリスト（ファイル × 期待される呼び出し文字列）
# ===========================================================================

# 各エントリ: (相対パス, [ソースに存在すべき部分文字列のリスト])
WIRING_CHECKLIST: list[tuple[str, list[str]]] = [
    (
        _ORCHESTRATOR,
        [
            "from core.llm_usage.context import bind_usage_context, set_current_feature",
            'bind_usage_context(\n        "pipeline",',
            "document_id=str(document_id)",
            "run_id=str(run_id)",
            'set_current_feature(f"pipeline:{stage}")',
        ],
    ),
    (
        _LEARNING,
        [
            "from core.llm_usage.context import usage_context",
            'usage_context("learning:chat", user_id=current_user["id"], course_id=course_id)',
            '_chat_feature = "learning:chat_casual" if _is_casual else "learning:chat"',
            "usage_context(_chat_feature, user_id=current_user[\"id\"], course_id=course_id)",
            'usage_context("learning:voice_stt", user_id=current_user["id"])',
            'usage_context("learning:voice_tts", user_id=current_user["id"])',
        ],
    ),
    (
        _ADMIN,
        [
            "from core.llm_usage.context import usage_context",
            'usage_context("admin:course_builder", user_id=current_user["id"])',
        ],
    ),
    (
        _LECTURE_STUDIO,
        [
            "from core.llm_usage.context import bind_usage_context, usage_context",
            'usage_context("admin:lecture_rewrite", user_id=current_user["id"])',
            'usage_context("admin:lecture_rewrite", user_id=current_user["id"], course_id=course_id)',
            'bind_usage_context("admin:lecture_generate", user_id=user_id, course_id=course_id)',
            'bind_usage_context("admin:lecture_tts", course_id=course_id)',
            'bind_usage_context("admin:lecture_generate", course_id=course_id)',
        ],
    ),
    (
        _ADMIN_ASSISTANT,
        [
            "from core.llm_usage.context import usage_context",
            'usage_context("admin:assistant", user_id=current_user["id"])',
        ],
    ),
    (
        _THEORY_COMPONENTS,
        [
            "from core.llm_usage.context import usage_context",
            'usage_context("admin:component_candidates", user_id=current_user["id"], course_id=body.course_id)',
        ],
    ),
    (
        _ATLAS_GENERATOR,
        [
            "from core.llm_usage.context import usage_context",
            'usage_context("admin:atlas_skeleton")',
            'usage_context("admin:atlas_assist")',
        ],
    ),
    (
        _TENSION_WORKER,
        [
            "from core.llm_usage.context import bind_usage_context",
            'bind_usage_context("learning:tension", user_id=str(user_id), course_id=str(course_id))',
        ],
    ),
    (
        _ANCHOR_WORKER,
        [
            "from core.llm_usage.context import bind_usage_context",
            'bind_usage_context("learning:structure_anchor", user_id=str(user_id), course_id=str(course_id))',
        ],
    ),
    (
        _RECON_WORKER,
        [
            "from core.llm_usage.context import bind_usage_context",
            'bind_usage_context("admin:reconstruction_authoring", document_id=str(document_id))',
        ],
    ),
    (
        _SCOPE_WORKER,
        [
            "from core.llm_usage.context import bind_usage_context",
            "bind_usage_context(",
            '"doubt:scope_candidates"',
        ],
    ),
    (
        _ASSUMPTION_WORKER,
        [
            "from core.llm_usage.context import bind_usage_context",
            'bind_usage_context("doubt:assumption_normalize", course_id=course_id or None)',
        ],
    ),
]


@pytest.mark.parametrize("rel_path,needles", WIRING_CHECKLIST, ids=[c[0] for c in WIRING_CHECKLIST])
def test_wiring_checklist(rel_path, needles):
    src = _read(rel_path)
    missing = [n for n in needles if n not in src]
    assert not missing, f"{rel_path}: missing expected wiring snippets: {missing}"


# ===========================================================================
# 2. リクエストハンドラファイルに bind_usage_context が現れない
#    （スレッド再利用による文脈漏れの構造的防止）
# ===========================================================================


class TestNoBindInRequestHandlerFiles:
    @pytest.mark.parametrize("rel_path", _ROUTE_FILES_WITH_HANDLERS_ONLY)
    def test_pure_request_handler_files_never_bind(self, rel_path):
        """学習チャット・コースビルダー・Admin Copilot・コンポーネント候補の各ルートは
        すべて FastAPI のリクエストハンドラ内で同期実行されるため、必ず ``usage_context``
        （with 文・スコープ限定）を使い、``bind_usage_context``（直接セット・reset なし）
        は一切使ってはならない。"""
        src = _read(rel_path)
        assert "bind_usage_context(" not in src, (
            f"{rel_path} is a request-handler-only routes file but calls bind_usage_context() "
            "— this leaks context across pooled request threads (see context.py docstring)."
        )

    def test_atlas_generator_never_binds(self):
        """atlas_generator の3関数は同期・リクエストスレッド内で呼ばれるため with 版のみ。"""
        src = _read(_ATLAS_GENERATOR)
        assert "bind_usage_context(" not in src

    def test_lecture_studio_bind_calls_are_confined_to_thread_target_functions(self):
        """lecture_studio.py は request handler と daemon-thread worker
        （``_batch_generate_worker`` / ``_batch_audio_worker`` /
        ``_batch_generate_and_audio_worker``）を同居させている。``bind_usage_context``
        の出現箇所はすべて、``threading.Thread(target=...)`` で実際にスレッド起動対象と
        なっている関数の本体内に限られることを検査する（request handler 本体には出現しない）。
        """
        src = _read(_LECTURE_STUDIO)

        thread_targets = sorted(set(re.findall(r"target=(\w+)", src)))
        assert thread_targets, "no threading.Thread(target=...) found in lecture_studio.py"

        def _function_span(fn_name: str) -> tuple[int, int]:
            start_match = re.search(rf"\ndef {re.escape(fn_name)}\(", src)
            assert start_match, f"def {fn_name} not found"
            start = start_match.start()
            rest = src[start + 1:]
            end_match = re.search(r"\n(?:def |@router)", rest)
            end = (start + 1 + end_match.start()) if end_match else len(src)
            return start, end

        target_spans = [_function_span(name) for name in thread_targets if f"\ndef {name}(" in src]
        assert target_spans, "none of the thread targets are locally-defined functions"

        bind_positions = [m.start() for m in re.finditer(r"bind_usage_context\(", src)]
        assert bind_positions, "expected at least one bind_usage_context( call in lecture_studio.py"

        for pos in bind_positions:
            assert any(start <= pos < end for start, end in target_spans), (
                f"bind_usage_context( call at offset {pos} in lecture_studio.py is outside all "
                "known thread-target function bodies — it may leak into a pooled request thread"
            )

    def test_lecture_studio_request_handlers_use_with_statement_only(self):
        """原稿スタジオの2つの request handler は usage_context（with）のみを使う。"""
        src = _read(_LECTURE_STUDIO)

        for fn_name in ("rewrite_lecture_script", "rewrite_lecture_studio_course_topic"):
            start_match = re.search(rf"\ndef {fn_name}\(", src)
            assert start_match, f"def {fn_name} not found"
            rest = src[start_match.start() + 1:]
            end_match = re.search(r"\n(?:def |@router)", rest[1:])
            body = rest[: end_match.start() + 1] if end_match else rest
            assert "bind_usage_context(" not in body, f"{fn_name} must not call bind_usage_context"
            assert "usage_context(" in body, f"{fn_name} should wrap its LLM call in usage_context"


# ===========================================================================
# 3. KNOWN_FEATURES の網羅性
# ===========================================================================


def _known_features() -> tuple[str, ...]:
    import core.llm_usage.schema as schema

    return schema.KNOWN_FEATURES


class TestKnownFeaturesCoverage:
    def test_pipeline_stage_features_all_registered(self):
        """orchestrator.py の report_start("<stage>") 呼び出しから動的に stage 名を
        抽出し、対応する "pipeline:<stage>" がすべて KNOWN_FEATURES にあることを検査する
        （schema.py への転記漏れがあれば検出できる）。"""
        src = _read(_ORCHESTRATOR)
        stages = sorted(set(re.findall(r'report_start\("([a-z_]+)"', src)))
        assert len(stages) >= 20, f"expected ~25 report_start stages, found {len(stages)}: {stages}"

        known = _known_features()
        missing = [f"pipeline:{s}" for s in stages if f"pipeline:{s}" not in known]
        assert not missing, f"pipeline stage features missing from KNOWN_FEATURES: {missing}"

    def test_bare_pipeline_placeholder_is_registered(self):
        assert "pipeline" in _known_features()

    @pytest.mark.parametrize(
        "feature",
        [
            "learning:chat",
            "learning:chat_casual",
            "learning:voice_stt",
            "learning:voice_tts",
            "learning:tension",
            "learning:structure_anchor",
            "admin:course_builder",
            "admin:lecture_rewrite",
            "admin:lecture_generate",
            "admin:lecture_tts",
            "admin:component_candidates",
            "admin:assistant",
            "admin:reconstruction_authoring",
            "admin:atlas_skeleton",
            "admin:atlas_assist",
            "doubt:scope_candidates",
            "doubt:assumption_normalize",
        ],
    )
    def test_wired_feature_registered(self, feature):
        assert feature in _known_features(), f"{feature!r} is wired but missing from KNOWN_FEATURES"

    def test_known_features_has_no_duplicates(self):
        known = _known_features()
        assert len(known) == len(set(known)), "KNOWN_FEATURES contains duplicate entries"


# ===========================================================================
# 4. 機能テスト: tension worker が実際に learning:tension で帰属する
#    （DB 不要 — フェイクセッション + フェイク agent。test_tension_worker.py と同型）
# ===========================================================================


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, script: dict):
        self._script = script

    def execute(self, stmt, params=None):
        sql = str(stmt)
        for key, rows in self._script.items():
            if key in sql:
                return _FakeResult(rows)
        return _FakeResult([])

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class TestTensionWorkerFunctionalAttribution:
    def test_agent_run_sees_learning_tension_context(self, monkeypatch):
        import core.tension.worker as worker
        from core.llm_usage.context import usage_context, current_usage_context
        from core.tension.schema import TensionMiningResult

        worker._session_call_counts.clear()
        worker._daily_call_counts.clear()

        history = [
            {"role": "user", "content": "なんとなく腑に落ちない"},
            {"role": "assistant", "content": "説明します"},
        ]
        script = {
            "payload->>'tension_hint' = 'true'": [
                ("id-1", {"text": "なんとなく腑に落ちない", "tension_hint": True}, None)
            ],
            "SET analyzed_at = now()": [("id-1",)],
            "FROM learning_chat_history": [(history,)],
            "FROM learning_courses": [({"topics": [], "chapters": []},)],
        }
        monkeypatch.setattr(worker, "_pg_session", lambda: _FakeSession(script))

        captured: dict = {}

        class _FakeAgent:
            def run(self, window, max_candidates=None):
                ctx = current_usage_context()
                captured["feature"] = ctx.feature
                captured["user_id"] = ctx.user_id
                captured["course_id"] = ctx.course_id
                return TensionMiningResult(candidates=[])

        monkeypatch.setattr(worker, "TensionMiningAgent", lambda: _FakeAgent())

        # テスト終了後に contextvar を元に戻す（bind_usage_context は reset しないため）。
        with usage_context("unattributed"):
            worker.run_tension_mining("u-attr-1", "c-attr-1", "t1")

        assert captured.get("feature") == "learning:tension"
        assert captured.get("user_id") == "u-attr-1"
        assert captured.get("course_id") == "c-attr-1"
