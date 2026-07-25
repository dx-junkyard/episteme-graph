"""discuss モード（「論文と話す」）ガードレールテスト — 設計書 §6.5 チェックリストの一括担保。

正本: `docs/features/discussion_mode_design.md` §6.5（Phase 0〜2 実装済み・Phase 3 未着手）。

discuss 系テストはすでに5ファイルに分散している。**本ファイルは重複実装をせず**、
§6.5 の7項目それぞれについて既存ファイルの担保箇所を明記したうえで、
不足分だけを追加する（`test_doubt_guardrails.py` / `test_next_steps_guardrails.py` と同じ
「構造検査 + 純粋ロジックの実行可能単体テスト」の流儀。共通アサーションは
`backend/tests/guardrail_helpers.py` を使う）。

§6.5 チェックリスト対応表（既存カバレッジ → 本ファイルでの追加分）:

| # | チェックリスト項目 | 既存カバレッジ | 本ファイルでの追加 |
|---|---|---|---|
| 1 | 閲覧不可 document のチャンクが検索結果・cited_sources に漏れない（fail-closed） | `test_search_visibility.py`（`allowed_document_ids` キーワード専用化・SQL句・`list_visible_document_ids` の5経路・ルート配線）が SQL/シグネチャレベルで担保済み | `learning_chat` 内で `search_chunks_with_metadata` が1回しか呼ばれず、`cited_sources` がその戻り値（`chunk_results`）だけから構築されること（フィルタ外チャンクの混入経路が無いことの構造証明）を追加 |
| 2 | discuss 応答に数値スコア・件数・網羅率が含まれない（禁止語彙は既存カタログ流用） | `test_discuss_mode.py::TestDiscussSystemPrompt`（プロンプトが数値非表示を指示）/ `test_discuss_opening.py::TestNoHypeLanguage`（opening.py に D層5語彙なし）/ `test_discuss_phase2_ui_static.py::TestNoNumericScoresOrHypeInDiscussJs`（discuss.js に confidence/スコア/達成率/踏破/ランキング/%なし）/ `test_doubt_guardrails.py::TestNoHypeLanguage`（app.js 全体に D層5語彙なし — discuss 追加前から担保） | D層5語彙フルカタログが (a) discuss.js 全体 (b) `_get_discuss_system_prompt` 本文 (c) index.html discuss 関連文言、の3箇所に無いことを追加（discuss.js は既存テストが5語彙中2語彙のみ、prompt/index.html は未検査だった）。なお discuss.js 冒頭コメントは DM6 の原則自体を「数値・件数・網羅率は出さない」と地の文で説明しており、この字面は禁止語彙リント対象にしない（UI 文言ではなく設計コメントであるため） |
| 3 | discuss プロンプトテンプレートに生成プロンプト必須要素が存在する（構造検査） | `test_discuss_mode.py::TestDiscussSystemPrompt` 全体（即答・生成プロンプト構造的必須・出所明示・数値非表示の4要素） + `TestDiscussSelectedBeforeCasualForPrompt` で網羅済み | 追加なし（過不足なしと判断） |
| 4 | スコープ「ソース論文のみ」で該当なしのとき、他スコープへ無断フォールバックしない | `test_discuss_mode.py::TestScopeResolution`（scope 分岐配線・422・無断拡大なし）/ `TestOutOfSourceNoticeKeptForDiscuss` | `learning_chat` 内の `search_chunks_with_metadata` 呼び出しが1回のみであること（=ヒット0件を理由にした再検索・スコープ差し替えの実装経路が構造的に存在しない）を追加（#1 と共用） |
| 5 | `_discussion` 予約 topic_id で interest_traces / tension prefilter が正常動作する | `test_discuss_mode.py::TestDiscussionTopicLabel`（表示ラベル変換の配線）が静的検査のみ担保 | `find_course_topic` / `record_interest_trace` / `judge_tension_hint` を実行可能な単体テストとして直接呼び出し、`_discussion` を特別扱いせず動作することを実地検証。加えて `core/tension/` `core/structure_anchor/` が discuss を一切参照しない（除外ロジックを持たない）ことを構造検査 |
| 6 | U層タグ `learning:chat_discuss` が casual / 通常チャットと分離記録される | `test_discuss_mode.py::TestFourBypassPoints`（if/elif/else による3値排他の配線・KNOWN_FEATURES 登録）/ `test_llm_usage_attribution.py`（ファイル×feature 配線チェックリストに discuss 済み収載） | contextvars ベースの `usage_context` を実行して3タグが実際に分離され、ネストを抜けると漏れないことを実地証明で追加 |
| 7 | 教員向け集計が `core/privacy.py` の k=3 を通る（リテラル再定義禁止） | 対応する discuss 専用集計は **Phase 3（v2）未着手** のため実体テストなし | 現時点のフォワードガードとして: discuss 関連コード（`core/discuss/`・learning.py・discuss.js）に k-匿名しきい値のリテラル再定義が無いこと、および教員/管理者向け discuss 集計エンドポイントがまだ存在しないこと（実装時は `core/privacy.py` 経由が必須という契約のドキュメント化）を追加 |

外部 DB・LLM への実接続は行わない（フェイク session / 純粋関数呼び出しのみ）。
"""

