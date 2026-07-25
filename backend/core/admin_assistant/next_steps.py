"""Next Steps エンジン（G層 — 状態から導出する To-Do）。

正本: docs/features/guidance_layer_design.md §3。

設計原則:
  - G1 完了フラグを持たない: サーバ状態（教材・解析 run・コース・binding・公開状態）から
    毎回決定論的に導出する。タスクを実施すれば項目は自動消滅する。
  - G2 非LLM・同期: ルールベースの投影のみ（LLM を呼ばない、P6 と同型）。
  - G3 Capability Registry を単一の真実源として再利用する: 各ルールは登録済みの
    capability を参照し、現在ロールで到達不能なルールは**評価すらしない**（fail-closed）。
  - G5 却下は保持: dismiss は `assistant_step_dismissals` に revoked=FALSE の行として
    永続化し、restore は revoked=TRUE への状態遷移（行削除しない, P4 と同型）。
  - G6 理由は事実文で: `reason` は根拠付きの事実文のみ。煽り文句・督促・数値の煽動はしない。
  - G7 既存層（A/B/C/D/R/V）の core コードは読むだけ。ここで行う書き込みは
    `assistant_step_dismissals` の upsert / revoked 更新のみ（監査記録は呼び出し側の
    routes が theory_review_events に対して行う）。

このモジュールは **FastAPI / LLM クライアント（openai 等）を import しない**（G2/G7）。
SQLAlchemy セッション（`core.postgres.get_session()` が返す Session 相当のオブジェクト）を
受け取って読み取り専用の投影を行うだけで、DDL やスキーマ変更は行わない。
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import text as sa_text

from core import atlas_store
from core import privacy
from core.admin_assistant import capabilities as caps
from core.admin_assistant import knowledge as admin_kb
from core.course_data import course_atlas_binding_facts
from core.course_data import (
    course_atlas_binding_pending,
    course_cartridge_id,
    iter_all_topics,
)
from core.help_kb import manual as help_manual
from core.status import projector as status_projector
from core.status import schema as status_schema

# ---------------------------------------------------------------------------
# ルールカタログ v1（設計 §3.1）
# ---------------------------------------------------------------------------

RULE_MATERIALS_NONE = "materials.none"
RULE_MATERIAL_ANALYSIS_FAILED = "material.analysis_failed"
RULE_MATERIAL_NO_COURSE = "material.no_course"
RULE_COURSE_NOT_PUBLISHED = "course.not_published"
RULE_COURSE_NO_ATLAS_BINDING = "course.no_atlas_binding"
# atlas_binding_lifecycle_design.md §5: コース起点で新分野を仮予約した後、骨格が
# 凍結されて割り当てを完了できる状態 / バインド済み node_id が現行凍結版に無い状態。
RULE_COURSE_ATLAS_BINDING_READY = "course.atlas_binding_ready"
RULE_COURSE_ATLAS_BINDING_STALE = "course.atlas_binding_stale"
RULE_COURSE_AUDIO_MISSING = "course.audio_missing"
# N13（#496 追随）: 本人所有の教材に未レビューの AI 図分類が残っている。
# `material.inventory_unvisited` は「見たかどうか」の押し付けになるため意図的に
# 実装しない（vision_ux_gap_survey_2026-07-17.md §5-5 の見送り推奨, G4）。
RULE_FIGURE_UNREVIEWED_MODES = "figure.unreviewed_modes"

# 利用者マニュアル KB（help_kb, manual_help_kb_design.md §4-1）: 需要側 + 供給側の
# 両面計器。改善ループを閉じるための3ルール（2026-07-25 追加）。
RULE_MANUAL_HELP_GAPS_PENDING = "manual.help_gaps_pending"     # 需要側: help_usage 無ヒット/未整備の k-匿名集計
RULE_ASSISTANT_KB_UNDOCUMENTED = "assistant_kb.undocumented"   # 供給側: capability の操作KB未整備
RULE_MANUAL_TODO_UNRESOLVED = "manual.todo_unresolved"         # TODO 可視化: 索引除外チャンク

SEVERITY_REQUIRED = "required"
SEVERITY_RECOMMENDED = "recommended"
SEVERITY_OPTIONAL = "optional"
SEVERITIES = (SEVERITY_REQUIRED, SEVERITY_RECOMMENDED, SEVERITY_OPTIONAL)
_SEVERITY_RANK = {SEVERITY_REQUIRED: 0, SEVERITY_RECOMMENDED: 1, SEVERITY_OPTIONAL: 2}

# rule_id -> {"severity": ..., "capability_id": ...}
# G3 fail-closed 判定（capabilities_for(role) 経由）とガードレールテストの両方が参照する。
RULE_CATALOG: dict[str, dict[str, str]] = {
    RULE_MATERIALS_NONE: {
        "severity": SEVERITY_REQUIRED,
        "capability_id": "materials.upload",
    },
    RULE_MATERIAL_ANALYSIS_FAILED: {
        "severity": SEVERITY_REQUIRED,
        "capability_id": "materials.upload",  # v1 は道案内のみ（設計 §3.1）
    },
    RULE_MATERIAL_NO_COURSE: {
        "severity": SEVERITY_REQUIRED,
        "capability_id": "course_builder.open",
    },
    RULE_COURSE_NOT_PUBLISHED: {
        "severity": SEVERITY_RECOMMENDED,
        "capability_id": "course.publish",
    },
    RULE_COURSE_NO_ATLAS_BINDING: {
        "severity": SEVERITY_RECOMMENDED,
        "capability_id": "course.atlas_binding",
    },
    RULE_COURSE_ATLAS_BINDING_READY: {
        "severity": SEVERITY_RECOMMENDED,
        "capability_id": "course.atlas_binding",  # 既存 capability を再利用（G3: registry 変更不要）
    },
    RULE_COURSE_ATLAS_BINDING_STALE: {
        "severity": SEVERITY_RECOMMENDED,
        "capability_id": "course.atlas_binding",
    },
    RULE_COURSE_AUDIO_MISSING: {
        "severity": SEVERITY_OPTIONAL,
        "capability_id": "lecture_studio.generate_audio",
    },
    RULE_FIGURE_UNREVIEWED_MODES: {
        "severity": SEVERITY_RECOMMENDED,
        "capability_id": "materials.review_figures",  # 道案内のみ（図モーダルへ, #496）
    },
    # manual_help_kb_design.md §4-1: 需要側 + 供給側の両面計器。
    RULE_MANUAL_HELP_GAPS_PENDING: {
        "severity": SEVERITY_RECOMMENDED,
        "capability_id": "manual_help.view_gaps",
    },
    RULE_ASSISTANT_KB_UNDOCUMENTED: {
        "severity": SEVERITY_OPTIONAL,
        "capability_id": "assistant_kb.view_undocumented",
    },
    RULE_MANUAL_TODO_UNRESOLVED: {
        "severity": SEVERITY_OPTIONAL,
        "capability_id": "manual_kb.view_todos",
    },
}

# manual.help_gaps_pending: help_anchor が空（無ヒット）行の集計バケツキー。
# 実データの help_anchor は常に "manual/<audience>/<file>#<anchor>" 形式であり衝突しない。
_HELP_GAP_NO_HIT_BUCKET = "no_hit"

MAX_STEPS = 10

# §8: 操作アシスタント初回ログイン cue の一度きりフラグ。専用テーブルを増やさず
# assistant_step_dismissals の 1 行で代用する（step_key はどのルールとも衝突しない）。
CUE_FIRST_LOGIN_KEY = "cue:first_login"


@dataclass
class NextStep:
    """1 件の Next Step（設計 §3 の dataclass。JSON シリアライズ可能）。"""

    step_key: str          # "{rule_id}:{target_id}" — dismissal の主キー
    rule_id: str            # ルールカタログの ID
    severity: str           # required | recommended | optional
    title: str              # 例: 「この教材からコースを作成する」
    reason: str             # 事実文 + 根拠（G6。煽り・督促・命令口調は禁止）
    capability_id: str      # 登録済み capability（G3, fail-closed）
    locate_plan: dict        # capability.locate_steps を target で具体化したもの
    target: dict             # {"material_id": ...} / {"course_id": ...}
    dismissible: bool = True  # v1 は全ルールで True（設計 §3.1）

    def to_dict(self) -> dict:
        return {
            "step_key": self.step_key,
            "rule_id": self.rule_id,
            "severity": self.severity,
            "title": self.title,
            "reason": self.reason,
            "capability_id": self.capability_id,
            "locate_plan": self.locate_plan,
            "target": self.target,
            "dismissible": self.dismissible,
        }


# ---------------------------------------------------------------------------
# 共通ヘルパ
# ---------------------------------------------------------------------------


def _iso(value: Any) -> str:
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    return str(value or "")


def _locate_plan_for(capability_id: str, ctx: Optional[dict] = None) -> dict:
    """capability の locate_steps を ctx（{course_id: ...} 等）で具体化した dict。"""
    cap = caps.get_capability(capability_id)
    if cap is None:
        return {"capability_id": capability_id, "steps": []}
    return {"capability_id": capability_id, "steps": cap.locate_steps_as_dicts(ctx or {})}


def _make_step(
    *,
    rule_id: str,
    target_id: str,
    title: str,
    reason: str,
    target: dict,
    ctx: Optional[dict] = None,
    dismissible: bool = True,
) -> NextStep:
    rule = RULE_CATALOG[rule_id]
    capability_id = rule["capability_id"]
    return NextStep(
        step_key=f"{rule_id}:{target_id}",
        rule_id=rule_id,
        severity=rule["severity"],
        title=title,
        reason=reason,
        capability_id=capability_id,
        locate_plan=_locate_plan_for(capability_id, ctx),
        target=target,
        dismissible=dismissible,
    )


def _course_has_atlas_node(data: dict) -> bool:
    """topics[].atlas_node_id が（章内・トップレベルいずれかで）1 つでも埋まっているか。

    走査自体は core/course_data.py::course_atlas_binding_facts に共通化済み
    （正本は Tier3-18 で core/status/projector.py から移設）。判定方針（このモジュールは
    cartridge_id 優先）はこちらに残す。
    """
    _, has_atlas_node = course_atlas_binding_facts(data)
    return has_atlas_node


def _course_needs_atlas_binding(data: dict) -> bool:
    """設計 §3.1: `data.cartridge_id` 明示が無く、かつ全 topics[].atlas_node_id が空のときのみ True。

    cartridge_id はあるが binding 途中（一部 topic だけ済み）のコースは対象外
    （設計テーブルの条件を文字通り AND として実装する）。
    """
    if not isinstance(data, dict):
        return True
    has_cartridge, has_atlas_node = course_atlas_binding_facts(data)
    if has_cartridge:
        return False
    return not has_atlas_node


# ---------------------------------------------------------------------------
# 各ルールの評価（決定論・非LLM。DB は読み取りのみ）
# ---------------------------------------------------------------------------


def _eval_materials_none(session, uid: str) -> list[tuple[NextStep, str]]:
    count = session.execute(
        sa_text("SELECT count(*) FROM documents WHERE uploaded_by = CAST(:uid AS uuid)"),
        {"uid": uid},
    ).scalar() or 0
    if count > 0:
        return []
    step = _make_step(
        rule_id=RULE_MATERIALS_NONE,
        target_id="global",
        title="教材をアップロードする",
        reason="登録されている教材がまだありません。",
        target={},
    )
    return [(step, "")]


def _eval_material_analysis_failed(session, uid: str) -> list[tuple[NextStep, str]]:
    rows = session.execute(
        sa_text(
            "SELECT id::text AS id, title, created_at FROM documents "
            "WHERE uploaded_by = CAST(:uid AS uuid) ORDER BY created_at ASC"
        ),
        {"uid": uid},
    ).mappings().fetchall()
    out: list[tuple[NextStep, str]] = []
    for row in rows:
        doc_id = row["id"]
        ms = status_projector.project_material_status(session, doc_id)
        if ms.state != status_schema.MATERIAL_STATE_ANALYSIS_FAILED:
            continue
        title = row["title"] or doc_id
        reason = f"教材『{title}』の解析が失敗しました。"
        if ms.reason:
            reason += f"（{ms.reason}）"
        step = _make_step(
            rule_id=RULE_MATERIAL_ANALYSIS_FAILED,
            target_id=doc_id,
            title=f"教材『{title}』の解析状況を確認する",
            reason=reason,
            target={"material_id": doc_id},
        )
        out.append((step, _iso(row["created_at"])))
    return out


def _eval_material_no_course(session, uid: str) -> list[tuple[NextStep, str]]:
    rows = session.execute(
        sa_text(
            "SELECT id::text AS id, source_path, title, created_at FROM documents "
            "WHERE uploaded_by = CAST(:uid AS uuid) ORDER BY created_at ASC"
        ),
        {"uid": uid},
    ).mappings().fetchall()
    out: list[tuple[NextStep, str]] = []
    for row in rows:
        doc_id = row["id"]
        ms = status_projector.project_material_status(session, doc_id)
        if ms.state != status_schema.MATERIAL_STATE_ANALYZED:
            continue
        material_id = row["source_path"] or doc_id
        # sources[].material_id は documents.id / documents.source_path の
        # どちらでも参照されうる（_resolve_document の慣例）ため両方で突合する。
        used = session.execute(
            sa_text("""
                SELECT EXISTS (
                    SELECT 1 FROM learning_courses,
                         jsonb_array_elements(COALESCE(data->'sources', '[]'::jsonb)) AS src
                    WHERE src->>'material_id' IN (:doc_id, :material_id)
                )
            """),
            {"doc_id": doc_id, "material_id": material_id},
        ).scalar()
        if used:
            continue
        title = row["title"] or doc_id
        step = _make_step(
            rule_id=RULE_MATERIAL_NO_COURSE,
            target_id=doc_id,
            title=f"教材『{title}』からコースを作成する",
            reason=f"教材『{title}』はどのコースからも参照されていません。",
            target={"material_id": doc_id},
        )
        out.append((step, _iso(row["created_at"])))
    return out


def _eval_course_not_published(session, uid: str) -> list[tuple[NextStep, str]]:
    rows = session.execute(
        sa_text("""
            SELECT id, title, created_at FROM learning_courses
            WHERE user_id = CAST(:uid AS uuid) AND is_template = TRUE AND is_published = FALSE
            ORDER BY created_at ASC
        """),
        {"uid": uid},
    ).mappings().fetchall()
    out: list[tuple[NextStep, str]] = []
    for row in rows:
        cid = row["id"]
        title = row["title"] or cid
        step = _make_step(
            rule_id=RULE_COURSE_NOT_PUBLISHED,
            target_id=cid,
            title=f"コース『{title}』を公開する",
            reason=f"コース『{title}』はテンプレートとして登録されていますが、まだ公開されていません。",
            target={"course_id": cid},
            ctx={"course_id": cid},
        )
        out.append((step, _iso(row["created_at"])))
    return out


def _eval_course_no_atlas_binding(session, uid: str) -> list[tuple[NextStep, str]]:
    rows = session.execute(
        sa_text(
            "SELECT id, title, data, created_at FROM learning_courses "
            "WHERE user_id = CAST(:uid AS uuid) ORDER BY created_at ASC"
        ),
        {"uid": uid},
    ).mappings().fetchall()
    out: list[tuple[NextStep, str]] = []
    for row in rows:
        data = row["data"] if isinstance(row["data"], dict) else {}
        if not _course_needs_atlas_binding(data):
            continue
        # atlas_binding_lifecycle_design.md §5: 凍結待ちを仮予約済みのコースには
        # 二重督促しない（course.atlas_binding_ready ルールが凍結後に代わりに出る）。
        if course_atlas_binding_pending(data):
            continue
        cid = row["id"]
        title = row["title"] or cid
        step = _make_step(
            rule_id=RULE_COURSE_NO_ATLAS_BINDING,
            target_id=cid,
            title=f"コース『{title}』に学習マップを割り当てる",
            reason=f"コース『{title}』には学習マップ（分野の地図）が割り当てられていません。",
            target={"course_id": cid},
            ctx={"course_id": cid},
        )
        out.append((step, _iso(row["created_at"])))
    return out


def _eval_course_atlas_binding_ready(session, uid: str) -> list[tuple[NextStep, str]]:
    """atlas_binding_lifecycle_design.md §5: 仮予約したドメインの骨格が凍結され、
    割り当てを完了できる状態（AND 条件: pending 非空 / 未バインドのまま /
    pending ドメインに凍結骨格あり / pending ドメインが active）。
    """
    rows = session.execute(
        sa_text(
            "SELECT id, title, data, created_at FROM learning_courses "
            "WHERE user_id = CAST(:uid AS uuid) ORDER BY created_at ASC"
        ),
        {"uid": uid},
    ).mappings().fetchall()
    skeleton_cache: dict[str, Any] = {}
    lifecycle_cache: dict[str, str] = {}
    out: list[tuple[NextStep, str]] = []
    for row in rows:
        data = row["data"] if isinstance(row["data"], dict) else {}
        pending = course_atlas_binding_pending(data)
        if not pending:
            continue
        if not _course_needs_atlas_binding(data):
            continue
        if pending not in skeleton_cache:
            try:
                skeleton_cache[pending] = atlas_store.load_learner_skeleton(pending, session)
            except Exception:  # noqa: BLE001
                skeleton_cache[pending] = None
        if skeleton_cache[pending] is None:
            continue
        if pending not in lifecycle_cache:
            try:
                lifecycle_cache[pending] = atlas_store.domain_lifecycle(session, pending)
            except Exception:  # noqa: BLE001
                lifecycle_cache[pending] = "active"
        if lifecycle_cache[pending] != "active":
            continue
        cid = row["id"]
        title = row["title"] or cid
        step = _make_step(
            rule_id=RULE_COURSE_ATLAS_BINDING_READY,
            target_id=cid,
            title=f"コース『{title}』の学習マップ割り当てを完了する",
            reason=(
                f"保留中の分野『{pending}』の骨格が凍結され、"
                f"コース『{title}』の学習マップ割り当てを完了できます。"
            ),
            target={"course_id": cid},
            ctx={"course_id": cid},
        )
        out.append((step, _iso(row["created_at"])))
    return out


def _eval_course_atlas_binding_stale(session, uid: str) -> list[tuple[NextStep, str]]:
    """atlas_binding_lifecycle_design.md §5: バインド済み node_id が現行凍結版に
    存在しない（改版で概念が削除・改名され、既存バインドが取り残された状態）。

    骨格の読みはドメイン単位で辞書キャッシュし、コースごとの N+1 を避ける。
    骨格が読めない（未凍結・DB 不通等）ドメインは判定不能として対象外にする
    （fail-closed。誤って stale と断定しない）。
    """
    rows = session.execute(
        sa_text(
            "SELECT id, title, data, created_at FROM learning_courses "
            "WHERE user_id = CAST(:uid AS uuid) ORDER BY created_at ASC"
        ),
        {"uid": uid},
    ).mappings().fetchall()
    skeleton_cache: dict[str, Any] = {}
    out: list[tuple[NextStep, str]] = []
    for row in rows:
        data = row["data"] if isinstance(row["data"], dict) else {}
        cartridge_id = course_cartridge_id(data)
        if not cartridge_id:
            continue
        if cartridge_id not in skeleton_cache:
            try:
                skeleton_cache[cartridge_id] = atlas_store.load_learner_skeleton(cartridge_id, session)
            except Exception:  # noqa: BLE001
                skeleton_cache[cartridge_id] = None
        skeleton = skeleton_cache[cartridge_id]
        if skeleton is None:
            continue
        known_ids = set(skeleton.concept_ids()) | set(skeleton.region_ids())
        stale_count = 0
        for topic in iter_all_topics(data):
            node_id = topic.get("atlas_node_id")
            if node_id and node_id not in known_ids:
                stale_count += 1
        if stale_count == 0:
            continue
        cid = row["id"]
        title = row["title"] or cid
        step = _make_step(
            rule_id=RULE_COURSE_ATLAS_BINDING_STALE,
            target_id=cid,
            title=f"コース『{title}』の学習マップ対応を確認する",
            reason=(
                f"コース『{title}』のトピック対応 {stale_count} 件が、"
                "現在の地図に存在しない概念を指しています。"
            ),
            target={"course_id": cid},
            ctx={"course_id": cid},
        )
        out.append((step, _iso(row["created_at"])))
    return out


def _eval_course_audio_missing(session, uid: str) -> list[tuple[NextStep, str]]:
    rows = session.execute(
        sa_text(
            "SELECT id, title, created_at FROM learning_courses "
            "WHERE user_id = CAST(:uid AS uuid) ORDER BY created_at ASC"
        ),
        {"uid": uid},
    ).mappings().fetchall()
    out: list[tuple[NextStep, str]] = []
    for row in rows:
        cid = row["id"]
        cs = status_projector.project_course_status(session, cid)
        script_has_content = cs.script_status in (
            status_schema.SCRIPT_STATUS_GENERATED,
            status_schema.SCRIPT_STATUS_PARTIAL,
        )
        audio_incomplete = cs.audio_status in (
            status_schema.AUDIO_STATUS_NONE,
            status_schema.AUDIO_STATUS_PARTIAL,
        )
        if not (script_has_content and audio_incomplete):
            continue
        title = row["title"] or cid
        step = _make_step(
            rule_id=RULE_COURSE_AUDIO_MISSING,
            target_id=cid,
            title=f"コース『{title}』の音声を生成する",
            reason=f"コース『{title}』は原稿が生成されていますが、音声が未生成のチャンクが残っています。",
            target={"course_id": cid},
        )
        out.append((step, _iso(row["created_at"])))
    return out


def _eval_figure_unreviewed_modes(session, uid: str) -> list[tuple[NextStep, str]]:
    """N13: 本人所有の教材に、AI が分類したが未レビューの図・画像がある（#496）。

    「AI 分類済み」= `suggested_mode <> 'unknown'`（migration 052 の既定値のままの行は
    分類が走っていないため対象にしない）。「未レビュー」= `mode_review_status = 'pending'`。
    レビューが済めば行の状態が変わり項目は自動消滅する（G1: 完了フラグを持たない）。
    """
    rows = session.execute(
        sa_text("""
            SELECT d.id::text AS id, d.source_path, d.title, d.created_at,
                   count(*) AS pending_count
            FROM documents d
            -- document_figures.document_id は UUID の文字列表現（TEXT）で保持している。
            -- documents.id（UUID）と比較する際は UUID 側を文字列化する。
            JOIN document_figures f ON f.document_id = d.id::text
            WHERE d.uploaded_by = CAST(:uid AS uuid)
              AND f.mode_review_status = 'pending'
              AND f.suggested_mode <> 'unknown'
            GROUP BY d.id, d.source_path, d.title, d.created_at
            ORDER BY d.created_at ASC
        """),
        {"uid": uid},
    ).mappings().fetchall()
    out: list[tuple[NextStep, str]] = []
    for row in rows:
        doc_id = row["id"]
        title = row["title"] or doc_id
        count = int(row["pending_count"] or 0)
        # locate の material_row アンカーは教材一覧の data-material-id（= source_path）で
        # 行解決するため、ctx には source_path 優先の id を渡す（_eval_material_no_course と同じ慣例）。
        material_row_id = row["source_path"] or doc_id
        step = _make_step(
            rule_id=RULE_FIGURE_UNREVIEWED_MODES,
            target_id=doc_id,
            title=f"教材『{title}』の図・画像の分類を確認する",
            reason=f"教材『{title}』に AI が分類した図・画像が {count} 件あり、まだ確認されていません。",
            target={"material_id": doc_id},
            ctx={"material_id": material_row_id},
        )
        out.append((step, _iso(row["created_at"])))
    return out


def _eval_manual_help_gaps_pending(session, uid: str) -> list[tuple[NextStep, str]]:
    """需要側計器（manual_help_kb_design.md §4-1）: 学生 HELP ルートの

    「無ヒット（payload.no_hit）」または「未整備節ヒット（payload.documented=false）」を
    anchor 単位（無ヒットは専用バケツ `_HELP_GAP_NO_HIT_BUCKET`）で集計し、k-匿名
    （`core/privacy.py` 正本、リテラル再定義しない）を満たすセルのみ点灯する。
    質問逐語・ユーザー特定情報は集計に使わない（P3。SQL は count のみ読む）。
    書き直し/削除で `status='superseded'` になった痕跡は除外する（機能3 と同型）。
    このルールは特定教員の所有物に紐づかない全体計器のため `uid` は使わない
    （他ルールと同一の評価関数シグネチャに揃えるための引数）。
    """
    del uid
    rows = session.execute(
        sa_text("""
            SELECT COALESCE(NULLIF(payload->>'help_anchor', ''), :no_hit_bucket) AS bucket_key,
                   count(*) AS cnt,
                   max(created_at) AS last_seen
            FROM interest_traces
            WHERE kind = 'help_usage'
              AND status <> 'superseded'
              AND (
                    COALESCE((payload->>'no_hit')::boolean, false) = true
                    OR COALESCE((payload->>'documented')::boolean, true) = false
              )
            GROUP BY bucket_key
        """),
        {"no_hit_bucket": _HELP_GAP_NO_HIT_BUCKET},
    ).mappings().fetchall()
    out: list[tuple[NextStep, str]] = []
    for row in rows:
        count = int(row["cnt"] or 0)
        if not privacy.meets_k_anonymity(count):
            continue
        bucket_key = row["bucket_key"]
        count_range = privacy.bucket_count_range(count)
        if bucket_key == _HELP_GAP_NO_HIT_BUCKET:
            title = "使い方に関する質問（該当節なし）を確認する"
            reason = (
                "受講者からの使い方に関する質問で、マニュアルに一致する説明が見つからない"
                f"ケースが複数（{count_range}件）あります。"
            )
            target = {"anchor": None}
        else:
            # 表示は人間可読な節タイトルを優先し、索引から引けないときだけ
            # anchor パスへ縮退する（引用の正本は target.anchor に保持）。
            label = help_manual.section_title_for_citation(bucket_key) or (
                bucket_key[len("manual/"):] if bucket_key.startswith("manual/") else bucket_key
            )
            title = f"受講者マニュアルの『{label}』節を確認する"
            reason = (
                f"受講者マニュアルの『{label}』節への質問が複数（{count_range}件）ありますが、"
                "説明が未整備です。"
            )
            target = {"anchor": bucket_key}
        step = _make_step(
            rule_id=RULE_MANUAL_HELP_GAPS_PENDING,
            target_id=bucket_key,
            title=title,
            reason=reason,
            target=target,
        )
        out.append((step, _iso(row["last_seen"])))
    return out


def _eval_assistant_kb_undocumented(session, uid: str) -> list[tuple[NextStep, str]]:
    """供給側計器（manual_help_kb_design.md §4-1）: capability registry のうち

    操作KB（`docs/admin_operations/*.md`）に対応節が無い・空の（= `documented=False`,
    admin_assistant/knowledge.py の判定と同一基準）ものを1件に集約して To-Do 化する。
    DB を読まない（capability registry + KB 索引のみの純投影）。
    """
    del session, uid
    undocumented_titles: list[str] = []
    for cap in caps.all_capabilities():
        section = admin_kb.section_for_howto(cap.howto_doc) if cap.howto_doc else None
        documented = bool(section) and bool(section.get("body"))
        if not documented:
            undocumented_titles.append(cap.title)
    count = len(undocumented_titles)
    if count == 0:
        return []
    sample = "、".join(undocumented_titles[:3])
    reason = f"操作ナレッジベースに説明が整備されていない機能が {count} 件あります（例: {sample}）。"
    step = _make_step(
        rule_id=RULE_ASSISTANT_KB_UNDOCUMENTED,
        target_id="global",
        title="操作ナレッジベースの未整備箇所を確認する",
        reason=reason,
        target={},
    )
    return [(step, "")]


def _eval_manual_todo_unresolved(session, uid: str) -> list[tuple[NextStep, str]]:
    """TODO 可視化（manual_help_kb_design.md §4-1）: TODO 注記のため索引から

    除外されている docs/manual の節が1件以上あれば点灯する。TODO を解消すれば
    次回導出（起動時索引再構築後）で対象から自動で外れる（G1: 完了フラグを持たない）。
    """
    del session, uid
    excluded = help_manual.excluded_sections()
    count = len(excluded)
    if count == 0:
        return []
    reason = f"マニュアルの TODO 注記が残っているため索引から除外されている節が {count} 件あります。"
    step = _make_step(
        rule_id=RULE_MANUAL_TODO_UNRESOLVED,
        target_id="global",
        title="マニュアルの TODO を解消する",
        reason=reason,
        target={},
    )
    return [(step, "")]


_RULE_EVALUATORS = {
    RULE_MATERIALS_NONE: _eval_materials_none,
    RULE_MATERIAL_ANALYSIS_FAILED: _eval_material_analysis_failed,
    RULE_MATERIAL_NO_COURSE: _eval_material_no_course,
    RULE_COURSE_NOT_PUBLISHED: _eval_course_not_published,
    RULE_COURSE_NO_ATLAS_BINDING: _eval_course_no_atlas_binding,
    RULE_COURSE_ATLAS_BINDING_READY: _eval_course_atlas_binding_ready,
    RULE_COURSE_ATLAS_BINDING_STALE: _eval_course_atlas_binding_stale,
    RULE_COURSE_AUDIO_MISSING: _eval_course_audio_missing,
    RULE_FIGURE_UNREVIEWED_MODES: _eval_figure_unreviewed_modes,
    RULE_MANUAL_HELP_GAPS_PENDING: _eval_manual_help_gaps_pending,
    RULE_ASSISTANT_KB_UNDOCUMENTED: _eval_assistant_kb_undocumented,
    RULE_MANUAL_TODO_UNRESOLVED: _eval_manual_todo_unresolved,
}


# ---------------------------------------------------------------------------
# 整列・上限・却下との突合（設計 §3 の 3. / 4.、DB 非依存の純ロジック）
# ---------------------------------------------------------------------------


def finalize_next_steps(entries: list[tuple["NextStep", str]], dismissed_keys: set) -> dict:
    """severity 順・古い順に整列し、上限 `MAX_STEPS` 件で切り詰める。

    `entries` は (NextStep, sort_ts) のタプル列（sort_ts は ISO 文字列。空文字は最古扱い）。
    DB に依存しない純ロジックなので fake データで直接テストできる。
    """
    visible: list[tuple[NextStep, str]] = []
    hidden: list[tuple[NextStep, str]] = []
    for step, sort_ts in entries:
        (hidden if step.step_key in dismissed_keys else visible).append((step, sort_ts))

    def _key(entry: tuple[NextStep, str]) -> tuple:
        step, sort_ts = entry
        return (_SEVERITY_RANK.get(step.severity, len(_SEVERITY_RANK)), sort_ts)

    visible.sort(key=_key)
    hidden.sort(key=_key)
    truncated = len(visible) > MAX_STEPS
    visible = visible[:MAX_STEPS]
    return {
        "steps": [step.to_dict() for step, _ in visible],
        "hidden": [step.to_dict() for step, _ in hidden],
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# assistant_step_dismissals ストア（G5/P4: 却下は状態遷移で保持、行削除しない）
# ---------------------------------------------------------------------------


def fetch_dismissed_keys(session, user_id: str) -> set:
    if not user_id:
        return set()
    rows = session.execute(
        sa_text(
            "SELECT step_key FROM assistant_step_dismissals "
            "WHERE user_id = CAST(:uid AS uuid) AND revoked = FALSE"
        ),
        {"uid": user_id},
    ).fetchall()
    return {r[0] for r in rows}


def dismiss_step(session, user_id: str, step_key: str) -> None:
    """却下を upsert する（既存行があれば revoked=FALSE に戻す。G5）。"""
    session.execute(
        sa_text("""
            INSERT INTO assistant_step_dismissals (id, user_id, step_key, revoked, dismissed_at)
            VALUES (gen_random_uuid(), CAST(:uid AS uuid), :step_key, FALSE, now())
            ON CONFLICT (user_id, step_key)
            DO UPDATE SET revoked = FALSE, dismissed_at = now()
        """),
        {"uid": user_id, "step_key": step_key},
    )
    session.commit()


def restore_step(session, user_id: str, step_key: str) -> bool:
    """revoked=TRUE に戻す（行削除しない, G5/P4）。"""
    result = session.execute(
        sa_text("""
            UPDATE assistant_step_dismissals SET revoked = TRUE
            WHERE user_id = CAST(:uid AS uuid) AND step_key = :step_key
        """),
        {"uid": user_id, "step_key": step_key},
    )
    session.commit()
    return (result.rowcount or 0) > 0


def is_cue_pending(session, user_id: str) -> bool:
    """初回ログイン cue の一度きりフラグ（設計 §8）。

    専用テーブルを増やさず `assistant_step_dismissals` の
    `step_key='cue:first_login'`（revoked=FALSE）行の有無で代用する。
    確認不能（DB 例外）時は呼び出し側で fail-closed（表示しない）にできるよう
    例外はそのまま送出する。
    """
    if not user_id:
        return False
    row = session.execute(
        sa_text(
            "SELECT 1 FROM assistant_step_dismissals "
            "WHERE user_id = CAST(:uid AS uuid) AND step_key = :key AND revoked = FALSE LIMIT 1"
        ),
        {"uid": user_id, "key": CUE_FIRST_LOGIN_KEY},
    ).fetchone()
    return row is None


# ---------------------------------------------------------------------------
# 公開エントリポイント（設計 §3）
# ---------------------------------------------------------------------------


def compute_next_steps(session, user: dict) -> dict:
    """状態から Next Steps を投影する（G1: 保存しない・毎回導出）。

    `user` は routes の current_user と同形（少なくとも "id" / "role" を持つ dict）。
    参照 capability が現在ロールで到達不能なルールは評価すらしない（G3 fail-closed）。
    返り値は `{"steps": [...], "hidden": [...], "truncated": bool}`。
    """
    user = user or {}
    uid = str(user.get("id") or "")
    role = str(user.get("role") or "")

    if not uid:
        return finalize_next_steps([], set())

    active_rules = [
        rule_id for rule_id, rule in RULE_CATALOG.items()
        if caps.can_access(rule["capability_id"], role)
    ]
    if not active_rules:
        return finalize_next_steps([], set())

    entries: list[tuple[NextStep, str]] = []
    for rule_id in active_rules:
        entries.extend(_RULE_EVALUATORS[rule_id](session, uid))

    dismissed_keys = fetch_dismissed_keys(session, uid)
    return finalize_next_steps(entries, dismissed_keys)
