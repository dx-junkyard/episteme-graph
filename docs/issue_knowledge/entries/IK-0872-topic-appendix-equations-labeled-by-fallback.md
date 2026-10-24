---
id: IK-0872
title: "トピック教材末尾の付録「この節で使う数式」が、式の名前を TeX のラベルキー（eq:modified_dcf）や内部 ID で書き、説明を固定文「この節の説明で参照する中心的な関係式です。」や英語の要約で埋め、本文（と生成した AI）が使わないと判断した式まで足し直す"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "授業用ドラフトの生成で、本文が使う式を学生が見返せる一覧を教材の末尾に添える"
  layers: [course_builder, lecture_studio, learner_experience_b]
classification:
  axes:
    processing: [wording, logic]
    structure: [responsibility]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸 wording: backend/core/course_content_builder.py の _appendix_equation_label は label → 印字番号 の順で名前を選ぶため、
    TeX の \label キー（eq:modified_dcf / eq:acf）が内部 ID の正規表現に当たらずそのまま名前になる。印字番号の判定は label が数字の
    ときだけ効くので、label が「6」の式は「式 (6)」ではなく「6」になる。_equation_material_description は summary 等が無いと
    固定文「この節の説明で参照する中心的な関係式です。」を返し（5914 行）、根拠の無い「中心的」を全式に同じ文で付ける。
    処理軸 logic: _ensure_required_equations_in_material（5821〜5852 行）は content_blocks の式を「本文に埋め込みが無い」だけを条件に
    足し直すので、生成した AI が注意点で「式ではない・断片なので使わない」と書いた式や、本文が「まだ扱わない」と書いた式が付録に
    戻る。構造軸 responsibility（medium）: 付録の中身を決める責務が、本文を書いた側ではなく決定論の後付け側にあり、本文の判断と
    食い違っても後付けが勝つ。接続軸 none（値は正しく届いている）。統制軸 none。保存済みの古い付録は生成時のまま残り、学習者側は
    表示投影で内部 ID が「（本文を特定できない要素）」に置き換わるだけ（意味のある名前にはならない）。
generalization:
  level: repo_pattern
  general_form: "生成物に決定論で付け足す一覧が、名前と説明を材料の有無に応じた代替物で埋め、本文の判断と食い違う項目まで足す"
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    教員ペルソナが原稿スタジオで「この節で使う数式」に内部 ID と英語の式説明、注意点と矛盾する埋め込みを、学生ペルソナが教材末尾で
    「eq:modified_dcf: この節の説明で参照する中心的な関係式です。」が 4 式並ぶのを記した。付録の組み立て（_ensure_required_equations_in_material
    → _appendix_equation_label / _equation_material_description）を辿った。run: uxsim/runs/c-astro-r25-studio-r1/20261022T123418Z-31804
    （seq6・14）・uxsim/runs/c-astro-r25-studio-r1/20261022T125729Z-44102（seq6・12）・uxsim/runs/c-astro-r25-studio-r1/20261022T125944Z-45915（seq6）。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "付録の名前の段を 1 箇所（_appendix_equation_label）で「印字番号（数字だけの label も式 (N)）→ 人が付けた名前（TeX のラベルキー・内部 ID・式 ID と同じ文字列は除く）→ 論文の式番号形の ID → 短い原文 → 「この節の式」」に揃え、名前も本体も無い式は行を作らない。説明は根拠のある要約だけ（英語の読み下し・「推定できなかった」の要約・固定文「中心的な関係式です」を使わない — 無ければ説明の行を出さない）。付録の対象はトピックに結ばれた式（linked_equation_ids）か本文が言及する式に限り、生成した注意点が触れた式は足さない。保存済みの旧い付録の読み時の付け直しはしていない（スライド境界・音声キャッシュの番号がずれるため。次の生成・書き換えで付け直される）。"
  landed_in:
    - "backend/core/course_content_builder.py"
    - "backend/tests/test_lecture_studio_round25.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "付録・根拠リンク・原稿スタジオの投影が同じ名前の段（_appendix_equation_label）を通るようにし、面ごとに名前の規則を書かない。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の固定コース（6be229e1）で付録を再生成した表示は未確認（第 2 周の st-08 で確かめる）"
      - "保存済みの旧い付録は次の生成まで旧い名前のまま"
