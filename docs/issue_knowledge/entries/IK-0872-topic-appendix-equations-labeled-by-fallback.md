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
    - "backend/tests/test_lecture_studio_round25_r5.py"
    - "backend/tests/test_lecture_studio_round25_r9.py"
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
history:
  - date: '2026-10-31'
    field: resolution.note
    from: "本文に現れない設定値（N = 15）は付録に足さない（_is_unmentioned_parameter_value）"
    to: >-
      第 25 周 第 9 波（t11）: 記号の言及の判定を `_text_mentions_bare_symbol` にし、添字・上付きの付いた形（`N_c`）と
      書体・修飾命令の中（`\\mathcal{N}`）は別の記号として数えない。学習回数 `N = 15` が本文の `N_c` / `\\mathcal{N}_{TOV}` を
      理由に付録「この節で使う数式」に選ばれていた。砂場の写しで t11 / t12 の付録が「外す」判定（付録が古い一覧に再び出る）になる
    reason: "第 25 周 面 D 第 9 周の判定（t11 の付録が本文に出ない N = 15 の 1 項目）"
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

第 4 周の再演: 残存（2 つの根）。①保存済みの旧い付録は予定どおり残る — 固定コース 6be229e1 は所有者が付け直していないので、学生 st-10 に t0 の「（本文を特定できない要素）: この節の説明で参照する中心的な関係式です。J=3」（本文は「J=3–2 は式ではない」と書く）と、t1 の座標目盛り 62°42'00" など 5 行（本文の注は「誤って取り込まれたので載せていない」と書く。1 行は中身が空）が届き、講義の最後のスライドにも同じ付録が出た。教材の中で本文と付録が真っ向から矛盾し、学生は「どちらを信じればいいか分からない」と記した。②付け直した付録にも残る — 教員 te-02 が自分のコース 0d70b5d0 の t3 で［付録を付け直す］を押すと、説明の行が英語の要約（「Background dark-energy pressure is defined through …」）になり（_equation_material_description は summary を言語を問わず採る — 英語を除くのは読み下しだけ）、注意点が「AI が復元した式なので埋め込んでいない」と書く状態方程式の式 P = w_DE ρを付録に埋め込み直した（仮説: 注意点の言及の判定 _text_mentions_equation が TeX の書き方の違い — \mathrm と \rm 等 — で一致を取れない。復元の印も付録の判定に入っていない）。run: uxsim/runs/c-astro-r25-studio-r4/20261024T234030Z-30759 seq6・11・13 / uxsim/runs/c-astro-r25-studio-r4/20261024T232812Z-98850 seq6・8。是正案: 説明は日本語の文のときだけ採る・付録の対象から復元の印のある式を外す（または印を付けて出す）・学習者の投影では名前の無い付録の行（「（本文を特定できない要素）」）を出さない（行数は変えずに空にするなら境目も音声も動かない）。

第 4 波で是正（2026-10-25）: ②付け直しの付録 — `_equation_material_description` は英語の要約も採らない（読み下しと同じ規則で日本語の文のときだけ）、`_ensure_required_equations_in_material` は AI の復元した式（印の正本 `learner_context_common.equation_latex_is_reconstructed`・表示の LaTeX が原文と違う式）を本文が自分で触れていない限り付録に足さない（IK-0977 と束ねた）。①保存済みの旧い付録が学生に届く件は「読み時に付け直さない」判断のまま是正していない（境目・音声が動くため、名前の無い行を学習者の投影で空にする案も第 4 波では採らなかった — 所有者の［付録を付け直す］と G層 `course.topic_appendix_stale` が出口）。ガードレール `backend/tests/test_lecture_studio_round25_r4.py`。

