---
id: IK-1013
title: "式の意味解析の LLM 応答が放棄（proxy の 5 分 client_disconnected）されると、失敗の印なしに「Equation semantics could not be inferred.」が式の summary・役割 unknown として残り、dsl_linking・thesis_reconstruction・contextual_explanation・discuss_opening の材料に本文として流れる"
status: open
recorded_at: 2026-10-27
resolved_at: null
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "式の意味解析の結果を後段（DSL 接続・中心命題・二層説明・議論の開幕）の材料にする"
  layers: [pipeline_a, hierarchical_explanation]
classification:
  axes:
    processing: [input_handling]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: low
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: equation_semantics は LLM 応答が得られないと record を「Equation semantics could not be inferred.」の summary・役割 unknown・
    confidence 0.0 の既定値で埋め、失敗を表す欄を持たない（または後段が読まない）。後段の入力 builder はその summary を通常の本文として
    材料に入れる。観察: 頭脳 A の 4208/4210 が放棄された後、4224（dsl_linking）で eq_eosnet / eq_tovnet / eq_total_loss が unknown・
    「could not be inferred」、4230（contextual_explanation）の lower_context に同じ文が 3 件、4236（discuss_opening）の author_choices の
    reason に同じ文。頭脳 B の 4213 は eq_total_loss に応えたのに 4223 で同じ失敗文のまま（IK-0724 の span_id 照合で弾かれた疑い）。
    構造軸 representation = 失敗と「意味が空の結果」を区別する表現が無い。接続軸 information = 失敗の事実が下流へ運ばれない。
    処理軸 input_handling = 下流の入力組み立てが既定値の文を素材として扱う。統制軸 none（確信度 low: 再試行・予算の制御は問わない）。
    確定には、equation_semantics の既定値の組み立て箇所と、後段 builder がその文を除外する条件の有無を読む。
generalization:
  level: general
  general_form: "失敗時に埋めた既定の説明文が結果と同じ欄に入り、後段がそれを内容として読み込んで生成物に混ぜる"
pattern: failure-reported-as-success
discovery:
  perspective: [data_inspection]
  note: "第 6 周の前の PINN TeX 再解析で、製品頭脳 A / B が後段の要求に失敗文が素材として入っていることを所見に残した（出所: uxsim/runs/round25/BRAIN_NOTES_product_A_r6pre.md 4210・4224・4230・4236、BRAIN_NOTES_product_B_r6pre.md 4223・4229）。"
resolution:
  perspective: [pending]
  note: "未解決。IK-0715 は縮退を教材行・グラフレビューに見せる投影で閉じたが、縮退で出来た既定の文が後段の材料に混ざることは止めていない。失敗の record に印（semantic_status など）を持たせ、後段 builder は印のある record の summary を材料に入れず「意味を推定できなかった式」として事実で扱う。"
  landed_in:
    - "docs/pipeline/agents.md"
    - "backend/core/discuss/opening.py"
    - "frontend/public/js/discuss.js"
    - "backend/tests/test_doubt_round25_wave7.py"
related: [IK-0715, IK-0825, IK-0873, IK-0909, IK-0724]
view_of: []
history: []
---

## 課題

症状: 再解析の後段の LLM 入力に「Equation semantics could not be inferred.」が式の意味・下位文脈・選択の理由として本文のように並ぶ。
学習者・教員向けの説明（contextual_explanation）と議論の開幕素材（discuss_opening）の材料に処理の失敗文が混ざる。

原因（仮説）: equation_semantics の応答放棄（proxy の 5 分 client_disconnected）で record が既定の文で埋まり、失敗を区別する印が後段に
伝わらない。IK-0715 の是正は表示面への縮退の投影で、材料の汚染は対象外だった（IK-0873 / IK-0909 は表示・書き換え根拠の側で同じ文を見た）。

出所: uxsim/runs/round25/BRAIN_NOTES_product_A_r6pre.md・BRAIN_NOTES_product_B_r6pre.md。

