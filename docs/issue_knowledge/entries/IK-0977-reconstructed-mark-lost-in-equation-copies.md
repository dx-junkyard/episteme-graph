---
id: IK-0977
title: "式の「AI が文脈から復元した式（原文と未照合）」という確かめられ方が、式の写し（生成した教材本文に $…$ で書き写した式・記号の吹き出しの式・教材図スタジオに渡す資料の式）へ運ばれないため、同じ式をチャットは「復元・未照合」と言い、教材・講義・記号の吹き出しは印なしの「式 (4)」で出し、教材図スタジオの AI は復元式を「資料の式」として図に描く"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/architecture/knowledge_reproduction_review_2026-09-19.md"
feature_context:
  realizing: "学生と教員が、教材・講義・記号の吹き出し・チャット・教材図のどこで式を見ても、それが原文の式か AI の復元かを同じく知る"
  layers: [course_builder, learner_experience_b, teaching_figures, concept_registry, rag_chat]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 information: 復元の印（reconstructed）は式の記録（chunks.formulas / content_blocks の式）が持ち、印を描くのは
    core/lecture.annotate_reconstructed_formulas と学習チャットの formula_facts だけ。①コース内容生成の本文は式を
    `$…$` の TeX で書き写すので、build_topic_slides の normalize_to_placeholder_format が作る [[FORMULA_N]] は式の記録と
    結ばれず reconstructed を持たない（読む・講義とも印なし）。②backend/core/symbol_lookup.py は equations[] に印を載せない
    （ファイル内に reconstructed の扱いが無い）。③backend/api/routes/teaching_figures.py::_build_grounding（374 行〜）は
    build_topic_evidence_items の式から id / label / latex / plain_text だけを写し、build_topic_evidence_items の式の項目にも
    reconstructed が無い（course_content_builder.py 3121 行〜）。構造軸 representation: 本文に書き写された式は式の記録の番地を
    持たないので、後から印を引き直せない。処理軸 none。統制軸 none（medium: どちらの言い方が正しいかは IK-0895 の
    旧い chunks.formulas の判定に依る — 学習チャットが言う「復元」が旧い判定の可能性は残るが、画面ごとに違う言い方になる原因は
    この課題の側）。
generalization:
  level: repo_pattern
  general_form: "値の確からしさの印を元の記録にだけ持たせ、値を書き写した先（生成文・別画面の材料・別の生成の入力）へ運ばないため、同じ値の確からしさが見る場所で違う"
pattern: condition-not-propagated
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    学生 st-06（懐疑的）が固定コース 6be229e1 の t11 で、記号 N_c の吹き出しは「式 (4)」を印なしで、教材と講義のスライド 5 も印なしで
    出すのに、チャットは同じ $L_{\mathrm{TOV}}$ を「AI が文脈から復元した式で原文と未照合」と答えたので「同じ式の確かめられ方が画面で
    違う」と諦めた。教員 te-02 は自分のコースで注意点に「w(a)=w_0+w_a(1-a) は AI 復元式なので埋め込まない」とあるトピックに教材図を
    頼み、AI は「資料の式 w(a) = w0 + wa(1 − a) に沿って」描いた。印の運ばれ方を 3 経路で辿った。
    run: uxsim/runs/c-astro-r25-studio-r4/20261024T234308Z-52347（seq6・12・13・15）/ uxsim/runs/c-astro-r25-studio-r4/20261024T232812Z-98850（seq14・16）。
