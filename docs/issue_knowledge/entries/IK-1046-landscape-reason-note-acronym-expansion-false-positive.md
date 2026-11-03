---
id: IK-1046
title: "論文の位置づけの理由に添える注記が、略称と引用の正式名の対応を見ず、引用に「Tolman-Oppenheimer-Volkoff equations」とあるのに「理由に挙がる「TOV」は、引用には現れません」と言う"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-28
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/knowledge_landscape_design.md"
feature_context:
  realizing: "学生が出典タブ・地図の論文の位置で、AI の配置の理由と引用の範囲の違いを読む"
  layers: [knowledge_landscape, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [none]
    connection: [meaning]
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
    処理軸 logic: agents/landscape_placement/reason_scope.py reason_terms_beyond_quote は理由文のラテン文字の語を引用に語境界一致で探すだけで、
    略称（頭字語）とその正式名（各語の頭文字の連なり）を同じ語として扱わない。接続軸 meaning: 注記は「その語の指すものが引用に無い」と読まれるが、
    判定しているのは表記の有無。構造軸 none（照合器を 1 本にした構造は正しい）、統制軸 none。
generalization:
  level: repo_pattern
  general_form: "表記の一致だけで「出典に無い」と判定し、同じものを指す別表記（略称と正式名）を見落として、根拠があるものに根拠が無いという注記を付ける"
pattern: substring-match-false-positive
discovery:
  perspective: [symptom_report, trace_walk]
  note: "学生 st-03 が出典タブで、引用に Tolman-Oppenheimer-Volkoff equations とあるのに TOV が現れないと言われて戸惑った（審判 C・run uxsim/runs/c-astro-r25-atlas-r7/20261027T172454Z-57525 seq 7）。同じ画面の「GWTC-4」「Ia」は引用に実際に無く、注記は正しい。reason_scope の照合を読んだ。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    core/landscape/schema.acronym_expanded_in を足し、reason_beyond_quote_note は頭字語（英字 2〜6・大文字 2 以上）が引用の連続する語
    （ハイフンも区切り・機能語 of/the/and 等は読み飛ばし可）の頭文字の連なりと大小無視で一致すれば注記から外す（決定論・LLM 0 回）。
    照合の正本（A層 reason_scope / validator の warning）は変えず表示の段で絞る。日本語の語は v1 の対象外のまま。
  landed_in:
    - "backend/core/landscape/schema.py"
    - "docs/features/atlas_relation_edges_design.md"
    - "docs/features/field_atlas_overlay_spec.md"
    - "backend/tests/test_atlas_review_round25_r8.py"
    - "backend/tests/test_atlas_review_round25_r7.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の配置の理由（TOV と Tolman-Oppenheimer-Volkoff equations）で出典タブの注記が消えること（次周の再演）"
      - "頭字語の頭文字が偶然一致する別の語句を正式名と誤る場合（例: 同じ頭文字の無関係な語の連なり）"
      - "切り詰めの照合（第 8 波）が、先頭の 3 文字以上が偶然一致する別の固有名を同じものと誤る場合"
related: [IK-0967, IK-0677]
view_of: []
history: []
---

## 課題

症状: 出典タブ「分野の中の位置づけ」の PINN 論文の「統計推論」の行で、理由「TOV 方程式を微分可能な制約として埋め込んだ…」に「理由に挙がる「TOV」は、
引用には現れません。」が付く。引用は「The method incorporates the Tolman-Oppenheimer-Volkoff equations as a mean-square ODE residual.」で、TOV は
その略称。学生は「現れていないと言われると少し戸惑います」と記した。

原因: 理由の語と引用の照合が表記の語境界一致だけで、頭字語と正式名の対応を見ない。

## 発見の観点

学生の症状報告（審判 C）から照合器を辿った。

## 解決の観点

未解決。

## 一般化

型 substring-match-false-positive の双対（表記一致の照合が、同じものの別表記を見ずに「無い」と誤る）。確かめ方: 引用に正式名・理由に頭字語の
組を照合器に通し、注記が付かないこと。頭字語が引用にも正式名にも無い組（GWTC-4 等）は従来どおり注記が付くこと。

## 第 7 波の是正（2026-10-28）

core/landscape/schema.acronym_expanded_in を足し、reason_beyond_quote_note は頭字語（英字 2〜6・大文字 2 以上）が引用の連続する語 （ハイフンも区切り・機能語 of/the/and 等は読み飛ばし可）の頭文字の連なりと大小無視で一致すれば注記から外す（決定論・LLM 0 回）。 照合の正本（A層 reason_scope / validator の warning）は変えず表示の段で絞る。日本語の語は v1 の対象外のまま。

## 第 8 周の再演（2026-10-28・judge_atlas_r8）

**判定できず（TOV）／同型の残り**。TOV の配置は引用が「(TOV)」を含む文に変わり注記は出なかった（是正の確かめにならない）。同じ画面の分子雲の配置で「理由に挙がる「Cepheus」は、引用には現れません。」— 引用は「…in the Cep B molecular cloud.」で、Cep は Cepheus の切り詰めた略（頭字語ではない）。第 7 波の `acronym_expanded_in` は頭字語だけを扱うので、切り詰め略と正式名の対応は同じ型のまま残る（run uxsim/runs/c-astro-r25-atlas-r8/20261027T224229Z-98423 seq 6・20261027T224409Z-99225 seq 7。st-01 は「Cep B は Cepheus B のことですよね」と記した）。

## 第 8 波の是正（2026-10-28）

切り詰めた略（引用「Cep B」⇔ 理由「Cepheus」）を `core/landscape/schema.truncation_matched_in` で扱う: 大文字始まり・英字だけ・
3 文字以上の語どうしで、短い方が長い方の先頭と大小無視で一致すれば同じものの別表記として注記から外す（頭字語の照合の双対・
決定論・LLM 0 回・A層の照合の正本は不変）。2 文字（Ia）・数字を含む型番（DR2）は照合しない。

## 第 9 周の再演（2026-10-31・judge_atlas_r9）

**GONE（切り詰めた略）**。分子雲の配置（理由「Cepheus B 分子雲のフィラメント」・引用「…in the Cep B molecular cloud.」）に「理由に挙がる「Cepheus」は、引用には現れません。」が出なくなった（st-03 run uxsim/runs/c-astro-r25-atlas-r9/20261031T015252Z-38248 seq 6・7 / st-01 20261031T015432Z-39319）。第 8 周は出ていた。注記は「理由に挙がる「Ia」は、引用には現れません。」（宇宙膨張・引用は BAO だけ）で残り、こちらは理由が引用より広いことを正しく言っている。
