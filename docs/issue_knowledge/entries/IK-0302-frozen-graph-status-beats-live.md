---
id: IK-0302
title: 解析時に焼き込まれた審査状態が現在の判断に勝ち、承認してもレビューが閉じない
status: resolved
recorded_at: 2026-08-29
resolved_at: 2026-08-29
sources:
  - docs/features/graph_dialogue_review_design.md §11.1
  - docs/features/graph_dialogue_review_design.md §13
feature_context:
  realizing: 理論操作グラフ上で未レビューのノードを順に確定していく
  layers: [theory_artifacts]
classification:
  primary: connection
  facets: [connection.version, structure.responsibility]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「解析時点のスナップショット（グラフ本体に焼き込まれた審査状態）と、
    現在の行が持つ審査状態という二つの世代が同じ画面に流れ込み、後段が常に古い方を
    採っていたこと」。各段は単体では正しく（グラフは解析結果の記録、行は現在の判断）、
    版の合流規則が無いことが原因なので接続。設計書 §11.1 が [critical] として
    「承認しても再取得に反映されない」ことを確認している。
generalization:
  level: repo_pattern
  general_form: 生成時に焼き込まれた状態が、その後の人間の判断より優先されて配信される
pattern: stale-derivative-served
discovery:
  perspective: [reproduction, adversarial_review]
  note: >-
    承認 → 再取得 → 表示、という一連の操作を通しで再現し、画面の状態が変わらないことから
    グラフ側の焼き込み値と行側の現在値を突き合わせた。
resolution:
  perspective: [carry_through, explicit_contract]
  note: >-
    合流規則を明示した。人間の判断に由来する語彙（承認・却下・要修正）は現在の行の値を
    優先し、解析由来の導出語彙は焼き込み値を保つ。焼き込み値を捨てる案は、解析時点の
    根拠が読めなくなるため採らなかった（後続の §13 追補で、解析時点のメモと現在の要確認
    理由を見出しと色で区別する表示も入れた）。
  landed_in:
    - backend/core/deliberation/graph_dialogue.py
    - backend/api/routes/theory_components.py
    - docs/features/graph_dialogue_review_design.md §11.1
related: [IK-0301, IK-0307]
view_of: []
history: []
---

## 課題

症状は「承認ボタンを押しても、画面を開き直すとそのノードが未レビューのまま戻る」。
レビューのループが閉じないので、教員は同じノードを何度も承認することになる。

原因は、ノードの審査状態に**二つの世代**があったこと。理論操作グラフは解析時点の
審査状態を自身の中に焼き込んで保存しており、いっぽう承認はコンポーネントの行を更新する。
読み出し側は常に焼き込み値を採っていたため、現在の判断が画面に出てこなかった。

## 発見の観点

承認から再取得までを通しで再現し（`reproduction`）、値が変わらない事実からグラフ本体の
焼き込み値と行の現在値を突き合わせた。実装直後の敵対的レビューの一環である。

## 解決の観点

どちらの世代を採るかを、値の**由来**で決める規則として明示した（`explicit_contract`）。
人間の判断に由来する語彙は現在の行から運び（`carry_through`）、解析由来の導出語彙は
焼き込みのまま残す。焼き込みを全面的に捨てると解析時点の根拠が読めなくなるため、
どちらかに寄せるのではなく、由来で分ける形にした。

## 一般化

スナップショットと現在値が同じ画面に流れ込む構造はどこにでも現れる（版に固定された
コース、キャッシュされた投影、凍結された骨格）。合流規則を書かないと、必ず古い方が
勝つ側に倒れる。辞書の型 `stale-derivative-served` に対応する。
