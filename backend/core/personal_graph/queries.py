"""個人知識ネットワーク用の SQL 読みプリミティブ（設計書 §4/§5）。

``core.personal_graph`` パッケージの中で**本人スコープ**の DB 読みを直接知るのは
このファイルのみ（Phase B の教員向けコーススコープ集約 ``bridges.py`` だけが例外として
自前の読みを持つ — 責務の分離は ``bridges.py`` docstring 参照）。
``core.postgres.get_session`` を直読みし、必ず try/finally で ``session.close()`` する
（開発ルール4）。FastAPI / routes / services / core.llm は import しない。

テーブル定義の正本: ``backend/db/020_interest_trace.sql``（interest_traces）、
``backend/db/036_reconstruction_loop.sql``（learner_reconstructions）、
``backend/db/init.sql``（learning_courses）。
"""

from __future__ import annotations

import json

from sqlalchemy import text as sa_text

from core.course_data import course_sources, course_title, iter_all_topics
from core.postgres import get_session as _pg_session


def _to_iso(value) -> str:
    """timestamptz 列を ISO 文字列へ正規化する（NULL は空文字。versioning 系と同じ扱い）。"""
    return value.isoformat() if value else ""


def _payload_dict(raw) -> dict:
    """JSONB 列を dict に正規化する（psycopg2 は通常 dict を返すが、文字列で来た場合も防御する）。"""
    if isinstance(raw, dict):
        return raw
    if raw:
        return json.loads(raw)
    return {}


def fetch_traces(user_id: str, course_id: str) -> list[dict]:
    """interest_traces から本人・コースの tension/question 行を読む。

    本人確定の絞り込み（TENSION_OWNED_STATUSES・superseded 除外等）は行わない
    （derive.py の責務。ここは生データの読み取りに徹する）。
    """
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, kind, status, topic_id, payload, created_at
                FROM interest_traces
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                  AND kind IN ('tension', 'question')
                ORDER BY created_at, id
            """),
            {"user_id": user_id, "course_id": course_id},
        ).fetchall()
    finally:
        session.close()
    return [
        {
            "id": str(r[0]),
            "kind": r[1],
            "status": r[2],
            "topic_id": r[3],
            "payload": _payload_dict(r[4]),
            "created_at": _to_iso(r[5]),
        }
        for r in rows
    ]


def fetch_reconstructions(user_id: str, course_id: str) -> list[dict]:
    """learner_reconstructions から本人・コースの行を読む。"""
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, item_id, claim_id, machine_verdict, self_check,
                       descended_to_symbol, revision_of, created_at
                FROM learner_reconstructions
                WHERE user_id = CAST(:user_id AS uuid) AND course_id = :course_id
                ORDER BY created_at, id
            """),
            {"user_id": user_id, "course_id": course_id},
        ).fetchall()
    finally:
        session.close()
    return [
        {
            "id": str(r[0]),
            "item_id": str(r[1]),
            "claim_id": str(r[2]),
            "machine_verdict": r[3],
            "self_check": r[4],
            "descended_to_symbol": bool(r[5]),
            "revision_of": str(r[6]) if r[6] else None,
            "created_at": _to_iso(r[7]),
        }
        for r in rows
    ]


