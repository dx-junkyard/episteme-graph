---
id: IK-0204
title: 層ごと作られた処理が画面に配線されず、誰も呼ばないまま完成として扱われる
status: resolved
recorded_at: 2026-07-16
resolved_at: 2026-07-16
sources:
  - docs/architecture/vision_ux_gap_survey_2026-07.md §2 G2
  - docs/architecture/vision_ux_gap_survey_2026-07.md §4
feature_context:
  realizing: 層を積み上げて機能を増やしつつ、各層の成果を利用者へ届ける
  layers: [frontend_admin_ui, usage_metering_u, deliberation_w]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [information]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals:
    - kind: value
      target_axis: connection
      neighbor_of: [connection.information, connection.contract]
      statement: 前段と後段をつなぐ経路そのものが用意されず、後段が前段の成果を一度も受け取らない
      confidence: medium
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 提供側の各処理は単体で正しく動き、入力の扱いにも計算にも不良は無い。
    構造: 層を横断する導線を誰が持つかが決まっておらず、各層が既存の画面へ間借りで足していく。
    この置き場所を決めない限り次の層でも配線が漏れる。分割の粒度との境界で迷った。
    接続: 提供された成果が呼び出し側へ渡らず、各段は単体では正しく見える。ただし値が途中で
    落ちるのではなく経路そのものが無いため、既存の値との当たりは完全ではない。
    統制: 完成の宣言が層の内側で行われ、外から到達できるかを見ないまま済みになる。担当の割り当ての
    不在とも読める。
generalization:
  level: repo_pattern
  general_form: >-
    提供された処理が呼び出し側へ配線されないまま残り、作ったのに誰も使えない状態が層の単位で生じる
discovery:
  perspective: [inventory, doc_code_diff]
  note: >-
    提供しているエンドポイントの一覧と、画面からの呼び出しの一覧を機械的に突合して差集合を
    作ったところ、層の単位でまとまった未呼び出し集合が現れた。症状報告では出てこない
    （誰も使っていない機能に苦情は来ない）。
resolution:
  perspective: [carry_through, responsibility_move]
  note: >-
    新しい実装ではなく配線が不足しているので、各層の成果を既存の画面へ繋ぐことで解消した。
    層を積む開発では、次の層を積む前に横断する導線を点検する段階を置くのが本質的な対策
    （調査の全体考察がそう結論している）。
  landed_in:
    - frontend/public/js/admin-llm-usage.js
    - frontend/public/js/deliberation.js
    - frontend/public/js/doubt-atlas.js
    - docs/architecture/vision_ux_gap_survey_2026-07.md §6
related: [IK-0205, IK-0203]
view_of: []
pattern: available-but-unwired
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.information, governance.completion]
    to: axes=processing=[none]; structure=[none]; connection=[information]; governance=[completion]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: processing=[none]; structure=[none]; connection=[information]; governance=[completion]
    to: processing=[none]; structure=[responsibility]; connection=[information]; governance=[completion]
    reason: 軸ごとの再判定で、層を横断する導線を誰が持つかが決まっていない点を構造軸に置いた
---

## 課題

**症状**: 使用量の可視化・教員向けの橋の集約・共有ダッシュボード・検証状態の記帳・同一性
リンクなど、層としては完成扱いの機能が画面から一度も使えない。提供しているのに呼ばれない
エンドポイントが多数あった。

**原因**: 層ごとに処理・永続化・テストを完結させる開発の進め方に対し、横断する画面側は層ごとに
導線を「間借り」して増築されてきた。その結果、接続されないまま残る層・埋もれる層・巡回先が
増え続ける層に分化した。個々の層は自分の中では完成しており、配線されていないことに層の内側
からは気づけない。

**軸ごとの再判定（2026-09-19）**: 構造軸を none から責務へ改めた。層を横断する導線を誰が持つかが
決まっておらず、各層が既存の画面へ間借りで足すという置き場所の設計が原因の一部にあるため。
接続軸は残すが、落ちているのは値ではなく経路そのものなので、既存の値との当たりは完全ではない
（新しい値の提案を 1 件出した）。

## 発見の観点

`inventory` と `doc_code_diff`。提供側の全エンドポイントと呼び出し側の全呼び出しを列挙し
差を取る、という棚卸しでしか見えない。層の単位でまとまって現れるのが特徴で、個別の
バグ報告としては上がってこない。

## 解決の観点

`carry_through`（成果を呼び出し側まで運ぶ）。新規の実装はほとんど要らず、既存の画面へ繋ぐ
だけで解消する。根本側は `responsibility_move` — 層を積むたびに横断の導線を誰が見るかを
決めること。調査は「次の層を積む前に、層を横断する画面の背骨を検討すべき段階にある」と
結論している。

## 一般化

同じ型は、管理画面のない運用機能・記録だけして誰も読まない計測・提供したが誰も呼ばない
内部 API で再発する。到達経路が無いことは処理の欠陥ではないため、テストは全て緑のまま
成立する点が見つけにくさの本体。辞書の型は `available-but-unwired`。
