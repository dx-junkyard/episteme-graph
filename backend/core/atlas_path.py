"""分野の地図 — 学習パス提案カードの決定論的生成 (Issue C-3, 仕様書 §8)。

「ここから学ぶ ↗」の応答として中央対話パネルに表示するカードのデータを組み立てる。
リアルタイム LLM 生成は行わない (§1.2-6): 入力は
  対象ノード + 地図データの投影 (related/juxtapose) + interest_traces
  + コーストピックの前提知識 (概念グラフ依存の近似) + 台帳状態 (ノード status)
であり、すべて決定論的に合成する。

守るべき設計原則:
- 各ステップに出所 (教材 / AI一般知識) と状態を明示する
- 行間ステップは「先生に聞くポイント」として質問テンプレートを添付する
- 暗黙の前提は台帳状態をそのまま表示するのみ (評価しない)
- 終端は可能なら並置 (2つの構造を黙って並べる) + 「自分で繋ぐ」入力。
  接続の言語化はしない
- 評価語 (重要・要注意・おすすめ 等)・推薦理由・誘導文言を一切含めない
- 情報を落とさない: 上限で省いたステップは notes に明示する (silent cap 禁止)

FastAPI に依存しない純関数モジュール (テスタビリティ確保)。
"""

from __future__ import annotations

from typing import Any

# 1カードに載せるステップ数の上限 (超過分は notes で明示して省く)
MAX_STEPS = 6

# 台帳状態 → 表示ラベル (§5 の語彙。評価語ではなく状態名)
#
# 正本は `core/atlas_state.py::PILL_LABELS`（共通キーは逐語一致させる。固定は
# `backend/tests/test_atlas_vocab_mirror.py`）。ここは "link" を持つぶんだけ広い。
#
# `verified` が意味するのは**コーパスの原文に裏付けの記帳がある**ことだけで、
# 実験によって確かめられたという主張ではない（SL1 の閉世界: 台帳は分野の射影ではなく
# コーパスの射影。検証の強さは D層台帳の verification_status が別軸で持つ）。
STATUS_LABELS = {
    "verified": "原文に裏付け",
    "contested": "解釈が分かれる",
    "assumed": "暗黙の前提",
    "gap": "行間 — AIが補完",
    "unvisited": "未訪問",
    "now": "いまここ",
    "fog": "論文未投入",
    "link": "接続",
}

# 台帳状態 → 台帳の事実の定型文 (評価しない。§8-3)
LEDGER_NOTES = {
    "verified": "台帳: 原文に記帳あり",
    "contested": "台帳: 複数の解釈が帰属つきで並存",
    "assumed": "台帳: 記帳された直接検証なし",
    "gap": "台帳: 原文に対応する記述なし（AIが補完した行間）",
    "unvisited": "台帳: 原文に記帳あり",
    "now": "台帳: 原文に記帳あり",
    "fog": "台帳: 記帳なし",
    "link": "台帳: 次の領域への接続",
}

# モデル知識由来の状態 (出所 = AI一般知識)。それ以外はコーパス由来 (出所 = 教材)
_MODEL_KNOWLEDGE_STATUSES = {"fog", "gap"}


def _source_for_status(status: str) -> tuple[str, str]:
    if status in _MODEL_KNOWLEDGE_STATUSES:
        return "model_knowledge", "AI一般知識"
    return "course_material", "教材"


def _teacher_question(label: str) -> str:
    """行間ステップに添付する「先生に聞くポイント」の質問テンプレート。"""
    return (
        f"「{label}」は原文に対応する記述が見つからず、AIが補完した行間です。"
        "この箇所はどのような仮定・根拠で埋まるのか、原文ではどう扱われているかを"
        "先生に確認したいです。"
    )


def _step(entry: dict, *, visited_labels: set[str]) -> dict[str, Any]:
    label = str(entry.get("label") or entry.get("node_id") or "")
    status = str(entry.get("status") or "")
    source, source_label = _source_for_status(status)
    step: dict[str, Any] = {
        "node_id": str(entry.get("node_id") or ""),
        "label": label,
        "status": status,
        "status_label": str(entry.get("pill") or STATUS_LABELS.get(status, status or "台帳状態は未導出")),
        "source": source,
        "source_label": source_label,
        "is_gap": status == "gap",
        "teacher_question": _teacher_question(label) if status == "gap" else "",
        "ledger_note": LEDGER_NOTES.get(status, ""),
        # 既習痕跡の事実提示 (評価しない)
        "visited_note": "この概念に関する問いの記録があります" if label in visited_labels else "",
    }
    return step


