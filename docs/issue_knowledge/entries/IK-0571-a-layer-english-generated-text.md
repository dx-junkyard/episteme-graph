---
id: IK-0571
title: "A層由来の英語の生成文（⚓ 本文・summary・teaching_takeaway・図キャプション）が日本語の学習画面にそのまま出る"
status: open
recorded_at: 2026-09-30
resolved_at: null
sources:
  - docs/architecture/persona_enactment_testing_design.md §18.7
feature_context:
  realizing: "ペルソナ通し受講 第 15 周（是正後の再演・c-astro-structure-30）の場面"
  layers: [pipeline_a, learner_experience_b]
classification:
  axes:
    processing: [unknown]
    structure: [representation]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: low
    structure: low
    connection: low
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 構造課題。表示側で隠すか A層で日本語を生成するか、翻訳の置き場が未定。 原因の性質で座標を置いた（症状の場所ではなく）。 未解決のため座標は暫定。
generalization:
  level: general
  general_form: "生成物の言語を表示先の言語と照合する段が無い"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction]
  note: "第 15 周の頭脳メモ（STILL / NEW）と審判の所見から。"
resolution:
  perspective: [pending]
  note: >-
    一部着手（2026-10-01）。run options `language` を paper_skeleton / thesis_reconstruction /
    narrative_annotator / component_assembly の生成文に運んだ（未指定なら prompt 不変・LLM 回数不変）。
    component の label / summary と equation_semantics の summary は、決定論の後段が英語キーワードで
    構造を導くため対象外（generation_language_design.md §2 GL4）。既存教材は再解析が要り、砂場での
    確認は未実施のため open のまま。
    追補（2026-10-02）: component の summary を英語キーワードで読んで構造を導いていた後段 3 箇所
    （internal_flow の relation / operation の補完 / support_role の補完）を、同じコールで宣言される
    語彙値（operation / primary_operation / responsibility_type）を先に読む形に寄せ、summary を
    言語指定の対象に加えた（label は stable_key と名前照合の材料なので不変）。図表の teaching_takeaway
    （決定論テンプレート）も言語に合わせた。式の summary は導出 step の operation を決める後段に
    代わる語彙値が無いため対象外のまま（generation_language_design.md §6）。
    追補（2026-10-31・第 25 周 第 10 波）: 記号の吹き出しの定義文の「（原文）」（第 9 波）を単体テストで固定（英語の定義文に印・
    和文には付けない）。定義の文が無い記号は「この論文には定義の記述が見つかりませんでした。」（`FACT_NO_DEFINITION`・既存）が
    出ることを確認した（第 10 周の記号 P はこの形 — 定義の「（原文）」は未観察のまま）。「別の論文にも同じ字形」の論文名は
    題名の正本 `document_refs.resolve_paper_titles`（同じ論文の別の取り込み → arXiv の写し・外部 API なし）を通し、arXiv 番号
    だけの題名を読める題名に置き換える（写しが無ければ番号のまま）。
  landed_in:
    - backend/tests/test_lecture_studio_round25_r10.py
    - src/episteme_graph/agents/generation_language.py
    - backend/core/document_pipeline/orchestrator.py
    - docs/features/generation_language_design.md
    - src/episteme_graph/agents/component_assembly/enrichment.py
    - src/episteme_graph/agents/component_assembly/component_refiner.py
    - src/episteme_graph/agents/component_assembly/claim_centered_planner.py
    - src/episteme_graph/agents/figure_table_semantics/agent.py
    - src/tests/agents/test_generation_language_structure.py
    - backend/core/text_excerpt.py
    - backend/core/component_context.py
    - backend/core/symbol_lookup.py
    - backend/tests/test_lecture_studio_round25_r7.py
    - backend/tests/test_lecture_studio_round25_r8.py
    - frontend/public/js/app.js
    - backend/tests/test_lecture_studio_round25_r9.py
related: [IK-0546, IK-0540]
view_of: []
history:
  - date: '2026-10-01'
    field: resolution.landed_in
    from: "[]"
    to: >-
      src/episteme_graph/agents/generation_language.py / {paper_skeleton,thesis_reconstruction,
      narrative_annotator,component_assembly}/{prompt,agent}.py / orchestrator._run_language・
      _language_run_kwargs
    reason: >-
      生成言語を 4 agent に運んだ。component の summary / label・式の summary は構造を導く後段が読むため
      未対応で、砂場の再解析で確かめるまで open のまま
  - date: '2026-10-02'
    field: resolution.landed_in
    from: "generation_language.py / orchestrator / generation_language_design.md"
    to: >-
      + component_assembly/{enrichment,component_refiner,claim_centered_planner}.py（語彙値を先に読む）/
      figure_table_semantics/agent.py（teaching_takeaway の言語）/ test_generation_language_structure.py
    reason: >-
      後段を語彙値に寄せて component の summary を言語指定の対象に加えた。式の summary は置き換える
      語彙値が無く対象外のまま。砂場の再解析は未実施なので open のまま

  - date: '2026-10-31'
    field: resolution.landed_in
    from: "部品の文脈・主張・使われ方の例文に「（原文）」の印"
    to: >-
      + 記号の吹き出しの定義文（symbol_lookup の definition.original_mark・app.js の吹き出しが印を描く）。
      上位・下位（graph のノード名）と概要は対象外のまま
    reason: "第 25 周 面 D 第 9 周 st-08（記号 N の定義「Across N = 15 independent training runs」に印が無い）"
