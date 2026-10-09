---
id: IK-0690
title: "論文との議論の開幕で、学習者向けの骨格ノードが解析の内部語（stage・review_status・source_backing_status・review_reasons）と英語の集約説明を持ったまま返り、学習者に見える"
status: resolved
recorded_at: 2026-10-10
resolved_at: 2026-10-10
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/discuss_opening_authoring_design.md"
feature_context:
  realizing: "学習者が論文との議論を開き、論文の骨格（バックボーン）を読んで最初の一手を選ぶ"
  layers: [discuss, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [information]
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
    確認: core/discuss/opening.py の project_backbone は脆い箇所の導出（_is_fragile_backbone_node /
    _backbone_fact_line）の入力として内部語の欄を持ったノードを作り、build_opening はそれをそのまま学習者向け
    DTO の documents[].backbone に入れていた。学習者ルートは表示投影（LearnerDisplayRoute）を通るが、投影の禁止
    キー（display_projection.FORBIDDEN_KEYS_LEARNER）は数値・指紋の欄で、review_status 等の解析状態の語を持たない。
    構造軸 aggregation = 「学習者に解析の内部語を出さない」規則が 1 箇所に無く、面ごとの絞り込みに任されている。
    接続軸 information = 導出用の中間の欄が学習者の段まで運ばれる。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "導出の途中で使う内部の欄を持った中間の値を、出力の直前で絞らずにそのまま利用者向けの応答に入れ、共通の遮断にもその語が無いため外へ出る"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction]
  note: "第 23 周の砂場で、学生 3 人の議論の開幕に stage: theory_basis / review_status / source_backing_status: source_backed / review_reasons: missing_atomic_claim と英語の description が並んだ。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: "脆い箇所の導出が済んだ後に、学習者へ返す骨格ノードを learner_backbone_node で node_id・label・stage_label と真偽 1 つ（backed）に絞り、英語の集約説明は出さない。画面（discuss.js）は backed だけを読む。"
  landed_in:
    - "backend/core/discuss/opening.py"
    - "frontend/public/js/discuss.js"
    - "backend/tests/test_round23_server_fixes.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "今回の是正は開幕の面だけの絞り込みで、原理の部品①②（遮断の正本と全経路の結線）には載せていない。表示投影の禁止語彙が解析状態の語を持たない限り、別の学習者向けの面で同型が出うる。"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "document 直付けの議論の開幕（同じ build_opening を通るが砂場で開いていない）"
      - "他の学習者向けの応答に review_status 等の解析状態の語が残っていないかの全経路の走査"
related: [IK-0685, IK-0579]
view_of: []
history: []
---

## 課題

症状: 議論の開幕に、解析の内部語（stage・review_status・source_backing_status・missing_atomic_claim）と英語の集約説明が並び、学習者が「中身が見えない」「英語と記号まじりで長い」と読んだ。原因: 導出用の中間の値を学習者の DTO にそのまま入れており、共通の表示投影もその語を落とさない。

## 発見の観点

ペルソナの通し受講（reproduction）。学生 3 人の全 run で同じ欄が出た。

## 解決の観点

導出が済んだ後に学習者向けの形へ絞る関数を置いた（TestLearnerBackbone）。再演で学生 2 人が内部語の消失を読んだ（GONE）。ただし規則は開幕の面に置いたもので、共通の遮断へは寄せていない。

## 一般化

中間の値を「どうせ投影が落とす」と考えて出力に入れると、投影の語彙に無い内部語は素通りする。確かめ方は、学習者向けの全ルートの応答を走査して解析状態の語（review_status・source_backing_status・review_reasons）が現れないことを見ること。
