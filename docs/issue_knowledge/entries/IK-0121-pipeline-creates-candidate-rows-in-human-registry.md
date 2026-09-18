---
id: IK-0121
title: 「昇格は人間の操作のみ」の条項を、パイプラインが候補行を作る形に読み替えてよいか
status: deferred
recorded_at: 2026-09-13
resolved_at: null
sources:
  - docs/architecture/knowledge_structure_review_2026-09-12.md §9.4 O-6
  - docs/features/concept_registry_design.md §13.3 P3-R1
  - docs/features/image_pipeline_knowledge_library_design.md
feature_context:
  realizing: 共通部品レジストリに、論文横断の同一性候補を教員のレビュー用として積む
  layers: [concept_registry, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [none]
    governance: [assignment, review]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    事実は確認済み — 共通部品ライブラリの条項は「昇格は人間の操作のみ」で、AI が書き込む経路を
    作らないと定めているが、候補生成の実装はそのライブラリの表に候補状態の行を作る。可視化の弁
    （凍結）は人間のままで、候補行はパイプラインの検索にも学習者にも届かない。争点は誰が何を
    書いてよいかという担当の割り当ての解釈であり、処理も表現も正しい。裁定が (b) なら候補の
    保存先を専用表へ移す差し替えになる。
generalization:
  level: repo_pattern
  general_form: >-
    人間だけが書けると定めた領域に、機械が候補状態の行を置いてよいかという、担当の割り当ての
    解釈が未確定のまま実装が先行する
pattern: ai-decides-instead-of-human
discovery:
  perspective: [invariant_audit, adversarial_review]
  note: >-
    新しい層の実装を、それが乗る既存層の不変条項と 1 行ずつ突き合わせたときに、条項の字面と
    実装の書き込み先が食い違っていることが見えた。
resolution:
  perspective: [deferred_decision]
  note: >-
    裁定が出るまで実装は現状維持とし、設計書の冒頭の判断表と、条項を持つ側の設計書の両方に
    注記を置いた（どちらを読んでも未決と分かる）。推奨は「認める」— 可視化の弁が凍結の拒否で
    構造的に守られ、他層の候補始まりと同型のため。
  landed_in: []
related: [IK-0116]
view_of: [IK-0116]
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.assignment, governance.review]
    to: axes=processing=[none]; structure=[none]; connection=[none]; governance=[assignment,
      review]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

共通部品ライブラリの条項は「昇格は人間の操作のみ」で、機械が書き込む経路を作らないと定めている。
一方、論文横断の同一性候補を作る実装は、そのライブラリの表に候補状態の行を置く。候補行は
凍結されない限りパイプラインの検索にも学習者にも届かないが、**表に行が増えること自体**が条項に
反するのかは解釈の問題として残っている。

## 発見の観点

新しい層を、それが乗る既存層の不変条項と 1 行ずつ突き合わせた（`invariant_audit`）。実装が
条項の字面とずれている箇所を、壊すつもりで探した結果として出た（`adversarial_review`）。

## 解決の観点

技術的にはどちらでも実装できる（候補の保存先を専用表へ移すだけ）が、条項の意味を変える判断は
実装者が決めてよい範囲を超えるため保留する（`deferred_decision`）。未決であることは、条項を
持つ側と新設側の**両方**の文書に書く（片方だけだと読み手が気づかない）。

## 一般化

既存の層に新しい層を積むとき、「この領域には人間しか書かない」型の条項は必ず再解釈を迫られる。
再解釈が必要になった時点で実装を止めるのではなく、**現状維持のまま両方の文書に未決と書いて
進める**のが、情報を落とさない扱い方になる。