def _visited_labels(interest_traces: list[dict] | None, candidates: list[str]) -> set[str]:
    """interest_traces (既習・いまの糸) から、痕跡のある候補ラベルを抽出する。"""
    visited: set[str] = set()
    for trace in interest_traces or []:
        text = f"{trace.get('text', '')} {trace.get('context_label', '')}"
        for label in candidates:
            if label and label in text:
                visited.add(label)
    return visited


def _current_thread(interest_traces: list[dict] | None) -> str:
    """いまの糸: 最新の未解決の問い (本人の言葉) をそのまま返す。"""
    for trace in interest_traces or []:
        if trace.get("kind") in ("question", "tension") and trace.get("status") in ("open", "revisited"):
            text = str(trace.get("text") or "").strip()
            if text:
                return text
    return ""


def _course_prerequisites(course_topics: list[dict] | None, node_label: str) -> list[str]:
    """コーストピックの prerequisites から、対象ノードの前提知識名を引く (概念グラフ依存の近似)。"""
    if not node_label:
        return []
    for topic in course_topics or []:
        title = str(topic.get("title") or "")
        if not title:
            continue
        if node_label in title or title in node_label:
            return [str(p) for p in (topic.get("prerequisites") or []) if p]
    return []


def build_learning_path_card(
    *,
    node_id: str,
    node_label: str,
    level: int,
    skeleton_version: str,
    node_status: str = "",
    node_pill: str = "",
    related: list[dict] | None = None,
    juxtapose: list[dict] | None = None,
    course_topics: list[dict] | None = None,
    interest_traces: list[dict] | None = None,
) -> dict[str, Any]:
    """学習パス提案カードを決定論的に組み立てる (§8)。

    related: 地図データの投影 (対象に繋がるノード列。依存順はクライアントの
    レベル別抽出に従う)。juxtapose: 並置候補 (リーダー線相手など)。
    """
    notes: list[str] = []

    # 1) 前提知識 (コースデータ由来) を先頭に置く
    prereq_labels = _course_prerequisites(course_topics, node_label)

    # 2) 対象 → 関連ノードの順にステップ化 (重複ラベルは1回だけ)
    entries: list[dict] = []
    seen_labels: set[str] = set()

    for p_label in prereq_labels:
        if p_label not in seen_labels:
            seen_labels.add(p_label)
            # コース由来の前提知識: 台帳状態は地図に無いので状態欄は事実のみ
            entries.append({"node_id": "", "label": p_label, "status": "",
                            "pill": "コースの前提知識"})

    target_entry = {"node_id": node_id, "label": node_label,
                    "status": node_status, "pill": node_pill}
    if node_label not in seen_labels:
        seen_labels.add(node_label)
        entries.append(target_entry)

    for entry in related or []:
        label = str(entry.get("label") or "")
        if not label or label in seen_labels:
            continue
        seen_labels.add(label)
        entries.append(entry)

    if len(entries) > MAX_STEPS:
        dropped = [str(e.get("label") or "") for e in entries[MAX_STEPS:]]
        notes.append("表示上限のため省略したステップ: " + "、".join(dropped))
        entries = entries[:MAX_STEPS]

    candidate_labels = [str(e.get("label") or "") for e in entries]
    visited = _visited_labels(interest_traces, candidate_labels)
    steps = [_step(e, visited_labels=visited) for e in entries]

    # 3) 終端: 可能なら並置 (2つの構造を黙って並べる)。接続の言語化はしない
    juxta_pair: list[dict[str, Any]] = []
    if juxtapose:
        # 対象自身と最初の並置候補、または候補2つをそのまま並べる
        pool = [e for e in juxtapose if str(e.get("label") or "")]
        if len(pool) >= 2:
            juxta_pair = [_step(pool[0], visited_labels=visited),
                          _step(pool[1], visited_labels=visited)]
        elif len(pool) == 1:
            juxta_pair = [_step(target_entry, visited_labels=visited),
                          _step(pool[0], visited_labels=visited)]
    terminal: dict[str, Any] = {
        "mode": "juxtapose" if juxta_pair else "single",
        "pair": juxta_pair,
        # 「自分で繋ぐ」入力欄は常に開く (本人の言葉で記録する経路)
        "connect_input": True,
    }

    return {
        "kind": "learning_path_proposal",
        "target": {
            "node_id": node_id,
            "label": node_label,
            "status": node_status,
            "status_label": node_pill or STATUS_LABELS.get(node_status, node_status),
        },
        "level": int(level),
        "skeleton_version": skeleton_version,
        "current_thread": _current_thread(interest_traces),
        "steps": steps,
        "terminal": terminal,
        "decisions": ["proceed", "edit", "dismiss"],
        "notes": notes,
    }