def fetch_topic_atlas_binding(course_id: str) -> dict[str, str]:
    """コースの topics[].atlas_node_id が非空のものだけ {topic_id: atlas_node_id} で返す。

    ``core.course_data.iter_all_topics`` 経由で読む（フラット topics[] + 章ネスト
    chapters[].topics[] の両方を走査する。course_data.py への素の dict アクセス禁止ルール）。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT data FROM learning_courses WHERE id = :course_id"),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return {}
    data = _payload_dict(row[0])
    binding: dict[str, str] = {}
    for topic in iter_all_topics(data):
        topic_id = topic.get("id")
        atlas_node_id = topic.get("atlas_node_id")
        if topic_id and atlas_node_id:
            binding[str(topic_id)] = str(atlas_node_id)
    return binding


def _claim_topic_map_from_data(data: dict) -> dict[str, str]:
    """``topics[].linked_claim_ids`` から {claim_id: topic_id} を組み立てる（N16）。

    ``linked_claim_ids`` は ``core/course_content_builder.py`` がトピック教材生成時に
    component 経由で決定論的に書き込む既存フィールド（``CourseTopic.linked_claim_ids``、
    正本は ``core/course_data.py``）。LLM を使わず、既存の教材構築結果を読むだけで
    claim_id → topic_id を逆引きできる。1つの claim が複数トピックに現れる場合は
    ``iter_all_topics`` の出現順で最初に見つかったトピックを採用する（決定論）。
    """
    mapping: dict[str, str] = {}
    for topic in iter_all_topics(data):
        topic_id = topic.get("id")
        if not topic_id:
            continue
        for claim_id in topic.get("linked_claim_ids") or []:
            claim_id = str(claim_id)
            if claim_id and claim_id not in mapping:
                mapping[claim_id] = str(topic_id)
    return mapping


def _topic_labels_from_data(data: dict) -> dict[str, str]:
    """``topics[].title`` から {topic_id: トピック題名} を組み立てる。

    「わたしの地図」の topic 縮退アンカー（``derive.py`` の ``_question_anchor`` /
    ``_tension_anchor`` のフォールバック分岐）に付ける ``anchor_label`` の材料。読み方は
    ``_claim_topic_map_from_data`` と同じ ``iter_all_topics`` 経由（フラット ``topics[]`` +
    章ネスト ``chapters[].topics[]`` の両方を走査。``course_data.py`` への素の dict
    アクセス禁止ルールに従い、正本アクセサのみを使う）。

    ``id`` が無い topic と ``title`` が空（未設定・空白のみ）の topic はキーごと省く
    （P4: 題名が引けないときは捏造せず、``anchor_label`` を空のままにする）。
    """
    labels: dict[str, str] = {}
    for topic in iter_all_topics(data):
        topic_id = topic.get("id")
        if not topic_id:
            continue
        title = str(topic.get("title") or "").strip()
        if title:
            labels[str(topic_id)] = title
    return labels


def fetch_topic_labels(course_id: str) -> dict[str, str]:
    """コースの {topic_id: トピック題名} を返す（題名未設定の topic はキーごと省く）。

    ``derive.derive_personal_network`` が topic 縮退アンカーの ``anchor_label`` 解決に
    使う（``fetch_topic_atlas_binding`` と同型の1 SELECT）。コースが無い場合は空 dict
    （PN-7 fail-closed。題名が引けなければ ``anchor_label`` は空のまま）。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT data FROM learning_courses WHERE id = :course_id"),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return {}
    return _topic_labels_from_data(_payload_dict(row[0]))


def fetch_topic_labels_for_courses(course_ids: list[str]) -> dict[str, dict[str, str]]:
    """複数コースのトピック題名を1クエリで ``{course_id: {topic_id: title}}`` として返す。

    ``fetch_topic_atlas_binding_for_courses`` / ``fetch_claim_topic_map_for_courses`` と
    同型（本人スコープの導出はコースをまたいで束ねられるため、``fetch_topic_labels`` を
    コース数だけ N 回呼ばない）。空リストは ``{}``、存在しないコースはキーごと欠落
    （PN-7 fail-closed）。
    """
    ids = [str(c) for c in course_ids if c]
    if not ids:
        return {}
    session = _pg_session()
    try:
        placeholders = ", ".join(f":cid_{i}" for i in range(len(ids)))
        rows = session.execute(
            sa_text(f"SELECT id, data FROM learning_courses WHERE id IN ({placeholders})"),
            {f"cid_{i}": course_id for i, course_id in enumerate(ids)},
        ).fetchall()
    finally:
        session.close()
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        result[str(row[0])] = _topic_labels_from_data(_payload_dict(row[1]))
    return result


