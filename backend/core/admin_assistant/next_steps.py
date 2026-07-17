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

from core.admin_assistant import capabilities as caps
from core.course_data import course_atlas_binding_facts
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
RULE_COURSE_AUDIO_MISSING = "course.audio_missing"
# N13（#496 追随）: 本人所有の教材に未レビューの AI 図分類が残っている。
# `material.inventory_unvisited` は「見たかどうか」の押し付けになるため意図的に
# 実装しない（vision_ux_gap_survey_2026-07-17.md §5-5 の見送り推奨, G4）。
RULE_FIGURE_UNREVIEWED_MODES = "figure.unreviewed_modes"

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
    RULE_COURSE_AUDIO_MISSING: {
        "severity": SEVERITY_OPTIONAL,
        "capability_id": "lecture_studio.generate_audio",
    },
    RULE_FIGURE_UNREVIEWED_MODES: {
        "severity": SEVERITY_RECOMMENDED,
        "capability_id": "materials.review_figures",  # 道案内のみ（図モーダルへ, #496）
    },
}

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
            JOIN document_figures f ON f.document_id = d.id
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


_RULE_EVALUATORS = {
    RULE_MATERIALS_NONE: _eval_materials_none,
    RULE_MATERIAL_ANALYSIS_FAILED: _eval_material_analysis_failed,
    RULE_MATERIAL_NO_COURSE: _eval_material_no_course,
    RULE_COURSE_NOT_PUBLISHED: _eval_course_not_published,
    RULE_COURSE_NO_ATLAS_BINDING: _eval_course_no_atlas_binding,
    RULE_COURSE_AUDIO_MISSING: _eval_course_audio_missing,
    RULE_FIGURE_UNREVIEWED_MODES: _eval_figure_unreviewed_modes,
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
