---
id: IK-0301
title: コンポーネント承認を「読み出し→全体書き戻し」で行い、CHECK 違反と出所情報の破壊を招いた
status: resolved
recorded_at: 2026-08-29
resolved_at: 2026-08-29
sources:
  - docs/features/graph_dialogue_review_design.md §11.1
feature_context:
  realizing: グラフを見ながらコンポーネントの承認・却下という状態遷移だけを確定する
  layers: [theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [contract]
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「状態遷移を、表示用モデルへの読み出しと書き戻しの往復で実装したこと」。
    読み出し側のモデルは自由語彙の型名を投影し、スコープ情報の追加キーを落とす表現に
    なっており、その値をそのまま書き戻すと CHECK 制約違反と出所情報の消失を起こす。
    処理の一箇所を直しても、読み出しモデルと永続モデルが同一だという前提のままでは
    別の遷移でも再発するため構造。設計書 §11.1 が両欠陥を file 単位で確認している。
generalization:
  level: general
  general_form: 部分的な状態変更を、全体の読み出しと書き戻しの往復で実装したために無関係な属性が壊れる
pattern: full-update-clobbers-unrelated-fields
discovery:
  perspective: [adversarial_review, trace_walk]
  note: >-
    初回実装に対する独立レビューで、承認 API の内部経路を入口から永続化まで辿り、
    表示用モデルの投影規則と永続モデルの制約を突き合わせたときに見えた。
resolution:
  perspective: [representation_change, guardrail_fix]
  note: >-
    遷移専用の更新（状態と審査に関わる列だけを書く）へ変え、書き戻し経路を使わない
    ことにした。監査には実行者を必ず載せる。遷移の SQL に無関係な列が現れないことを
    テストで固定した。
  landed_in:
    - backend/api/routes/theory_components.py
    - backend/tests/test_graph_review_guardrails.py
    - docs/features/graph_dialogue_review_design.md §11.1
related: [IK-0302, IK-0303]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.representation, connection.contract]
    to: axes=processing=[none]; structure=[representation]; connection=[contract]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は二つ。①コンポーネントを承認すると 500 が返る。②承認したコンポーネントの
出所（元の要素 ID・図の対応）が失われる。

遡った原因は一つで、承認・却下という**状態遷移**を、既存行を表示用モデルへ読み出して
全フィールドを書き戻す更新経路で実装していたことにある。表示用モデルは型名として
自由記述の値を投影するため書き戻しで制約違反になり、追加キーを保持しない設計のため
スコープ情報が欠落したまま上書きされる。さらにこの経路の監査記帳は実行者を持たず、
誰が承認したかも残らなかった。

## 発見の観点

実装直後の敵対的レビュー（`adversarial_review`）で、承認 API の入口から永続化までを
1 本辿り（`trace_walk`）、読み出し側の投影規則と書き込み側の制約表を突き合わせた。
表示のための緩い表現と、永続のための厳しい制約が、同じ値の上で出会っていた。

## 解決の観点

「遷移で変わる列だけを書く」へ表現を変えた（`representation_change`）。読み書きで
同じモデルを使い回すのをやめ、遷移の意味に対応する最小の更新を置いた。単に投影規則を
直す案（`single_point_fix`）は、次に列が増えたときに同じ事故が起きるため採らなかった。
遷移 SQL に無関係な列が現れないことをテストで固定した（`guardrail_fix`）。

## 一般化

「編集フォームの保存」と同じ経路で「承認」「公開」「アーカイブ」を実装すると、常に
同型の事故が起きる。部分的な意味を持つ操作は、部分的な書き込みとして表現する。
辞書の型 `full-update-clobbers-unrelated-fields` に対応する。