def _topic_claim_binding_from_data(data: dict, topic_id: str) -> dict:
    """``data`` から1トピックの ``{claim_ids, topic_label}`` を組み立てる（範囲モード用）。

    ``_claim_topic_map_from_data`` と同じ ``iter_all_topics`` 経由の読み方（フラット
    ``topics[]`` + 章ネスト ``chapters[].topics[]`` の両方を走査。素の dict アクセス
    禁止ルールに合わせ ``course_data.py`` の正本関数のみを使う）。``claim_ids`` は
    ``linked_claim_ids`` の出現順・重複除去（最初の出現を残す）。見つからなければ
    ``{"claim_ids": [], "topic_label": ""}``（P4: 欠落をエラーにしない）。
    """
    for topic in iter_all_topics(data):
        if str(topic.get("id") or "") != str(topic_id):
            continue
        claim_ids: list[str] = []
        seen: set[str] = set()
        for claim_id in topic.get("linked_claim_ids") or []:
            claim_id = str(claim_id)
            if claim_id and claim_id not in seen:
                seen.add(claim_id)
                claim_ids.append(claim_id)
        topic_label = str(topic.get("title") or "").strip()
        return {"claim_ids": claim_ids, "topic_label": topic_label}
    return {"claim_ids": [], "topic_label": ""}


def fetch_topic_claim_binding(course_id: str, topic_id: str) -> dict:
    """コースの1トピックについて ``{claim_ids: [...], topic_label: str}`` を返す。

    近傍関係ビューの範囲モード（``nearby.py``、設計正本
    ``docs/features/personal_map_nearby_design.md``）専用の読み。``topics[].
    linked_claim_ids``（``course_content_builder.py`` が component 経由で決定論的に
    書き込む既存フィールド）をそのまま読むだけで、AI 推定を一切行わない。コースが無い・
    トピックが見つからない場合は ``{"claim_ids": [], "topic_label": ""}``（PN-7 fail-closed）。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT data FROM learning_courses WHERE id = :course_id"),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return {"claim_ids": [], "topic_label": ""}
    return _topic_claim_binding_from_data(_payload_dict(row[0]), topic_id)


def fetch_claim_topic_map(course_id: str) -> dict[str, str]:
    """コースの ``topics[].linked_claim_ids`` から {claim_id: topic_id} を解決する（N16）。

    ``learner_reconstructions.claim_id`` はどのトピックの教材にも紐づかない場合
    ``topic_id=None`` のまま導出される（旅が atlas 骨格・コーススコープ近傍へ構造的に
    到達できない既知の限界。設計書 §14）。ここでは既存の決定論的マッピング
    （``course_content_builder.py`` がトピック生成時に component 経由で書き込む
    ``linked_claim_ids``）を逆引きすることで、claim が実際に教材へ組み込まれている
    ケースについてのみ topic_id を解決する（解決不能な claim はキー自体が無い＝
    従来どおり None のまま。P4: 情報を落とさない・fail-closed）。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT data FROM learning_courses WHERE id = :course_id"),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return {}
    return _claim_topic_map_from_data(_payload_dict(row[0]))


# ---------------------------------------------------------------------------
# Phase P-0.5（意味論移行, 設計書 §5.1〜5.3）向けの本人スコープ読み取りプリミティブ。
# course_id を所有境界の WHERE 条件にせず、出所（provenance）として返却 dict に含める。
# ---------------------------------------------------------------------------