from __future__ import annotations

import re
from pathlib import Path

from api import services
from core.course_data import find_course_topic
from core.llm_usage.context import current_usage_context, usage_context
from core.tension.prefilter import judge_tension_hint
from tests.guardrail_helpers import assert_module_tree_forbids, assert_source_forbids, extract_function_source

_BACKEND = Path(__file__).resolve().parents[1]
_REPO = _BACKEND.parent
_LEARNING_PATH = _BACKEND / "api" / "routes" / "learning.py"
_LEARNING_SRC = _LEARNING_PATH.read_text(encoding="utf-8")
_DISCUSS_JS_PATH = _REPO / "frontend" / "public" / "js" / "discuss.js"
_DISCUSS_JS_SRC = _DISCUSS_JS_PATH.read_text(encoding="utf-8")
_INDEX_HTML_SRC = (_REPO / "frontend" / "public" / "index.html").read_text(encoding="utf-8")
_CORE_DISCUSS_DIR = _BACKEND / "core" / "discuss"

# 実装済み（learning.py:162-163）: 予約 topic_id。文字列を直接ハードコードせず定数扱いに
# するとテストが api.routes.learning のフルインポート（DB 依存の重い import チェーン）を
# 要求してしまうため、既存 discuss テスト群（test_discuss_mode.py 等）と同じ流儀でリテラルを
# 直接使う。
_DISCUSSION_TOPIC_ID = "_discussion"

# D層ガードレール（test_doubt_guardrails.py）と同じ禁止語彙リスト。
# test_discuss_opening.py が opening.py に対して同じリストを使っている（正本はこちら）。
_BANNED_WORDS = ("疑え", "ノーベル賞", "危険地帯", "要注意ゾーン", "崩壊させよ")


# ---------------------------------------------------------------------------
# §6.5-1 / §6.5-4: 検索は1回のみ・cited_sources はその結果のみから構築される
# ---------------------------------------------------------------------------


class TestSingleFilteredSearchFeedsCitedSources:
    """learning_chat 内で `search_chunks_with_metadata` が複数回呼ばれたり、
    ヒット0件を理由に別スコープ・別関数で再検索したりする経路が無いことを構造的に
    確認する。DM1（無断フォールバック禁止）・DM2（可視性 fail-closed）の両方が
    「そもそも検索の入口が1つしかない」という構造で担保されていることの裏付け。
    """

    def _learning_chat_body(self) -> str:
        return extract_function_source(_LEARNING_SRC, "learning_chat")

    def test_search_chunks_with_metadata_called_exactly_once(self):
        body = self._learning_chat_body()
        assert body.count("search_chunks_with_metadata(") == 1

    def test_cited_sources_appended_exactly_once_from_chunk_results_loop(self):
        body = self._learning_chat_body()
        # cited_sources へ書き込むのは1箇所のみ（他ソースからの混入経路が無い）。
        assert body.count("cited_sources.append(") == 1
        # その1箇所が `for r in chunk_results:` ループの内側にあること。
        loop_start = body.index("for r in chunk_results:")
        append_idx = body.index("cited_sources.append(")
        assert loop_start < append_idx
        # ループと append の間に別の検索呼び出しが挟まっていないこと。
        between = body[loop_start:append_idx]
        assert "search_chunks_with_metadata(" not in between

    def test_no_second_search_or_scope_widening_after_initial_call(self):
        """最初の（唯一の）検索呼び出しより後ろに、2回目の検索呼び出しや
        `list_visible_document_ids` への差し替え呼び出しが無いこと（= ヒット0件の
        discuss 分岐以降で暗黙に可視集合へ広げ直す経路が構造的に存在しない）。"""
        body = self._learning_chat_body()
        anchor = "chunk_results = search_chunks_with_metadata("
        after = body[body.index(anchor) + len(anchor):]
        assert "search_chunks_with_metadata(" not in after
        assert "list_visible_document_ids(" not in after


# ---------------------------------------------------------------------------
# §6.5-2: 数値スコア・件数・網羅率・煽り語彙(D層カタログ) が discuss の全表示面に無い
# ---------------------------------------------------------------------------


