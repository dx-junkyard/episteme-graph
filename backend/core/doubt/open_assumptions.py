"""未検証合意リスト（Open Assumptions List, D3-2）の自動編纂。

台帳から「高負荷（段階: 高以上）× 低検証（untested / unknown またはスコープ空欄）」を
抽出し、紐づく疑義・検証提案・学習者シグナルを合成する。

原則:
  - リストは台帳の**投影**であり編集不可。台帳の状態変化（スコープ記帳）で自動的に増減する。
  - 生数値スコアを返さない（load は段階ラベル、疑義数は段階ラベル）。
  - 順位づけ・スコア化・賞レース的な価値づけ演出は禁止（§8-5）。並び順は負荷段階→依存数。
"""

from __future__ import annotations

import logging

from sqlalchemy import text as sa_text

from core.doubt.load_calculator import load_percentiles
from core.doubt.naive_signal import has_naive_signal
from core.doubt.scope import document_scope_clause, normalize_document_ids
from core.doubt.schema import load_level_for_score, scope_coverage_level
from core.doubt.support_paths import build_support_context, compute_support_lines_from_context
from core.label_vocab import VERIFICATION_PROPOSAL_PRESENCE_LABELS, verification_proposal_presence
from core.label_vocab import ASSUMPTION_LOAD_UNCOMPUTABLE_NOTE

logger = logging.getLogger(__name__)

_LOW_VERIFICATION_STATUSES = ("untested", "unknown")

# SL-4: 反証条件群からの到達可能性の要約（最良値。reachable > by_document（IK-1148・本文・文献の
# 読み直し）> next_generation > unreachable > unassessed。条件なしは呼び出し側で "" のまま返す）。
_REACHABILITY_PRIORITY = {
    "reachable": 0, "by_document": 1, "next_generation": 2, "unreachable": 3, "unassessed": 4,
}


def _falsification_reachability_summary(conditions: list[dict]) -> str:
    """not_formulable を除いた条件群の最良到達可能性（SL-4, §6.1）。"""
    candidates = [str(c.get("reachability") or "unassessed") for c in conditions if isinstance(c, dict)]
    if not candidates:
        return ""
    return min(candidates, key=lambda r: _REACHABILITY_PRIORITY.get(r, 99))


def _challenge_count_label(count: int) -> str:
    """疑義数の段階ラベル（数値スコア化しない）。"""
    if count <= 0:
        return ""
    if count == 1:
        return "1件"
    if count <= 3:
        return "少数"
    return "複数"


def target_label(
    session, target_type: str, target_id: str, *, document_id: str | None, labels=None,
) -> str:
    """台帳対象の表示ラベル（見つからなければ id をそのまま — 呼び出し側の判定の契約）。

    正本は ``core/doubt/target_labels.py``（IK-0901）。``document_id`` は必須のキーワード引数で、
    グラフのノード id・式の agent ID はその論文の読める名前（式の印字番号・段の日本語名）に
    なる（論文の中でしか一意でない — IK-0899）。``labels`` に ``LedgerTargetLabels`` を渡すと
    論文ごとの辞書を使い回す（地図・一覧で何件も名付けるとき）。
    """
    from core.doubt.target_labels import LedgerTargetLabels

    resolver = labels if labels is not None else LedgerTargetLabels(session)
    name = resolver.raw(target_type, target_id, document_id=document_id)
    if name:
        return name
    return target_id


def related_confirmed_assumption(session, anchor_id: str) -> dict | None:
    """anchor（claim / equation / component id）に対応する confirmed 前提を引く（D3-6）。

    学習者の structure_anchor 確定時の「事後の静かな表示」用。通知しない・
    押し付けない — 表示は帰属事実のみ（B層の「接続を宣言しない」と同じ規律）。
    """
    anchor_id = str(anchor_id or "").strip()
    if not anchor_id:
        return None
    try:
        row = session.execute(
            sa_text("""
                SELECT id::text, statement
                FROM assumption_nodes
                WHERE status IN ('confirmed', 'operationalized')
                  AND (
                    created_from->'node_ids' @> to_jsonb(CAST(:aid AS text))
                    OR created_from->'claim_ids' @> to_jsonb(CAST(:aid AS text))
                    OR created_from->'equation_ids' @> to_jsonb(CAST(:aid AS text))
                  )
                ORDER BY confirmed_at DESC NULLS LAST
                LIMIT 1
            """),
            {"aid": anchor_id},
        ).fetchone()
        if row is None:
            return None
        return {"assumption_id": str(row[0]), "statement": str(row[1] or "")}
    except Exception:
        logger.debug("related assumption lookup failed for anchor=%s", anchor_id, exc_info=True)
        return None


