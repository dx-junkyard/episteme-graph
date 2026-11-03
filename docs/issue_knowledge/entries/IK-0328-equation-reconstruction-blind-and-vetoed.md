---
id: IK-0328
title: PDF 由来の数式は原文を見せずに復元させ、復元値を抽出値と同じ列に保存し、しかし導出からは一律に締め出す
status: resolved
recorded_at: 2026-09-19
resolved_at: 2026-09-19
sources:
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md §3
  - docs/architecture/knowledge_structure_review_2026-09-12/A_fidelity.md
feature_context:
  realizing: 論文の数式を意味付きの式レコードにし、導出連鎖と理論操作グラフの背骨として学習者・教員に見せる
  layers: [pipeline_a, knowledge_objects, theory_artifacts, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理軸: 導出可否を決める分岐に「再構成の信頼度が高ければ救う」条件が書かれているのに、
    直後の無条件の上書き（consistency_review なら常に偽）がそれを潰していた。単一処理の不良。
    構造軸: 式の latex 列が「抽出した値」と「AI が復元した値」を区別できず、復元値が抽出値の
    顔で下流（chunks の式・学習者射影）へ流れた。表現を変えなければ再発する。接続軸:
    「PDF テキスト層は信用しない」という前段の条件が、後段では「一切導出に使えない」に
    読み替えられていた。条件の意味は伝わったが範囲が変わったので medium。統制軸: 順序・
    予算・担当の要素は無い。
generalization:
  level: general
  general_form: 入力を信用しない方針を「原文を隠す」と「派生値を一律に無効化する」で実装したため、派生値が原文の顔で保存され、かつ下流では使えないという矛盾が生じる
pattern: projection-mistaken-for-source
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    原本 PDF の式 (4) と DB の式 (4) を並べたところ内容が別物で、artifact の
    source_extraction.latex が全件 null・reconstruction.latex だけが埋まっていることから、
    プロンプトが原文を省略している箇所と、導出可否の無条件 veto に行き着いた。
resolution:
  perspective: [explicit_contract, representation_change]
  note: >-
    原文は「隠す」のではなく untrusted と明示して見せる（TB1 の区画作法）。導出の veto は
    その式固有の疑い（mismatch・ラベル不一致・corrupted）に限定し、PDF 由来という一律の
    理由だけなら既存の救済条件で判定する。食い違った低信頼の復元は印字番号を名乗らせず
    降格する。復元由来は chunks の式に latex_source / reconstructed を付け、学習者向けには
    事実文 1 行を添える。列は足さない（migration なし）。
  landed_in:
    - src/episteme_graph/agents/equation_semantics/prompt.py
    - src/episteme_graph/agents/equation_semantics/schema.py
    - src/episteme_graph/agents/derivation_chain/agent.py
    - backend/core/document_pipeline/persistence.py
    - backend/core/learner_context_common.py
    - frontend/public/js/app.js
  verification:
    methods: [guardrail]
    unverified:
      - 是正後の再解析による実データでの効果の実測（LLM の live 呼び出しを行わない方針のため次回の解析待ち）
      - docker で組み上げた実機での E2E
related: [IK-0320, IK-0019, IK-0304]
view_of: []
history: []
---

## 課題

**症状**: PDF 経路の全論文で式の latex が 100% AI の復元値になり、agent 自身の整合判定で
27〜35% が原文と食い違う。式 (4) が別の式に差し替わって保存された例がある。同時に
全式が導出不可となり、導出連鎖は claim 連鎖に縮退、理論操作グラフの式リンクは 0、
export は全本 failed_validation。

**原因**: ①プロンプトが PDF 由来の数式原文を `[OMITTED]` に置き換え文脈だけから復元させる
②永続化が `extraction.latex or reconstruction.latex` で復元値を抽出値の列に入れる
③導出可否の分岐で `consistency_review` が無条件に偽を上書きし、救済条件が死んでいる。

## 発見の観点

原本と成果の再現照合（`reproduction`）で式の中身が違うことに気づき、artifact の
`source_extraction` / `reconstruction` / `confidence_policy` の実値（`data_inspection`）から
3 箇所のコードに遡った。

## 解決の観点

不信の方針は「隠す」でなく「明示する」契約に置き換え（`explicit_contract`）、値の出所を
表現として持たせた（`representation_change`）。「再構成を導出の根拠にしてよいか」は
指揮者判断 J-1（backing は partially_source_backed 止まり・確定は人間）。

## 一般化

「入力を信用しない」を派生値の一律無効化で実装すると、派生値は作られるのに使えず、
しかも保存時に原文の顔をする。信用の判定は値ごとの疑いに限定し、値の出所は列か印で持つ。