related: [IK-0555, IK-0454, IK-0427, IK-0429]
view_of: []
history: []
---

## 課題

症状: 学生の教材末尾に「eq:modified_dcf: この節の説明で参照する中心的な関係式です。」「eq:acf: …中心的な関係式です。」が並び、
どれが中心か読めない。本文は「ここではまだ DCF 法には入らず」と書くのに DCF の式が並ぶ。教員の編集欄では同じ付録に
「eq_blk_003_0066: S = integral d^4 x sqrt(-g) …」「6: S equals the spacetime integral …」と内部 ID・英語の要約が出て、注意点が
「式ではないので埋め込まなかった」と言う式の埋め込みが残る。原因: 付録の名前と説明が材料の有無に応じた代替物（ラベルキー・
固定文・英語の要約）で埋まり、付録の対象が本文の判断ではなく content_blocks の有無で決まる。

## 発見の観点

ペルソナの通し受講（reproduction）と付録の組み立ての追跡（trace_walk）。

## 解決の観点

解決済み（2026-10-23）。名前は人が読む表記（印字番号・短い原文）だけ、説明は根拠のあるものだけにし、対象は本文が参照する式に限る。

## 一般化

決定論の後付けが生成物の判断を上書きする型。確かめ方は、label が TeX キーの式・summary の無い式・本文が扱わない式を含む
トピックで付録を生成し、名前にキーが出ず、固定文が出ず、扱わない式が出ないことを見ること。

第 2 周の再演: 残存（保存済みの付録は読み時に付け直さない判断のため、固定コース 6be229e1 の t0 の旧形式「（本文を特定できない要素）: この節の説明で参照する中心的な関係式です。J=3」が学生に届き続け、t16 の編集欄にも `eq_blk_003_0066: S = integral …` が残る。生成し直した書き換えでは付録が外れた = 生成時の是正は効く。run: uxsim/runs/c-astro-r25-studio-r2/20261023T020907Z-69975 seq6・20261023T021041Z-70961 seq11。付け直しの扱いはオーナー判断の候補 — uxsim/runs/round25/judge_studio_r2.md §6）。

第 2 波で是正（2026-10-23）: 保存済みの付録は読み時に付け直さない判断のまま（オーナー判断の推奨どおり）、原稿スタジオに明示の操作 [付録を付け直す]（`POST .../course-topics/{topic_id}/appendix/reattach`・DB 非変更・生成時と同じ `_ensure_required_*` を通す `course_content_builder.reattach_generated_appendix`。押す前に保存で音声が作り直しになりスライドの区切りが変わり得ることを確かめる）と、保存済みの付録がいまの根拠から作る形と違うトピックに事実文（course-structure の `appendix_state`）を置き、G層 `course.topic_appendix_stale`（optional・道案内のみ）を足した。教員が付録を消したトピックは対象外。固定コースの旧付録が付け直されるのは教員が操作して保存したとき（砂場では未実演）。ガードレール `backend/tests/test_lecture_studio_round25_r2.py`。

第 3 周の再演: 残存（予定どおり — 保存済みの付録は教員が［付録を付け直す］で保存するまで旧形式。固定コース 6be229e1 の t0 は editor の te-04 には付け直せず（押せない理由の文は出た）、付録の「eq_eqcand_inline_blk_…: この節の説明で参照する中心的な関係式です。」が残る。この式は原文「13CO (J=3–2)」の J=3 を式として切り出したもの（A層の式候補の誤り — 付録の形式とは別の根）。所有コース 1bad7d9f の t0 では事実文と［付録を付け直す］が出て、AI の書き換えが付録を外した。run: uxsim/runs/c-astro-r25-studio-r3/20261023T071920Z-61773 seq5・13）。
