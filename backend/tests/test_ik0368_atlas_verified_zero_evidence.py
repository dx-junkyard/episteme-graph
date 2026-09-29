"""IK-0368: verified でも根拠 0 本なら「原文0本に裏付け」という自己矛盾の文を組まない。"""

from __future__ import annotations

from core import atlas_state as st


def _signals(**kw):
    return st.ConceptSignals(key=kw.pop("key", "c1"), label=kw.pop("label", "概念"), **kw)


def test_seed_verified_with_zero_evidence_states_seed_origin():
    signals = _signals(seed_status=st.STATUS_VERIFIED)
    status, source = st.derive_node_status(signals)
    assert (status, source) == (st.STATUS_VERIFIED, st.STATUS_SOURCE_SEED)
    line = st.verify_line_for(status, signals)
    assert "0本" not in line
    assert "裏付け" not in line
    assert line == st.SEED_VERIFY_LINE
    assert "骨格（教員レビュー済）" in line
    assert st.find_evaluative_language(line) == []


def test_verified_passed_with_zero_evidence_without_seed_does_not_claim_backing():
    # 呼び出し側が verified を渡したが原文の引用が 0 本 (seed 由来でもない) の防御分岐。
    signals = _signals(component_count=1)
    line = st.verify_line_for(st.STATUS_VERIFIED, signals)
    assert "0本" not in line
    assert "裏付け" not in line
    assert line == st.VERIFIED_WITHOUT_EVIDENCE_LINE
    assert st.find_evaluative_language(line) == []


def test_scope_is_kept_for_zero_evidence_verified():
    signals = _signals(seed_status=st.STATUS_VERIFIED, scope_text="低エネルギー領域")
    line = st.verify_line_for(st.STATUS_VERIFIED, signals)
    assert line.startswith(st.SEED_VERIFY_LINE)
    assert "スコープ: 低エネルギー領域" in line


def test_positive_evidence_wording_unchanged():
    signals = _signals(evidence_count=2, evidence_refs=("式(3)",))
    status, _ = st.derive_node_status(signals)
    assert status == st.STATUS_VERIFIED
    assert st.verify_line_for(status, signals) == "検証: 原文2本に裏付け（式(3)）。"


def test_seed_wording_for_other_statuses_unchanged():
    signals = _signals(seed_status=st.STATUS_ASSUMED)
    assert st.verify_line_for(st.STATUS_UNKNOWN, signals) == (
        "検証: 台帳に記帳なし。骨格（教員レビュー済）の初期表示。"
    )
