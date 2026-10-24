"""第 25 周 面 D（原稿スタジオ → 学生の受講）の行為の実行と画面の投影。

行為 id と引数は ``uxsim/actions/studio_actions.py``。学生の ``learning.lecture.audio_status`` /
``learning.lecture.sequence`` も本モジュールが扱う（既存の行為 id のまま・画面どおりの投影に差し替える）。

- 画面が覚えるもの（``PersonaSession.scratch``）: ``studio_course_id`` / ``studio_structure``（course-structure）/
  ``studio_scripts``（lecture-scripts）/ ``studio_settings`` / ``studio_topic``（選んだトピックの DTO と位置）/
  ``studio_draft``（編集中の欄）/ ``studio_chunk``（選んだチャンク）/ ``studio_last_split``（最後のプレビュー）/
  ``studio_suggestions``（トピック別の図の提案）/ ``figure_studio``（モーダル 1 つ分の状態）/
  ``lecture_audio``（学生: course/topic 別の audio-status）。
- 投影は**画面と同じ欄を同じ形で**（IK-0634 / IK-0628 の型）。各関数の docstring に写した JS の関数名を書く。
  画面が描かない欄（``figures_restored`` / audio-status の ``note`` / 生の ``n_items`` 等）は投影にも出さない。
  代わりに審判（``uxsim/oracles/studio.py``）が読むための要約を ``HttpTrace.digest`` の ``studio_*`` キーに置く。
- このモジュールは ``actions_exec`` を import しない（``actions_exec`` が本モジュールの ``HANDLERS`` を取り込む。
  投影は ``state.project_observation`` から遅延 import で ``project`` を呼ぶ）。

API runner で再現できない画面の部分（ここで近似したこと）は ``runner_note`` に残す:
- 教材欄のカーソル位置は無い。区切りは段落の境目（段落が足りなければ、もう一方の欄の区切りに最も近い文の境目）に、
  図は ``after_heading`` の見出しの直後 → 提案の引用箇所 → 末尾の順に入れる（第 25 周 H2・H3）。
- スライドタブは、トピックを開いている間はそのトピックのチャンクだけを描く（第 25 周 H1）。
- 確認ダイアログ（図の提案の作り直し）は押した人が続行したものとして進める。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Callable, Optional

from uxsim.runner.state import PersonaSession, _clip, _error, _generic, render_material_text

# ---------------------------------------------------------------------------
# 画面の文言の逐語ミラー（JS が正本。変えたら JS を読んで写し直す）
# ---------------------------------------------------------------------------
# admin-lecture-studio.js lsRenderCourseStructure
COURSE_STATUS_LABELS = {"draft": "未着手", "partial": "一部生成", "generated": "生成済み",
                        "audio_generated": "音声生成済み", "no_chunks": "教材がまだありません"}  # IK-0952
COURSE_NO_CHAPTERS = "章構造がありません。コースビルダーでコース設計を完了してください。"
# lsAudioLangLabel
LANG_LABELS = {"ja": "日本語", "en": "English"}
# lsAudioReadinessGuidance
AUDIO_GUIDANCE = {
    "no_course": ("音声化するコースを選択してください", "コースを選択"),
    "loading": ("コースの音声準備状態を確認しています", "完了を待つ"),
    "content_incomplete": ("スライドの準備が完了していません", "コース内容生成を行う"),
    "script_incomplete": ("読み上げ原稿が未生成のスライドがあります", "原稿生成を行う"),
}
# lsTopicCoverageMessage
COVERAGE_MESSAGES = {
    "missing": "このトピックに対応するClaimまたはコンポーネントの対応付けが弱いため、本文説明を自動生成するには確認が必要です。",
    "weak": "根拠候補はありますが、授業用ドラフトとして使う前に教員確認が必要です。",
    "sufficient": "授業用ドラフトを支える根拠候補があります。",
}
# lsRenderSlideIndicatorEl
SPLIT_FAILED = "⚠ 分割プレビューの取得に失敗しました — 1枚に統合して表示しています"
# lsCourseDraftHtml / ボタンの title（ツールチップ）
MARKER_BTN_TITLE = ("最後にフォーカスしていた入力欄（教材 or 本文説明）のカーソル位置に区切りを挿入します。"
                    "もう一方には自動挿入されないため、対応する位置には手動で追加してください。")
DRAFT_HELP = ("数式は $...$ / $$...$$、埋め込みは ![[equation:id]]・![[figure:id]]・![[source:id]]・"
              "![[claim:id]]・![[component:id]] で記述できます。")
# lsSaveCourseTopicDraft / lsRewriteCourseTopicDraft / lsShowActionStatus
SAVE_OK = "授業用ドラフトを保存しました"
SAVE_FAIL = "保存に失敗しました"
REWRITE_OK = "提案を反映しました。保存すると確定します。"
REWRITE_FAIL = "AI提案に失敗しました"
REWRITE_SKIPPED = "AI提案を実行しませんでした"
REWRITE_NO_PROMPT = "指示を入力してください"
# admin-lecture-studio.js（第 25 周 第 2 波）
REATTACH_FAIL = "付録を付け直せませんでした"
REATTACH_BUTTON = "付録を付け直す"
SPLIT_MISMATCH_CONFIRM = "このまま保存すると 1 枚に統合して配信されます。"
# lsRenderSlidesPanel / lsSlideAudioBadgeHtml
SLIDE_LONG_WARN = "⚠ 受講画面で縮小表示されます。=== で分割を検討してください"
SLIDE_NO_SPOKEN = "読み上げ原稿がありません"
BADGE_LANG_MISMATCH = "言語不一致 ⚠"
BADGE_NONE = "音声未生成"
WM_PREFIX = "要素の相互作用: "
# lsCourseEvidenceHtml
EVIDENCE_EMPTY = "このトピックに対応する根拠リンク候補はまだありません。"
# element-vocab.js KIND_LABELS
KIND_LABELS = {"component": "論理要素", "claim": "主張", "equation": "数式", "figure": "図", "source": "出典",
               "evidence": "根拠箇所", "derivation": "導出", "shared_part": "共通部品"}
# lsLoadStumbleSummary / lsRenderStumbleSummary / lsStumbleAxis
STUMBLE_NO_DOCUMENT = "このコースに紐づく解析済みドキュメントが見つかりません。"
STUMBLE_FETCH_FAIL = "つまづき集計を取得できませんでした。"
STUMBLE_EMPTY_TOPIC = "このトピックの claim には、まだ再構成の記録がありません。"
STUMBLE_EMPTY_DOC = "このドキュメントには、まだ再構成の記録がありません。"
STUMBLE_EMPTY_TAIL = "学習者が再構成に挑戦すると、ここに難所が集計されます（k=3 未満は「まだデータなし」）。"
STUMBLE_NOTE = "段階ラベルとレンジのみを表示します（k=3 の匿名化。個人は特定されません）。"
STUMBLE_NO_DATA = "まだデータなし"
STUMBLE_AXES = (("誤り率", ("error_rate",)), ("記号降下", ("symbol_descent",)),
                ("判定の乖離", ("verdict_self_check_divergence",)), ("質問・誤解", ("faq", "questions")))
# lsFigureSuggestionsHtml ほか
SUGGEST_EMPTY = "このトピックの図の提案はまだありません。[提案を生成] を押すと、教材本文と学習者の記録の集計から候補を作ります。"
SUGGEST_DEGRADED = "AIによる提案の生成に失敗しました。時間をおいて再度お試しください。"
SUGGEST_OK = "提案を生成しました"
SUGGEST_DROPPED = "提案を生成しました（本文と対応が取れなかった候補は保存していません）"
SUGGEST_FAIL = "提案の生成に失敗しました"
SUGGEST_REGEN_CONFIRM = ("提案を作り直すと、まだ判断していない候補は差し替え済みになります。"
                         "作成済みの図と、却下した候補はそのまま残ります。")
SUGGEST_ALREADY = "この提案はすでに判断済みです。"
SUGGEST_STATUS_LABELS = {"candidate": "候補", "accepted": "作成中", "dismissed": "却下", "superseded": "差し替え済み"}
SUGGEST_BASIS_LABELS = {"text_analysis": "根拠: 教材本文の解析", "learner_signals": "根拠: 学習者の記録の集計",
                        "both": "根拠: 教材本文の解析と学習者の記録の集計"}
# admin-figure-studio.js
FIGURE_KIND_LABELS = {"concept_map": "概念関係図", "process_flow": "プロセス図", "structure_diagram": "構造・構成図",
                      "comparison": "対比図", "timeline": "時系列図", "coordinate": "座標・幾何図",
                      "data_plot_schematic": "模式グラフ", "other": "その他"}
TEACHING_STATUS_LABELS = {"draft": "下書き", "adopted": "採用済み", "retired": "回収済み"}
ADOPT_NOTICE = "保存するとこのトピックの生成済み音声は作り直しになります。トピックを保存するまで学習者には表示されません。"
SNAPSHOT_FAILED_NOTICE = "保存されました（配信用の画像の更新に失敗したため、再保存をおすすめします）。"
STUDIO_EMPTY_PREVIEW = "まだ図はありません。右の欄に「どんな図がほしいか」を書いて送ると、ここにプレビューが出ます。"
STUDIO_NO_CHANGE = "この応答では図は変わっていません。"
STUDIO_DEGRADED = "（AI 応答は生成できませんでした）"
STUDIO_SEND_FAIL = "送信に失敗しました"
STUDIO_SAVE_FAIL = "保存に失敗しました"
STUDIO_PRESET_HEAD = "次の方針で初版を描いてください: "
# app.js updateLectureToggleAvailability / startLecture
LECTURE_DISABLED_TITLE = "このトピックの音声はまだ生成されていません（管理画面で音声を生成してください）"
LECTURE_DISABLED_HINT = "音声未生成"
LECTURE_STALE_TITLE = "音声の言語が更新待ちです"
LECTURE_SLIDE_NO_AUDIO = "🔇 音声未生成"

PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
MARKER_TEXT = "\n===\n"
_MARKER_RE = re.compile(r"^\s*===\s*$", re.MULTILINE)
_FIGURE_EMBED_RE = re.compile(r"!\[\[\s*figure\s*:\s*([^\]\s]+)\s*\]\]")
_SVG_TEXT_RE = re.compile(r"<text\b[^>]*>(.*?)</text>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_FORMULA_PH_RE = re.compile(r"\[\[FORMULA_\d+\]\]")


def _runner_note(session: PersonaSession, note: str) -> None:
    session.scratch.setdefault("runner_notes", []).append(note)


def _digest(ctx, key: str, value: dict, *, index: int = -1) -> None:
    """直近（または ``index`` 番目）の HttpTrace の digest に審判向けの要約を置く。"""
    traces = getattr(ctx.client, "traces", None)
    if not traces:
        return
    try:
        trace = traces[index]
    except IndexError:
        return
    digest = getattr(trace, "digest", None)
    if isinstance(digest, dict):
        digest[key] = value


def _sha(*parts: str) -> str:
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()[:16]


def _ui(session: PersonaSession, view: dict, status: Optional[int] = 200) -> None:
    """画面に出すものを ``_studio`` に包んで置く（``project`` が読む）。"""
    session.last_status = status
    session.last_body = {"_studio": view}


def _lines(value: Any) -> list[str]:
    if isinstance(value, list):
        out = []
        for x in value:
            if isinstance(x, dict):
                x = x.get("question") or x.get("text") or ""
            if str(x or "").strip():
                out.append(str(x).strip())
        return out
    if isinstance(value, str):
        return [x.strip() for x in value.splitlines() if x.strip()]
    return []


# ---------------------------------------------------------------------------
# 教員: コースとトピック
# ---------------------------------------------------------------------------

def _topics(structure: Any) -> list[dict]:
    """course-structure の章 → トピックを並び順で（chapter_index / topic_index / 通し番号を添える）。"""
    out: list[dict] = []
    for ci, ch in enumerate((structure or {}).get("chapters") or [] if isinstance(structure, dict) else []):
        if not isinstance(ch, dict):
            continue
        for ti, t in enumerate(ch.get("topics") or []):
            if isinstance(t, dict):
                out.append({"chapter_index": ch.get("chapter_index", ci), "topic_index": t.get("topic_index", ti),
                            "chapter_title": ch.get("title", ""), "topic": t, "no": len(out) + 1})
    return out


def topic_formulas(topic: dict) -> list[dict]:
    """lsTopicFormulas: content_blocks の equations 項目（描画できる形が 1 つでもあれば残す）。"""
    out: list[dict] = []
    for block in (topic or {}).get("content_blocks") or []:
        if not isinstance(block, dict) or block.get("type") != "equations":
            continue
        for item in block.get("items") or []:
            if not isinstance(item, dict) or not (item.get("latex") or item.get("plain_text") or item.get("raw_text")):
                continue
            out.append({"id": item.get("equation_id") or f"TOPIC_FORMULA_{len(out)}", "label": item.get("label") or "",
                        "latex": item.get("latex") or "", "plain_text": item.get("plain_text") or "",
                        "raw_text": item.get("raw_text") or "", "is_display": True})
    return out


def topic_material_source(topic: dict) -> str:
    """lsTopicStudentMaterialSource: student_material が無ければ題名・要約・数式の埋め込みから組む。"""
    material = (topic or {}).get("student_material") or {}
    if isinstance(material, dict) and material.get("source_text"):
        return str(material["source_text"])
    lines: list[str] = []
    if topic.get("title"):
        lines.append("## " + str(topic["title"]))
    if topic.get("summary"):
        lines += ["", str(topic["summary"])]
    for f in topic_formulas(topic):
        if f.get("id"):
            lines += ["", "![[equation:" + str(f["id"]) + "]]"]
    return "\n".join(lines).strip()


def cautions_display(topic: dict, cautions: list[str]) -> list[str]:
    """lsCautionsDisplayText: 欄が保存時のままなら、サーバが表示語に直した ``cautions_display`` を見せる
    （プレビュー・読み取りの区画）。教員が書き換えた欄はそのまま。"""
    display = topic.get("cautions_display")
    if not isinstance(display, list):
        return cautions
    saved = _lines(topic.get("cautions_saved") if "cautions_saved" in topic else topic.get("cautions"))
    return _lines(display) if saved == list(cautions) else cautions


def draft_from_topic(topic: dict) -> dict:
    """lsCourseDraftHtml が描く編集欄の初期値。"""
    return {"key_concepts": _lines(topic.get("key_concepts")),
            "material": topic_material_source(topic),
            "spoken": str(topic.get("spoken_script") or topic.get("content") or ""),
            "cautions": _lines(topic.get("cautions")),
            "check_questions": [q for q in (topic.get("check_questions") or []) if isinstance(q, dict)]}


def evidence_state(topic: dict) -> Optional[dict]:
    """lsTopicEvidenceState（IK-0875 の是正後）: サーバが 1 つの値で返す根拠の状態（status / label / message /
    unmapped_heading）。旧サーバ（evidence_state なし）は None — 呼び出し側が旧の推定にフォールバックする。"""
    st = topic.get("evidence_state") if isinstance(topic.get("evidence_state"), dict) else None
    return st if st and st.get("status") else None


def coverage_label(topic: dict) -> str:
    """見出しの pill（サーバの evidence_state.label。旧サーバは状態語そのまま）。"""
    st = evidence_state(topic)
    if st is not None:
        return str(st.get("label") or "")
    return coverage_status(topic)


def coverage_status(topic: dict) -> str:
    """lsTopicCoverageStatus（evidence_state.status。旧サーバは従来の推定）。"""
    st = evidence_state(topic)
    if st is not None:
        return str(st["status"])
    cov = topic.get("coverage") if isinstance(topic.get("coverage"), dict) else {}
    if cov.get("status"):
        return str(cov["status"])
    if topic.get("content_source") == "source_excerpt" and topic.get("content_confidence") == "none":
        return "missing"
    if topic.get("content_confidence") == "none":
        return "weak"
    if topic.get("content_confidence"):
        return "sufficient"
    return "sufficient" if topic.get("linked_component_ids") else "weak"


def coverage_message(topic: dict) -> str:
    st = evidence_state(topic)
    if st is not None:
        return str(st.get("message") or "")
    cov = topic.get("coverage") if isinstance(topic.get("coverage"), dict) else {}
    if cov.get("message"):
        return str(cov["message"])
    return COVERAGE_MESSAGES.get(coverage_status(topic), COVERAGE_MESSAGES["sufficient"])


def _studio_course_id(ctx) -> str:
    return str(ctx.args.get("course_id") or ctx.session.scratch.get("studio_course_id") or ctx.session.course_id or "")


def _load_course(ctx, cid: str) -> tuple[Optional[int], Any]:
    s = ctx.session
    status, structure = ctx.call("GET", "/api/admin/courses/{course_id}/lecture-studio/course-structure",
                                 overrides={"course_id": cid})
    s_status, scripts = ctx.call("GET", "/api/admin/courses/{course_id}/lecture-scripts", overrides={"course_id": cid})
    _st_status, settings = ctx.call("GET", "/api/admin/courses/{course_id}/lecture-studio/settings",
                                    overrides={"course_id": cid})
    if s.scratch.get("studio_course_id") != cid:
        # lsSelectCourse: コース切替の開始時に前コースの状態をクリアする（Issue #491 §2-2）
        for k in ("studio_topic", "studio_draft", "studio_chunk", "studio_last_split", "figure_studio"):
            s.scratch.pop(k, None)
        s.scratch["studio_suggestions"] = {}
    s.scratch["studio_course_id"] = cid
    s.course_id = cid
    s.scratch["studio_structure"] = structure if status == 200 and isinstance(structure, dict) else None
    # 読み込み失敗も「読み込み完了（結果は空）」として扱う（音声生成は fail-closed で無効のまま）
    s.scratch["studio_scripts"] = scripts if s_status == 200 and isinstance(scripts, list) else []
    s.scratch["studio_settings"] = settings if isinstance(settings, dict) else {}
    s.scratch["studio_loaded"] = True
    return status, structure


def _open_course(ctx) -> None:
    cid = _studio_course_id(ctx)
    if not cid:
        ctx.missing("course_id")
        return
    status, structure = _load_course(ctx, cid)
    s = ctx.session
    if status != 200:
        s.last_status, s.last_body = status, structure
        return
    _ui(s, {"kind": "course", "structure": structure, "settings": s.scratch.get("studio_settings"),
            "readiness": audio_readiness_state(s)})


def _pick_topic(session: PersonaSession, hint: Any, prefer: str) -> Optional[dict]:
    rows = _topics(session.scratch.get("studio_structure"))
    if not rows:
        return None
    h = str(hint or "").strip()
    if h:
        if h.isdigit():
            i = int(h)
            return rows[i - 1] if 1 <= i <= len(rows) else None
        for r in rows:
            if h in str(r["topic"].get("title") or ""):
                return r
        return None
    if prefer == "long":
        return max(rows, key=lambda r: (len(topic_material_source(r["topic"])), -r["no"]))
    return rows[0]


def _draft_split_payload(session: PersonaSession) -> dict:
    d = session.scratch.get("studio_draft") or {}
    # updateCourseSlideIndicator: 受講側と同じ自動ページ分割で数える（formulas は空・文書 ID は渡さない）
    return {"display_text": d.get("material") or "", "spoken_text": d.get("spoken") or None, "formulas": [],
            "auto_paginate": True}


def _split_summary(status: Optional[int], body: Any) -> dict:
    """lsFetchSplitSlides の正規化（失敗は 1 枚へ縮退 + error）。"""
    if status != 200 or not isinstance(body, dict):
        return {"error": True, "display_count": 1, "spoken_count": 0, "mismatch": False, "slides": []}
    slides = [sd for sd in body.get("slides") or [] if isinstance(sd, dict)]
    return {"error": False, "display_count": int(body.get("display_segment_count") or 0),
            "spoken_count": int(body.get("spoken_segment_count") or 0), "mismatch": bool(body.get("mismatch")),
            "auto_paginated": bool(body.get("auto_paginated")), "spoken_degraded": bool(body.get("spoken_degraded")),
            "auto_paginate_suppressed": bool(body.get("auto_paginate_suppressed")),
            "notes": [str(n) for n in body.get("notes") or [] if n] if isinstance(body.get("notes"), list) else [],
            "slides": slides}


def indicator_text(split: dict) -> str:
    """lsRenderSlideIndicatorEl と同じ文。"""
    if split.get("error"):
        return SPLIT_FAILED
    d, sp = split.get("display_count", 0), split.get("spoken_count", 0)
    notes = list(split.get("notes") or [])
    if split.get("mismatch"):
        return f"⚠ 表示 {d} / 読み上げ {sp} — 1枚に統合して配信されます" + ("。" + " ".join(notes) if notes else "")
    if split.get("spoken_degraded"):
        return (f"⚠ 表示 {d} 枚に対し読み上げが分割できないため、このトピックの読み上げはタイマー送りになります"
                "（=== で明示分割すると解消できます）")
    if sp == 0:
        return f"表示 {d} 枚 / 読み上げ未入力"
    return (f"表示 {d} 枚" + ("（自動ページ分割）" if split.get("auto_paginated") else "") + f" / 読み上げ {sp} 区切り ✓"
            + (" — " + " ".join(notes) if split.get("auto_paginate_suppressed") and notes else ""))


def _wm_digest(slides: list[dict]) -> list[dict]:
    out = []
    for sd in slides:
        wm = sd.get("wm")
        if isinstance(wm, dict):
            out.append({"slide_index": sd.get("slide_index"), "keys": sorted(str(k) for k in wm),
                        "level": wm.get("level"), "level_label": wm.get("level_label"),
                        "has_fact": bool(str(wm.get("fact") or "").strip()),
                        "numeric_keys": sorted(str(k) for k, v in wm.items()
                                               if isinstance(v, (int, float)) and not isinstance(v, bool))})
    return out


def _preview_topic(ctx) -> dict:
    """授業用ドラフトのスライド枚数インジケータを問い合わせ、要約を digest と scratch に残す。"""
    s = ctx.session
    payload = _draft_split_payload(s)
    status, body = ctx.call("POST", "/api/admin/lecture-studio/preview-split", json=payload)
    split = _split_summary(status, body)
    d = s.scratch.get("studio_draft") or {}
    topic = (s.scratch.get("studio_topic") or {}).get("topic") or {}
    summary = {"scope": "topic", "course_id": s.scratch.get("studio_course_id") or "", "topic_id": topic.get("id") or "",
               "text_sha": _sha(d.get("material") or "", d.get("spoken") or ""),
               "status": status, **{k: v for k, v in split.items() if k != "slides"},
               "indicator": indicator_text(split), "wm": _wm_digest(split.get("slides") or []),
               "has_marker_material": bool(_MARKER_RE.search(d.get("material") or "")),
               "has_marker_spoken": bool(_MARKER_RE.search(d.get("spoken") or "")),
               "figure_embeds": _FIGURE_EMBED_RE.findall(d.get("material") or "")}
    s.scratch["studio_last_split"] = summary
    _digest(ctx, "studio_split", summary)
    return summary


def can_save(structure: Any) -> bool:
    """lsCourseCanSave（IK-0908）: course-structure の ``can_save`` が偽でなければ保存できる（旧サーバは真）。"""
    return not (isinstance(structure, dict) and structure.get("can_save") is False)


def _topic_view(session: PersonaSession, **extra: Any) -> dict:
    sel = session.scratch.get("studio_topic") or {}
    structure = session.scratch.get("studio_structure") or {}
    extra.setdefault("save_notice", str(structure.get("topic_audio_save_notice") or ""))
    # 第 2 波: 保存できない理由（save_notice）・届き方（delivery.message）・付録の付け直しの可否
    extra.setdefault("can_save", can_save(structure))
    extra.setdefault("save_block_notice", str(structure.get("save_notice") or ""))
    delivery = structure.get("delivery") if isinstance(structure.get("delivery"), dict) else {}
    extra.setdefault("delivery_message", str(delivery.get("message") or ""))
    return {"kind": "topic", "topic": sel.get("topic") or {}, "chapter_title": sel.get("chapter_title", ""),
            "draft": session.scratch.get("studio_draft") or {}, "split": session.scratch.get("studio_last_split") or {},
            "view": session.scratch.get("studio_draft_view") or "edit", **extra}


# 原稿スタジオの読み取り専用の区画に出てはいけない内部表記（審判 T7）。製品の表示投影
# （backend/core/display_projection.py INTERNAL_ID_TOKEN_RE）の系統を写した近似 — uxsim は backend を import しない
INTERNAL_ID_RE = re.compile(
    r"\b(?:eq_(?:blk|tex|eqcand|op|cand)_[0-9A-Za-z_]+|claim_span_[0-9A-Za-z_]+|theory_op_[0-9A-Za-z_]+|"
    r"synth_claim_[0-9A-Za-z_]+|ev_[0-9a-f]{6,}|sym_\d+|k1:[0-9a-f]{8,})\b|"
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")
FAILURE_STATE_PHRASES = ("could not be inferred", "could not be determined", "could not be resolved",
                         "semantics unavailable", "repair failed")
_EMBED_ANY_RE = re.compile(r"!?\[\[[a-z_]+:[^\]]*\]\]")


def readonly_text_flags(topic: dict, draft: dict) -> dict:
    """原稿スタジオの読み取り専用の区画（注意点・重要な概念・根拠リンクの見出しと要約・確認文・教材欄の埋め込み
    記法の外）に内部 ID と失敗状態の固定文が出ているか（審判 T7）。埋め込み記法の中身は教員が編集する番地なので数えない。"""
    areas: dict[str, list[str]] = {
        "cautions": cautions_display(topic, list(draft.get("cautions") or [])),
        "key_concepts": list(draft.get("key_concepts") or []),
        "coverage": [coverage_message(topic)],
        "evidence": [str(e.get(k) or "") for e in topic.get("evidence_items") or [] if isinstance(e, dict)
                     for k in ("title", "summary")],
        "material_outside_embeds": [_EMBED_ANY_RE.sub("", str(draft.get("material") or ""))],
    }
    ids: dict[str, list[str]] = {}
    phrases: dict[str, list[str]] = {}
    for area, texts in areas.items():
        joined = "\n".join(texts)
        found = sorted(set(INTERNAL_ID_RE.findall(joined)))
        if found:
            ids[area] = found[:5]
        low = joined.lower()
        hit = [p for p in FAILURE_STATE_PHRASES if p in low]
        if hit:
            phrases[area] = hit
    return {"internal_ids": ids, "failure_phrases": phrases}


#: backend/core/lecture_studio_state.py::EVIDENCE_UNITS_ONLY_MESSAGE の書き出し（一致はテストで固定）
EVIDENCE_UNITS_ONLY_HEAD = "学ぶ単位は選ばれていますが、コース内容生成の前です。"


def coverage_digest(topic: dict) -> dict:
    """同じトピックの根拠の状態を言う区画ごとの値（審判 T8）。中央の根拠リンクの見出し（coverage_status）と、
    右ペインの根拠リンク（evidence_items の有無）。"""
    items = [e for e in topic.get("evidence_items") or [] if isinstance(e, dict)]
    state = topic.get("evidence_state") if isinstance(topic.get("evidence_state"), dict) else {}
    return {"topic_id": str(topic.get("id") or ""), "center_status": coverage_status(topic),
            # 第 25 周 studio H22: 学ぶ単位だけのとき（コース内容生成の前）の専用の文 — 一覧が空なのはその文どおり
            "center_units_only": str(state.get("message") or "").startswith(EVIDENCE_UNITS_ONLY_HEAD),
            "right_pane_has_items": bool(items),
            "right_pane_kinds": sorted({str(e.get("kind")) for e in items if e.get("kind")})}


def topic_grounding_empty(topic: dict) -> bool:
    """書き換えの根拠に使えるものがトピックに無いか（evidence_items・linked_*・学ぶ単位の解決済みの中身）。"""
    if any(isinstance(e, dict) for e in topic.get("evidence_items") or []):
        return False
    for key in ("linked_component_ids", "linked_claim_ids", "linked_equation_ids", "linked_chunk_ids",
                "material_chunk_ids"):
        if topic.get(key):
            return False
    return True


def _select_topic(ctx) -> None:
    s = ctx.session
    if not s.scratch.get("studio_structure"):
        ctx.missing("studio_course")
        return
    prefer = str(ctx.args.get("prefer") or "first").strip().lower()
    row = _pick_topic(s, ctx.args.get("topic_hint"), prefer)
    if row is None:
        _ui(s, {"kind": "topic_miss", "hint": str(ctx.args.get("topic_hint") or ""),
                "rows": _topics(s.scratch.get("studio_structure"))})
        return
    s.scratch["studio_topic"] = row
    s.scratch["studio_draft"] = draft_from_topic(row["topic"])
    view = str(ctx.args.get("view") or "edit").strip().lower()
    s.scratch["studio_draft_view"] = "preview" if view == "preview" else "edit"
    s.scratch["studio_rightpane"] = "evidence"
    _preview_topic(ctx)
    _digest(ctx, "studio_readonly", {"course_id": s.scratch.get("studio_course_id") or "",
                                     "topic_id": str(row["topic"].get("id") or ""),
                                     **readonly_text_flags(row["topic"], s.scratch["studio_draft"])})
    _digest(ctx, "studio_coverage", {"course_id": s.scratch.get("studio_course_id") or "",
                                     **coverage_digest(row["topic"])})
    _ui(s, _topic_view(s))


def _need_draft(ctx) -> bool:
    if ctx.session.scratch.get("studio_draft") is None or not ctx.session.scratch.get("studio_topic"):
        ctx.missing("studio_topic")
        return False
    return True


_SENTENCE_END_RE = re.compile(r"(?<=[。！？])|(?<=[.!?])(?=\s)")


def _marker_ratio(text: str) -> Optional[float]:
    """欄の最初の区切りの相対位置（0〜1）。区切りが無ければ None。"""
    m = _MARKER_RE.search(text or "")
    if not m or not (text or "").strip():
        return None
    return m.start() / max(len(text), 1)


def insert_marker(text: str, after_paragraph: Optional[int], *,
                  target_ratio: Optional[float] = None) -> tuple[str, int, str]:
    """区切り ``\n===\n`` をカーソル位置の代わりに入れる（lsInsertSlideMarkerIntoTextarea はカーソル位置）。

    返り値は (本文, 入れた段落番号, 入れ方)。入れ方は ``paragraph``（段落の境目）/ ``sentence``（文の境目 — 段落が
    足りないとき・第 25 周 H2）/ ``end``（文の境目も無く末尾 — 末尾の区切りは区切りにならない）。
    文の境目は ``target_ratio``（もう一方の欄の区切りの相対位置。無ければ真ん中）に最も近いものを選ぶ。
    """
    text = text or ""
    spans = [m.span() for m in PARAGRAPH_SPLIT.finditer(text)]
    paragraphs = len(spans) + 1 if text.strip() else 0
    if paragraphs >= 2:
        k = after_paragraph if isinstance(after_paragraph, int) and after_paragraph >= 1 else None
        if k is None and target_ratio is not None:
            # もう一方の欄の区切りに相対位置で最も近い段落の境目
            k = 1 + min(range(len(spans)), key=lambda i: abs(spans[i][0] / max(len(text), 1) - target_ratio))
        k = k if k is not None else max(1, paragraphs // 2)
        k = min(k, paragraphs - 1)
        pos = spans[k - 1][0]
        return text[:pos] + MARKER_TEXT + text[pos:].lstrip("\n"), k, "paragraph"
    body = text.rstrip()
    cuts = [m.start() for m in _SENTENCE_END_RE.finditer(body) if 0 < m.start() < len(body)]
    if cuts:
        ratio = 0.5 if target_ratio is None else target_ratio
        pos = min(cuts, key=lambda c: abs(c / max(len(body), 1) - ratio))
        return text[:pos].rstrip() + MARKER_TEXT + text[pos:].lstrip(), max(paragraphs, 1), "sentence"
    return text + MARKER_TEXT, max(paragraphs, 1), "end"


def _insert_slide_marker(ctx) -> None:
    if not _need_draft(ctx):
        return
    s = ctx.session
    field = "spoken" if str(ctx.args.get("field") or "").strip().lower() in ("spoken", "本文説明") else "material"
    raw = ctx.args.get("after_paragraph")
    try:
        after = int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        after = None
    draft = s.scratch["studio_draft"]
    other = draft.get("spoken" if field == "material" else "material") or ""
    draft[field], k, how = insert_marker(draft.get(field) or "", after, target_ratio=_marker_ratio(other))
    s.scratch["studio_draft_view"] = "edit"
    label = "本文説明" if field == "spoken" else "教材"
    if how == "paragraph":
        _runner_note(s, f"runner_default: 区切りはカーソル位置の代わりに{label}欄の段落 {k} の後に入れた")
    elif how == "sentence":
        _runner_note(s, f"runner_default: {label}欄は段落が足りないので、区切りはカーソル位置の代わりに"
                        + ("もう一方の欄の区切りの位置に最も近い" if _marker_ratio(other) is not None else "真ん中に最も近い")
                        + "文の境目に入れた")
    else:
        _runner_note(s, f"runner_default: {label}欄は段落も文の境目も足りず、区切りは末尾になった"
                        "（末尾の区切りはスライドの区切りにならない）")
    _preview_topic(ctx)
    notice = f"（{label}欄に区切りを挿入した。ボタンの説明: {MARKER_BTN_TITLE}）"
    if how == "end":
        notice += "（欄に文の境目が無く、区切りは欄の末尾に入った）"
    _ui(s, _topic_view(s, notice=notice))


def _edit_draft(ctx) -> None:
    if not _need_draft(ctx):
        return
    s = ctx.session
    field = str(ctx.args.get("field") or "material").strip().lower()
    field = {"教材": "material", "本文説明": "spoken", "spoken_script": "spoken", "重要な概念": "key_concepts",
             "注意点": "cautions"}.get(field, field)
    if field not in ("material", "spoken", "key_concepts", "cautions"):
        field = "material"
    draft = s.scratch["studio_draft"]
    current = draft.get(field)
    text = "\n".join(current) if isinstance(current, list) else str(current or "")
    changed = False
    find, replace = ctx.args.get("find"), ctx.args.get("replace")
    if find not in (None, "") and str(find) in text:
        text = text.replace(str(find), str(replace or ""), 1)
        changed = True
    append = ctx.args.get("append")
    if append not in (None, ""):
        text = (text.rstrip("\n") + "\n\n" + str(append)).strip("\n") if text.strip() else str(append)
        changed = True
    draft[field] = _lines(text) if field in ("key_concepts", "cautions") else text
    s.scratch["studio_draft_view"] = "edit"
    if find not in (None, "") and not changed:
        _runner_note(s, "runner_default: 置き換える文字列が欄に見つからなかったので何も変えていない")
    _preview_topic(ctx)
    _ui(s, _topic_view(s, notice="" if changed else "（欄は変わっていない）"))


def _save_payload(session: PersonaSession) -> dict:
    sel = session.scratch.get("studio_topic") or {}
    d = session.scratch.get("studio_draft") or {}
    return {"chapter_index": sel.get("chapter_index"), "topic_index": sel.get("topic_index"),
            "key_concepts": list(d.get("key_concepts") or []),
            "student_material": {"source_format": "eg-markdown-v1", "source_text": d.get("material") or ""},
            "spoken_script": d.get("spoken") or "", "cautions": list(d.get("cautions") or []),
            "check_questions": list(d.get("check_questions") or [])}


def _save_topic(ctx) -> None:
    """lsSaveCourseTopicDraft: PUT → 成功なら「授業用ドラフトを保存しました」（失敗は理由を出さずに「保存に失敗しました」）。"""
    if not _need_draft(ctx):
        return
    s = ctx.session
    sel = s.scratch["studio_topic"]
    topic = sel["topic"]
    tid = str(topic.get("id") or "")
    structure0 = s.scratch.get("studio_structure") or {}
    if not can_save(structure0):
        # lsCourseDraftHtml（IK-0908）: 保存ボタンは無効（title に save_notice）。押しても何も起きない
        ctx.missing("topic_save_disabled")
        _ui(s, _topic_view(s, status_line=f"（［原稿を保存する］は押せない: {structure0.get('save_notice') or ''}）"),
            status=None)
        return
    payload = _save_payload(s)
    d0 = s.scratch.get("studio_draft") or {}
    sig = _sha(d0.get("material") or "", d0.get("spoken") or "")
    last0 = s.scratch.get("studio_last_split") or {}
    confirm_line = ""
    if last0.get("mismatch") and last0.get("text_sha") == sig and s.scratch.get("studio_slide_confirmed") != sig:
        # lsSaveCourseTopicDraft（第 2 波）: 枚数が食い違うまま保存するときは確認（notes か既定の一文）→ OK とみなす
        confirm_line = "（確認: " + (" ".join(last0.get("notes") or []) or SPLIT_MISMATCH_CONFIRM) + " → OK）"
        s.scratch["studio_slide_confirmed"] = sig
    status, body = ctx.main(json=payload, overrides={"topic_id": tid, "course_id": s.scratch.get("studio_course_id")})
    d = s.scratch["studio_draft"]
    sha = _sha(d.get("material") or "", d.get("spoken") or "")
    last = s.scratch.get("studio_last_split") or {}
    structure = s.scratch.get("studio_structure") or {}
    before = bool((s.scratch.get("studio_audio_notice") or {}).get(tid)) or bool(
        topic.get("has_topic_audio") and structure.get("topic_audio_save_notice"))
    notice = str(body.get("notice") or "") if isinstance(body, dict) else ""
    _digest(ctx, "studio_saved", {"course_id": s.scratch.get("studio_course_id") or "", "topic_id": tid,
                                  "status": status, "text_sha": sha,
                                  "preview": last if last.get("text_sha") == sha else None,
                                  "figure_embeds": _FIGURE_EMBED_RE.findall(d.get("material") or ""),
                                  "notice_shown_before": before,
                                  # IK-0877 の是正後: 保存の応答の notice（音声を消した事実文）を状態行に足す
                                  "notice_shown_after": bool(notice),
                                  "audio_existed": topic.get("has_topic_audio") if "has_topic_audio" in topic else None,
                                  "audio_invalidated": body.get("audio_invalidated") if isinstance(body, dict) else None,
                                  # 第 2 波（審判 T10・T11）: 保存ボタンが押せる表示だったか・保存後の根拠リンクに図があるか
                                  "can_save_shown": can_save(structure0),
                                  "figures_missing_after_refresh": None})
    saved_digest = None
    traces_now = getattr(ctx.client, "traces", None) or []
    if traces_now and isinstance(getattr(traces_now[-1], "digest", None), dict):
        saved_digest = traces_now[-1].digest.get("studio_saved")
    if status == 200:
        topic["student_material"] = payload["student_material"]
        topic["spoken_script"] = payload["spoken_script"]
        topic["key_concepts"] = payload["key_concepts"]
        topic["cautions"] = payload["cautions"]
        topic["check_questions"] = payload["check_questions"]
        topic["status"] = "generated"
        if isinstance(body, dict) and body.get("audio_invalidated"):
            topic["has_topic_audio"] = False
        s.remember("studio_saved_topics", f"{s.scratch.get('studio_course_id')}:{tid}")
        if _refresh_topic_server_facts(ctx, topic) and isinstance(saved_digest, dict):
            in_evidence = {str(e.get("figure_id") or e.get("id") or "") for e in topic.get("evidence_items") or []
                           if isinstance(e, dict) and str(e.get("kind") or "") == "figure"}
            in_evidence |= {str(x) for x in topic.get("linked_figure_ids") or []}
            saved_digest["figures_missing_after_refresh"] = sorted(
                f for f in saved_digest.get("figure_embeds") or [] if f not in in_evidence)
    if status == 200:
        delivery = body.get("delivery") if isinstance(body, dict) and isinstance(body.get("delivery"), dict) else {}
        line = SAVE_OK + (f"。{notice}" if notice else "") + (f" {delivery['message']}" if delivery.get("message") else "")
    else:
        # 第 2 波: 失敗はサーバの detail（lsApiDetailText）、無ければ「保存に失敗しました」
        line = _api_detail_text(body) or SAVE_FAIL
    if confirm_line:
        line = f"{confirm_line} {line}"
    _ui(s, _topic_view(s, status_line=line), status=status)


def _api_detail_text(body: Any) -> str:
    """lsApiDetailText: detail が文字列ならそれ、dict なら message。"""
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, dict) and detail.get("message"):
        return str(detail["message"])
    return ""


TOPIC_SERVER_KEYS = ("evidence_items", "evidence_state", "cautions_display", "document_ids", "has_topic_audio",
                     "uses_topic_material", "evidence_links", "linked_figure_ids", "appendix_state", "coverage")


def _refresh_topic_server_facts(ctx, topic: dict) -> bool:
    """lsRefreshTopicServerFacts（第 2 波）: 保存・採用の後に course-structure を読み直し、そのトピックのサーバ側の
    事実（根拠・付録の状態・音声の有無…）とコースの can_save / save_notice / delivery を差し替える（fail-soft）。"""
    s = ctx.session
    cid = s.scratch.get("studio_course_id")
    if not cid:
        return False
    status, data = ctx.call("GET", "/api/admin/courses/{course_id}/lecture-studio/course-structure",
                            overrides={"course_id": cid})
    if status != 200 or not isinstance(data, dict):
        return False
    fresh = next((r["topic"] for r in _topics(data) if str(r["topic"].get("id") or "") == str(topic.get("id") or "")), None)
    if fresh is None:
        return False
    for key in TOPIC_SERVER_KEYS:
        if key in fresh:
            topic[key] = fresh[key]
        elif key == "appendix_state":
            topic[key] = None
    structure = s.scratch.get("studio_structure")
    if isinstance(structure, dict):
        for key in ("can_save", "save_notice", "share_mark", "delivery", "figures_index"):  # IK-0950
            if key in data:
                structure[key] = data[key]
            else:
                structure.pop(key, None)
    return True


def _rewrite_topic(ctx) -> None:
    """lsRewriteCourseTopicDraft: 指示が空なら送らない / 成功は欄に反映するだけ（保存しない）。"""
    if not _need_draft(ctx):
        return
    s = ctx.session
    prompt = str(ctx.args.get("prompt") or "").strip()
    if not prompt:
        _ui(s, _topic_view(s, status_line=REWRITE_NO_PROMPT))
        return
    payload = _save_payload(s)
    payload["prompt"] = prompt
    if ctx.args.get("model"):
        payload["model"] = str(ctx.args["model"])
    topic = s.scratch["studio_topic"]["topic"]
    tid = str(topic.get("id") or "")
    grounding_empty = topic_grounding_empty(topic)
    before = dict(s.scratch.get("studio_draft") or {})
    status, body = ctx.main(json=payload, overrides={"topic_id": tid, "course_id": s.scratch.get("studio_course_id")})
    if status != 200 or not isinstance(body, dict):
        _digest(ctx, "studio_rewrite", {"course_id": s.scratch.get("studio_course_id") or "", "topic_id": tid,
                                        "status": status, "grounding_empty": grounding_empty, "llm_called": False,
                                        "figures_restored": False, "restore_shown": False})
        _ui(s, _topic_view(s, status_line=REWRITE_FAIL))
        return
    material = body.get("student_material") if isinstance(body.get("student_material"), dict) else {}
    unchanged = (str(material.get("source_text") or "") == str(before.get("material") or "")
                 and str(body.get("spoken_script") or "") == str(before.get("spoken") or ""))
    skipped = bool(body.get("skipped") or body.get("llm_skipped") or body.get("no_grounding"))
    if body.get("changed") is False:
        unchanged = True
    _digest(ctx, "studio_rewrite", {"course_id": s.scratch.get("studio_course_id") or "", "topic_id": tid,
                                    "status": status, "grounding_empty": grounding_empty, "llm_called": not skipped,
                                    "unchanged": unchanged,
                                    # IK-0874 の是正後: skipped は「実行しませんでした」、changed:false はサーバの notice
                                    # （旧サーバは変化の有無にかかわらず「提案を反映しました」）
                                    "reported_applied": not skipped and not (body.get("changed") is False
                                                                             and body.get("notice")),
                                    "figures_restored": bool(body.get("figures_restored")),
                                    # 画面（lsRewriteCourseTopicDraft）は figures_restored を描かない
                                    "restore_shown": False})
    if body.get("skipped"):
        # lsRewriteCourseTopicDraft: 欄に反映せず、サーバの notice（無ければ「AI提案を実行しませんでした」）だけ
        _ui(s, _topic_view(s, status_line=str(body.get("notice") or REWRITE_SKIPPED)))
        return
    s.scratch["studio_draft"] = {"key_concepts": _lines(body.get("key_concepts")),
                                 "material": str(material.get("source_text") or ""),
                                 "spoken": str(body.get("spoken_script") or ""),
                                 "cautions": _lines(body.get("cautions")),
                                 "check_questions": [q for q in body.get("check_questions") or [] if isinstance(q, dict)]}
    _preview_topic(ctx)
    # 第 2 波（IK-0909 / IK-0908）: 根拠を省いた事実（grounding_notice）・保存されないこと（save_notice）を添える
    extra = " ".join(str(body[k]) for k in ("grounding_notice", "save_notice") if body.get(k))
    _ui(s, _topic_view(s, status_line=str(body.get("notice") or REWRITE_OK) + (f" {extra}" if extra else "")))


def _reattach_appendix(ctx) -> None:
    """``#ls-course-reattach-appendix-btn``（IK-0872 / IK-0880 の残り・第 2 波）: 確認（appendix_reattach_confirm）→
    POST …/appendix/reattach → 変われば編集欄へ入れる（保存はしない）→ 状態行はサーバの notice。"""
    if not _need_draft(ctx):
        return
    s = ctx.session
    topic = s.scratch["studio_topic"]["topic"]
    structure = s.scratch.get("studio_structure") or {}
    appendix = topic.get("appendix_state") if isinstance(topic.get("appendix_state"), dict) else None
    if not (appendix and appendix.get("message")):
        ctx.missing("reattach_appendix_button")
        _ui(s, _topic_view(s, status_line=f"（［{REATTACH_BUTTON}］は出ていない — 付録はいまの根拠と同じ形）"), status=None)
        return
    if not can_save(structure):
        ctx.missing("reattach_appendix_disabled")
        _ui(s, _topic_view(s, status_line=f"（［{REATTACH_BUTTON}］は押せない: {structure.get('save_notice') or ''}）"),
            status=None)
        return
    confirm = str(structure.get("appendix_reattach_confirm") or "")
    sel = s.scratch["studio_topic"]
    d = s.scratch.get("studio_draft") or {}
    payload = {"chapter_index": sel.get("chapter_index"), "topic_index": sel.get("topic_index"),
               "student_material": {"source_format": "eg-markdown-v1", "source_text": d.get("material") or ""},
               "cautions": list(d.get("cautions") or [])}
    status, body = ctx.main(json=payload, overrides={"topic_id": str(topic.get("id") or ""),
                                                     "course_id": s.scratch.get("studio_course_id")})
    head = f"（確認: {confirm} → OK）" if confirm else ""
    if status != 200 or not isinstance(body, dict):
        _ui(s, _topic_view(s, status_line=f"{head} {_api_detail_text(body) or REATTACH_FAIL}".strip()), status=status)
        return
    material = body.get("student_material") if isinstance(body.get("student_material"), dict) else {}
    if body.get("changed") and material:
        topic["student_material"] = material
        topic["appendix_state"] = None
        d["material"] = str(material.get("source_text") or "")
        s.scratch["studio_draft"] = d
        _preview_topic(ctx)
    _ui(s, _topic_view(s, status_line=f"{head} {body.get('notice') or ''}".strip()), status=status)


def _chunk_doc(chunk: dict) -> str:
    return str(chunk.get("document_id") or chunk.get("material_id") or "")


def topic_chunk_ids(topic: dict) -> list[str]:
    """course-structure のトピックが持つチャンク（``linked_chunk_ids`` → ``material_chunk_ids`` の順・重複なし）。"""
    out: list[str] = []
    for key in ("linked_chunk_ids", "material_chunk_ids"):
        for cid in (topic or {}).get(key) or []:
            if cid and str(cid) not in out:
                out.append(str(cid))
    return out


def _pick_chunk(session: PersonaSession, hint: Any) -> Optional[dict]:
    """スライドタブで描くチャンク。

    hint があれば原稿一覧から選ぶ（教員が明示的にチャンクを選んだ）。hint が無いときは、トピックを開いている間は
    そのトピックのチャンクの先頭に限る（第 25 周 H1: コース全体で最長のチャンクを選び、開いているトピックと
    無関係な住所の題名のチャンクを開いていた）。トピックにチャンクが無ければ None（呼び出し側が
    ``unsupported_ui_state`` として投影する）。トピックを開いていなければ従来どおり。
    """
    chunks = [c for c in session.scratch.get("studio_scripts") or [] if isinstance(c, dict)]
    if not chunks:
        return None
    h = str(hint or "").strip()
    topic = (session.scratch.get("studio_topic") or {}).get("topic") if not h else None
    if topic:
        ids = topic_chunk_ids(topic)
        by_id = {str(c.get("chunk_id") or c.get("id") or ""): c for c in chunks}
        return next((by_id[i] for i in ids if i in by_id), None)
    if h:
        if h.isdigit():
            i = int(h)
            return chunks[i - 1] if 1 <= i <= len(chunks) else None
        for c in chunks:
            if h in str(c.get("section_title") or "") or h in str(c.get("display_text") or c.get("text") or ""):
                return c
        return None
    marked = [c for c in chunks if _MARKER_RE.search(str(c.get("display_text") or ""))]
    if marked:
        return marked[0]
    return max(chunks, key=lambda c: len(str(c.get("display_text") or c.get("text") or "")))


def slide_effective_length(text: str) -> int:
    """lsSlideEffectiveLength: 文字数 + [[FORMULA_N]] 1 個 = 60 文字換算。"""
    text = str(text or "")
    placeholders = _FORMULA_PH_RE.findall(text)
    return len(_FORMULA_PH_RE.sub("", text)) + len(placeholders) * 60


def slide_audio_badge(chunk: dict, slide_index: int, course_language: str) -> str:
    """lsSlideAudioBadgeHtml: 言語不一致 > 音声あり > 音声未生成。"""
    lang = str(chunk.get("spoken_language") or "ja")
    if lang != course_language:
        return BADGE_LANG_MISMATCH
    ready = chunk.get("audio_ready_slides") if isinstance(chunk.get("audio_ready_slides"), int) else 0
    return f"音声あり ({lang})" if ready > slide_index else BADGE_NONE


def _chunk_slides(ctx) -> None:
    s = ctx.session
    if not s.scratch.get("studio_loaded"):
        ctx.missing("studio_course")
        return
    chunk = _pick_chunk(s, ctx.args.get("chunk_hint"))
    if chunk is None:
        topic = (s.scratch.get("studio_topic") or {}).get("topic")
        if topic and not str(ctx.args.get("chunk_hint") or "").strip() and s.scratch.get("studio_scripts"):
            # 製品の原稿スタジオは授業用ドラフト（トピック）にスライドカードを持たない（インジケータだけ）。
            # スライドタブはチャンク一覧から選んだチャンクを描くので、トピックにチャンクが無ければ開けない（H1）
            _runner_note(s, "unsupported_ui_state: 開いているトピックにはチャンクが紐づいておらず、スライドタブで"
                            "描けるチャンクが無い（トピックのスライドは授業用ドラフトのインジケータだけ）")
            _ui(s, {"kind": "chunk_none_for_topic", "topic": topic, "split": s.scratch.get("studio_last_split") or {}})
            return
        if not s.scratch.get("studio_scripts"):
            # 第 25 周 studio H19: 原稿一覧が空なら、製品の空の一覧（lsRenderChunkList）の文を出す。HTTP は出さない
            _runner_note(s, "runner: 原稿（チャンク）の一覧が空なので、スライドタブで選べるチャンクが無い")
            _ui(s, {"kind": "chunk_list_empty"})
            return
        _ui(s, {"kind": "chunk_miss", "hint": str(ctx.args.get("chunk_hint") or ""),
                "count": len(s.scratch.get("studio_scripts") or [])})
        return
    s.scratch["studio_chunk"] = chunk
    display = str(chunk.get("display_text") or chunk.get("text") or "")
    spoken = str(chunk.get("spoken_text") or "")
    payload = {"display_text": display, "spoken_text": spoken or None, "formulas": chunk.get("formulas") or [],
               "auto_paginate": False}
    if _chunk_doc(chunk):
        payload["document_id"] = _chunk_doc(chunk)
    status, body = ctx.call("POST", "/api/admin/lecture-studio/preview-split", json=payload)
    split = _split_summary(status, body)
    summary = {"scope": "chunk", "course_id": s.scratch.get("studio_course_id") or "",
               "document_id_sent": bool(payload.get("document_id")), "status": status,
               **{k: v for k, v in split.items() if k != "slides"}, "indicator": indicator_text(split),
               "wm": _wm_digest(split.get("slides") or []),
               "server_slide_mismatch": bool(chunk.get("slide_mismatch"))}
    _digest(ctx, "studio_split", summary)
    _ui(s, {"kind": "chunk_slides", "chunk": chunk, "split": split, "summary": summary,
            "language": str((s.scratch.get("studio_settings") or {}).get("lecture_language") or "ja")})


def _evidence_pane(ctx) -> None:
    if not _need_draft(ctx):
        return
    s = ctx.session
    s.scratch["studio_rightpane"] = "evidence"
    _ui(s, {"kind": "evidence", "topic": (s.scratch.get("studio_topic") or {}).get("topic") or {}})


def _stumble_claim_ids(topic: dict) -> set[str]:
    """lsStumbleClaimIdsForTopic: evidence_links の kind=claim の target_id || id。"""
    ids = set()
    for link in topic.get("evidence_links") or []:
        if isinstance(link, dict) and link.get("kind") == "claim":
            i = link.get("target_id") or link.get("id")
            if i:
                ids.add(str(i))
    return ids


STUMBLE_TOPIC_UNRESOLVED = "このトピックの論文が決まっていません。"  # lsLoadStumbleSummary の既定の文


def topic_document_id(topic: dict, structure: dict) -> str:
    """lsTopicDocumentId → lsCoursePrimaryDocumentId（topic.document_ids の先頭 → documents が 1 本 → sources の先頭）。"""
    ids = [str(x) for x in (topic or {}).get("document_ids") or [] if x]
    if ids:
        return ids[0]
    docs = [d for d in (structure or {}).get("documents") or [] if isinstance(d, dict)]
    if len(docs) == 1 and docs[0].get("document_id"):
        return str(docs[0]["document_id"])
    sources = (structure or {}).get("sources") or ((structure or {}).get("data") or {}).get("sources") or []
    for src in sources:
        if isinstance(src, dict) and src.get("material_id"):
            return str(src["material_id"])
    return ""


def _stumble_pane(ctx) -> None:
    """lsLoadStumbleSummary: 選択中のチャンク（無ければ原稿一覧の先頭）の文書で読む。"""
    if not _need_draft(ctx):
        return
    s = ctx.session
    s.scratch["studio_rightpane"] = "stumble"
    topic = (s.scratch.get("studio_topic") or {}).get("topic") or {}
    structure = s.scratch.get("studio_structure") or {}
    if "documents" in structure or "document_ids" in topic:
        # IK-0876 の是正後（lsLoadStumbleSummary / lsCoursePrimaryDocumentId）: トピックの論文 → コースの論文が 1 本なら
        # それ → sources の先頭。選択中のチャンクや原稿一覧の先頭には頼らない
        doc, document_from = topic_document_id(topic, structure), "topic"
        if not doc:
            _ui(s, {"kind": "stumble", "message": str(structure.get("topic_document_unresolved_notice")
                                                      or STUMBLE_TOPIC_UNRESOLVED)})
            return
    else:  # 旧サーバ: 選択中のチャンク（無ければ原稿一覧の先頭）の文書
        chunk = s.scratch.get("studio_chunk") or next((c for c in s.scratch.get("studio_scripts") or []
                                                       if isinstance(c, dict)), None)
        doc = _chunk_doc(chunk) if isinstance(chunk, dict) else ""
        document_from = "selected_chunk" if s.scratch.get("studio_chunk") else "first_chunk"
        if not doc:
            _ui(s, {"kind": "stumble", "message": STUMBLE_NO_DOCUMENT})
            return
    status, body = ctx.main(overrides={"document_id": doc})
    if status != 200 or not isinstance(body, dict):
        _ui(s, {"kind": "stumble", "message": STUMBLE_FETCH_FAIL})
        return
    claims = [c for c in body.get("claims") or [] if isinstance(c, dict)]
    ids = _stumble_claim_ids(topic)
    filtered = [c for c in claims if str(c.get("claim_id") or "") in ids] if ids else claims
    numeric_axes = []
    for c in claims:
        axes = c.get("axes") if isinstance(c.get("axes"), dict) else {}
        for label, path in STUMBLE_AXES:
            v: Any = axes
            for k in path:
                v = v.get(k) if isinstance(v, dict) else None
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                numeric_axes.append(label)
    _digest(ctx, "studio_stumble", {"document_from": document_from,
                                    "topic_has_claims": bool(ids), "claims": len(claims), "shown": len(filtered),
                                    "numeric_axes": sorted(set(numeric_axes))})
    _ui(s, {"kind": "stumble", "claims": filtered, "has_filter": bool(ids)})


def audio_readiness_state(session: PersonaSession) -> str:
    """lsAudioReadinessState（busy は扱わない — この試験では音声生成を起こさない）。"""
    if not session.scratch.get("studio_course_id"):
        return "no_course"
    if not session.scratch.get("studio_loaded"):
        return "loading"
    structure = session.scratch.get("studio_structure") or {}
    content = structure.get("course_content_status") if isinstance(structure, dict) else None
    chunks = [c for c in session.scratch.get("studio_scripts") or [] if isinstance(c, dict)]
    if not (isinstance(content, dict) and content.get("status") == "completed") or not chunks:
        return "content_incomplete"
    if not all(c.get("status") and c.get("status") != "ungenerated" for c in chunks):
        return "script_incomplete"
    return "ready"


def _audio_readiness(ctx) -> None:
    s = ctx.session
    cid = _studio_course_id(ctx)
    if not cid:
        ctx.missing("course_id")
        return
    if not s.scratch.get("studio_loaded") or s.scratch.get("studio_course_id") != cid:
        _load_course(ctx, cid)
    state = audio_readiness_state(s)
    _digest(ctx, "studio_readiness", {"course_id": cid, "state": state})
    _ui(s, {"kind": "readiness", "state": state, "structure": s.scratch.get("studio_structure") or {},
            "language": str((s.scratch.get("studio_settings") or {}).get("lecture_language") or "ja")})


# ---------------------------------------------------------------------------
# 教員: 図の提案と教材図スタジオ
# ---------------------------------------------------------------------------

def _topic_id(session: PersonaSession) -> str:
    return str(((session.scratch.get("studio_topic") or {}).get("topic") or {}).get("id") or "")


def _suggestions(ctx) -> None:
    """lsLoadFigureSuggestions: 取得に失敗しても空の一覧として描く（res.ok でなければ null → 空）。"""
    if not _need_draft(ctx):
        return
    s = ctx.session
    s.scratch["studio_rightpane"] = "figures"
    tid = _topic_id(s)
    status, body = ctx.main(overrides={"topic_id": tid, "course_id": s.scratch.get("studio_course_id")})
    data = body if status == 200 and isinstance(body, dict) else {"suggestions": []}
    s.scratch.setdefault("studio_suggestions", {})[tid] = data
    _digest(ctx, "studio_suggestions", {"status": status, "shown_empty_on_error": status != 200,
                                        "count": len([x for x in data.get("suggestions") or [] if isinstance(x, dict)])})
    _ui(s, {"kind": "suggestions", "data": data})


def _suggestions_generate(ctx) -> None:
    if not _need_draft(ctx):
        return
    s = ctx.session
    tid = _topic_id(s)
    cached = (s.scratch.get("studio_suggestions") or {}).get(tid) or {}
    if any(isinstance(x, dict) and x.get("status") == "candidate" for x in cached.get("suggestions") or []):
        _runner_note(s, f"runner_default: 作り直しの確認（{SUGGEST_REGEN_CONFIRM}）は「作り直す」で続けた")
    status, body = ctx.main(overrides={"topic_id": tid, "course_id": s.scratch.get("studio_course_id")})
    if status != 200 or not isinstance(body, dict):
        detail = body.get("detail") if isinstance(body, dict) else None
        _ui(s, {"kind": "suggestions", "data": cached or {"suggestions": []},
                "status_line": str(detail or SUGGEST_FAIL)})
        return
    data = {"suggestions": body.get("suggestions") or [], "degraded": bool(body.get("degraded")),
            "note": body.get("note") or ""}
    s.scratch.setdefault("studio_suggestions", {})[tid] = data
    line = (data["note"] or SUGGEST_DEGRADED) if data["degraded"] else (SUGGEST_DROPPED if body.get("dropped") else SUGGEST_OK)
    _ui(s, {"kind": "suggestions", "data": data, "status_line": line})


def _visible_suggestions(session: PersonaSession) -> list[dict]:
    data = (session.scratch.get("studio_suggestions") or {}).get(_topic_id(session)) or {}
    return [x for x in data.get("suggestions") or [] if isinstance(x, dict) and x.get("status") != "superseded"]


def _studio_open(session: PersonaSession, *, preset: Optional[dict] = None) -> dict:
    """FigureStudio.open: モーダル 1 つ分の状態（閉じたら破棄・会話は保存されない）。"""
    material = (session.scratch.get("studio_draft") or {}).get("material") or ""
    pos = len(material)
    anchor_found = False
    if preset and preset.get("anchor_excerpt"):
        needle = str(preset["anchor_excerpt"]).strip()
        idx = material.find(needle) if needle else -1
        if idx < 0 and needle:
            idx = material.find(needle[:40])
        if idx >= 0:
            pos, anchor_found = idx, True
    studio = {"topic_id": _topic_id(session), "history": [], "messages": [], "current_svg": "", "title": "",
              "caption": "", "figure_kind": (preset or {}).get("figure_kind") or "",
              "suggestion_id": (preset or {}).get("id") or None, "insert_pos": pos, "adopted_figure_id": None,
              "status_line": "", "anchor_found": anchor_found}
    session.scratch["figure_studio"] = studio
    return studio


_HEADING_LINE_RE = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)


def appendix_start(material: str, headings: list) -> Optional[int]:
    """lsMoveCursorBeforeGeneratedAppendix: 最初の自動生成付録の見出し（行頭・行末の空白可）の位置。無ければ None。"""
    first: Optional[int] = None
    for heading in headings or []:
        if not heading:
            continue
        m = re.search(r"(^|\n)" + re.escape(str(heading)) + r"[ \t]*(\n|$)", material or "")
        if m:
            at = m.start() + len(m.group(1))
            first = at if first is None or at < first else first
    return first


def as_paragraph(material: str, pos: int, text: str) -> str:
    """lsAsParagraph: 挿入位置の前後を空行で区切る（前が空なら前置きなし・後ろが空なら改行 1 つ）。"""
    before, after = (material or "")[:pos], (material or "")[pos:]
    lead = "" if not before else ("" if before.endswith("\n\n") else ("\n" if before.endswith("\n") else "\n\n"))
    trail = "\n" if not after else ("" if after.startswith("\n\n") else ("\n" if after.startswith("\n") else "\n\n"))
    return lead + text + trail


def heading_insert_pos(material: str, hint: Any) -> Optional[tuple[int, str, str]]:
    """``after_heading``（見出しの一部か、見出しの番号 1 始まり）の直後のカーソル位置。

    返り値は (位置, 前に足す文字, 見出しの文字)。見出しの次に空行があれば空行の頭（埋め込みが行として入る）、
    無ければ見出しの行末で改行を 1 つ足す（教員が Enter を押して空行を作ったのと同じ）。見つからなければ None。
    """
    material = material or ""
    h = str(hint or "").strip().lstrip("#").strip()
    if not h:
        return None
    heads = list(_HEADING_LINE_RE.finditer(material))
    hit = None
    if h.isdigit():
        i = int(h)
        hit = heads[i - 1] if 1 <= i <= len(heads) else None
    else:
        hit = next((m for m in heads if h in m.group(1)), None)
    if hit is None:
        return None
    end = hit.end()
    if material[end:end + 2] == "\n\n":
        return end + 1, "", hit.group(1)
    return end, "\n", hit.group(1)


def svg_texts(svg: str) -> list[str]:
    """プレビューの図に人が読める文字（``<text>`` の中身）。"""
    out = []
    for m in _SVG_TEXT_RE.findall(svg or ""):
        t = re.sub(r"\s+", " ", _TAG_RE.sub("", m)).strip()
        if t and t not in out:
            out.append(t)
    return out[:30]


def _send_turn(ctx, studio: dict, instruction: str) -> None:
    """_sendTurn: 返答を会話欄に・SVG があればプレビューを差し替え（無ければ「変わっていません」）。"""
    s = ctx.session
    studio["messages"].append(("user", instruction))
    body = {"history": list(studio["history"]), "instruction": instruction, "current_svg": studio.get("current_svg") or "",
            "topic_id": studio.get("topic_id") or ""}
    if studio.get("suggestion_id"):
        body["suggestion_id"] = studio["suggestion_id"]
    if ctx.args.get("model"):
        body["model"] = str(ctx.args["model"])
    status, data = ctx.call("POST", "/api/admin/courses/{course_id}/figure-studio/turn", json=body,
                            overrides={"course_id": s.scratch.get("studio_course_id")})
    if status != 200 or not isinstance(data, dict):
        detail = data.get("detail") if isinstance(data, dict) else None
        studio["messages"].append(("note", str(detail or STUDIO_SEND_FAIL)))
        return
    reply = str(data.get("reply") or "")
    if reply:
        studio["messages"].append(("ai", reply))
    studio["history"] += [{"role": "user", "content": instruction}, {"role": "assistant", "content": reply}]
    if data.get("svg_source"):
        studio["current_svg"] = str(data["svg_source"])
        studio["adopted_figure_id"] = None
    else:
        studio["messages"].append(("note", STUDIO_NO_CHANGE))
    if data.get("title") and not studio.get("title"):
        studio["title"] = str(data["title"])
    if data.get("caption") and not studio.get("caption"):
        studio["caption"] = str(data["caption"])
    if data.get("figure_kind"):
        studio["figure_kind"] = str(data["figure_kind"])
    if data.get("degraded"):
        studio["messages"].append(("note", STUDIO_DEGRADED))
    _digest(ctx, "studio_turn", {"degraded": bool(data.get("degraded")), "has_svg": bool(data.get("svg_source")),
                                 "figure_kind": str(data.get("figure_kind") or "")})


def _pick_suggestion(session: PersonaSession, hint: Any) -> Optional[dict]:
    rows = [x for x in _visible_suggestions(session) if x.get("status") in ("candidate", "accepted")]
    if not rows:
        return None
    h = str(hint or "").strip()
    if not h:
        return rows[0]
    if h.isdigit():
        i = int(h)
        return rows[i - 1] if 1 <= i <= len(rows) else None
    return next((x for x in rows if h in str(x.get("gap_reason") or "") + str(x.get("figure_brief") or "")), None)


def _build_from_suggestion(ctx) -> None:
    """lsOpenFigureStudioForSuggestion: スタジオを開いて方針入りの初回依頼を送り、提案を accepted にする。"""
    if not _need_draft(ctx):
        return
    s = ctx.session
    sug = _pick_suggestion(s, ctx.args.get("suggestion_hint"))
    if sug is None:
        _ui(s, {"kind": "suggestions", "data": (s.scratch.get("studio_suggestions") or {}).get(_topic_id(s)) or {},
                "status_line": "（押せる「この図を作る」が見当たらない）"})
        return
    studio = _studio_open(s, preset=sug)
    if sug.get("figure_brief"):
        instruction = STUDIO_PRESET_HEAD + str(sug["figure_brief"])
        if sug.get("anchor_excerpt"):
            instruction += "\n対象箇所（教材本文からの引用）: " + str(sug["anchor_excerpt"])
        if sug.get("figure_kind"):
            instruction += "\n図の種類: " + FIGURE_KIND_LABELS.get(str(sug["figure_kind"]), str(sug["figure_kind"]))
        _send_turn(ctx, studio, instruction)
    status, body = ctx.call("POST", "/api/admin/courses/{course_id}/figure-suggestions/{suggestion_id}/accept",
                            overrides={"course_id": s.scratch.get("studio_course_id"), "suggestion_id": str(sug.get("id"))})
    (s.scratch.get("studio_suggestions") or {}).pop(_topic_id(s), None)
    if status == 409:
        studio["status_line"] = str((body or {}).get("detail") if isinstance(body, dict) else "") or SUGGEST_ALREADY
    elif status and status >= 400:
        studio["status_line"] = str((body or {}).get("detail") if isinstance(body, dict) else "") or "提案の状態を更新できませんでした"
    _ui(s, {"kind": "figure_studio", "studio": studio})


def _turn(ctx) -> None:
    if not _need_draft(ctx):
        return
    s = ctx.session
    studio = s.scratch.get("figure_studio")
    if not isinstance(studio, dict) or studio.get("topic_id") != _topic_id(s):
        studio = _studio_open(s)
        _runner_note(s, "runner_default: 教材図スタジオを「🖼 図を挿入」から開いた（挿入位置は after_heading の"
                        "見出しの直後、無ければ教材欄の末尾）")
    if ctx.args.get("after_heading"):
        studio["after_heading"] = str(ctx.args["after_heading"])
    instruction = str(ctx.args.get("instruction") or "").strip()
    if not instruction:
        _ui(s, {"kind": "figure_studio", "studio": studio})
        return
    _send_turn(ctx, studio, instruction)
    _ui(s, {"kind": "figure_studio", "studio": studio})


def _adopt(ctx) -> None:
    """_saveFigure(true) → onInsert（lsApplyFigureInsert）→ updateCourseSlideIndicator。"""
    if not _need_draft(ctx):
        return
    s = ctx.session
    studio = s.scratch.get("figure_studio")
    if not isinstance(studio, dict) or not studio.get("current_svg") or studio.get("adopted_figure_id"):
        # 画面のボタンは図ができていない・保存済みのあいだ無効
        _ui(s, {"kind": "figure_studio", "studio": studio or _studio_open(s), "adopt_disabled": True})
        return
    body = {"svg_source": studio["current_svg"], "title": studio.get("title") or "", "caption": studio.get("caption") or "",
            "figure_kind": studio.get("figure_kind") or "other", "topic_id": studio.get("topic_id") or "", "adopt": True}
    if studio.get("suggestion_id"):
        body["suggestion_id"] = studio["suggestion_id"]
    status, data = ctx.main(json=body, overrides={"course_id": s.scratch.get("studio_course_id")})
    if status != 200 or not isinstance(data, dict):
        detail = data.get("detail") if isinstance(data, dict) else None
        studio["status_line"] = str(detail or STUDIO_SAVE_FAIL)
        _ui(s, {"kind": "figure_studio", "studio": studio})
        return
    fid = str(data.get("figure_id") or "")
    studio["adopted_figure_id"] = fid
    studio["status_line"] = ADOPT_NOTICE + (" " + SNAPSHOT_FAILED_NOTICE if data.get("image_snapshot_failed") else "")
    _digest(ctx, "studio_adopt", {"course_id": s.scratch.get("studio_course_id") or "", "topic_id": studio.get("topic_id"),
                                  "figure_id": fid,
                                  "linked": fid in [str(x) for x in data.get("linked_figure_ids") or []],
                                  "evidence_link": isinstance(data.get("evidence_link"), dict)})
    if fid:
        draft = s.scratch["studio_draft"]
        material = draft.get("material") or ""
        heading = heading_insert_pos(material, ctx.args.get("after_heading") or studio.get("after_heading"))
        prefix = ""
        if heading is not None:
            pos, prefix, head_text = heading
            where = f"見出し「{head_text}」の直後"
        else:
            pos = min(max(int(studio.get("insert_pos") or 0), 0), len(material))
            where = "提案の引用箇所の前" if studio.get("anchor_found") else "教材欄の末尾"
            if ctx.args.get("after_heading") or studio.get("after_heading"):
                where += "（指定した見出しが教材欄に無かった）"
        # lsMoveCursorBeforeGeneratedAppendix（第 1 周の是正後）: カーソルが自動生成の付録（式・図の一覧の見出し）より
        # 後ろなら最初の付録の見出しの前へ寄せる。lsAsParagraph: 埋め込みを前後とも空行で区切った段落にする
        structure = s.scratch.get("studio_structure") or {}
        moved = appendix_start(material, structure.get("generated_appendix_headings") or [])
        if moved is not None and pos >= moved:
            pos, prefix = moved, ""
            where += "（自動生成の付録より前へ寄せた）"
        if structure.get("generated_appendix_headings") is not None:
            embed = as_paragraph(material, pos, "![[figure:" + fid + "]]")
        else:
            embed = prefix + "![[figure:" + fid + "]]"  # 旧サーバ・旧画面（段落化なし）
        draft["material"] = material[:pos] + embed + material[pos:]
        studio["insert_pos"] = pos + len(embed)
        topic = (s.scratch.get("studio_topic") or {}).get("topic") or {}
        if isinstance(data.get("evidence_link"), dict):
            topic["evidence_links"] = list(topic.get("evidence_links") or []) + [data["evidence_link"]]
        s.scratch.setdefault("studio_audio_notice", {})[str(studio.get("topic_id"))] = True
        s.remember("adopted_figures", fid)
        _runner_note(s, "runner_default: 図の埋め込みはカーソル位置の代わりに" + where + "に入れた"
                        + ("" if heading is not None else "（after_heading に見出しを渡すとその直後に入れる）"))
    _preview_topic(ctx)
    _ui(s, {"kind": "figure_studio", "studio": studio, "after": _topic_view(s)})


def _existing(ctx) -> None:
    if not _need_draft(ctx):
        return
    s = ctx.session
    status, body = ctx.main(overrides={"course_id": s.scratch.get("studio_course_id")})
    figs = [f for f in (body or {}).get("figures") or [] if isinstance(f, dict)] if status == 200 and isinstance(body, dict) else []
    index = (s.scratch.get("studio_structure") or {}).get("figures_index") or {}
    _ui(s, {"kind": "existing", "figures": figs, "index": index if isinstance(index, dict) else {}})


# ---------------------------------------------------------------------------
# 学生: レクチャーと教材の図
# ---------------------------------------------------------------------------

def material_summary(session: PersonaSession) -> dict:
    """学生の教材（直前に開いたトピック）の、審判が突き合わせる欄だけ。"""
    m = session.material if isinstance(session.material, dict) else {}
    chunks = [c for c in m.get("chunks") or [] if isinstance(c, dict)]
    topic_based = bool(chunks) and all(str(c.get("id") or "").startswith("topic:") for c in chunks)
    fig_ids: list[str] = []
    raw_embeds: list[str] = []
    for c in chunks:
        for f in c.get("figures") or []:
            if isinstance(f, dict) and f.get("figure_id") and str(f["figure_id"]) not in fig_ids:
                fig_ids.append(str(f["figure_id"]))
        raw_embeds += _FIGURE_EMBED_RE.findall(str(c.get("text") or ""))
    return {"topic_id": str(m.get("topic_id") or session.topic_id or ""), "pages": len(chunks),
            "topic_based": topic_based, "figure_ids": fig_ids, "raw_figure_embeds": sorted(set(raw_embeds))}


def _audio_status_call(ctx) -> tuple[Optional[int], Any]:
    s = ctx.session
    status, body = ctx.call("GET", "/api/learning/lecture/courses/{course_id}/topics/{topic_id}/audio-status")
    if status == 200 and isinstance(body, dict):
        s.scratch.setdefault("lecture_audio", {})[f"{s.course_id}:{s.topic_id}"] = body
    note = (body.get("lecture_note") or body.get("note")) if isinstance(body, dict) else None  # IK-0879: lecture_note
    _digest(ctx, "studio_student", {"course_id": s.course_id, "topic_id": s.topic_id, "status": status,
                                    "has_audio": bool(isinstance(body, dict) and body.get("has_audio")),
                                    "stale_language": bool(isinstance(body, dict) and body.get("stale_language")),
                                    "audio_total_slides": body.get("total_slides") if isinstance(body, dict) else None,
                                    "note_present": bool(str(note or "").strip()),
                                    "material": material_summary(s), "sequence": None})
    return status, body


def _toggle_state(body: Any) -> dict:
    """refreshTopicAudioStatus（IK-0879 の是正後）: has_audio / stale_language / スライドの有無（total_slides > 0）/
    lecture_note（サーバの事実文）。旧サーバは total_slides があっても has_slides で入口を開けない（has_audio のみ）。"""
    if not isinstance(body, dict):
        return {"has_audio": False, "has_slides": False, "stale": False, "note": "", "legacy": False}  # 取得失敗: 理由なし
    legacy = "lecture_note" not in body
    return {"has_audio": bool(body.get("has_audio")), "stale": bool(body.get("stale_language")),
            "has_slides": not legacy and int(body.get("total_slides") or 0) > 0,
            "note": str(body.get("lecture_note") or ""), "legacy": legacy}


def _lecture_audio_status(ctx) -> None:
    """refreshTopicAudioStatus → updateLectureToggleAvailability。"""
    status, body = _audio_status_call(ctx)
    if status != 200 or not isinstance(body, dict):
        # 取得失敗時は無効化のまま（app.js）
        _ui(ctx.session, {"kind": "lecture_toggle", **_toggle_state(None)}, status=status)
        return
    _ui(ctx.session, {"kind": "lecture_toggle", **_toggle_state(body)})


def _lecture_sequence(ctx) -> None:
    """startLecture（IK-0879 の是正後）: 音声もスライドも無いトピックでは開始しない（ボタンが押せない）。音声が無い
    だけならスライドがあれば開始し、全スライドをタイマー送りにする。"""
    s = ctx.session
    status, audio = _audio_status_call(ctx)
    toggle = _toggle_state(audio if status == 200 else None)
    has_audio = toggle["has_audio"]
    if not has_audio and not toggle["has_slides"]:
        _runner_note(s, "unsupported_ui_state: このトピックは音声もスライドも無く「🎙 講義」が押せない（app.js startLecture）— "
                        "レクチャーの列は読まずに、押せないボタンの表示を投影した")
        _ui(s, {"kind": "lecture_toggle", **toggle, "attempted_start": True})
        return
    seq_status, seq = ctx.main()
    if seq_status != 200 or not isinstance(seq, dict):
        s.last_status, s.last_body = seq_status, seq
        return
    slides = lecture_deck(seq)
    _digest(ctx, "studio_student", {"course_id": s.course_id, "topic_id": s.topic_id, "status": seq_status,
                                    "has_audio": has_audio, "audio_total_slides": audio.get("total_slides"),
                                    "note_present": bool(str(audio.get("lecture_note") or audio.get("note") or "").strip()),
                                    "material": material_summary(s),
                                    "sequence": {"total_slides": seq.get("total_slides"), "deck": len(slides),
                                                 "topic_based": any(str(x.get("chunk_id") or "").startswith("topic:")
                                                                    for x in seq.get("segments") or [] if isinstance(x, dict)),
                                                 "slides_without_audio": sum(1 for x in slides if not x.get("has_audio")),
                                                 "figure_ids": sorted({str(f.get("figure_id")) for x in slides
                                                                       for f in x.get("figures") or []
                                                                       if isinstance(f, dict) and f.get("figure_id")})}})
    for x in slides:
        for f in x.get("figures") or []:
            if isinstance(f, dict) and f.get("figure_id"):
                s.remember("lecture_figures", str(f["figure_id"]))
    s.scratch["lecture_deck"] = slides
    s.scratch["lecture_pos"] = 0
    s.scratch["lecture_stale"] = bool(audio.get("stale_language"))
    # 第 25 周 studio H16: 画面はスライドを 1 枚ずつ出す（全スライドを 1 回の観察で並べない）。送りは learning.lecture.next
    _ui(s, {"kind": "lecture_deck", "slides": slides, "pos": 0, "stale": s.scratch["lecture_stale"]})


def _lecture_next(ctx) -> None:
    """app.js の ◀ ▶ / 音声の終わりの自動送り / 音声の無いスライドのタイマー送り（秒数は出さない）。HTTP は出さない。"""
    s = ctx.session
    slides = s.scratch.get("lecture_deck")
    if not isinstance(slides, list) or not slides:
        return ctx.missing("lecture_deck")
    to = str(ctx.args.get("to") or "next").strip().lower()
    pos = int(s.scratch.get("lecture_pos") or 0)
    if to in ("prev", "back", "◀", "前"):
        pos = max(pos - 1, 0)
    elif to in ("last", "end", "最後"):
        pos = len(slides) - 1
    else:
        pos = min(pos + 1, len(slides) - 1)
    s.scratch["lecture_pos"] = pos
    seen = s.scratch.setdefault("lecture_seen", [])
    if pos not in seen:
        seen.append(pos)
    _digest(ctx, "studio_lecture_progress", {"course_id": s.course_id, "topic_id": s.topic_id,
                                             "reached_last": pos == len(slides) - 1})
    _ui(s, {"kind": "lecture_deck", "slides": slides, "pos": pos, "stale": bool(s.scratch.get("lecture_stale"))})


def lecture_deck(seq: dict) -> list[dict]:
    """buildLectureDeck: セグメントのスライドを 1 列に（スライドが無いセグメントは 1 枚に合成）。"""
    deck: list[dict] = []
    for seg in seq.get("segments") or []:
        if not isinstance(seg, dict):
            continue
        slides = seg.get("slides") or [{"slide_index": 0, "display_text": seg.get("text") or "",
                                        "spoken_text": seg.get("spoken_text") or "", "formulas": seg.get("formulas") or [],
                                        "figures": seg.get("figures") or [], "has_audio": seg.get("has_audio")}]
        for sl in slides:
            if not isinstance(sl, dict):
                continue
            deck.append({"chunk_id": seg.get("chunk_id"), "display_text": sl.get("display_text") or "",
                         "formulas": sl.get("formulas") or seg.get("formulas") or [],
                         "figures": sl.get("figures") or seg.get("figures") or [],
                         "has_audio": bool(sl.get("has_audio")),
                         "previously_touched": bool(seg.get("previously_touched")),
                         "evidence_items": seg.get("evidence_items") or []})
    return deck


def _figure_candidates(session: PersonaSession) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    m = session.material if isinstance(session.material, dict) else {}
    pools = [c.get("figures") or [] for c in m.get("chunks") or [] if isinstance(c, dict)]
    pools += [x.get("figures") or [] for x in session.scratch.get("lecture_deck") or [] if isinstance(x, dict)]
    for pool in pools:
        for f in pool:
            if isinstance(f, dict) and f.get("figure_id") and str(f["figure_id"]) not in seen:
                seen.add(str(f["figure_id"]))
                out.append(f)
    return out


def _figure_view(ctx) -> None:
    s = ctx.session
    figs = _figure_candidates(s)
    if not figs:
        ctx.missing("figure_in_material")
        return
    hint = str(ctx.args.get("figure_hint") or "").strip()
    fig = figs[0]
    if hint:
        if hint.isdigit() and 1 <= int(hint) <= len(figs):
            fig = figs[int(hint) - 1]
        else:
            fig = next((f for f in figs if hint in str(f.get("caption") or "")), figs[0])
    fid = str(fig.get("figure_id"))
    url = str(fig.get("image_url") or "")
    if url.startswith("/api/learning/"):
        status, body = ctx.call("GET", url)
    else:
        status, body = ctx.main(overrides={"figure_id": fid})
    _digest(ctx, "studio_figure_view", {"course_id": s.course_id, "topic_id": s.topic_id, "figure_id": fid,
                                        "status": status, "image_url_kind": "learning" if url.startswith("/api/learning/")
                                        else ("admin" if url.startswith("/api/admin/") else ("none" if not url else "other"))})
    _ui(s, {"kind": "figure_view", "figure": fig, "ok": status == 200,
            "binary": body.get("_binary") if isinstance(body, dict) else None}, status=200)


HANDLERS: dict[str, Callable[[Any], None]] = {
    "admin.lecture_studio.open_course": _open_course,
    "admin.lecture_studio.select_topic": _select_topic,
    "admin.lecture_studio.insert_slide_marker": _insert_slide_marker,
    "admin.lecture_studio.edit_draft": _edit_draft,
    "admin.lecture_studio.save_topic": _save_topic,
    "admin.lecture_studio.rewrite_topic": _rewrite_topic,
    "admin.lecture_studio.chunk_slides": _chunk_slides,
    "admin.lecture_studio.right_pane_evidence": _evidence_pane,
    "admin.lecture_studio.right_pane_stumble": _stumble_pane,
    "admin.lecture_studio.audio_readiness": _audio_readiness,
    "admin.figure_studio.suggestions": _suggestions,
    "admin.figure_studio.suggestions_generate": _suggestions_generate,
    "admin.figure_studio.build_from_suggestion": _build_from_suggestion,
    "admin.figure_studio.turn": _turn,
    "admin.figure_studio.adopt": _adopt,
    "admin.figure_studio.existing": _existing,
    "admin.lecture_studio.reattach_appendix": _reattach_appendix,  # 第 2 波（IK-0872 / IK-0880）
    "learning.lecture.figure_view": _figure_view,
    # 既存の行為 id（画面どおりの投影・審判の要約に差し替える）
    "learning.lecture.audio_status": _lecture_audio_status,
    "learning.lecture.sequence": _lecture_sequence,
    "learning.lecture.next": _lecture_next,  # 第 25 周 studio H16
}


def execute(action_id: str, ctx) -> bool:
    """扱った行為なら実行して True（共通の取り決め: ``uxsim/runs/round25`` の各面と同じ形）。"""
    handler = HANDLERS.get(action_id)
    if handler is None:
        return False
    handler(ctx)
    return True


def ui_only(action_id: str, session: PersonaSession) -> bool:
    """この面に API の無い画面だけの行為は無い（全部 HTTP を伴うか、読み込み済みの状態を描く）。"""
    return False


# ---------------------------------------------------------------------------
# 投影
# ---------------------------------------------------------------------------

#: admin-lecture-studio.js lsRenderChunkList の空の一覧の文（<br> は改行）
CHUNK_LIST_EMPTY = ("教材のチャンクが見つかりません。\n"
                    "教材がコースに紐づけられているか、PDF解析が完了しているかを確認してください。")


def course_meta(structure: dict) -> str:
    """lsRenderCourseTree のコース名の括弧（IK-0877 の是正後）: トピック音声の状態があればその事実文、無ければ従来の
    チャンク原稿の状態。音声準備の投影も同じ規則で作る（第 25 周 studio H18: 準備の行為だけ course_status の語彙で
    括弧を作り、保存の前後で見出しの語が入れ替わって見えた）。"""
    s = structure or {}
    ta = s.get("topic_audio_status") if isinstance(s.get("topic_audio_status"), dict) else None
    # 第 3 波（IK-0952）: トピック教材が無い（no_topics）ときもサーバの事実文を出す
    if ta and ta.get("status") and ta.get("message"):
        return str(ta["message"])
    meta = COURSE_STATUS_LABELS.get(str(s.get("course_status") or ""), str(s.get("course_status") or ""))
    if (s.get("total_chunks") or 0) > 0:
        meta += f" ({s.get('generated_chunks') or 0}/{s.get('total_chunks')})"
    return meta


_CHAPTER_PREFIX_RE = re.compile(r"^第\s*[0-9０-９一二三四五六七八九十]+\s*章")


def chapter_heading(index: int, title: Any) -> str:
    """lsChapterHeading（第 3 波・IK-0952）: 章題が「第N章」で始まるなら番号を重ねない。"""
    t = str(title or "").strip()
    return t if _CHAPTER_PREFIX_RE.match(t) else f"第{index + 1}章: {t}"


def _course_lines(structure: dict, settings: Any) -> list[str]:
    s = structure or {}
    meta = course_meta(s)
    lines = [f"原稿スタジオ — コース: {s.get('title') or 'コース'}（{meta}）"]
    # lsRenderCourseTree（第 3 波）: 題名の下に共有の印（IK-0950・editor のときだけ）、状態の後に題名の数式の事実文（IK-0951）
    if s.get("share_mark"):
        lines.append(str(s["share_mark"]))
    if s.get("title_notice"):
        titles = [str(x) for x in s.get("raw_tex_titles") or [] if x]
        lines.append(str(s["title_notice"]) + (f"（{' / '.join(titles)}）" if titles else ""))
    lang = str((settings or {}).get("lecture_language") or "ja") if isinstance(settings, dict) else "ja"
    lines.append(f"読み上げ言語: {LANG_LABELS.get(lang, lang)}")
    chapters = [c for c in s.get("chapters") or [] if isinstance(c, dict)]
    if not chapters:
        lines.append(COURSE_NO_CHAPTERS)
        return lines
    n = 0
    for ci, ch in enumerate(chapters):
        lines.append(chapter_heading(ci, ch.get("title")))
        for t in ch.get("topics") or []:
            if isinstance(t, dict):
                n += 1
                lines.append(f"  {n}. {t.get('title') or '無題'}")
    lines.append("（左ペインのタブ: コース / 文書構造 / コンポーネント）")
    return lines


def _readiness_lines(state: str) -> list[str]:
    if state == "ready":
        return ["コース設定メニュー: 「音声生成を実行する」は押せる（押すと読み上げ言語を選ぶモーダルが開く）"]
    reason, action = AUDIO_GUIDANCE.get(state, ("", ""))
    return ["コース設定メニュー: 「音声生成を実行する」は押せない（グレーアウト）",
            f"  {reason}" + (f" → {action}" if action else "")]


def _draft_lines(view: dict) -> list[str]:
    topic, draft, split = view.get("topic") or {}, view.get("draft") or {}, view.get("split") or {}
    edit = view.get("view") != "preview"
    lines = [f"トピック: {topic.get('title') or '無題'}" + (f"（{view.get('chapter_title')}）" if view.get("chapter_title") else ""),
             "［プレビュー］［編集］" + ("（いま: 編集）" if edit else "（いま: プレビュー）")]
    concepts = draft.get("key_concepts") or []
    lines.append("重要な概念: " + ("、".join(concepts) if concepts else "重要な概念はまだ入力されていません。"))
    formulas = topic_formulas(topic)
    evidence = topic.get("evidence_items") or []
    if edit:
        lines.append("教材（編集欄）:")
        lines.append(_clip(draft.get("material") or "", 2600) or "（空）")
        lines.append("本文説明（編集欄・音声読み上げ対象）:")
        lines.append(_clip(draft.get("spoken") or "", 1600) or "（空）")
        lines.append("［+ スライド区切りを挿入］［🖼 図を挿入］ " + str(split.get("indicator") or ""))
    else:
        lines.append("教材:")
        lines.append(_clip(render_material_text({"text": draft.get("material") or "", "formulas": formulas,
                                                 "evidence_items": evidence}), 2600))
        lines.append("本文説明:")
        lines.append(_clip(render_material_text({"text": draft.get("spoken") or "", "formulas": formulas,
                                                 "evidence_items": evidence}), 1600))
    cautions = draft.get("cautions") or []
    if not edit:
        cautions = cautions_display(topic, list(cautions))
    # 第 25 周 studio H20: 製品は注意点を 1 行 1 項目のリストで描く（「、」で 1 行に連結しない）
    if cautions:
        lines.append("注意点:")
        lines += [f"- {c}" for c in cautions]
    else:
        lines.append("注意点: 注意点はまだ入力されていません。")
    qs = draft.get("check_questions") or []
    lines.append("確認問題: " + (str(qs[0].get("question") or "") if qs else "（なし）"))
    pill = coverage_label(topic)
    lines.append("根拠リンク: " + (f"[{pill}] " if pill else "") + coverage_message(topic))
    st = evidence_state(topic)
    if st and st.get("unmapped_heading"):
        lines.append(f"  {st['unmapped_heading']}")
    chips = [f"{KIND_LABELS.get(str(e.get('kind')), str(e.get('kind')))}: {e.get('title') or e.get('summary') or ''}".strip()
             for e in evidence if isinstance(e, dict)]
    if chips:
        lines.append("  " + " / ".join(c[:60] for c in chips[:10]) + (" …ほか" if len(chips) > 10 else ""))
    # lsCourseDraftHtml（第 2 波）: 付録の状態 → 保存できない理由 → 届き方 → 音声の事実文の順（data-appendix-stale /
    # data-topic-save-notice / data-topic-delivery-notice / data-topic-audio-save-notice）
    saveable = view.get("can_save", True) is not False
    appendix = topic.get("appendix_state") if isinstance(topic.get("appendix_state"), dict) else None
    if appendix and appendix.get("message"):
        lines.append(str(appendix["message"]))
        lines.append(f"［{REATTACH_BUTTON}］" + ("" if saveable else f"（押せない: {view.get('save_block_notice') or ''}）"))
    if not saveable and view.get("save_block_notice"):
        lines.append(str(view["save_block_notice"]))
    if saveable and view.get("delivery_message"):
        lines.append(str(view["delivery_message"]))
    lines.append(DRAFT_HELP)
    if topic.get("has_topic_audio") and view.get("save_notice"):
        lines.append(str(view["save_notice"]))  # 保存の前の事実文（topic_audio_save_notice — IK-0877）
    lines.append("［原稿を保存する］" + ("" if saveable else "（押せない）") + "［AI で書き換え］")
    return lines


def _evidence_lines(topic: dict) -> list[str]:
    items = [e for e in topic.get("evidence_items") or [] if isinstance(e, dict)]
    lines = ["右ペイン: 根拠リンク"]
    if not items:
        return lines + [coverage_message(topic) or EVIDENCE_EMPTY]  # lsRenderCourseEvidence（IK-0875）
    for e in items[:20]:
        kind = KIND_LABELS.get(str(e.get("kind")), str(e.get("kind")))
        title = str(e.get("title") or "").strip()
        summary = str(e.get("summary") or e.get("caption") or "").strip()
        head = f"- {kind}" + (f": {title}" if title and title != summary and not e.get("title_is_excerpt") else "")
        lines.append(head + (f" — {summary[:160]}" if summary else (f" — $$${e['latex']}$$" if e.get("latex") else "")))
    if len(items) > 20:
        lines.append("- …ほか")
    return lines


def _stumble_lines(view: dict) -> list[str]:
    lines = ["右ペイン: つまづき"]
    if view.get("message"):
        return lines + [view["message"]]
    claims = view.get("claims") or []
    if not claims:
        return lines + [(STUMBLE_EMPTY_TOPIC if view.get("has_filter") else STUMBLE_EMPTY_DOC), STUMBLE_EMPTY_TAIL]
    for c in claims[:15]:
        axes = c.get("axes") if isinstance(c.get("axes"), dict) else {}
        parts = []
        for label, path in STUMBLE_AXES:
            v: Any = axes
            for k in path:
                v = v.get(k) if isinstance(v, dict) else None
            parts.append(f"{label}: {v if v not in (None, '') else STUMBLE_NO_DATA}")
        lines.append(f"- {c.get('label') or c.get('claim_type') or ''}")
        lines.append("  " + " ／ ".join(parts))
        lines.append("  ［この claim の問いを確認］［ドラフトの該当箇所へ］")
    return lines + [STUMBLE_NOTE]


def _suggestion_lines(view: dict) -> list[str]:
    data = view.get("data") or {}
    lines = ["右ペイン: 図の提案", "［提案を生成］"]
    if view.get("status_line"):
        lines.append(f"（表示: {view['status_line']}）")
    if data.get("degraded"):
        lines.append(str(data.get("note") or SUGGEST_DEGRADED))
    visible = [x for x in data.get("suggestions") or [] if isinstance(x, dict) and x.get("status") != "superseded"]
    if not visible:
        if not data.get("degraded"):
            lines.append(SUGGEST_EMPTY)
        return lines
    for i, x in enumerate(visible, 1):
        status = str(x.get("status") or "candidate")
        kind = x.get("figure_kind_label") or FIGURE_KIND_LABELS.get(str(x.get("figure_kind")), str(x.get("figure_kind") or ""))
        head = f"{i}. {kind}"
        if status != "candidate":
            head += f"［{SUGGEST_STATUS_LABELS.get(status, status)}］"
        if x.get("confidence_label"):
            head += f"（{x['confidence_label']}）"
        lines.append(head)
        for key in ("gap_reason", "figure_brief"):
            if x.get(key):
                lines.append(f"   {x[key]}")
        if x.get("anchor_excerpt"):
            lines.append(f"   「{str(x['anchor_excerpt'])[:120]}」")
        if x.get("signal_basis"):
            lines.append("   " + SUGGEST_BASIS_LABELS.get(str(x["signal_basis"]), str(x["signal_basis"])))
        buttons = (["［この図を作る］"] if status in ("candidate", "accepted") else []) + (["［却下］"] if status == "candidate" else [])
        if buttons:
            lines.append("   " + "".join(buttons))
    return lines


def _studio_lines(view: dict) -> list[str]:
    st = view.get("studio") or {}
    lines = ["教材図スタジオ（タブ: AIで図を作る / 既存の図から選ぶ）"]
    svg = st.get("current_svg") or ""
    if svg:
        kind = FIGURE_KIND_LABELS.get(str(st.get("figure_kind") or ""), str(st.get("figure_kind") or ""))
        lines.append("プレビュー: 図が表示されている" + (f"（{kind}）" if kind else ""))
        texts = svg_texts(svg)
        lines.append("  図の中の文字: " + (" / ".join(texts) if texts else "（文字なし）"))
    else:
        lines.append("プレビュー: " + STUDIO_EMPTY_PREVIEW)
    lines.append(f"図のタイトル: {st.get('title') or '（空）'}")
    lines.append(f"キャプション（受講者に表示されます）: {st.get('caption') or '（空）'}")
    lines.append("AIとの相談:")
    for role, text in (st.get("messages") or [])[-8:]:
        who = {"user": "あなた", "ai": "AI", "note": "（注記）"}.get(role, role)
        lines.append(f"  {who}: {_clip(str(text), 600)}")
    adopt_ok = bool(svg) and not st.get("adopted_figure_id")
    lines.append(("［採用して挿入］" if adopt_ok else "［採用して挿入］（押せない）") + ("［下書きとして保存］" if adopt_ok else "［下書きとして保存］（押せない）")
                 + "［破棄］")
    if st.get("adopted_figure_id"):
        lines.append("（採用済み）")
    if st.get("status_line"):
        lines.append(str(st["status_line"]))
    if view.get("adopt_disabled"):
        lines.append("（「採用して挿入」は押せなかった — まだ図ができていないか、保存済み）")
    if isinstance(view.get("after"), dict):
        lines.append("—— 教材欄（挿入後）——")
        lines += _draft_lines(view["after"])
    return lines


def _chunk_slide_lines(view: dict) -> list[str]:
    chunk, split = view.get("chunk") or {}, view.get("split") or {}
    lang = view.get("language") or "ja"
    lines = [f"チャンク: {chunk.get('section_title') or '（節名なし）'}" + ("［⚠ 区切り数の不一致］" if chunk.get("slide_mismatch") else ""),
             "表示内容タブ: ［プレビュー］［原稿］［数式一覧］［スライド］（いま: スライド）", indicator_text(split)]
    slides = split.get("slides") or []
    formulas = chunk.get("formulas") or []
    for i, sd in enumerate(slides):
        head = f"スライド {i + 1} / {len(slides)}  [{slide_audio_badge(chunk, i, lang)}]"
        wm = sd.get("wm") if isinstance(sd.get("wm"), dict) else None
        if wm and wm.get("level_label"):
            head += f"  [{WM_PREFIX}{wm['level_label']}]"
        lines.append(head)
        lines.append("  " + _clip(render_material_text({"text": sd.get("display_text") or "", "formulas": formulas}), 900))
        if slide_effective_length(sd.get("display_text") or "") > 600:
            lines.append("  " + SLIDE_LONG_WARN)
        if wm and wm.get("fact"):
            lines.append("  " + str(wm["fact"]))
        spoken = sd.get("spoken_text")
        lines.append("  読み上げ: " + (_clip(render_material_text({"text": spoken, "formulas": formulas}), 500) if spoken else SLIDE_NO_SPOKEN))
        lines.append("  ［▶ 試聴］")
    return lines


def _toggle_lines(view: dict) -> list[str]:
    """updateLectureToggleAvailability（IK-0879 の是正後）。文はサーバの lecture_note。旧サーバ（lecture_note なし）は
    従来の固定文。"""
    note = str(view.get("note") or "")
    if view.get("has_audio"):
        return ["教材区画のヘッダ: ［📖 読む｜🎙 講義］ — 「🎙 講義」は押せる"
                + (f"（ツールチップ: {LECTURE_STALE_TITLE}）" if view.get("stale") else "")]
    if view.get("has_slides"):
        lines = ["教材区画のヘッダ: ［📖 読む｜🎙 講義］ — 「🎙 講義」は押せる（音声なし・スライドはタイマー送り）"]
        if note:
            lines += [f"  ボタン横の表示: {LECTURE_SLIDE_NO_AUDIO}", f"  ツールチップ: {note}"]
        return lines
    if not view.get("legacy", True):
        lines = ["教材区画のヘッダ: ［📖 読む｜🎙 講義］ — 「🎙 講義」は押せない（グレーアウト）"]
        if note:
            lines += ["  ボタン横の表示: 講義なし", f"  ツールチップ: {note}"]
        if view.get("attempted_start"):
            lines.append("  （押しても講義は始まらない）")
        return lines
    lines = ["教材区画のヘッダ: ［📖 読む｜🎙 講義］ — 「🎙 講義」は押せない（グレーアウト）",
             f"  ボタン横の表示: {LECTURE_DISABLED_HINT}", f"  ツールチップ: {LECTURE_DISABLED_TITLE}"]
    if view.get("attempted_start"):
        lines.append("  （押しても講義は始まらない）")
    return lines


def _deck_lines(view: dict) -> list[str]:
    slides = view.get("slides") or []
    lines = ["レクチャー（再生画面）: ◀ ▶ 進捗 ▶ ❓ 質問" + (f"（{LECTURE_STALE_TITLE}）" if view.get("stale") else "")]
    if not slides:
        return lines + ["（スライドが無い）"]
    # 第 25 周 studio H16: いま表示しているスライド 1 枚だけ（画面の #lecture-slide-stage）
    i = min(max(int(view.get("pos") or 0), 0), len(slides) - 1)
    sl = slides[i]
    head = f"スライド {i + 1} / {len(slides)}" + ("" if sl.get("has_audio") else f"  {LECTURE_SLIDE_NO_AUDIO}")
    if sl.get("previously_touched"):
        head += "  （前に触れた箇所）"
    lines.append(head)
    lines.append("  " + _clip(render_material_text({"text": sl.get("display_text") or "", "formulas": sl.get("formulas"),
                                                    "figures": sl.get("figures"), "evidence_items": sl.get("evidence_items"),
                                                    "drop_unresolved_embeds": True}), 1200))
    if i == len(slides) - 1:
        lines.append("（最後のスライド。▶ で先へは進まない）")
    elif not sl.get("has_audio"):
        lines.append("（このスライドは音声が無いので、一定の時間で次のスライドへ送られる）")
    return lines


def project(action_id: str, status: Optional[int], body: Any) -> Optional[str]:
    """この面の応答をペルソナが読む画面の文に投影する（扱わない行為は None）。"""
    if action_id not in HANDLERS and action_id != "admin.lecture_studio.scripts":
        return None
    if isinstance(body, dict) and body.get("_precondition"):
        return f"この操作に必要なものがまだ画面に無い（{body['_precondition']}）。"
    view = body.get("_studio") if isinstance(body, dict) else None
    if not isinstance(view, dict):
        if status is None:
            return "画面が応答しなかった（接続できない / 時間切れ）。"
        if status >= 400:
            return _clip(_error(status, body))
        return None  # 既存の投影（_generic 等）に任せる
    kind = view.get("kind")
    if kind == "course":
        lines = _course_lines(view.get("structure") or {}, view.get("settings")) + _readiness_lines(view.get("readiness") or "")
    elif kind == "topic_miss":
        lines = [f"「{view.get('hint')}」に合うトピックがコースツリーに無い。トピック:"] + \
                [f"  {r['no']}. {r['topic'].get('title') or '無題'}" for r in view.get("rows") or []]
    elif kind == "topic":
        lines = (["（表示: " + view["status_line"] + "）"] if view.get("status_line") else []) + _draft_lines(view)
        if view.get("notice"):
            lines.append(view["notice"])
    elif kind == "evidence":
        lines = _evidence_lines(view.get("topic") or {})
    elif kind == "stumble":
        lines = _stumble_lines(view)
    elif kind == "readiness":
        st = view.get("structure") or {}
        meta = course_meta(st)
        lines = [f"コース: {st.get('title') or ''}（{meta}）・読み上げ言語: {LANG_LABELS.get(view.get('language'), view.get('language'))}"] + \
            _readiness_lines(view.get("state") or "")
    elif kind == "suggestions":
        lines = _suggestion_lines(view)
    elif kind == "figure_studio":
        lines = _studio_lines(view)
    elif kind == "existing":
        figs = view.get("figures") or []
        lines = ["教材図スタジオ: 既存の図から選ぶ"]
        if not figs and not view.get("index"):
            lines.append("（このコースの図はまだ無い）")
        for i, f in enumerate(figs, 1):
            status_label = TEACHING_STATUS_LABELS.get(str(f.get("status") or ""), str(f.get("status") or ""))
            kind_label = FIGURE_KIND_LABELS.get(str(f.get("figure_kind") or ""), str(f.get("figure_kind") or ""))
            lines.append(f"{i}. {f.get('title') or '（題なし）'}［{status_label}・{kind_label}］ {f.get('caption') or ''}".rstrip())
        extracted = [v for v in (view.get("index") or {}).values() if isinstance(v, dict) and not v.get("teaching")]
        if extracted:
            lines.append(f"論文から抽出された図: " + " / ".join(str(v.get("caption") or "")[:40] for v in extracted[:8]))
    elif kind == "chunk_list_empty":
        lines = [CHUNK_LIST_EMPTY]
    elif kind == "chunk_miss":
        hint = str(view.get("hint") or "").strip()
        # 第 25 周 studio H19: hint が空のとき空の鉤括弧の文を作らない。画面の文ではないので印を付ける
        lines = [f"（ハーネス: 「{hint}」に合うチャンクが原稿一覧に無い。これは画面の文ではない）" if hint else
                 "（ハーネス: どのチャンクかの指定が無いので、スライドタブで何も選んでいない。これは画面の文ではない）"]
    elif kind == "chunk_none_for_topic":
        t = view.get("topic") or {}
        lines = [f"トピック「{t.get('title') or '無題'}」に紐づくチャンクは原稿一覧に無い — 表示内容タブの［スライド］で"
                 "描けるものが無い。",
                 "このトピックのスライド枚数は授業用ドラフトのインジケータで見る: "
                 + str((view.get("split") or {}).get("indicator") or "（未取得）")]
    elif kind == "chunk_slides":
        lines = _chunk_slide_lines(view)
    elif kind == "lecture_toggle":
        lines = _toggle_lines(view)
    elif kind == "lecture_deck":
        lines = _deck_lines(view)
    elif kind == "figure_view":
        f = view.get("figure") or {}
        cap = str(f.get("caption") or "").strip()
        lines = [("図の画像が表示された" if view.get("ok") else "図の画像が表示されない（読み込めなかった）")
                 + (f": {cap}" if cap else "")]
        if f.get("explanation"):
            lines.append(f"  説明: {f['explanation']}")
    else:
        lines = _generic(view)
    return _clip("\n".join(x for x in lines if x is not None))