---

## 課題

A層由来の英語の生成文（⚓ 本文・summary・teaching_takeaway・図キャプション）が日本語の学習画面にそのまま出る。

## 発見の観点

第 15 周（是正後の再演）の頭脳メモと審判の所見から。

## 解決の観点

未解決。構造課題。表示側で隠すか A層で日本語を生成するか、翻訳の置き場が未定。

2026-10-02: 後段の依存表を作り（`generation_language_design.md` §6.1）、語彙値に置き換えられる 3 箇所を置き換えて component の summary を対象に加えた。置き換えられないのは式の summary（導出 step の operation を決める）と概念照合（意味の照合）。

2026-10-01: A層で生成言語を指定する側を選び、run options `language` を 4 agent（paper_skeleton / thesis_reconstruction / narrative_annotator / component_assembly）の生成文へ運んだ。component の summary / label と式の summary は後段の英語キーワード照合が構造を決めるため対象外で、残る（`generation_language_design.md` §5）。

## 一般化

原因は仮説（未確定）。
生成物の言語を表示先の言語と照合する段が無い。

## 第 3 周の再演（第 25 周・面 A 第 3 周、2026-10-23）

残存。学生の部品の文脈の「この論文での位置づけ」が英語の定型文（「Connects theory quantities to the observables the thesis uses.」）、
主張の excerpt が英語、記号 F_EOS の定義が英語の切れ端（「the EOS feature vector ... to stellar profiles」）。
run: uxsim/runs/c-astro-r25-share-r3/20261023T033153Z-19784 seq9・seq11。

## 第 4 周の再演（第 25 周・面 A 第 4 周、2026-10-25）

第 4 周の再演: 残存（部品の文脈の位置づけ「Connects theory quantities to the observables the thesis uses.」「Derives the core relation the thesis depends on.」、主張 3 件、F_EOS の定義の文の英語の切れ端、同一性リンクの行の部品の入力・出力「radius, central density, EOS features」「stellar profiles」。run: uxsim/runs/c-astro-r25-share-r4/20261024T233359Z-43631 seq7・10、uxsim/runs/c-astro-r25-share-r4/20261024T233212Z-21482 seq9、uxsim/runs/c-astro-r25-share-r4/20261024T232812Z-98848 seq6）

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 残存（部品の文脈の位置づけ「Connects theory quantities to the observables the thesis uses.」・主張 3 件・同一性の候補の部品名がすべて英語・F_EOS の定義「the EOS feature vector ... to stellar profiles」）。音声の学生には読み上げても聞き取れない材料になる。run: uxsim/runs/c-astro-r25-share-r5/20261027T071307Z-52079 seq9・11、uxsim/runs/c-astro-r25-share-r5/20261027T071522Z-64789 seq7、uxsim/runs/c-astro-r25-share-r5/20261027T071135Z-43804 seq2

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演: STILL。再解析後の TeX 版でも、同一性の候補の要約（11 件中 10 件）・部品名（TOV residual loss at collocation points ほか）・位置づけ（Directly supports the central thesis. …）・主張 5 件・⚓3 の主張文・標準の説明の根拠の主張が英語。run: uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq2、uxsim/runs/c-astro-r25-share-r6/20261027T122930Z-7994 seq7・10、uxsim/runs/c-astro-r25-share-r6/20261027T123545Z-11458 seq3

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: STILL。同一性の候補の要約・部品名・⚓ の主張文（A second neural network solves …）が英語のまま。

第 25 周 面 D 第 7 周（2026-10-28）: STILL。学生 st-07 が t11 の講義中に ⚓ を開くと、部品名・位置づけ・主張文が英語のまま。記号 P の吹き出しの「主張では次のように使われています」の文も英語。run: uxsim/runs/c-astro-r25-studio-r7/20261027T173357Z-61213。

