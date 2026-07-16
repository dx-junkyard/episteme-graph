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
