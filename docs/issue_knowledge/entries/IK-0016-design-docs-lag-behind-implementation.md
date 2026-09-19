---
id: IK-0016
title: 設計書の「非スコープ」や索引が、別の層で実装済みの事実に追随しない
status: resolved
recorded_at: 2026-09-10
resolved_at: 2026-09-10
sources:
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §4 第1波 11
  - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §10
feature_context:
  realizing: 設計書と索引を読んで、何が実装済みで何がまだかを判断できるようにする
  layers: [docs]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [none]
    governance: [completion, assignment]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 文書も実装もそれぞれ内部では整合している。
    構造: 済んだかどうかの状態の正本が実装側と文書側に分かれている。
    接続: 段階間で落ちているものは無い。
    統制: 済みの定義が決まっておらず、文書側を更新する担当も決まっていない。
    時機だけを決めても、誰が戻すかが空いていれば同じずれが出る。
generalization:
  level: general
  general_form: ある事柄が済んだかどうかの状態を、実装と文書が別々に持ち、片方だけが進む
pattern: doc-drifts-from-code
discovery:
  perspective: [doc_code_diff, inventory]
  note: >-
    設計書の「非スコープ」節を全件抽出し、コードの現況と一件ずつ突き合わせた。非スコープと
    書かれたまま別の層で実装されているものが複数あり、索引側にも実装済みを未実装と書いた行が
    残っていた。個別の誤記ではなく、更新の時機が決まっていないことの帰結だった。
resolution:
  perspective: [doc_correction, explicit_contract]
  note: >-
    ずれていた記述を直したうえで、状態ヘッダと解消注記の書き方を運用の規約として置いた。
    実装が別の層へ移ったときに、元の設計書へ注記を戻す手続きを決めることで、次のずれを
    一件ずつの心がけに頼らない形にした。
  landed_in:
    - ea1569d
    - docs/architecture/vision_ux_gap_six_lenses_2026-09-10.md §10
    - docs/development_checklist.md §5
related: [IK-0017]
view_of: [IK-0221]
history:
  - date: '2026-09-19'
    field: classification
    from: primary=governance facets=[governance.completion, structure.aggregation]
    to: axes=processing=[none]; structure=[aggregation]; connection=[none]; governance=[completion]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: processing=[none]; structure=[aggregation]; connection=[none]; governance=[completion]
    to: processing=[none]; structure=[aggregation]; connection=[none]; governance=[completion, assignment]
    reason: 軸ごとの再判定で、済みの定義に加えて「文書側を更新する担当が決まっていない」割り当ての要素を追加した
---

## 課題

症状は、設計書に「非スコープ」と書かれている機能が別の層で実装済みだったり、索引が実装済みの
ものを未実装として載せていたりすることである。読み手は、何が済んでいて何がまだかを文書から
判断できない。

原因は、済んだかどうかという状態の正本が実装側と文書側に分かれていて、文書側を誰がいつ
更新するかが決まっていないことにある。個々の記述は書かれた時点では正しかった。実装が別の
場所で進んだときに戻ってくる手続きが無いだけである。

軸ごとに読み直すと、統制には済みの定義と担当の割り当ての二つがある。更新の時機だけを決めても、
実装が別の層へ移ったときに誰が元の設計書へ戻すのかが空いていれば、同じずれが繰り返される。

## 発見の観点

設計書の「非スコープ」節を全件抽出して、コードの現況と一件ずつ突き合わせた。ずれは一箇所では
なく複数あり、しかも分野の違う設計書に散っていた。個別の誤記ではなく、更新の時機の問題で
あることがその分布から分かった。

## 解決の観点

記述を直すだけでは同じずれが戻る。状態ヘッダと解消注記の書き方を運用の規約として置き、
実装が別の層へ移ったときに元の設計書へ注記を返す手続きを決めた。文書側だけの修正で済むので、
実装への影響は無い。

## 一般化

設計と実装を別のファイルで持つプロジェクトすべてで再発する。設計書が多いほど、また機能が
層をまたいで動くほど強く出る。辞書の `doc-drifts-from-code` に対応し、処方は「済んだかどうかの
状態の正本を決め、更新の時機を手続きにする」。