class TestNoHypeOrNumericLanguageAcrossDiscussSurfaces:
    """D層の禁止語彙カタログを discuss の主要表示面すべてに適用する。

    既存テストは discuss.js に対してカタログの一部（「疑え」のみ）しか検査しておらず、
    `_get_discuss_system_prompt`（learning.py）と index.html は未検査だった
    （app.js は test_doubt_guardrails.py が既にフルカタログで担保済みのため対象外）。
    """

    def _discuss_prompt_body(self) -> str:
        return _LEARNING_SRC.split("def _get_discuss_system_prompt(")[1].split("\ndef ")[0]

    def test_discuss_system_prompt_forbids_full_hype_catalog(self):
        assert_source_forbids(
            self._discuss_prompt_body(), _BANNED_WORDS, context="_get_discuss_system_prompt"
        )

    def test_discuss_js_forbids_full_hype_catalog(self):
        assert_source_forbids(_DISCUSS_JS_SRC, _BANNED_WORDS, context="discuss.js")

    def test_index_html_discuss_forbids_full_hype_catalog(self):
        assert_source_forbids(_INDEX_HTML_SRC, _BANNED_WORDS, context="index.html")


# ---------------------------------------------------------------------------
# §6.5-5: `_discussion` 予約 topic_id で interest_traces / tension prefilter が動く
# ---------------------------------------------------------------------------


class _FakeInsertSession:
    """record_interest_trace が使う execute().fetchone() / commit() / close() のみを
    実装したフェイク session（test_search_visibility.py の _CapturingSession と同型）。
    """

    def __init__(self, row=("22222222-2222-2222-2222-222222222222",)):
        self._row = row
        self.captured_params: dict | None = None
        self.committed = False
        self.closed = False

    def execute(self, _sql, params=None):
        self.captured_params = params or {}
        return self

    def fetchone(self):
        return self._row

    def commit(self):
        self.committed = True

    def close(self):
        self.closed = True


class TestDiscussionTopicIdDoesNotSpecialCase:
    def test_find_course_topic_returns_none_for_reserved_id_without_error(self):
        """既存コースに `_discussion` という id のトピックは無いため None が返る
        （例外にならない・特別扱いのハードコードも無い）。"""
        course_data = {"topics": [{"id": "topic-1", "title": "T1"}, {"id": "topic-2", "title": "T2"}]}
        assert find_course_topic(course_data, _DISCUSSION_TOPIC_ID) is None

    def test_find_course_topic_none_course_data_also_safe(self):
        assert find_course_topic(None, _DISCUSSION_TOPIC_ID) is None

    def test_record_interest_trace_accepts_reserved_topic_id_unchanged(self, monkeypatch):
        session = _FakeInsertSession()
        monkeypatch.setattr(services, "_pg_session", lambda: session)

        trace_id = services.record_interest_trace(
            "user-1", "course-1", _DISCUSSION_TOPIC_ID,
            kind="question",
            text="なぜこの設計を採用したのですか?",
            extra_payload={"entry_mode": "discuss"},
        )

        assert trace_id == "22222222-2222-2222-2222-222222222222"
        assert session.committed is True
        # topic_id がそのまま（変換・拒否なし）で SQL パラメータに渡っていること。
        assert session.captured_params["tid"] == _DISCUSSION_TOPIC_ID
        assert session.captured_params["kind"] == "question"

    def test_judge_tension_hint_has_no_topic_parameter(self):
        """TensionPrefilter はそもそも topic_id を受け取らない純関数であることを
        シグネチャで確認する（= discuss か通常トピックかで分岐しようがない設計）。"""
        import inspect

        params = list(inspect.signature(judge_tension_hint).parameters)
        assert params == ["user_text", "recent_user_texts"]

    def test_judge_tension_hint_works_for_a_typical_discuss_message(self):
        """discuss での典型的な発話（ヘッジマーカー含み）でも通常どおり判定できる
        （実行可能な単体テスト）。"""
        assert judge_tension_hint("なんとなく引っかかるのですが、前提は本当に成り立ちますか?", []) is True
        assert judge_tension_hint("ありがとうございます", []) is False

    def test_tension_module_never_references_discuss(self):
        """core/tension/ が `_discussion` / discuss 語彙を一切参照しないこと
        （= discuss 用の除外・特別扱いロジックが無いことの構造的裏付け）。"""
        assert_module_tree_forbids(
            _BACKEND / "core" / "tension", ["_discussion", "DISCUSSION_TOPIC_ID", "discuss"]
        )

    def test_structure_anchor_module_never_references_discuss(self):
        assert_module_tree_forbids(
            _BACKEND / "core" / "structure_anchor", ["_discussion", "DISCUSSION_TOPIC_ID", "discuss"]
        )