def _live_assumption_ids(session, ids: list[str]) -> set[str]:
    """確定済み（confirmed / operationalized）の前提の id（IK-1058）。候補・却下は載せない。"""
    if not ids:
        return set()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id::text FROM assumption_nodes
                WHERE id::text = ANY(:ids) AND status IN ('confirmed', 'operationalized')
            """),
            {"ids": list(ids)},
        ).fetchall()
    except Exception:  # noqa: BLE001
        logger.debug("assumption status lookup failed", exc_info=True)
        return set()
    return {str(r[0]) for r in rows}


def compile_open_assumptions(
    session,
    course_id: str,
    *,
    document_ids,
    include_challenger_names: bool = False,
    percentile_document_ids=None,
    exclusion=None,
    include_uncomputable_assumptions: bool = False,
) -> list[dict]:
    """未検証合意リストを台帳から編纂する（読み取り専用の投影）。

    include_challenger_names=False（学習者向け）では疑義者名を一切含めない。
    True（教員向け）でも名前は challenge 詳細参照用で、リスト自体には
    型と段階ラベルのみを載せる。

    範囲（IK-0852, 2026-10-23）: ``document_ids`` は**必須**。route 層がコースの教材から
    求めた document 集合を渡す（``core/doubt/scope.py``）。台帳行の course_id 列では絞らない
    （列は記帳の出所の意味だけを持つ）。空集合なら SQL を出さずに空を返す（fail-closed）。
    ``course_id`` は学習者シグナル（naive signal は受講コースの痕跡）を引くときだけ使う。

    percentile_document_ids（optional）: 負荷の段階（高 / 最高位）の基準にする範囲。
    既定は ``document_ids`` と同じ。ゼミ前ブリーフのように 1 論文に絞って読むときも
    「高」の意味を変えたくない呼び出し側が、より広い範囲を渡す。

    exclusion（optional・IK-0933）: ``core.doubt.population.ClaimChainExclusion``。claim チェーンの
    導出ステップ（前提ではない）を一覧から外す判定。省略時は内部で作る。呼び出し側が渡すと
    ``exclusion.excluded`` で「外したか」を読み、一覧に事実文を添えられる。

    include_uncomputable_assumptions（optional・IK-1058）: 依存の広がりを計算できない確定済みの
    前提（手で記帳しグラフ上の要素に結ばれていない — load_score NULL）も、``load_level=""`` +
    ``load_note``（計算できない旨の事実文）で載せる。教員の未検証合意リストだけが渡す
    （ゼミ前ブリーフ・discuss 開幕・学習者の一覧は「高負荷」の意味のまま）。
    """
    if exclusion is None:
        from core.doubt.population import ClaimChainExclusion

        exclusion = ClaimChainExclusion(session)
    scope_ids = normalize_document_ids(document_ids)
    if not scope_ids:
        return []
    basis_ids = normalize_document_ids(percentile_document_ids) or scope_ids
    # IK-0970: 段の閾値は一覧と同じ母集団（導出の手順を外した後）で取る。
    p50, p90, p99 = load_percentiles(session, document_ids=basis_ids, exclusion=exclusion)

    # SL-3: 独立支持経路の共有文脈は**対象の document ごと**に1度だけ構築し、同じ論文の行で再利用する
    # （IK-1120・第 25 周 第 10 波: 範囲は台帳と同じ「対象の document」— IK-0852。コースの教材全体を
    # 範囲にすると、別の論文の観測が支持線に数えられ、台帳の「単一」がリストでは「複数」になる）。
    # 行の document が引けない旧行だけコースの範囲に戻す（情報を落とさない）。
    support_ctx_by_document: dict[str, object] = {}

    def _support_ctx_for(document_id: str):
        key = document_id or ""
        if key not in support_ctx_by_document:
            ids = [document_id] if document_id else scope_ids
            support_ctx_by_document[key] = build_support_context(session, document_ids=ids)
        return support_ctx_by_document[key]

    where, params = document_scope_clause(scope_ids)
    # IK-1058: 依存の広がりを計算できない前提（NULL）は、求められたときだけ母集団に入れる。
    load_filter = (
        "(load_score IS NOT NULL OR target_type = 'assumption')"
        if include_uncomputable_assumptions else "load_score IS NOT NULL"
    )
    rows = session.execute(
        sa_text(f"""
            SELECT target_id, target_type, verification_status,
                   verification_scopes, consensus_behavioral, load_score,
                   falsification_conditions, document_id::text
            FROM epistemic_ledger
            WHERE {where}
              AND {load_filter}
        """),
        params,
    ).fetchall()
    uncomputable_ok = (
        _live_assumption_ids(session, [
            str(r[0]) for r in rows if str(r[1]) == "assumption" and r[5] is None
        ])
        if include_uncomputable_assumptions else set()
    )

    from core.doubt.target_labels import LedgerTargetLabels

    labels = LedgerTargetLabels(session)
    items: list[dict] = []
    for row in rows:
        target_id = str(row[0])
        target_type = str(row[1])
        row_document_id = str(row[7] or "") if len(row) > 7 else ""
        if exclusion.excludes(target_type, target_id, row_document_id):
            continue
        status = str(row[2] or "unknown")
        scopes = row[3] if isinstance(row[3], list) else []
        behavioral = int(row[4] or 0)
        load_uncomputable = row[5] is None
        if load_uncomputable and not (target_type == "assumption" and target_id in uncomputable_ok):
            continue
        load_level = load_level_for_score(
            float(row[5]) if row[5] is not None else None, p50, p90, p99
        )
        if not load_uncomputable and load_level not in ("high", "highest"):
            continue
        low_verification = status in _LOW_VERIFICATION_STATUSES or len(scopes) == 0
        if not low_verification:
            continue

        # SL-1/SL-4: 反証条件の記帳状況（§6.1）。not_formulable は「定式化できない」という
        # 人間の明示記帳であり、それ以外の記帳とは別に扱う（反証不可能の記帳 vs 未検討）。
        falsification_conditions = row[6] if isinstance(row[6], list) else []
        non_not_formulable = [
            c for c in falsification_conditions
            if isinstance(c, dict) and str(c.get("kind") or "") != "not_formulable"
        ]
        has_falsification_condition = bool(non_not_formulable)
        falsification_not_formulable = (not has_falsification_condition) and any(
            isinstance(c, dict) and str(c.get("kind") or "") == "not_formulable"
            for c in falsification_conditions
        )
        reachability_summary = _falsification_reachability_summary(non_not_formulable)

        # SL-3: 独立支持経路の段階（導出失敗は "" のまま — キーは常に返す）。
        support_line_level = ""
        try:
            support_lines = compute_support_lines_from_context(
                _support_ctx_for(row_document_id), target_type, target_id
            )
            if support_lines:
                support_line_level = str(support_lines.get("level") or "")
        except Exception:  # noqa: BLE001
            logger.debug(
                "support line computation failed for %s/%s", target_type, target_id, exc_info=True
            )
            support_line_level = ""

        challenge_rows = session.execute(
            sa_text("""
                SELECT c.id::text, c.challenge_type, c.status, u.profile_name
                FROM challenges c
                LEFT JOIN users u ON u.id = c.challenger_id
                WHERE c.target_type IN ('assumption', 'claim')
                  AND c.target_id = :tid
                  AND c.status <> 'withdrawn'
            """),
            {"tid": target_id},
        ).fetchall()
        # IK-1121: 完了した提案と未完了の提案を言い分ける（件数は外へ出さない — 段階語だけ）。
        proposal_row = session.execute(
            sa_text("""
                SELECT COUNT(*) FILTER (WHERE vp.status <> 'completed'),
                       COUNT(*) FILTER (WHERE vp.status = 'completed')
                FROM verification_proposals vp
                JOIN challenges c ON c.id = vp.challenge_id
                WHERE c.target_id = :tid AND vp.status <> 'withdrawn'
            """),
            {"tid": target_id},
        ).fetchone()
        proposal_open = int(proposal_row[0] or 0) if proposal_row else 0
        proposal_completed = (
            int(proposal_row[1] or 0) if proposal_row is not None and len(proposal_row) > 1 else 0
        )
        proposal_state = verification_proposal_presence(proposal_open, proposal_completed)

        challenge_types = sorted({str(r[1]) for r in challenge_rows})
        item = {
            "target_id": target_id,
            "target_type": target_type,
            # IK-0901: 表示名の正本（読めなければ「名前を引けない対象」— 内部 ID を出さない）。
            "statement": labels.label(target_type, target_id, document_id=row_document_id),
            # IK-0899: 行キーの論文（画面はこれを添えて台帳詳細を開く）。
            "document_id": row_document_id,
            "verification_status": status,
            "scope_coverage": scope_coverage_level(len(scopes)),
            "scope_count_is_zero": len(scopes) == 0,
            "load_level": load_level,
            "dependent_count": behavioral,
            "challenge_count_label": _challenge_count_label(len(challenge_rows)),
            "challenge_types": challenge_types,
            "has_verification_proposal": bool(proposal_state),
            # IK-1121: 提案の進み具合（open / completed / mixed）と画面の語（正本 label_vocab）。
            "verification_proposal_state": proposal_state,
            "verification_proposal_label": VERIFICATION_PROPOSAL_PRESENCE_LABELS.get(proposal_state, ""),
            # naive signal は anchor 語彙と一致する対象種別のみ引く
            "has_naive_signal": (
                has_naive_signal(target_type, target_id, course_id)
                if target_type in ("claim", "equation", "component")
                else False
            ),
            # SL-1/SL-3/SL-4: 覆る条件・到達可能性・支持線（すべて事実。数値は出さない）。
            "has_falsification_condition": has_falsification_condition,
            "falsification_not_formulable": falsification_not_formulable,
            "reachability_summary": reachability_summary,
            "support_line_level": support_line_level,
        }
        if load_uncomputable:
            # IK-1058: 値が無いことを「載せない」と読まず、計算できない段として言う（数値なし）。
            item["load_note"] = ASSUMPTION_LOAD_UNCOMPUTABLE_NOTE
        if include_challenger_names:
            # IK-1007: 疑義を出した人は本人が登録した名前だけ（無ければ名前なしの事実語）。
            from core.account_display import label_or_unregistered

            item["challengers"] = sorted({label_or_unregistered(r[3]) for r in challenge_rows})
        items.append(item)

    # 並び順は負荷段階 → 依存数（順位づけの演出はしない）
    level_order = {"highest": 0, "high": 1}
    items.sort(key=lambda i: (level_order.get(i["load_level"], 9), -i["dependent_count"], i["target_id"]))
    return items