def fetch_traces_for_user(user_id: str) -> list[dict]:
    """interest_traces から本人の tension/question 行を、コース条件なしで読む。

    ``fetch_traces``（コーススコープ, Phase P-0）との差分は2点: (1) WHERE に course_id
    条件を持たない（本人の全コースを横断して読む）、(2) 返却 dict に ``course_id`` を
    追加する（呼び出し側 ``derive.build_person_network`` がコースごとにグルーピングし
    直すための出所メタ。設計書 §5.1「course_id は所有境界ではなく provenance」）。
    本人確定の絞り込み（TENSION_OWNED_STATUSES・superseded 除外等）は行わない
    （derive.py の責務。ここは生データの読み取りに徹する）。
    """
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, kind, status, topic_id, payload, created_at, course_id
                FROM interest_traces
                WHERE user_id = CAST(:user_id AS uuid)
                  AND kind IN ('tension', 'question')
                ORDER BY created_at, id
            """),
            {"user_id": user_id},
        ).fetchall()
    finally:
        session.close()
    return [
        {
            "id": str(r[0]),
            "kind": r[1],
            "status": r[2],
            "topic_id": r[3],
            "payload": _payload_dict(r[4]),
            "created_at": _to_iso(r[5]),
            "course_id": str(r[6]) if r[6] is not None else "",
        }
        for r in rows
    ]


def fetch_reconstructions_for_user(user_id: str) -> list[dict]:
    """learner_reconstructions から本人の行を、コース条件なしで読む。

    ``fetch_reconstructions`` との差分は ``fetch_traces_for_user`` と同型
    （course_id 条件を外し、返却 dict に ``course_id`` を追加するだけ）。
    """
    session = _pg_session()
    try:
        rows = session.execute(
            sa_text("""
                SELECT id, item_id, claim_id, machine_verdict, self_check,
                       descended_to_symbol, revision_of, created_at, course_id
                FROM learner_reconstructions
                WHERE user_id = CAST(:user_id AS uuid)
                ORDER BY created_at, id
            """),
            {"user_id": user_id},
        ).fetchall()
    finally:
        session.close()
    return [
        {
            "id": str(r[0]),
            "item_id": str(r[1]),
            "claim_id": str(r[2]),
            "machine_verdict": r[3],
            "self_check": r[4],
            "descended_to_symbol": bool(r[5]),
            "revision_of": str(r[6]) if r[6] else None,
            "created_at": _to_iso(r[7]),
            "course_id": str(r[8]) if r[8] is not None else "",
        }
        for r in rows
    ]


def fetch_topic_atlas_binding_for_courses(course_ids: list[str]) -> dict[str, dict[str, str]]:
    """複数コースの topics[].atlas_node_id を1クエリで ``{course_id: {topic_id: atlas_node_id}}``
    として返す（設計書 §5.1〜5.3。個人ネットワークがコースをまたいで束ねられるため、
    ``fetch_topic_atlas_binding`` をコース数だけ N 回呼ぶのではなく、対象コース ID 集合を
    まとめて解決する）。空リストは ``{}``。読み方は ``fetch_topic_atlas_binding`` と同じ
    ``core.course_data.iter_all_topics`` 経由（素の dict アクセス禁止）。
    """
    ids = [str(c) for c in course_ids if c]
    if not ids:
        return {}
    session = _pg_session()
    try:
        placeholders = ", ".join(f":cid_{i}" for i in range(len(ids)))
        rows = session.execute(
            sa_text(f"SELECT id, data FROM learning_courses WHERE id IN ({placeholders})"),
            {f"cid_{i}": course_id for i, course_id in enumerate(ids)},
        ).fetchall()
    finally:
        session.close()
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        course_id = str(row[0])
        data = _payload_dict(row[1])
        binding: dict[str, str] = {}
        for topic in iter_all_topics(data):
            topic_id = topic.get("id")
            atlas_node_id = topic.get("atlas_node_id")
            if topic_id and atlas_node_id:
                binding[str(topic_id)] = str(atlas_node_id)
        result[course_id] = binding
    return result


def fetch_claim_topic_map_for_courses(course_ids: list[str]) -> dict[str, dict[str, str]]:
    """複数コースの claim→topic_id マップを1クエリで ``{course_id: {claim_id: topic_id}}``
    として返す（N16, 設計書 §5.1〜5.3 の本人スコープ版と同型）。個人ネットワークが
    コースをまたいで束ねられるため、``fetch_claim_topic_map`` をコース数だけ N 回
    呼ぶのではなく、対象コース ID 集合をまとめて解決する。空リストは ``{}``。
    """
    ids = [str(c) for c in course_ids if c]
    if not ids:
        return {}
    session = _pg_session()
    try:
        placeholders = ", ".join(f":cid_{i}" for i in range(len(ids)))
        rows = session.execute(
            sa_text(f"SELECT id, data FROM learning_courses WHERE id IN ({placeholders})"),
            {f"cid_{i}": course_id for i, course_id in enumerate(ids)},
        ).fetchall()
    finally:
        session.close()
    result: dict[str, dict[str, str]] = {}
    for row in rows:
        course_id = str(row[0])
        data = _payload_dict(row[1])
        result[course_id] = _claim_topic_map_from_data(data)
    return result


def fetch_course_titles(course_ids: list[str]) -> dict[str, str]:
    """複数コースのタイトルを ``{course_id: title}`` で返す（設計書 §5.1）。

    ``core.course_data.course_title`` アクセサ経由で ``learning_courses.data`` からタイトルを
    引く（素の dict アクセス禁止）。存在しない・削除済みコース、タイトル未設定のコースは
    キー自体を省く（P-0.5 完了条件: コースを削除・終了しても、本人の痕跡は概念上
    「本人の地図」から消えない — タイトルが引けなくなるだけでノード自体は残る）。
    """
    ids = [str(c) for c in course_ids if c]
    if not ids:
        return {}
    session = _pg_session()
    try:
        placeholders = ", ".join(f":cid_{i}" for i in range(len(ids)))
        rows = session.execute(
            sa_text(f"SELECT id, data FROM learning_courses WHERE id IN ({placeholders})"),
            {f"cid_{i}": course_id for i, course_id in enumerate(ids)},
        ).fetchall()
    finally:
        session.close()
    titles: dict[str, str] = {}
    for row in rows:
        course_id = str(row[0])
        data = _payload_dict(row[1])
        title = course_title(data)
        if title:
            titles[course_id] = title
    return titles


# ---------------------------------------------------------------------------
# Phase P-2（旅の経路探索, journey.py）向けの読み取りプリミティブ。
# ---------------------------------------------------------------------------


def fetch_component_document_id(component_id: str) -> str | None:
    """theory_components.source_scope->>'document_id' を返す（無ければ None）。

    theory_components は document_id 列を持たず、``source_scope`` JSONB に
    格納する（``routes/theory_components.py::_normalize_source_scope`` 参照）。
    旅の [1] 論文ローカルグラフの起点解決に使う。component_id が UUID として
    不正な場合も例外を投げず None に倒す（PN-7 fail-closed）。
    """
    session = _pg_session()
    try:
        try:
            row = session.execute(
                sa_text(
                    "SELECT source_scope->>'document_id' FROM theory_components "
                    "WHERE id = CAST(:id AS uuid)"
                ),
                {"id": component_id},
            ).fetchone()
        except Exception:
            return None
    finally:
        session.close()
    return str(row[0]) if row and row[0] else None


def fetch_claim_document_id(claim_id: str) -> str | None:
    """theory_claims.document_id を返す（無ければ None）。claim アンカーの document 解決用。"""
    session = _pg_session()
    try:
        try:
            row = session.execute(
                sa_text("SELECT document_id FROM theory_claims WHERE id = CAST(:id AS uuid)"),
                {"id": claim_id},
            ).fetchone()
        except Exception:
            return None
    finally:
        session.close()
    return str(row[0]) if row and row[0] else None


def fetch_component_graph(document_id: str) -> dict:
    """theory_component_graphs.graph_json の最新1件を返す（無ければ ``{}``）。

    ``routes/theory_components.py::_stored_component_graph`` と同じ「最新1件」読みだが、
    ここでは正規化（``_normalize_stored_component_graph``。theory_components 一覧との突合や
    レガシー main label 補正を行う）はしない — 旅の traversal は ComponentGraphAgent が
    出力する生フィールド（``component_id`` / ``label`` / ``graph_layer`` /
    ``member_component_ids`` / ``linked_claim_ids`` 等）をそのまま読むだけで足りるため
    （component 一覧を別途引く必要が無い軽量な読み取り）。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("""
                SELECT graph_json
                FROM theory_component_graphs
                WHERE document_id = :document_id
                ORDER BY updated_at DESC
                LIMIT 1
            """),
            {"document_id": document_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return {}
    graph = _payload_dict(row[0])
    return graph if isinstance(graph, dict) else {}


def fetch_course_document_ids(course_id: str) -> set[str]:
    """コースの ``sources[]`` から document_id 集合を解決する（旅の [3] コース内限定用）。

    ``routes/theory_components.py::list_theory_components`` 末尾の解決 SQL と同型:
    source が明示 ``document_id`` を持てばそれを、無ければ ``material_id`` を集めて
    ``chunks.material_id -> chunks.document_id`` で解決する。解決できない material_id は
    黙って無視する（PN-7 fail-closed。件数に言及しない）。
    """
    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT data FROM learning_courses WHERE id = :course_id"),
            {"course_id": course_id},
        ).fetchone()
        if not row:
            return set()
        data = _payload_dict(row[0])
        document_ids: set[str] = set()
        material_ids: list[str] = []
        for source in course_sources(data):
            if source.get("document_id"):
                document_ids.add(str(source["document_id"]))
            if source.get("material_id"):
                material_ids.append(str(source["material_id"]))
        if material_ids:
            placeholders = ", ".join(f":mid_{i}" for i in range(len(material_ids)))
            mid_rows = session.execute(
                sa_text(f"""
                    SELECT DISTINCT document_id FROM chunks
                    WHERE material_id IN ({placeholders}) AND document_id IS NOT NULL
                """),
                {f"mid_{i}": material_id for i, material_id in enumerate(material_ids)},
            ).fetchall()
            document_ids.update(str(r[0]) for r in mid_rows if r[0])
        return document_ids
    finally:
        session.close()