resolution:
  perspective: [carry_through, canonical_source]
  note: "印の判定の正本 learner_context_common.equation_latex_is_reconstructed を平坦形にも効かせ（原文の LaTeX・原文と一致すれば印なし = IK-0895 を旧い run に）、写しの照合 reconstructed_latex_keys / latex_copy_is_reconstructed（正規化後の完全一致）を足した。prepare_topic_slide_input が本文の写しに印を引き直し、annotate_reconstructed_formulas も正本を通す。講義の DTO LectureFormulaItem が印を落としていたのを直し、app.js は [[FORMULA_N]] の写しと記号の吹き出しでも式カードと同じ印を描く。根拠の式（build_topic_evidence_items）・symbol_lookup（式一覧・原文の LaTeX の無い定義式）・教材図スタジオの資料の行と RECONSTRUCTED_EQUATION_CONSTRAINT も同じ印を持つ。"
  landed_in:
    - "backend/core/learner_context_common.py"
    - "backend/core/lecture.py"
    - "backend/core/course_content_builder.py"
    - "backend/core/symbol_lookup.py"
    - "backend/api/schemas.py"
    - "backend/api/routes/teaching_figures.py"
    - "backend/core/teaching_figures/prompt.py"
    - "frontend/public/js/app.js"
    - "backend/tests/test_lecture_studio_round25_r4.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "印の判定を 1 関数に集め、写しの照合も同じ判定の上に 1 組だけ置いて、教材・講義・吹き出し・根拠・図スタジオ・チャットの全経路がそれを通る形を採った。固有の差分: 写しは番地を持たないので、LaTeX の正規化一致で元の記録に結び直す。"
  verification:
    methods: [guardrail]
    unverified:
      - "固定コースの t11 / t16 で教材・講義・吹き出し・チャット・図スタジオの 5 か所の印が一致するかは実機で見ていない（第 5 周 st-02）"
      - "写しの照合は正規化後の完全一致だけなので、本文が式を書き換えて写した場合（項の順の入れ替え等）は印が付かない（付け過ぎない側に倒した）"
      - "図スタジオの AI がプロンプトの規則に従うかは実モデルで見ていない"
      - "原稿スタジオのプレビューの式の描画には印を足していない"
related: [IK-0895, IK-0577, IK-0870, IK-0872]
view_of: []
history: []
---

## 課題

症状: 同じ式が、チャットでは「AI 復元・原文と未照合」、教材・講義・記号の吹き出しでは印なしの「式 (4)」。教材図スタジオの AI は、
注意点が「復元式なので埋め込まない」と書いた式を「資料の式」と呼んで図に描き、キャプション・図中文字として学生に届きうる。

原因: 復元の印を式の記録だけが持ち、書き写した先（生成文の TeX・吹き出しの式・図スタジオの資料）へ運ばない。

## 発見の観点

学生と教員の通し受講（reproduction）で、同じ式を別の画面で見たときの言い方の差として記され、印の経路を辿った（trace_walk）。

## 解決の観点

第 25 周 第 4 波で是正（2026-10-25）。印の判定の正本 learner_context_common.equation_latex_is_reconstructed を平坦形にも効かせ（原文の LaTeX・原文と一致すれば印なし = IK-0895 を旧い run に）、写しの照合 reconstructed_latex_keys / latex_copy_is_reconstructed（正規化後の完全一致）を足した。

## 一般化

確からしさの印は値と一緒に運ぶ。確かめ方: 復元の印がある式を 1 つ選び、教材・講義・記号の吹き出し・チャット・教材図スタジオの資料の
5 か所で同じ印が出ることを見る。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 運ぶ側は効いている（教材の「この節で使う数式」・記号の吹き出しの式に印が付く）。反対に**付きすぎ**が出た — 学習チャットに渡す出典の抜粋のほぼすべてに「番号のない式［AI復元］」が名指しなしで付き、TeX 経路の原文の式を含む抜粋にも付く（IK-0997・根は IK-0645）。run: uxsim/runs/c-astro-r25-share-r5/20261027T071307Z-52079 seq6・9、uxsim/runs/mailbox/req-04055.json・req-04056.json

第 5 周の再演（2026-10-27）: **一部 GONE**。記号の吹き出しとチャットはどちらも同じ式に「AI復元」を付け（2 か所で一致）、図スタジオの AI は教材外の補足を図に入れないと言い分けた（te-01）。t16 の本文・講義・付録には復元式が無く、残り 3 か所は評価できなかった。新しい形 2 つを分けた: 吹き出しが壊れた復元を伏せずに式として描く（IK-1008）／チャットの事実行が番号の無い復元式を抜粋ごとに 1 語に畳む（IK-1009）。run: uxsim/runs/c-astro-r25-studio-r5/20261027T072352Z-12975 seq13・15〜17。