## 発見の観点

後段の入力（dsl_linking・contextual_explanation・discuss_opening）を読み、前段の放棄の記録と突き合わせた（data_inspection）。

## 解決の観点

未解決。失敗を値の欄ではなく状態の欄で表し、後段が状態を見て材料から外す。

## 一般化

既定値の文を結果の欄に入れると、下流は成功と区別できない。型 failure-reported-as-success（record 単位の縮退）。

学生の画面への波及（面 B 第 6 周・2026-10-27）: st-05 の discuss 開幕で PINN（TeX 版・第 6 周の前に再解析）の区画の「この論文が答えようとした問い」が生の「unknown」、目指したこと・章の流れが「題名から…推測される（本文が入力に無いため未確認）」と学生に出た（同じ区画の『この論文の主張』は本文由来で食い違う）。paper_skeleton が題名だけで走った結果（r6_prereqs の実測）が、失敗の印なしに学習者の開幕まで届いた。run: uxsim/runs/c-r25-doubt-r6/20261027T123134Z-9179（seq15）。

第 7 周の再演: STILL（2026-10-28）。st-08 の discuss 開幕で PINN 論文の「この論文が答えようとした問い」が生の「unknown」、目指したこと・骨格が「題名からの推定（本文が入力に無い）」。同じ論文の主張と教材本文は本文由来。run: uxsim/runs/c-r25-doubt-r7/20261027T172411Z-57109（seq15）。

第 7 波の部分是正（2026-10-28・学習者の開幕だけ）: `core/discuss/opening.py::project_thesis` は解析の代替語（生の「unknown」・none・n/a・不明 等、
`is_unfilled_value`）だけの中心の問い・目指したこと・主張の合成文を中身として出さず、問いが代替語だったときだけ
`central_question_note`（「この論文の中心の問いはまだ整理されていません。」）を足す。`discuss.js` は問いの区画を出さずその事実文 1 行を描く。
**根（失敗の印なしに既定の文が後段の材料へ入る）は未是正のまま**で、目指したこと・骨格の「題名からの推定（本文が入力に無い）」は
推定であることを自分で言っているので残した。status は open のまま。


第 8 周の再演: 学習者側 GONE（2026-10-28）。st-05 の開幕で PINN の「この論文が答えようとした問い」に中身（Can a physics-informed neural network framework … ?）が出た（第 7 周は unknown）。残り: 問い・目的・骨格は英語のまま、開幕の論文見出しの 1 本が arXiv 番号（2606.02318v1）— IK-0901 / IK-0664 系。本エントリの本体（パイプラインの既定文が後段の材料に流れる）は未是正のまま。run: uxsim/runs/c-r25-doubt-r8/20261027T224211Z-98239（seq15）。

第 9 周（2026-10-31）: st-05 の discuss 開幕で PINN（TeX 版）の区画が「この論文の中心の問いはまだ整理されていません。」+ 目指したこと・骨格「本文が入力に無い」（第 7 波の学習者側の部分是正どおり・根は open）。**第 8 周の「学習者側 GONE」は固定コースが PDF 版 6be229e1 だったための見かけ**で、TeX 版のコース 1bad7d9f に戻すと根の症状が出る。run: uxsim/runs/c-r25-doubt-r9/20261031T015310Z-38509（seq15）。

## 第 10 周の再演（第 25 周・面 B 第 10 周、2026-10-31・judge_doubt_r10 — 第 9 周と同じコース・同じ論文）

STILL（同じ対象で前後比較）。PINN（TeX 版）の区画は第 9 周と同じく「この論文の中心の問いはまだ整理されていません。」+「題名から…推測される（本文が入力に無いため未確認）」+ 骨格の章が「本文は入力に無い」。同じ区画の「この論文の主張」には本文由来の要約と主張が並び、学生は「なぜ本文が無いと言うの？」と書いた。run: uxsim/runs/c-r25-doubt-r10/20261031T053655Z-69809（seq15）。