def fetch_library_entry_names(entry_ids: list[str]) -> dict[str, str]:
    """library_entries.name を id → name で返す（``status='active'`` のみ。retired は除外）。"""
    ids = [str(e) for e in entry_ids if e]
    if not ids:
        return {}
    session = _pg_session()
    try:
        placeholders = ", ".join(f"CAST(:id_{i} AS uuid)" for i in range(len(ids)))
        try:
            rows = session.execute(
                sa_text(f"""
                    SELECT id::text, name FROM library_entries
                    WHERE id IN ({placeholders}) AND status = 'active'
                """),
                {f"id_{i}": entry_id for i, entry_id in enumerate(ids)},
            ).fetchall()
        except Exception:
            return {}
    finally:
        session.close()
    return {str(r[0]): (r[1] or "") for r in rows}


def fetch_library_entry_source_document_ids(entry_id: str) -> list[str]:
    """library_entries.source_document_ids（``status='active'`` のみ）を返す。

    retired・存在しない entry は空リスト（PN-7 fail-closed）。
    """
    session = _pg_session()
    try:
        try:
            row = session.execute(
                sa_text("""
                    SELECT source_document_ids FROM library_entries
                    WHERE id = CAST(:id AS uuid) AND status = 'active'
                """),
                {"id": entry_id},
            ).fetchone()
        except Exception:
            return []
    finally:
        session.close()
    if not row:
        return []
    raw = row[0]
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    if raw:
        try:
            data = json.loads(raw)
        except Exception:
            return []
        return [str(x) for x in data if x] if isinstance(data, list) else []
    return []


