"""第 25 周 面 D（原稿スタジオ → 学生の受講）の審判 — A 契約・B 原則（決定論・LLM 0 回・DB を読まない）。

材料は runner が ``HttpTrace.digest`` に置いた ``studio_*`` の要約（``uxsim/runner/studio_exec.py``）。応答の抜粋
（2000 字）は教材本文・スライド列で切れるので使わない。仮説は事実と仮説の文で書き、数を書かない（PE7・PE10）。
数（枚数・区画数）は根拠の ``server_rows`` にだけ置く。

教員側（原稿スタジオ・教材図スタジオ）:
- T1 プレビューのスライドに付く負荷ラベル（``wm``）に生の数の欄がある / 段階ラベルが無い（TT2・WMレンズ）
- T2 つまづきの集計の軸に生の数がある（k-匿名の段階ラベル・レンジで出すはず — P3）
- T3 図の提案の取得に失敗したのに、画面が「提案はまだありません」と出す（失敗と空を区別しない）
- T4 AI 書き換えが図の埋め込みを末尾へ戻した（``figures_restored``）のに、画面がそれを言わない
- T5 採用した図がトピックの登録（``linked_figure_ids``）に入っていない（§7.1b の要石）
- T6 授業用ドラフトの保存で生成済み音声が消えるのに、保存の前にも後にもその事実文が画面に出ていない
- T7 原稿スタジオの読み取り専用の区画（注意点・重要な概念・根拠リンク・確認文・教材欄の埋め込み記法の外）に、
  内部 ID か解析の失敗状態の固定文（「…could not be inferred.」等）が出ている（第 25 周 H10）
- T8 同じトピックで、中央の根拠リンクの見出しと右ペインの根拠リンクが根拠の有無について食い違う
  （見出しが「根拠候補はあります」なのに一覧が空 / 見出しが「対応付けが弱い」なのに一覧に項目が並ぶ）
- T9 AI 書き換えの根拠に使えるものがトピックに無いのに AI が呼ばれた / 欄が変わらないのに画面が「反映しました」と言う
- T10 保存ボタンが押せる表示（course-structure の ``can_save`` が偽でない）だったのに、保存が権限で拒まれた（403）—
  保存できないことが押す前に言われていない（IK-0908 の型。第 25 周 第 2 波）
- T11 採用した図の埋め込みを含めて保存したのに、保存の後に読み直した根拠リンク（evidence_items / linked_figure_ids）に
  その図が無い（IK-0910 の型。runner が保存の後に course-structure を読み直した結果）

学生側（レクチャー・教材）:
- S1 トピック教材の区画の数と、音声の準備状態（audio-status）が数えるスライドの数が違う
- S2 レクチャーの列のスライドの数と、トピック教材の区画の数が違う
- S3 音声が無いのに audio-status に事実文（``note``）が無い

面をまたぐ（教員 → 学生、同じ run の中で同じコース・トピック）:
- X1 原稿スタジオのプレビューが言う枚数（保存した本文で問い合わせたもの）と、保存の後に学生が見た教材の区画数が違う
- X2 保存の後に、学生の audio-status がそのトピックの音声を用意済みと数えている（保存で消えるはず）
- X3 採用して保存した図が、保存の後の学生の教材で図として解決されていない
- X4 学生の教材に出ている図の画像が配信されない（採用した図なら blocked）

周をまたぐ（第 25 周 studio H24・``check_campaign``）: 教員と学生のレーンは別の run に分かれるので、X1 を campaign の
全 run で突き合わせる。同じコース・トピックの、学生が見る前の最後の教員のプレビュー（``studio_split``）と、学生の
教材の区画数を比べる。その間に別の本文の保存（``studio_saved`` の text_sha が違う）があれば突き合わせない。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from uxsim.oracles.findings import FindingFactory
from uxsim.schema import Finding, TranscriptStep

TEACHER_LAYERS = ["lecture_studio", "frontend_admin_ui"]
FIGURE_LAYERS = ["teaching_figures", "frontend_admin_ui"]
STUDENT_LAYERS = ["lecture_player", "frontend_learning_ui"]
CROSS_LAYERS = ["lecture_studio", "lecture_player"]
WM_ALLOWED_KEYS = frozenset({"level", "level_label", "fact", "degraded"})


def _digests(step: TranscriptStep, key: str) -> Iterator[dict]:
    for t in step.http:
        d = (getattr(t, "digest", None) or {}).get(key)
        if isinstance(d, dict):
            yield d


def _make(factory: FindingFactory, step: TranscriptStep, *, oracle: str, severity: str, hypothesis: str,
          layers: list[str], key: str, quote: str = "", server_rows: Optional[dict] = None) -> Finding:
    return factory.make(oracle=oracle, severity=severity, step=step, hypothesis=hypothesis, layers=layers,
                        quote=quote, server_rows=server_rows, fingerprint_key=f"studio:{key}:{step.action_id}")


def _teacher_checks(step: TranscriptStep, factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    for split in _digests(step, "studio_split"):
        for wm in split.get("wm") or []:
            extra = sorted(set(wm.get("keys") or []) - WM_ALLOWED_KEYS)
            if wm.get("numeric_keys") or extra:
                out.append(_make(factory, step, oracle="B", severity="principle", layers=TEACHER_LAYERS, key="wm_numeric",
                                 quote=", ".join(sorted(set(wm.get("numeric_keys") or []) | set(extra))),
                                 hypothesis="スライドの負荷ラベルに、段階ラベル以外の生の数の欄が載っている"))
            if not str(wm.get("level_label") or "").strip():
                out.append(_make(factory, step, oracle="A", severity="inconsistent", layers=TEACHER_LAYERS, key="wm_label",
                                 hypothesis="負荷の高いスライドに付く負荷ラベルに、表示する段階ラベルが無い"))
    for sv in _digests(step, "studio_saved"):
        if sv.get("status") == 403 and sv.get("can_save_shown") is True:
            out.append(_make(factory, step, oracle="A", severity="confused", layers=TEACHER_LAYERS, key="save_forbidden_unannounced",
                             hypothesis="保存ボタンが押せる表示だったのに保存が権限で拒まれ、保存できないことが押す前に言われていない"))
        missing = sv.get("figures_missing_after_refresh")
        if sv.get("status") == 200 and isinstance(missing, list) and missing:
            out.append(_make(factory, step, oracle="A", severity="inconsistent", layers=FIGURE_LAYERS, key="saved_figure_not_in_evidence",
                             server_rows={"figure_ids": missing[:5]},
                             hypothesis="採用した図を含めて保存したのに、保存の後の根拠リンクにその図が出ていない"))
    for st in _digests(step, "studio_stumble"):
        if st.get("numeric_axes"):
            out.append(_make(factory, step, oracle="B", severity="principle", layers=TEACHER_LAYERS, key="stumble_numeric",
                             quote=", ".join(st["numeric_axes"]),
                             hypothesis="つまづきの集計の軸が段階ラベル・レンジではなく生の数で返っている（k-匿名の表示から外れる）"))
    for sg in _digests(step, "studio_suggestions"):
        if sg.get("shown_empty_on_error"):
            out.append(_make(factory, step, oracle="A", severity="confused", layers=FIGURE_LAYERS, key="suggest_error_as_empty",
                             server_rows={"status": sg.get("status")},
                             hypothesis="図の提案を読み込めなかったのに、画面は「このトピックの図の提案はまだありません」と出し、"
                                        "失敗と候補が無いことを区別していない"))
    for rw in _digests(step, "studio_rewrite"):
        if rw.get("figures_restored") and not rw.get("restore_shown"):
            out.append(_make(factory, step, oracle="A", severity="confused", layers=TEACHER_LAYERS, key="rewrite_restore_silent",
                             hypothesis="AI 書き換えで消えた図の埋め込みをサーバが本文の末尾へ戻したのに、画面は図の位置が"
                                        "変わったことを言わない"))
    for ad in _digests(step, "studio_adopt"):
        if ad.get("figure_id") and not ad.get("linked"):
            out.append(_make(factory, step, oracle="A", severity="inconsistent", layers=FIGURE_LAYERS, key="adopt_not_linked",
                             hypothesis="採用した図がトピックの図の登録に入っておらず、AI 書き換えや回収のときに守られない"))
    for ro in _digests(step, "studio_readonly"):
        ids = ro.get("internal_ids") or {}
        phrases = ro.get("failure_phrases") or {}
        if ids:
            out.append(_make(factory, step, oracle="A", severity="confused", layers=TEACHER_LAYERS, key="readonly_internal_ids",
                             quote=", ".join(sorted({x for v in ids.values() for x in v}))[:200],
                             server_rows={"areas": sorted(ids)},
                             hypothesis="原稿スタジオの読み取り専用の区画に、解析層の内部 ID がそのまま出ている"
                                        "（教員が読む名前の材料に内部の表記が使われている疑い）"))
        if phrases:
            out.append(_make(factory, step, oracle="A", severity="confused", layers=TEACHER_LAYERS, key="readonly_failure_text",
                             quote=", ".join(sorted({x for v in phrases.values() for x in v})),
                             server_rows={"areas": sorted(phrases)},
                             hypothesis="原稿スタジオの区画に、解析が失敗した状態を表す英語の固定文が名前や要約として出ている"))
    for cv in _digests(step, "studio_coverage"):
        status, has_items = cv.get("center_status"), cv.get("right_pane_has_items")
        if status in ("weak", "sufficient") and has_items is False and not cv.get("center_units_only"):
            # 第 25 周 studio H22: 学ぶ単位だけのとき（「コース内容生成の前です」の専用の文）は一覧の空が文どおり
            out.append(_make(factory, step, oracle="A", severity="inconsistent", layers=TEACHER_LAYERS, key="coverage_vs_list",
                             server_rows={"center_status": status, "right_pane_has_items": False},
                             hypothesis="中央の根拠リンクの見出しは根拠候補があると言うのに、右ペインの根拠リンクの一覧は空で、"
                                        "同じトピックの根拠の有無を区画ごとに違って言っている"))
        elif status == "missing" and has_items and missing_has_mapped_items(cv):
            out.append(_make(factory, step, oracle="A", severity="inconsistent", layers=TEACHER_LAYERS, key="coverage_vs_list",
                             server_rows={"center_status": status, "right_pane_kinds": cv.get("right_pane_kinds")},
                             hypothesis="中央の根拠リンクの見出しは対応付けが無い・弱いと言うのに、右ペインの一覧には根拠の項目が"
                                        "並び、同じトピックの根拠の有無を区画ごとに違って言っている"))
    for rw in _digests(step, "studio_rewrite"):
        if rw.get("status") == 200 and rw.get("grounding_empty") and rw.get("llm_called"):
            out.append(_make(factory, step, oracle="A", severity="confused", layers=TEACHER_LAYERS, key="rewrite_empty_grounding",
                             hypothesis="書き換えの根拠に使える要素がトピックに何も無いのに AI が呼ばれ、空の材料で書き換えが"
                                        "走った疑い（呼ぶ前に事実文で止めていない）"))
        if rw.get("status") == 200 and rw.get("unchanged") and rw.get("reported_applied"):
            out.append(_make(factory, step, oracle="A", severity="confused", layers=TEACHER_LAYERS, key="rewrite_unchanged_reported",
                             hypothesis="AI 書き換えで欄が何も変わらなかったのに、画面は「提案を反映しました」と言い、変化が無かった"
                                        "ことを言わない"))
    for sv in _digests(step, "studio_saved"):
        # 保存の応答の事実文（notice_shown_after — IK-0877 の是正後）も「後に出た」に数える。音声が無かったと
        # 分かっている保存（audio_existed=False かつ audio_invalidated が偽）は消える音声が無いので当てない
        no_audio = sv.get("audio_existed") is False and not sv.get("audio_invalidated")
        if sv.get("status") == 200 and not sv.get("notice_shown_before") and not sv.get("notice_shown_after") \
                and not no_audio:
            out.append(_make(factory, step, oracle="A", severity="confused", layers=TEACHER_LAYERS, key="save_audio_silent",
                             hypothesis="授業用ドラフトの保存でこのトピックの生成済み音声が消えるのに、保存の前にも後にも"
                                        "その事実文が画面に出ていない"))
    return out


def _student_checks(step: TranscriptStep, factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    for sd in _digests(step, "studio_student"):
        mat = sd.get("material") or {}
        same_topic = str(mat.get("topic_id") or "") == str(sd.get("topic_id") or "")
        pages = mat.get("pages") if same_topic and mat.get("topic_based") else None
        total = sd.get("audio_total_slides")
        if sd.get("status") == 200 and isinstance(pages, int) and isinstance(total, int) and pages != total:
            out.append(_make(factory, step, oracle="A", severity="inconsistent", layers=STUDENT_LAYERS, key="audio_vs_material",
                             server_rows={"material_pages": pages, "audio_total_slides": total},
                             hypothesis="トピック教材の区画の数と、音声の準備状態が数えるスライドの数が違う（図を数えない分割が"
                                        "混ざっている疑い）"))
        seq = sd.get("sequence") if isinstance(sd.get("sequence"), dict) else None
        if seq and seq.get("topic_based") and isinstance(pages, int) and isinstance(seq.get("total_slides"), int) \
                and seq["total_slides"] != pages:
            out.append(_make(factory, step, oracle="A", severity="inconsistent", layers=STUDENT_LAYERS, key="deck_vs_material",
                             server_rows={"material_pages": pages, "lecture_slides": seq["total_slides"]},
                             hypothesis="レクチャーのスライドの数と、同じトピックの教材の区画の数が違い、レクチャーと"
                                        "教材表示のページ境界が一致していない"))
        if sd.get("status") == 200 and sd.get("has_audio") is False and not sd.get("note_present"):
            out.append(_make(factory, step, oracle="A", severity="confused", layers=STUDENT_LAYERS, key="audio_no_note",
                             hypothesis="音声が無いトピックの音声の準備状態に、音声が無いことを言う事実文が付いていない"))
    return out


def _cross_checks(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    out: list[Finding] = []
    saves: dict[tuple[str, str], list[tuple[int, dict]]] = {}
    adopted: dict[str, set[str]] = {}
    for step in steps:
        for sv in _digests(step, "studio_saved"):
            if sv.get("status") == 200:
                saves.setdefault((str(sv.get("course_id")), str(sv.get("topic_id"))), []).append((step.seq, sv))
        for ad in _digests(step, "studio_adopt"):
            if ad.get("figure_id"):
                adopted.setdefault(str(ad.get("course_id")), set()).add(str(ad["figure_id"]))
    for step in steps:
        for sd in _digests(step, "studio_student"):
            key = (str(sd.get("course_id")), str(sd.get("topic_id")))
            before = [sv for seq, sv in saves.get(key, []) if seq < step.seq]
            if not before:
                continue
            last = before[-1]
            mat = sd.get("material") or {}
            same_topic = str(mat.get("topic_id") or "") == key[1]
            pages = mat.get("pages") if same_topic and mat.get("topic_based") else None
            preview = last.get("preview") if isinstance(last.get("preview"), dict) else None
            delivered = delivered_count(preview) if preview else None
            if preview and not preview.get("error") and isinstance(pages, int) \
                    and isinstance(delivered, int) and delivered != pages:
                out.append(_make(factory, step, oracle="A", severity="inconsistent", layers=CROSS_LAYERS, key="preview_vs_material",
                                 server_rows={"studio_preview_slides": delivered, "material_pages": pages},
                                 hypothesis="原稿スタジオのスライド枚数のインジケータ（保存した本文で問い合わせたもの）と、保存の後に"
                                            "学生が見た教材の区画の数が違う（プレビューと配信の分割が一致していない）"))
            if sd.get("status") == 200 and sd.get("has_audio"):
                out.append(_make(factory, step, oracle="A", severity="inconsistent", layers=CROSS_LAYERS, key="audio_after_save",
                                 hypothesis="授業用ドラフトの保存の後も、学生の画面がそのトピックの音声を用意済みとして数えている"
                                            "（保存で音声が消えるはずの経路と食い違う）"))
            if same_topic:
                saved_figs = set(last.get("figure_embeds") or []) & adopted.get(key[0], set())
                missing = sorted(f for f in saved_figs if f not in (mat.get("figure_ids") or [])
                                 or f in (mat.get("raw_figure_embeds") or []))
                if missing:
                    out.append(_make(factory, step, oracle="A", severity="inconsistent", layers=CROSS_LAYERS, key="adopted_unresolved",
                                     server_rows={"figure_ids": missing},
                                     hypothesis="教員が採用して保存した図が、保存の後の学生の教材で図として解決されていない"))
        for fv in _digests(step, "studio_figure_view"):
            if fv.get("status") == 200:
                continue
            is_adopted = str(fv.get("figure_id")) in adopted.get(str(fv.get("course_id")), set())
            out.append(_make(factory, step, oracle="A", severity="blocked" if is_adopted else "confused",
                             layers=CROSS_LAYERS if is_adopted else STUDENT_LAYERS, key="figure_image_missing",
                             server_rows={"status": fv.get("status"), "image_url_kind": fv.get("image_url_kind")},
                             hypothesis=("教員が採用した図の画像が、学生の教材で配信されない" if is_adopted else
                                         "学生の教材に出ている図の画像が読み込めない")))
    return out


def check(steps: list[TranscriptStep], factory: FindingFactory) -> list[Finding]:
    """面 D の規則を transcript にかける（教員側・学生側・面をまたぐもの）。"""
    out: list[Finding] = []
    for step in steps:
        if step.action_id.startswith(("admin.lecture_studio.", "admin.figure_studio.")):
            out += _teacher_checks(step, factory)
        elif step.action_id.startswith("learning.lecture."):
            out += _student_checks(step, factory)
    out += _cross_checks(steps, factory)
    return out


def summarize(steps: list[TranscriptStep]) -> dict[str, Any]:
    """oracle_notes 用: どの突き合わせが材料を持っていたか（数は残すが仮説には使わない）。"""
    keys = ("studio_split", "studio_saved", "studio_adopt", "studio_student", "studio_figure_view", "studio_stumble",
            "studio_readonly", "studio_coverage", "studio_rewrite")
    return {k: sum(1 for s in steps for _ in _digests(s, k)) for k in keys}


def delivered_count(split: Any) -> Any:
    """プレビューの要約から**配信の枚数**（第 25 周第 5 周 H27）。

    ``display_count``（= ``display_segment_count``）は教材欄と読み上げ欄の区切りが合わないとき統合前の教材欄の枚数で、
    製品は ``mismatch: true`` と 1 枚の ``slides`` を返して「1枚に統合して配信されます」と言う。配信の枚数は
    ``delivered_count``（= ``len(slides)``）。旧い digest（delivered_count が無い）は ``1 if mismatch else display_count``。"""
    if not isinstance(split, dict):
        return None
    if isinstance(split.get("delivered_count"), int):
        return split["delivered_count"]
    d = split.get("display_count")
    if not isinstance(d, int):
        return None
    return 1 if split.get("mismatch") else d


def check_campaign(runs: dict[str, list[TranscriptStep]]) -> list[dict[str, Any]]:
    """周をまたぐ X1（第 25 周 studio H24・第 4 周）。``runs`` は run_id → transcript。

    返すのは突き合わせの記録（仮説の材料 — 数は ``server_rows`` 相当の欄にだけ置く）。``mismatch`` が真の組が
    「教員のプレビューの枚数と学生の区画数が違う」候補。同じ run の中の組は ``_cross_checks`` が扱うので除く。"""
    teacher: list[tuple[str, str, int, dict]] = []  # (at, run, seq, split or saved)
    for run_id, steps in runs.items():
        for st in steps:
            for sp in _digests(st, "studio_split"):
                if sp.get("scope") == "topic" and isinstance(delivered_count(sp), int):
                    teacher.append((st.at, run_id, st.seq, {"kind": "split", **sp}))
            for sv in _digests(st, "studio_saved"):
                if sv.get("status") == 200:
                    teacher.append((st.at, run_id, st.seq, {"kind": "saved", **sv}))
    teacher.sort(key=lambda x: x[0])
    out: list[dict[str, Any]] = []
    for run_id, steps in runs.items():
        for st in steps:
            for sd in _digests(st, "studio_student"):
                mat = sd.get("material") or {}
                key = (str(sd.get("course_id") or ""), str(sd.get("topic_id") or ""))
                if not mat.get("topic_based") or str(mat.get("topic_id") or "") != key[1]:
                    continue
                before = [t for t in teacher if t[0] < st.at and t[1] != run_id
                          and (str(t[3].get("course_id") or ""), str(t[3].get("topic_id") or "")) == key]
                splits = [t for t in before if t[3]["kind"] == "split"]
                if not splits:
                    continue

                def _saved_text(t) -> bool:
                    if "matches_stored" not in t[3]:
                        return True  # 旧い digest（第 6 周より前）は区別できないので従来どおり比べる
                    sha = t[3].get("text_sha")
                    return bool(t[3].get("matches_stored")) or any(
                        b[3]["kind"] == "saved" and b[3].get("text_sha") == sha for b in before)

                # 第 25 周 第 6 周 H31: 比べる相手は保存済みの本文（保存したもの・開いた時点の本文）のプレビューだけ。
                # 保存できない教員の書き換えの案（te-04 seq6）を学生の区画数と比べ、3 組とも食い違いに数えていた
                drafts = [t for t in splits if not _saved_text(t)]
                saved_splits = [t for t in splits if _saved_text(t)]
                if drafts and (not saved_splits or drafts[-1][0] > saved_splits[-1][0]):
                    d = drafts[-1]
                    out.append({"course_id": key[0], "topic_id": key[1], "teacher_run": d[1], "teacher_seq": d[2],
                                "student_run": run_id, "student_seq": st.seq, "teacher_saved_this_text": False,
                                "preview_slides": delivered_count(d[3]), "material_pages": mat.get("pages"),
                                "collapsed": bool(d[3].get("mismatch")), "draft_only": True, "mismatch": False})
                if not saved_splits:
                    continue
                last = saved_splits[-1]
                later_saves = [t for t in before if t[3]["kind"] == "saved" and t[0] > last[0]
                               and t[3].get("text_sha") != last[3].get("text_sha")]
                if later_saves:
                    continue  # プレビューの後に別の本文が保存された — この組は比べない
                saved = any(t[3]["kind"] == "saved" and t[3].get("text_sha") == last[3].get("text_sha") for t in before)
                out.append({"course_id": key[0], "topic_id": key[1], "teacher_run": last[1], "teacher_seq": last[2],
                            "student_run": run_id, "student_seq": st.seq, "teacher_saved_this_text": saved,
                            "preview_slides": delivered_count(last[3]), "material_pages": mat.get("pages"),
                            # 統合（mismatch）の組は記録に残すが、比べるのは配信の枚数どうし（H27）
                            "collapsed": bool(last[3].get("mismatch")), "draft_only": False,
                            "mismatch": isinstance(mat.get("pages"), int) and mat["pages"] != delivered_count(last[3])})
    return out


def check_campaign_saves(runs: dict[str, list[TranscriptStep]],
                         fetch_total: Callable[[str, str], Any]) -> list[dict[str, Any]]:
    """第 25 周 第 6 周 H32: 教員が保存したトピックごとに、学生の講義 API の ``total_slides`` を直接引いて比べる。

    学生レーンが同じトピックを見ないと ``check_campaign`` は組を作らない（t16・t17 が cross に 1 組も出なかった）。
    ``fetch_total(course_id, topic_id)`` は読み取りだけの 1 回（int か、引けなければ None）。保存ごとではなく
    (course, topic) ごとの最後の保存と比べる（後の保存が前の保存を上書きする）。"""
    last: dict[tuple[str, str], tuple[str, str, int, dict]] = {}
    for run_id, steps in runs.items():
        for st in steps:
            for sv in _digests(st, "studio_saved"):
                if sv.get("status") != 200:
                    continue
                key = (str(sv.get("course_id") or ""), str(sv.get("topic_id") or ""))
                if not all(key):
                    continue
                if key not in last or last[key][0] <= st.at:
                    last[key] = (st.at, run_id, st.seq, sv)
    out: list[dict[str, Any]] = []
    for (course_id, topic_id), (_, run_id, seq, sv) in sorted(last.items()):
        preview = sv.get("preview") if isinstance(sv.get("preview"), dict) else None
        delivered = delivered_count(preview) if preview else None
        try:
            total = fetch_total(course_id, topic_id)
        except Exception:  # noqa: BLE001 — 引けなかった事実だけを残す
            total = None
        total = total if isinstance(total, int) and not isinstance(total, bool) else None
        out.append({"kind": "student_api", "course_id": course_id, "topic_id": topic_id, "teacher_run": run_id,
                    "teacher_seq": seq, "preview_slides": delivered, "student_total_slides": total,
                    "collapsed": bool((preview or {}).get("mismatch")), "unavailable": total is None,
                    "mismatch": isinstance(total, int) and isinstance(delivered, int) and total != delivered})
    return out


def reattached_topics(runs: dict[str, list[TranscriptStep]]) -> dict[tuple[str, str], tuple[str, str, int]]:
    """第 25 周 第 10 波（studio H43）: ［付録をまとめて付け直す］（``admin.lecture_studio.reattach_appendix_all``）が 2xx で
    保存したトピック。digest ``studio_reattach_all`` の ``applied_topic_ids`` と ``outcome_topic_ids``（応答の outcomes の
    topic_id）から (course_id, topic_id) → (時刻, run_id, seq)。後の手が前の手を上書きする。純関数。"""
    out: dict[tuple[str, str], tuple[str, str, int]] = {}
    for run_id, steps in runs.items():
        for st in steps:
            for t in st.http:
                d = (getattr(t, "digest", None) or {}).get("studio_reattach_all")
                status = getattr(t, "status", None)
                if not isinstance(d, dict) or not (isinstance(status, int) and 200 <= status < 300):
                    continue
                cid = str(d.get("course_id") or "")
                tids = [str(x) for x in list(d.get("applied_topic_ids") or []) + list(d.get("outcome_topic_ids") or []) if x]
                for tid in tids:
                    key = (cid, tid)
                    if cid and (key not in out or out[key][0] <= st.at):
                        out[key] = (st.at, run_id, st.seq)
    return out


def check_campaign_reattach(runs: dict[str, list[TranscriptStep]], fetch_total: Callable[[str, str], Any], *,
                            exclude: Optional[set] = None) -> list[dict[str, Any]]:
    """一括付け直しで保存したトピックごとに学生の講義 API の ``total_slides`` を 1 回読む（H43）。所有者側の枚数は
    一括の手の応答に無いので ``preview_slides`` は None（審判が版の写しか preview-split で補う）。``exclude`` は
    ``check_campaign_saves`` が既に組にした (course_id, topic_id)。"""
    out: list[dict[str, Any]] = []
    for (course_id, topic_id), (_, run_id, seq) in sorted(reattached_topics(runs).items()):
        if exclude and (course_id, topic_id) in exclude:
            continue
        try:
            total = fetch_total(course_id, topic_id)
        except Exception:  # noqa: BLE001
            total = None
        total = total if isinstance(total, int) and not isinstance(total, bool) else None
        out.append({"kind": "student_api", "source": "reattach_all", "course_id": course_id, "topic_id": topic_id,
                    "teacher_run": run_id, "teacher_seq": seq, "preview_slides": None, "student_total_slides": total,
                    "collapsed": False, "unavailable": total is None, "mismatch": False})
    return out


def missing_has_mapped_items(cv: dict) -> bool:
    """第 25 周 第 7 周 studio H35: 根拠の状態が missing のとき、右ペインの項目のうち「対応付けられていない候補」の見出しの
    下に並ぶものは設計どおり（IK-1010）。それ以外の解析の項目（見出しなし・「論文の解析から拾った根拠」）があるときだけ食い違い。
    区画ごとの件数（``right_pane_sections``）が無い古い digest は従来どおり項目の有無で読む。"""
    sections = cv.get("right_pane_sections")
    if not isinstance(sections, dict):
        return True
    return int(sections.get("analysis") or 0) > 0


def check_campaign_dir(campaign_dir: Path,
                       fetch_total: Optional[Callable[[str, str], Any]] = None,
                       out_name: str = "campaign_studio_cross.jsonl") -> list[dict[str, Any]]:
    """``runs/<campaign>`` の全 run を読んで ``check_campaign`` をかけ、``campaign_studio_cross.jsonl`` に書く。

    ``fetch_total`` を渡すと、保存したトピックごとに学生の講義 API を直接引く組（``kind: student_api``・H32）も足す。"""
    import json

    from uxsim.oracles.findings import load_transcript

    runs = {d.name: load_transcript(d) for d in sorted(Path(campaign_dir).iterdir())
            if d.is_dir() and (d / "transcript.jsonl").is_file()}
    rows = [{"kind": "student_view", **r} for r in check_campaign(runs)]
    if fetch_total is not None:
        rows += check_campaign_saves(runs, fetch_total)
    with (Path(campaign_dir) / out_name).open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


def student_persona_of(runs: dict[str, list[TranscriptStep]]) -> str:
    """campaign の学生ペルソナ（最初に現れた ``st-``）。審判の読み取りのアカウントに使う。"""
    for steps in runs.values():
        for st in steps:
            if str(st.persona_id or "").startswith("st-"):
                return str(st.persona_id)
    return ""
