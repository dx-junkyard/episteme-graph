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
    structure: [representation, decomposition]
    connection: [contract]
    governance: [review]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals:
    - kind: value
      target_axis: structure
      neighbor_of: [structure.decomposition, structure.representation]
      statement: 操作の意味が及ぶ範囲と、実際に書き換える範囲が対応していない
      confidence: medium
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 投影も書き戻しも書かれたとおりに動いており、単一処理の不良は見当たらない。値が制約に触れるのは前段の兼用が原因で、処理の誤りではない。

    構造: 読み出し用の緩い表現と永続用の厳しい制約を同じモデルで兼ねている点が表現に当たる。加えて、部分的な意味しか持たない操作を全列の書き込みとして実装している点が粒度に当たる（責務の二重とも読めるため中）。

    接続: 読み出しの出力が書き込みの入力制約を満たさない点が契約に当たる。同一の往復の中なので、段階間と呼べるかで迷い中とした。

    統制: 承認という手続の記帳に実行者が載らず、誰が確定したかが残らなかった点が確認の手続と記録に当たる。
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
  - date: '2026-09-19'
    field: classification.axes
    from: axes=processing=[none]; structure=[representation]; connection=[contract]; governance=[none]
    to: axes=processing=[none]; structure=[representation, decomposition]; connection=[contract]; governance=[review]
    reason: 軸ごとの再判定で、統制軸に承認の記帳（実行者の欠落）という要素を認め、構造軸に書き込み範囲の粒度を第2の値として足した
---

## 課題

症状は二つ。①コンポーネントを承認すると 500 が返る。②承認したコンポーネントの
出所（元の要素 ID・図の対応）が失われる。

遡った原因は一つで、承認・却下という**状態遷移**を、既存行を表示用モデルへ読み出して
全フィールドを書き戻す更新経路で実装していたことにある。表示用モデルは型名として
自由記述の値を投影するため書き戻しで制約違反になり、追加キーを保持しない設計のため
スコープ情報が欠落したまま上書きされる。さらにこの経路の監査記帳は実行者を持たず、
誰が承認したかも残らなかった。

4 軸で見直すと、統制軸にも要素がある。承認という手続の記帳に実行者が載らず、誰が確定したかが残らなかったからである。
構造軸も、読み書きで同じモデルを兼ねるという表現の問題に加えて、操作の意味が及ぶ範囲より広い範囲を書き換えるという粒度の問題を併せ持つ。

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