def fetch_document_titles(document_ids: list[str]) -> dict[str, str]:
    """documents.title を id → title で返す（旅の [3] 他教材ラベル用）。"""
    ids = [str(d) for d in document_ids if d]
    if not ids:
        return {}
    session = _pg_session()
    try:
        placeholders = ", ".join(f"CAST(:id_{i} AS uuid)" for i in range(len(ids)))
        try:
            rows = session.execute(
                sa_text(f"SELECT id::text, title FROM documents WHERE id IN ({placeholders})"),
                {f"id_{i}": doc_id for i, doc_id in enumerate(ids)},
            ).fetchall()
        except Exception:
            return {}
    finally:
        session.close()
    return {str(r[0]): (r[1] or "") for r in rows}


def fetch_confirmed_identity_links(document_id: str) -> list[dict]:
    """document 内の confirmed 同一性リンクを返す（PN-6: candidate/rejected は読まない）。

    正本は ``core.deliberation.identity_links.confirmed_links_for_document``（Phase W-β）。
    ``core.personal_graph`` パッケージ内で DB を直接知るのはこのファイルのみ、という
    docstring 上の規約を維持するため、journey.py は本関数経由でのみ同一性リンクを読む。
    W-β が未適用/未実装の環境でも旅が縮退できるよう、import・実行時エラーは吸収して
    空リストへフォールバックする（PN-7）。
    """
    try:
        from core.deliberation.identity_links import confirmed_links_for_document
    except Exception:
        return []
    try:
        return confirmed_links_for_document(document_id)
    except Exception:
        return []