# ---------------------------------------------------------------------------
# §6.5-6: U層タグ `learning:chat_discuss` が casual / 通常チャットと分離記録される
# ---------------------------------------------------------------------------


class TestUsageContextSeparatesDiscussFromCasualAndNormal:
    """静的な配線検査（if/elif/else 排他・KNOWN_FEATURES 登録）は
    test_discuss_mode.py / test_llm_usage_attribution.py が既に担保済み。ここでは
    contextvars ベースの実装（core/llm_usage/context.py）を実際に動かし、3つの
    feature タグが同時に混ざらず、ネストを抜けると正しく巻き戻ることを実地検証する。
    """

    def test_three_chat_features_are_mutually_isolated(self):
        assert current_usage_context().feature == "unattributed"

        with usage_context("learning:chat_discuss"):
            assert current_usage_context().feature == "learning:chat_discuss"
            with usage_context("learning:chat_casual"):
                assert current_usage_context().feature == "learning:chat_casual"
            # casual のネストを抜けたら discuss に戻る（漏れない）。
            assert current_usage_context().feature == "learning:chat_discuss"
            with usage_context("learning:chat"):
                assert current_usage_context().feature == "learning:chat"
            assert current_usage_context().feature == "learning:chat_discuss"

        # 最外のブロックを抜けたら既定値に戻る。
        assert current_usage_context().feature == "unattributed"

    def test_sequential_calls_do_not_bleed_into_each_other(self):
        with usage_context("learning:chat"):
            a = current_usage_context().feature
        with usage_context("learning:chat_casual"):
            b = current_usage_context().feature
        with usage_context("learning:chat_discuss"):
            c = current_usage_context().feature
        assert (a, b, c) == ("learning:chat", "learning:chat_casual", "learning:chat_discuss")
        assert len({a, b, c}) == 3


# ---------------------------------------------------------------------------
# §6.5-7: 教員向け discuss 集計は Phase 3 未着手 — 現時点のフォワードガード
# ---------------------------------------------------------------------------


class TestFutureDiscussAggregationMustGoThroughPrivacyModule:
    """discuss 専用の教員向け k-匿名集計は設計書 §6.4（Phase 3・v2）でまだ実装されていない。

    ここでは (a) 実装が始まる前に k=3 のリテラル再定義がどこにも紛れ込んでいないこと、
    (b) 教員/管理者向けの discuss 集計エンドポイントがまだ存在しないこと — の2点を
    フォワードガードとして固定する。Phase 3 で集計を実装する際は、この
    テストクラスを拡張し `core/privacy.py` の `K_ANONYMITY` / `meets_k_anonymity` /
    `bucket_count_range` を経由することを要求すること（既存の
    test_next_steps_guardrails.py / test_personal_graph_guardrails.py と同じ規律）。
    """

    _K_ANON_REDEFINITION_PATTERNS = ("K_ANONYMITY =", "K_ANONYMITY=")

    def test_no_k_anonymity_literal_redefinition_in_core_discuss(self):
        assert_module_tree_forbids(_CORE_DISCUSS_DIR, self._K_ANON_REDEFINITION_PATTERNS)

    def test_no_k_anonymity_literal_redefinition_in_learning_py(self):
        assert_source_forbids(_LEARNING_SRC, self._K_ANON_REDEFINITION_PATTERNS, context="learning.py")

    def test_no_k_anonymity_literal_redefinition_in_discuss_js(self):
        assert_source_forbids(_DISCUSS_JS_SRC, self._K_ANON_REDEFINITION_PATTERNS, context="discuss.js")

    def test_no_admin_facing_discuss_aggregation_endpoint_registered_yet(self):
        """`/api/admin/...` 配下（あるいは admin 系ルータ名）に discuss を含むパスの
        エンドポイントがまだ登録されていないこと。実装されたら本テストは失敗するので、
        そのときに k-匿名ゲートの配線ガードへ差し替えること。"""
        routes_dir = _BACKEND / "api" / "routes"
        pattern = re.compile(r'@(\w*admin\w*)\.(get|post|put|patch|delete)\("([^"]*)"\)', re.IGNORECASE)
        offending: list[str] = []
        for path in sorted(routes_dir.rglob("*.py")):
            src = path.read_text(encoding="utf-8")
            for match in pattern.finditer(src):
                if "discuss" in match.group(3).lower():
                    offending.append(f"{path}:{match.group(0)}")
        assert offending == [], f"admin-facing discuss endpoint(s) found: {offending}"

    def test_no_dedicated_discuss_router_file_exists_yet(self):
        assert not (_BACKEND / "api" / "routes" / "discuss.py").exists()