第 5 周の再演（2026-10-27）: ①**STILL（予定どおり）** — 所有者が付け直していない t0 の付録（J=3 を「中心的な関係式」）と t17 の英語の図題「FIG. 1. Solutions for µ from Equation 9 for several values of c2」が学生に届く。②**付け直しの新しい形** — te-01 が t16 で［付録を付け直す］を押すと英語の説明と内部 ID は消えたが、付録は「- この節の式」+ L_4 の定義式 1 つになり、項目名が何の式かを言わず、本文の「個々の L_i の形はここでは追わず」と食い違う。同じ画面の注意点（生成時の古い文）は付け直した式の範囲を「内容が載っていないので埋め込まない」と言う（IK-0948 の古い注意点と重なる）。是正案: 付け直しの対象は本文が触れた式に限る・名前の無い式は付録に足さない（または「本文で参照していない式」と言う）。run: uxsim/runs/c-astro-r25-studio-r5/20261027T070623Z-23494 seq10、uxsim/runs/c-astro-r25-studio-r5/20261027T072111Z-9659 seq6・11。

第 5 波で是正（2026-10-27・②）: 付録の名前の段に「定める記号」（`$記号$ を定める式`・`_equation_defined_symbol`）を足し、どの段でも名前が付かない式（旧「この節の式」）は付録に足さない。本文が同じ文の中で扱わない語（追わ / 扱わ / 立ち入ら / 触れない / 省略 / 割愛）と共にその式の印字番号・記号・添字の族に触れていれば足さず（`_text_declines_equation` — t16 の「個々の L_i の形はここでは追わず」で L_4 を足さない）、本文に既に埋め込んだ式と同じ記号を定める式（同じ作用の式の断片 eq_blk_003_0066）も足さない。生成時と［付録を付け直す］が同じ関数を通る。①（保存済みの旧い付録）は判断のまま。未確認: 固定コースの t16 で付け直した付録が実機でどうなるか。

第 6 周の再演（面 B の学生・2026-10-27）: 残存。st-05 の t13（1bad7d9f）の「この節で使う数式」の見出しが TeX のラベル「eq:total_loss:」「eq:tov_residual:」で、主結果のトピックに損失関数の式が並ぶ。st-06 の t2 は「eq:dcf」「eq:modified_dcf」「eq:mod_dcf」「eq:acf」と「（本文を特定できない要素）」が全行同文「この節の説明で参照する中心的な関係式です。」。run: uxsim/runs/c-r25-doubt-r6/20261027T123134Z-9179（seq6）/ uxsim/runs/c-r25-doubt-r6/20261027T123621Z-11841（seq6）。

第 6 周の再演（2026-10-27）: ① **STILL**（保存済み・付け直していないトピック）: t0 の付録「（本文を特定できない要素）: この節の説明で参照する中心的な関係式です。J=3」が本文「式ではありません」と食い違い、t1 は本文の注「図の座標目盛りが誤って取り込まれたもので教材には載せていません」の直下に 62°42'00" 等 5 件が「中心的な関係式」で並ぶ（講義の最後の枚も同じ）。t11 は「eq_eqcand_inline_…: N equals 15」（学生には「（本文を特定できない要素）」）、t10 は「4:」。② **GONE**: t16 の付け直しは名前の無い式を足さず、足す式が無いので付録ごと出なかった（第 5 波の見込みどおり）。ただし知らせが固定文のまま → IK-1032。run: uxsim/runs/c-astro-r25-studio-r6/20261027T122226Z-3522 seq9、uxsim/runs/c-astro-r25-studio-r6/20261027T123457Z-10833 seq6・11・14・16。

第 7 周の再演（2026-10-28）: ① **STILL（予定どおり・保存済み）**。t0「J=3」が「中心的な関係式」、t1 の座標目盛り 4 件 + 本文なし 1 件、t11 の「1: N_EOS: x = log(…) maps to …」「（本文を特定できない要素）: N equals 15」（英語の読み下し）。第 6 波の所有者の［付録をまとめて付け直す］は 15 トピックを提示したが、場面に押す手が無かった。付録の古いトピックの知らせの読みにくさは IK-1054。run: uxsim/runs/c-astro-r25-studio-r7/20261027T172130Z-55411 seq6・11・16、uxsim/runs/c-astro-r25-studio-r7/20261027T173357Z-61213 seq19。