def fetch_confirmed_links_for_shared_part(shared_part_id: str) -> list[dict]:
    """shared_part（library_entry）に紐づく同一性リンクのうち confirmed のみ返す（PN-6）。

    ``core.deliberation.identity_links.list_for_shared_part`` は候補・却下も含めた
    全 status を返す設計（P4: あるインスタンス要素の同一性リンク履歴を教員が振り返れる
    ようにするため）なので、ここで confirmed のみへ絞り込んでから journey.py に渡す
    （candidate/rejected を旅の根拠にしない）。
    """
    try:
        from core.deliberation.identity_links import list_for_shared_part
    except Exception:
        return []
    try:
        rows = list_for_shared_part(shared_part_id)
    except Exception:
        return []
    return [row for row in rows if row.get("status") == "confirmed"]


def fetch_component_ledger_statuses(component_ids: list[str]) -> dict[str, str]:
    """``epistemic_ledger`` の検証状態を ``component_id -> verification_status`` で返す。

    近傍関係ビュー（``nearby.py``、設計書
    ``docs/features/personal_map_nearby_design.md`` §3.3）専用の読み。キーは
    ``target_type='component'`` / ``target_id = component_id`` で、
    ``core/doubt/ledger_builder.py`` が TheoryOperationGraph の main 層ノードについて
    作る行と同一（同じキーを別解釈しない）。

    **``load_score`` は読まない**（PMN-4: 数値を返さない）。台帳行が無い component は
    キーごと欠落させる（呼び出し側が ``verification: null`` に倒す）。行が無い・
    テーブルが無い等は空 dict へ倒す（PN-7 fail-closed）。
    """
    ids = [str(c) for c in component_ids if c]
    if not ids:
        return {}
    session = _pg_session()
    try:
        placeholders = ", ".join(f":cid_{i}" for i in range(len(ids)))
        try:
            rows = session.execute(
                sa_text(f"""
                    SELECT target_id, verification_status
                    FROM epistemic_ledger
                    WHERE target_type = 'component' AND target_id IN ({placeholders})
                """),
                {f"cid_{i}": cid for i, cid in enumerate(ids)},
            ).fetchall()
        except Exception:
            return {}
    finally:
        session.close()
    return {str(r[0]): str(r[1] or "") for r in rows if r and r[0]}


def fetch_center_support_fact_line(
    component_id: str, *, document_id: str = "", course_id: str = ""
) -> str:
    """中心ノード1件の独立支持経路の**事実文だけ**を返す（無ければ空文字）。

    実体は ``core/doubt/support_paths.py``（SL-3）。``level`` / ``cut_members`` /
    ``observation_roots`` は返さない（PMN-4。``fact_line`` は既存の学習者向け台帳 API
    が既に学習者へ出している文言と同一なので、そのまま出せる）。

    最大流の計算は中心1件のみに使う（上流・下流の全ノードには回さない — 設計書 §3.4）。
    計算不能・グラフ不在・例外はすべて空文字へ倒す（PN-7 fail-closed）。
    """
    if not component_id:
        return ""
    # 遅延 import: core.doubt は core.personal_graph の必須依存にしない（台帳未導入でも動く）
    from core.doubt.support_paths import (
        build_support_context,
        compute_support_lines_from_context,
    )

    session = _pg_session()
    try:
        try:
            ctx = build_support_context(
                session, course_id=str(course_id or ""), document_id=str(document_id or "")
            )
            result = compute_support_lines_from_context(ctx, "component", str(component_id))
        except Exception:
            return ""
    finally:
        session.close()
    if not isinstance(result, dict):
        return ""
    return str(result.get("fact_line") or "")


# ---------------------------------------------------------------------------
# 「広がりの装置」（好奇心の情報設計）向けの読み取りプリミティブ。
# 設計正本は ``docs/features/personal_map_nearby_design.md``。
# ---------------------------------------------------------------------------


def fetch_course_cartridge_id(course_id: str) -> str:
    """コースの**明示** cartridge_id を返す（無ければ ``""``）。

    ``core.course_data.course_cartridge_id`` を経由するだけで、解析 run 由来の
    **導出**（``core.atlas_state.resolve_course_cartridge``）にはフォールバックしない。
    範囲ビューの分野接続行（装置4）・「名前のある霧」（装置1, ``atlas_fog.py``）が、
    無関係な既定カートリッジの骨格へ誤接続しないための意図的な制約
    （``core/course_data.py`` の素の dict アクセス禁止ルールに従い、読みは
    ``course_cartridge_id`` アクセサ経由のみ）。
    """
    # 遅延 import: 本ファイル冒頭の import 群を軽く保つ（他アクセサと同じ流儀を踏襲しつつ、
    # 新規ヘルパー限定の依存として分離する）。
    from core.course_data import course_cartridge_id

    session = _pg_session()
    try:
        row = session.execute(
            sa_text("SELECT data FROM learning_courses WHERE id = :course_id"),
            {"course_id": course_id},
        ).fetchone()
    finally:
        session.close()
    if not row:
        return ""
    return course_cartridge_id(_payload_dict(row[0]))


