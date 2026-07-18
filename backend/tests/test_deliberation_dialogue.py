"""W層 面③「対話的検討」（``core/deliberation/dialogue.py``）の純粋部の単体テスト。

設計書 `docs/features/element_deliberation_workspace_design.md` §5・§7・§11。DB 実接続・
実 LLM は使わない。``generate_conversation_turn`` を monkeypatch したフェイクで
``run_turn`` の縮退（degraded）挙動を検証し、``build_llm_messages`` / ``grounding_to_text``
はどちらも DB 非依存の純粋関数なので fake dict で直接検証する。``check_and_count_llm_call``
は in-memory の ``CostGate`` のみに依存するため DB なしで境界値を検証できる。
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.deliberation import dialogue  # noqa: E402
from core.deliberation.schema import ElementRef, SCOPE_DOCUMENT, SCOPE_DOMAIN  # noqa: E402


# ---------------------------------------------------------------------------
# build_llm_messages: grounding は最初の user メッセージにのみ注入する（設計書 §5）
# ---------------------------------------------------------------------------


class TestBuildLlmMessages:
    def test_no_prior_messages_injects_grounding_into_new_user_turn(self):
        messages = dialogue.build_llm_messages([], "質問です", "GROUNDING_TEXT")
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert "GROUNDING_TEXT" in messages[0]["content"]
        assert messages[0]["content"].endswith("質問です")

    def test_grounding_injected_only_into_first_user_turn_not_later_ones(self):
        prior = [
            {"role": "user", "content": "最初の質問"},
            {"role": "assistant", "content": "最初の回答"},
        ]
        messages = dialogue.build_llm_messages(prior, "2回目の質問", "GROUNDING_TEXT")
        assert len(messages) == 3
        assert "GROUNDING_TEXT" in messages[0]["content"]
        assert messages[0]["content"].endswith("最初の質問")
        # 2ターン目以降には grounding を再注入しない。
        assert "GROUNDING_TEXT" not in messages[1]["content"]
        assert "GROUNDING_TEXT" not in messages[2]["content"]
        assert messages[2]["content"] == "2回目の質問"

    def test_empty_grounding_text_leaves_content_unchanged(self):
        messages = dialogue.build_llm_messages([], "質問です", "")
        assert messages[0]["content"] == "質問です"

    def test_assistant_role_preserved_in_history(self):
        prior = [{"role": "assistant", "content": "以前の回答"}]
        messages = dialogue.build_llm_messages(prior, "続き", "G")
        assert messages[0] == {"role": "assistant", "content": "以前の回答"}
        # 会話履歴の最初のターンが assistant の場合、最初の user ターン（今回の新規発話）に注入。
        assert "G" in messages[1]["content"]


# ---------------------------------------------------------------------------
# grounding_to_text: 純粋な整形関数
# ---------------------------------------------------------------------------


class TestGroundingToText:
    def test_formats_label_and_fields(self):
        grounding = {
            "breakdown": {
                "element_type": "theory_claim",
                "label": "claim label",
                "fields": {"claim_type": "empirical", "text": "This is a claim."},
                "notes": ["support_status=candidate（source_backed でない）"],
            },
            "positioning": {"available": False},
        }
        text = dialogue.grounding_to_text(grounding)
        assert "[要素] claim label（theory_claim）" in text
        assert "- claim_type: empirical" in text
        assert "- text: This is a claim." in text
        assert "(注記) support_status=candidate（source_backed でない）" in text

    def test_skips_bulky_nested_field_keys(self):
        grounding = {
            "breakdown": {
                "element_type": "figure",
                "label": "fig",
                "fields": {
                    "caption_text": "a caption",
                    "apparatus_candidates": [{"name": "vacuum chamber"}],
                },
                "notes": [],
            },
            "positioning": {"available": False},
        }
        text = dialogue.grounding_to_text(grounding)
        assert "caption_text" in text
        assert "apparatus_candidates" not in text
        assert "vacuum chamber" not in text

    def test_skips_empty_field_values(self):
        grounding = {
            "breakdown": {
                "element_type": "theory_component",
                "label": "c",
                "fields": {"summary": "", "inputs": [], "dependencies": {}},
                "notes": [],
            },
            "positioning": {"available": False},
        }
        text = dialogue.grounding_to_text(grounding)
        assert "summary" not in text
        assert "inputs" not in text
        assert "dependencies" not in text

    def test_formats_available_positioning_lenses(self):
        grounding = {
            "breakdown": {"element_type": "theory_claim", "label": "c", "fields": {}, "notes": []},
            "positioning": {
                "available": True,
                "lenses": {
                    "intra_document": {"items": [{"label": "掲載セクション", "value": "3. Method"}]},
                    "atlas": None,
                },
            },
        }
        text = dialogue.grounding_to_text(grounding)
        assert "[位置づけ:intra_document]" in text
        assert "- 掲載セクション: 3. Method" in text
        assert "[位置づけ:atlas]" not in text

    def test_unavailable_positioning_includes_note(self):
        grounding = {
            "breakdown": {"element_type": "theory_claim", "label": "c", "fields": {}, "notes": []},
            "positioning": {"available": False, "note": "位置づけレンズの取得に失敗したため内訳のみ返す"},
        }
        text = dialogue.grounding_to_text(grounding)
        assert "(注記) 位置づけレンズの取得に失敗したため内訳のみ返す" in text


# ---------------------------------------------------------------------------
# 面③ 要素中心コンテキストビュー（Issue #498）: build_grounding の "context" キー
# （context_lens.build を monkeypatch した fail-soft 挙動込み）
# ---------------------------------------------------------------------------


class TestBuildGroundingContext:
    def _patch_breakdown_and_positioning(self, monkeypatch):
        monkeypatch.setattr(
            dialogue.decomposition, "build",
            lambda ref: {"element_type": "theory_claim", "label": "L", "fields": {}, "notes": []},
        )
        monkeypatch.setattr(dialogue.positioning, "build", lambda ref: {})

    def test_grounding_includes_context_key_from_context_lens(self, monkeypatch):
        self._patch_breakdown_and_positioning(monkeypatch)
        fake_context = {"focus": {"label": "f"}, "upper": [], "lower": [], "notes": []}
        monkeypatch.setattr(dialogue.context_lens, "build", lambda ref: fake_context)

        grounding = dialogue.build_grounding(_sample_ref())
        assert grounding["context"] == fake_context

    def test_grounding_context_is_none_for_shared_part(self, monkeypatch):
        # context_lens.build() は shared_part（scope='domain'）に対して None を返す契約。
        self._patch_breakdown_and_positioning(monkeypatch)
        monkeypatch.setattr(dialogue.context_lens, "build", lambda ref: None)

        grounding = dialogue.build_grounding(_sample_ref(SCOPE_DOMAIN))
        assert grounding["context"] is None

    def test_context_lens_exception_degrades_to_none_without_raising(self, monkeypatch):
        # build_grounding 自身も念のため例外を握って None に縮退する（W6・overview の
        # 三本目の try/except と同じ防御の二重化）。
        self._patch_breakdown_and_positioning(monkeypatch)

        def boom(ref):
            raise RuntimeError("simulated context_lens outage")

        monkeypatch.setattr(dialogue.context_lens, "build", boom)

        grounding = dialogue.build_grounding(_sample_ref())
        assert grounding["context"] is None


class TestGroundingToTextContextLens:
    """grounding_to_text() の面③レンダリング（純粋関数・DB 非依存）。"""

    def _base_grounding(self, context) -> dict:
        return {
            "breakdown": {"element_type": "theory_claim", "label": "c", "fields": {}, "notes": []},
            "positioning": {"available": False},
            "context": context,
        }

    def test_none_context_is_omitted_without_error(self):
        text = dialogue.grounding_to_text(self._base_grounding(None))
        assert "[文脈:" not in text

    def test_missing_context_key_is_omitted_without_error(self):
        grounding = {
            "breakdown": {"element_type": "theory_claim", "label": "c", "fields": {}, "notes": []},
            "positioning": {"available": False},
        }
        text = dialogue.grounding_to_text(grounding)
        assert "[文脈:" not in text

    def test_unidentified_role_renders_fact_not_a_guess(self):
        context = {
            "focus": {
                "label": "focus label",
                "contextual_role": None,
                "contextual_role_status": "unidentified",
            },
            "upper": [],
            "lower": [],
            "notes": [],
        }
        text = dialogue.grounding_to_text(self._base_grounding(context))
        assert "[文脈: 中心要素] focus label" in text
        assert "上位構造との関係は未同定" in text

    def test_identified_role_renders_role_text_and_status_label(self):
        context = {
            "focus": {
                "label": "focus label",
                "contextual_role": "中心命題を支持する",
                "contextual_role_status": "source_backed",
            },
            "upper": [],
            "lower": [],
            "notes": [],
        }
        text = dialogue.grounding_to_text(self._base_grounding(context))
        assert "この文脈での役割: 中心命題を支持する（原文根拠）" in text

    def test_upper_and_lower_lanes_render_relation_label_and_status(self):
        context = {
            "focus": {"label": "f", "contextual_role": None, "contextual_role_status": "unidentified"},
            "upper": [
                {"relation_label": "の根拠となる", "label": "上位要素", "relation_status": "source_backed"},
            ],
            "lower": [
                {"relation_label": "を構成要素として含む", "label": "下位要素", "relation_status": "candidate"},
            ],
            "notes": ["注記事実"],
        }
        text = dialogue.grounding_to_text(self._base_grounding(context))
        assert "[文脈: 上位構造]" in text
        assert "- の根拠となる: 上位要素（原文根拠）" in text
        assert "[文脈: 下位構造]" in text
        assert "- を構成要素として含む: 下位要素（AI候補）" in text
        assert "(注記) 注記事実" in text

    def test_empty_lanes_are_omitted_entirely(self):
        context = {
            "focus": {"label": "f", "contextual_role": None, "contextual_role_status": "unidentified"},
            "upper": [],
            "lower": [],
            "notes": [],
        }
        text = dialogue.grounding_to_text(self._base_grounding(context))
        assert "[文脈: 上位構造]" not in text
        assert "[文脈: 下位構造]" not in text

    def test_confirmed_status_renders_teacher_confirmed_label(self):
        context = {
            "focus": {"label": "f", "contextual_role": None, "contextual_role_status": "unidentified"},
            "upper": [
                {"relation_label": "の構成要素である", "label": "親", "relation_status": "confirmed"},
            ],
            "lower": [],
            "notes": [],
        }
        text = dialogue.grounding_to_text(self._base_grounding(context))
        assert "（教員確認済み）" in text

    def test_never_renders_raw_confidence_string(self):
        context = {
            "focus": {"label": "f", "contextual_role": "役割", "contextual_role_status": "source_backed"},
            "upper": [{"relation_label": "r", "label": "l", "relation_status": "candidate"}],
            "lower": [{"relation_label": "r2", "label": "l2", "relation_status": "source_backed"}],
            "notes": ["note"],
        }
        text = dialogue.grounding_to_text(self._base_grounding(context))
        assert "confidence" not in text.lower()


class TestInstructionHeaderContextGuidance:
    """_INSTRUCTION_HEADER が文脈レンズの引用・AI候補の非断定を教員向けに指示すること。"""

    def test_header_instructs_citing_relations_and_not_asserting_ai_candidates_as_fact(self):
        header = dialogue._INSTRUCTION_HEADER
        assert "AI候補" in header
        assert "確定した事実であるかのように述べないでください" in header


# ---------------------------------------------------------------------------
# run_turn: LLM 呼び出しの成功/失敗（縮退）挙動
# ---------------------------------------------------------------------------


def _sample_ref(scope: str = SCOPE_DOCUMENT) -> ElementRef:
    if scope == SCOPE_DOCUMENT:
        return ElementRef(scope=SCOPE_DOCUMENT, element_type="theory_claim", element_id="c1", document_id="doc-1")
    return ElementRef(scope=SCOPE_DOMAIN, element_type="shared_part", element_id="sp-1", domain_key="particle_physics")


class TestRunTurn:
    def test_successful_turn_returns_reply_and_annotations(self, monkeypatch):
        fake_annotation = SimpleNamespace(
            model_dump=lambda: {
                "kind": "meaning", "body": "text", "evidence": ["quote"], "reason": "because", "confidence": 0.6,
            }
        )
        fake_parsed = SimpleNamespace(reply="ここが要点です。", annotations=[fake_annotation])

        def fake_generate(messages, response_format, *, images=None, model=None):
            assert messages, "messages should not be empty"
            return fake_parsed

        monkeypatch.setattr(dialogue, "generate_conversation_turn", fake_generate)

        result = dialogue.run_turn(
            _sample_ref(),
            prior_messages=[],
            user_content="これは何を意味しますか？",
            grounding_text="GROUNDING",
            user_id="u1",
        )
        assert result.degraded is False
        assert result.reply == "ここが要点です。"
        assert result.annotations == [
            {"kind": "meaning", "body": "text", "evidence": ["quote"], "reason": "because", "confidence": 0.6}
        ]

    def test_llm_failure_degrades_to_fallback_text(self, monkeypatch):
        def fake_generate(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(dialogue, "generate_conversation_turn", fake_generate)

        result = dialogue.run_turn(
            _sample_ref(), prior_messages=[], user_content="質問", grounding_text="G", user_id="u1",
        )
        assert result.degraded is True
        assert result.annotations == []
        assert result.reply == dialogue._DEGRADED_REPLY

    def test_empty_reply_falls_back_to_degraded_text(self, monkeypatch):
        fake_parsed = SimpleNamespace(reply="   ", annotations=[])
        monkeypatch.setattr(dialogue, "generate_conversation_turn", lambda *a, **k: fake_parsed)

        result = dialogue.run_turn(
            _sample_ref(), prior_messages=[], user_content="質問", grounding_text="G", user_id="u1",
        )
        assert result.degraded is False
        assert result.reply == dialogue._DEGRADED_REPLY

    def test_images_are_forwarded_to_generate_conversation_turn(self, monkeypatch):
        captured = {}

        def fake_generate(messages, response_format, *, images=None, model=None):
            captured["images"] = images
            return SimpleNamespace(reply="ok", annotations=[])

        monkeypatch.setattr(dialogue, "generate_conversation_turn", fake_generate)

        dialogue.run_turn(
            _sample_ref(),
            prior_messages=[],
            user_content="この図は何ですか？",
            grounding_text="G",
            images=[b"\x89PNG..."],
            user_id="u1",
        )
        assert captured["images"] == [b"\x89PNG..."]


# ---------------------------------------------------------------------------
# check_and_count_llm_call: session/day 上限（in-memory CostGate、DB 非依存）
# ---------------------------------------------------------------------------


class TestCheckAndCountLlmCall:
    def _fake_settings(self, *, per_session: int, per_day: int) -> SimpleNamespace:
        return SimpleNamespace(
            deliberation_max_calls_per_session=per_session,
            deliberation_max_calls_per_day=per_day,
        )

    def test_session_limit_blocks_after_threshold(self, monkeypatch):
        monkeypatch.setattr(
            dialogue, "get_settings", lambda: self._fake_settings(per_session=2, per_day=100)
        )
        session_id = f"session-{uuid.uuid4()}"
        user_id = f"user-{uuid.uuid4()}"
        assert dialogue.check_and_count_llm_call(session_id, user_id) is True
        assert dialogue.check_and_count_llm_call(session_id, user_id) is True
        assert dialogue.check_and_count_llm_call(session_id, user_id) is False

    def test_daily_limit_blocks_across_different_sessions_same_user(self, monkeypatch):
        monkeypatch.setattr(
            dialogue, "get_settings", lambda: self._fake_settings(per_session=100, per_day=2)
        )
        user_id = f"user-{uuid.uuid4()}"
        assert dialogue.check_and_count_llm_call(f"s-{uuid.uuid4()}", user_id) is True
        assert dialogue.check_and_count_llm_call(f"s-{uuid.uuid4()}", user_id) is True
        assert dialogue.check_and_count_llm_call(f"s-{uuid.uuid4()}", user_id) is False

    def test_distinct_sessions_do_not_share_session_counter(self, monkeypatch):
        monkeypatch.setattr(
            dialogue, "get_settings", lambda: self._fake_settings(per_session=1, per_day=100)
        )
        user_id = f"user-{uuid.uuid4()}"
        assert dialogue.check_and_count_llm_call(f"s-{uuid.uuid4()}", user_id) is True
        # 別セッションなら1回目はまだ許可される（セッション単位カウンタ）。
        assert dialogue.check_and_count_llm_call(f"s-{uuid.uuid4()}", user_id) is True


# ---------------------------------------------------------------------------
# N2: 同一性候補の供給（collect_identity_candidates / build_grounding /
# grounding_to_text の捏造ガード分岐）
# ---------------------------------------------------------------------------


def _figure_ref() -> ElementRef:
    return ElementRef(scope=SCOPE_DOCUMENT, element_type="figure", element_id="f1", document_id="doc-1")


class TestCollectIdentityCandidates:
    def _patch_settings(self, monkeypatch, top_k: int = 5) -> None:
        monkeypatch.setattr(
            dialogue,
            "get_settings",
            lambda: SimpleNamespace(deliberation_identity_candidates_top_k=top_k),
        )

    def test_document_scoped_ref_collects_frozen_entries(self, monkeypatch):
        self._patch_settings(monkeypatch)
        monkeypatch.setattr(
            dialogue, "get_latest_analysis_run",
            lambda **k: {"cartridge_id": "particle_physics"},
        )
        captured: dict = {}

        def fake_search(**kwargs):
            captured.update(kwargs)
            return [
                {
                    "entry_id": "e-1", "name": "PMT",
                    "aliases": ["photomultiplier", ""], "summary": "s", "distance": 0.12,
                },
                {"entry_id": "", "name": "id なしはスキップ"},
            ]

        monkeypatch.setattr(dialogue, "search_frozen_entries", fake_search)

        breakdown = {"label": "光電子増倍管", "fields": {"text": "PMT を使う"}}
        out = dialogue.collect_identity_candidates(_sample_ref(), breakdown)

        # 供給は実在エントリの事実（id・名称・aliases）のみ。distance 等の数値は載せない。
        assert out == [{"shared_part_id": "e-1", "name": "PMT", "aliases": ["photomultiplier"]}]
        assert captured["domain_key"] == "particle_physics"
        assert captured["top_k"] == 5
        # クエリは内訳（label + 既知テキストフィールド）から決定論的に組み立てる。
        assert "光電子増倍管" in captured["query_text"]
        assert "PMT を使う" in captured["query_text"]
        # theory_claim → theory_component エントリに絞り込む。
        assert captured["entry_type"] == "theory_component"

    def test_figure_element_filters_apparatus_entries(self, monkeypatch):
        self._patch_settings(monkeypatch)
        monkeypatch.setattr(
            dialogue, "get_latest_analysis_run", lambda **k: {"cartridge_id": "pp"}
        )
        captured: dict = {}

        def fake_search(**kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(dialogue, "search_frozen_entries", fake_search)
        dialogue.collect_identity_candidates(
            _figure_ref(), {"label": "fig", "fields": {"caption_text": "装置図"}}
        )
        assert captured["entry_type"] == "apparatus"

    def test_domain_scoped_ref_returns_empty_without_search(self, monkeypatch):
        called = {"search": False}
        monkeypatch.setattr(
            dialogue, "search_frozen_entries",
            lambda **k: called.__setitem__("search", True) or [],
        )
        out = dialogue.collect_identity_candidates(
            _sample_ref(SCOPE_DOMAIN), {"label": "x", "fields": {}}
        )
        assert out == []
        assert called["search"] is False

    def test_unresolved_domain_key_returns_empty_without_search(self, monkeypatch):
        self._patch_settings(monkeypatch)
        monkeypatch.setattr(dialogue, "get_latest_analysis_run", lambda **k: None)
        called = {"search": False}
        monkeypatch.setattr(
            dialogue, "search_frozen_entries",
            lambda **k: called.__setitem__("search", True) or [],
        )
        out = dialogue.collect_identity_candidates(
            _sample_ref(), {"label": "x", "fields": {}}
        )
        assert out == []
        assert called["search"] is False

    def test_zero_top_k_skips_search(self, monkeypatch):
        self._patch_settings(monkeypatch, top_k=0)
        monkeypatch.setattr(
            dialogue, "get_latest_analysis_run", lambda **k: {"cartridge_id": "pp"}
        )
        called = {"search": False}
        monkeypatch.setattr(
            dialogue, "search_frozen_entries",
            lambda **k: called.__setitem__("search", True) or [],
        )
        assert dialogue.collect_identity_candidates(_sample_ref(), {"label": "x", "fields": {}}) == []
        assert called["search"] is False

    def test_search_failure_degrades_to_empty(self, monkeypatch):
        self._patch_settings(monkeypatch)
        monkeypatch.setattr(
            dialogue, "get_latest_analysis_run", lambda **k: {"cartridge_id": "pp"}
        )

        def boom(**kwargs):
            raise RuntimeError("search down")

        monkeypatch.setattr(dialogue, "search_frozen_entries", boom)
        assert dialogue.collect_identity_candidates(_sample_ref(), {"label": "x", "fields": {}}) == []


class TestBuildGroundingIdentitySupply:
    def test_grounding_includes_identity_candidates(self, monkeypatch):
        monkeypatch.setattr(
            dialogue.decomposition, "build",
            lambda ref: {"element_type": "theory_claim", "label": "L", "fields": {"text": "T"}, "notes": []},
        )
        monkeypatch.setattr(dialogue.positioning, "build", lambda ref: {})
        monkeypatch.setattr(
            dialogue, "get_latest_analysis_run", lambda **k: {"cartridge_id": "pp"}
        )
        monkeypatch.setattr(
            dialogue, "search_frozen_entries",
            lambda **k: [{"entry_id": "e-1", "name": "N", "aliases": []}],
        )
        monkeypatch.setattr(
            dialogue, "get_settings",
            lambda: SimpleNamespace(deliberation_identity_candidates_top_k=5),
        )
        grounding = dialogue.build_grounding(_sample_ref())
        assert grounding["identity_candidates"] == [
            {"shared_part_id": "e-1", "name": "N", "aliases": []}
        ]

    def test_domain_scoped_ref_gets_empty_candidates(self, monkeypatch):
        monkeypatch.setattr(
            dialogue.decomposition, "build",
            lambda ref: {"element_type": "shared_part", "label": "L", "fields": {}, "notes": []},
        )
        monkeypatch.setattr(dialogue.positioning, "build", lambda ref: {})
        grounding = dialogue.build_grounding(_sample_ref(SCOPE_DOMAIN))
        assert grounding["identity_candidates"] == []


class TestGroundingToTextIdentityGuard:
    def _base_grounding(self) -> dict:
        return {
            "breakdown": {"element_type": "theory_claim", "label": "c", "fields": {}, "notes": []},
            "positioning": {"available": False},
        }

    def test_candidates_render_fact_list_and_conditional_instruction(self):
        grounding = self._base_grounding()
        grounding["identity_candidates"] = [
            {"shared_part_id": "e-1", "name": "PMT", "aliases": ["photomultiplier"]},
            {"shared_part_id": "e-2", "name": "TPC", "aliases": []},
        ]
        text = dialogue.grounding_to_text(grounding)
        assert "[同分野の既存の共通部品（凍結済みライブラリ）]" in text
        assert "- id=e-1: PMT（別名: photomultiplier）" in text
        assert "- id=e-2: TPC" in text
        # 「該当がある場合のみ」+ 一覧内の実在 id 限定（§6-1 の推奨案）。
        assert "一覧に無い id を作らないこと" in text
        assert "該当が無ければ identity の注釈は追加しないでください" in text
        # 供給あり時に 0 件ガードは出ない。
        assert "供給されていないため" not in text

    def test_zero_candidates_emit_fabrication_guard(self):
        text = dialogue.grounding_to_text(self._base_grounding())
        assert "kind='identity' の注釈は追加しないでください" in text
        assert "[同分野の既存の共通部品" not in text

    def test_header_only_conditionally_allows_identity(self):
        # 指示ヘッダは identity を無条件に促さない（一覧が示されている場合に限定）。
        header = dialogue._INSTRUCTION_HEADER
        assert "一覧が無いときは identity を使わないでください" in header
        assert "同一性の気づき" not in header