第 25 周 第 7 波（面 D 側・2026-10-28）: 表示側の是正（翻訳はしない・LLM 0 回）。判定 `text_excerpt.is_untranslated_text`・印の正本 `label_vocab.ORIGINAL_TEXT_MARK`「（原文）」。⚓ のチップと見出しは表示名（`display_label` — 親 unit の日本語の見出し）を優先し、表示する名前が原文のままなら根拠の項目の `title_original_mark` を添える。部品の文脈は位置づけの優先順を保ったまま日本語の候補を先に選び、それでも原文なら `narrative_role_original_mark`、部品名に `label_original_mark`、主張に `original_mark`。記号の吹き出しの使われ方の例文（`mentions[]`）にも `original_mark`。A層の生成言語（解析のやり直し）は対象外のままなので open（原文の印は「原文の引用である」ことを言うだけで、日本語化はしていない）。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

STILL。6be229e1 の部品の文脈の位置づけ・支持構造・前提・注意・主張が英語（uxsim/runs/c-astro-r25-share-r8/20261027T224047Z-97606 seq7・uxsim/runs/c-astro-r25-share-r8/20261027T224858Z-2266 seq7）。

第 8 周（面 D・2026-10-28）: **STILL（印は付いた・区画で揃わない）**。⚓ の名前・主張に「（原文）」の印が付き（第 7 波）、学生は英語の理由を読めた。ただし部品の文脈の前提（Profile outputs are differentiable…）・注意（Constraint is soft…）・関連・上位下位の英文には印が無い（印は label / narrative_role / supports.claims だけ）。記号 P の吹き出しの別論文は arXiv 番号だけ（IK-0552 系）。run: uxsim/runs/c-astro-r25-studio-r8/20261027T224929Z-2551 seq13・14（C 所見 ac3e66e2…）。

第 8 波（面 D・2026-10-31）: 部品の文脈の区画をそろえた。`component_context._build_field_refs`（前提・入力・出力）/ `_build_cautions`（注意）/ `_build_dependencies`（関連 — 理由が原文のとき、理由が無ければ解決できた相手の名前が原文のとき。生の参照 ID には付けない）にも `original_mark`、学習画面（`renderSupportsTextSection` / `renderContextDependencySection`）が項目の末尾に印を付ける。上位・下位（グラフのノード名）は対象外のまま。A層の生成言語は対象外なので open のまま。

## 第 9 周（第 25 周・面 D 第 9 周、2026-10-31・judge_studio_r9）

**STILL（部品の文脈の区画そろえは GONE・残りは記号の定義と生成ラベル）**。⚓ の部品の文脈で、前提（Profile outputs are differentiable…）・注意（Constraint is soft…）・関連の英文に「（原文）」が付き、名前・位置づけ・主張と揃った（第 8 波の区画そろえ）。残り: ①記号の吹き出しの**定義**（N:「Across N = 15 independent training runs」）に印が無い — `symbol_lookup` は使われ方の例文（mention）にだけ `original_mark` を付け、definition には付けない。②上位・下位のノード名（Training loss system ほか）は対象外のまま英語。③チャットの画面文脈の「論文の流れの中での役割」が英語の生成文（製品頭脳 B 4296）。run: uxsim/runs/c-astro-r25-studio-r9/20261031T020200Z-43015 seq12・13。

## 第 10 周（第 25 周・面 D 第 10 周、2026-10-31・judge_studio_r10）

記号の定義の「（原文）」は**未観察**（st-08 が引いた記号 P は「この論文には定義の記述が見つかりませんでした」で、使われ方の 2 件には「（原文）」が付く）。`symbol_lookup` の `definition.original_mark` と学習画面・投影の描画はコードで確認のみ。STILL: ⚓ の部品の文脈（位置づけ・上位下位・前提・注意・主張）が英語の生成文（「（原文）」付き）、〔出典: トピック概要〕が英語の原文のまま（t0 / t1 / t2）。run: uxsim/runs/c-astro-r25-studio-r10/20261031T054537Z-74536 seq12・13、uxsim/runs/c-astro-r25-studio-r10/20261031T053843Z-70580 seq6・13・17。

## 第 11 周（第 25 周・面 D 第 11 周、2026-10-31・judge_studio_r11）

⚓ の部品の文脈の摘要行の件数は **GONE**（「前提 · 注意 · 数式 · 主張」）。記号の定義の「（原文）」は**未観察**のまま（st-08 が引いた N_c は登録簿に定義が無い — `definition_status: used`。使われ方も割れた添字で 0 件 = IK-1157）。記号の「別の論文」の題名も行が出る記号を引いておらず未観察。STILL: 部品の文脈の位置づけ・上位下位・主張と〔出典: トピック概要〕が英語の原文。観察するなら定義のある記号（6be229e1 の PDF 版なら N_EOS / N_TOV）を材料に指定する。run: uxsim/runs/c-astro-r25-studio-r11/20261031T102933Z-6346 seq12・13、uxsim/runs/c-astro-r25-studio-r11/20261031T102539Z-4317 seq6・13・17。