def fetch_atlas_concept_context(cartridge_id: str, atlas_node_id: str) -> dict | None:
    """凍結骨格から1概念の文脈（所属領域・骨格エッジの隣接概念・同領域の他概念）を読む。

    「名前のある霧」（``atlas_fog.py``、装置1）と範囲ビューの分野接続行
    （``nearby.build_topic_range``、装置4）が共用する骨格読みの単一集約点。

    読むのは ``atlas_store.load_learner_skeleton``（**凍結版のみ**を返す既存正本）
    経由のみ — draft を読む経路は作らない。概念が骨格中に見つからない・骨格が無い・
    import/実行時の例外はすべて ``None`` へ倒す（fail-soft。呼び出し側は事実文を
    出さないだけに留め、異常として演出しない）。

    戻り値（見つかった場合）::

        {
            "concept_id": str, "concept_label": str,
            "region_id": str, "region_label": str,
            "edge_neighbor_ids": [str, ...],
            "sibling_concepts": [{"id": str, "label": str}, ...],
            "edge_neighbors": [{"id": str, "label": str, "region_label": str}, ...],
        }

    ``edge_neighbor_ids`` は骨格エッジで直接つながる概念 ID の全量（順序は骨格の
    エッジ出現順・重複除去）。``edge_neighbors`` はそのうち骨格中に実在が確認できた
    ものだけを label/region_label 付きで列挙する（存在しない ID への言及はしない）。
    座標・件数・seed_status 等の数値は一切含めない。
    """
    cartridge_id = str(cartridge_id or "").strip()
    atlas_node_id = str(atlas_node_id or "").strip()
    if not cartridge_id or not atlas_node_id:
        return None
    try:
        # 遅延 import（core.doubt と同じ流儀）: atlas_store は core.personal_graph の
        # 必須依存にしない。
        from core.atlas_store import load_learner_skeleton
    except Exception:
        return None
    try:
        skeleton = load_learner_skeleton(cartridge_id)
    except Exception:
        return None
    if skeleton is None:
        return None

    concept_index: dict[str, tuple] = {}
    for region in skeleton.regions:
        for concept in region.concepts:
            concept_index[str(concept.id)] = (region, concept)

    hit = concept_index.get(atlas_node_id)
    if hit is None:
        return None
    region, concept = hit

    neighbor_ids: list[str] = []
    for edge in skeleton.edges:
        from_id = str(edge.from_id)
        to_id = str(edge.to_id)
        other = None
        if from_id == atlas_node_id and to_id != atlas_node_id:
            other = to_id
        elif to_id == atlas_node_id and from_id != atlas_node_id:
            other = from_id
        if other and other not in neighbor_ids:
            neighbor_ids.append(other)

    edge_neighbors: list[dict] = []
    for neighbor_id in neighbor_ids:
        neighbor_hit = concept_index.get(neighbor_id)
        if neighbor_hit is None:
            continue
        neighbor_region, neighbor_concept = neighbor_hit
        edge_neighbors.append(
            {
                "id": neighbor_id,
                "label": str(neighbor_concept.label or ""),
                "region_label": str(neighbor_region.label or ""),
            }
        )

    sibling_concepts = [
        {"id": str(c.id), "label": str(c.label or "")}
        for c in region.concepts
        if str(c.id) != atlas_node_id
    ]

    return {
        "concept_id": str(concept.id),
        "concept_label": str(concept.label or ""),
        "region_id": str(region.id),
        "region_label": str(region.label or ""),
        "edge_neighbor_ids": neighbor_ids,
        "sibling_concepts": sibling_concepts,
        "edge_neighbors": edge_neighbors,
    }
