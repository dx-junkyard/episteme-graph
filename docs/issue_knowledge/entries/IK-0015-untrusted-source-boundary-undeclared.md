---
id: IK-0015
title: 資料由来のテキストを信頼しない境界が宣言されず、経路ごとに扱いが分かれていた
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/architecture/six_lenses_2026-09-10/known_issues_architecture.md B-01
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 10
  - docs/architecture/trust_boundary_pdf_input.md
feature_context:
  realizing: 取り込んだ論文の本文を、要約や説明を作るための入力として使う
  layers: [pipeline_a, rag_chat, shared_infra]
classification:
  primary: structure
  facets: [structure.aggregation, governance.review]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「第三者が書いたテキストをどこから信頼しないかの規約に正本が無く、経路ごとに
    別々の扱いになっている」こと。各経路の処理は動作しており、条件も情報も落ちていない。
    規約が一箇所に集まっていないかぎり、経路が増えるたびに扱いが分かれ続ける。確認手段は
    B-01 行が引く語の不在と、既存の注入検査が別の話題しか覆っていなかったこと。
generalization:
  level: general
  general_form: 外部由来の入力をどこで信頼しないかの規約が分散し、新しい経路が検査の外に出る
pattern: guardrail-does-not-cover-new-path
discovery:
  perspective: [inventory, guardrail_failure]
  note: >-
    資料由来のテキストがモデルへの入力に載る経路を棚卸しし、既存の注入検査が何を覆って
    いるかと突き合わせた。検査は別の話題を見ており、資料本文を指示と混ぜない規律は
    どこにも宣言されていなかった。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    資料本文を載せるときの区切りと注意文を定数として一箇所に置き、経路がそれを使う形に
    そろえた。規約そのものは文書に正本を置き、経路の一覧と検査を対応させた。表示と読み上げ
    の前に制御文字を落とす衛生も同じ規約の一部として含めた。
  landed_in:
    - backend/core/text_hygiene.py
    - backend/tests/test_pdf_trust_boundary_guardrails.py
    - docs/architecture/trust_boundary_pdf_input.md
related: [IK-0013]
view_of: [IK-0320]
history: []
---

## 課題

症状は無い。症状が出る前に、資料由来のテキストがモデルへの入力へ載る経路が増え続けており、
どこから信頼しないかの規約が明文化されていなかった。既存の注入検査は別の話題しか覆って
いない。

原因は、規約の正本が無く、扱いが経路ごとに分かれていることにある。個々の経路は動作するし、
現時点で実害も観測されていない。ただ、経路が増えるたびに扱いが分かれるので、検査の外に
出る経路が必ず生まれる。

## 発見の観点

資料由来のテキストが入力に載る経路を棚卸しし、既存の検査が覆っている範囲と突き合わせた。
覆っていない領域が広く、しかも「覆っていない」ことを知らせるものが無い。検査の空白が
そのまま見えた。

## 解決の観点

経路ごとに注意文を書く案は採らなかった。言い換えが生まれて規律が薄まるからである。区切りと
注意文を定数として一箇所に置き、経路をそこへそろえた。規約自体は文書に正本を置いて、
経路の一覧と検査を対応させた。表示と読み上げの前の衛生も同じ規約に含めた。

## 一般化

外部由来の入力を扱うシステムすべてで再発する。規約が暗黙のうちは、新しい経路が黙って
検査の外に出る。辞書の `guardrail-does-not-cover-new-path` に対応し、処方は「規約の正本を
置き、経路の一覧と検査の一覧を突き合わせる」。