第 8 周の再演（2026-10-28）: **STILL（保存済み・予定どおり）**。所有者の［付録をまとめて付け直す］は今周も押されていない。学生 API で t0 の付録「（本文を特定できない要素）: …中心的な関係式です。」+「J=3」、t1 の同じ文 ×5 + 座標目盛り（本文の注「座標目盛りが誤って式として取り込まれた…載せていません」と自己矛盾）、t11 の項目名が英語の読み下し（「1: N_EOS: x = log(…)」「N equals 15」・番号 1 と 3）。run: uxsim/runs/c-astro-r25-studio-r8/20261027T224552Z-353 seq6・14・16、uxsim/runs/c-astro-r25-studio-r8/20261027T224929Z-2551 seq19。

## 第 9 周（第 25 周・面 D 第 9 周、2026-10-31・judge_studio_r9）

**所有者の［付録をまとめて付け直す］の初観察（判断 1）— t0・t1 は GONE、t11 は形を変えて STILL**。te-01 が 15 トピックをまとめて付け直し、場面の手で版 5 を発行した後、学生 API で t0 の付録（`eq_eqcand_inline_…: …中心的な関係式です。` = J=3）と t1 の付録（座標目盛り 5 件）は見出しごと消え、本文の注（「J=3–2 は輝線のラベルで式ではない」「座標目盛りが誤って式として取り込まれた」）と食い違わなくなった。t11 は英語の項目名（`N_EOS: x = log(…) maps to …` ほか）と欠番が消え、「$N$ を定める式 $N = 15$〔AI復元〕」の 1 項目に置き換わったが、この $N = 15$ は「15 回の独立した学習の実行」の数で、TOV の課し方のトピックの本文に出ない（st-08 が記号を引いて「場違い」と読んだ）。原因: 付録の項目はトピックの `linked_equation_ids` / `content_blocks` から取り、本文で使うかを見ない（t11 の linked に `eq_eqcand_inline_blk_f141a051_820_…`（N = 15）と M_max の文中候補が入っている）。名前の段は直ったが、項目の選び方（本文が使わない式まで足す）の残り。版 4 ⇄ 版 5 の写しの比較で付け直しの差は 15 トピックの本文にだけ出た。run: uxsim/runs/c-astro-r25-studio-r9/20261031T012109Z-34801 seq5、uxsim/runs/c-astro-r25-studio-r9/20261031T015559Z-39886 seq6・13、uxsim/runs/c-astro-r25-studio-r9/20261031T020200Z-43015 seq6・10・12。知らせが結果を言い分けない件は IK-1080。

## 第 10 周（第 25 周・面 D 第 10 周、2026-10-31・judge_studio_r10）

**GONE（t11）**。第 9 波の是正で 3.2（t11）・3.3（t12）が「付録が古い」一覧に戻り、te-01 の［付録をまとめて付け直す］で t11 は付録の節ごと外れ（いまの根拠から載せる式・図が無い）、t12 は  = 15$ が消えて「この節で参照する図」（FIG. 1）だけが残った（版 5 ⇄ 版 6 の写しで確認）。st-08 の t11（版 7）は「次のトピックへ」で終わり、講義 3 / 3 の最後の枚にも付録が出ない。run: uxsim/runs/c-astro-r25-studio-r10/20261031T052922Z-65584 seq4・5、uxsim/runs/c-astro-r25-studio-r10/20261031T054537Z-74536 seq6・10。

## 第 11 周（第 25 周・面 D 第 11 周、2026-10-31・judge_studio_r11）

**STILL（保存物・コース 1bad7d9f）**。doubt 面の st-08 が 1bad7d9f の t2 で「### この節で使う数式」の項目名 `eq:dcf` / `eq:modified_dcf` / `eq:mod_dcf` / `eq:acf` と「（本文を特定できない要素）」、全行同じ「この節の説明で参照する中心的な関係式です。」を読んだ（製品頭脳の所見として面 D で判定）。現行の `_appendix_equation_label` / `_equation_material_description` はこの形を作らない — 旧規則の保存物。`lecture_studio_state.topic_appendix_state` で 1bad7d9f を計算すると 20 トピック中 16 が「付録が古い」で、所有者の［付録をまとめて付け直す］を待つ（保存物は自動で書き直さない）。面 D の固定コース 6be229e1 の t2 に付録は無い。run: uxsim/runs/c-r25-doubt-r11/20261031T102036Z-2368 seq6。
