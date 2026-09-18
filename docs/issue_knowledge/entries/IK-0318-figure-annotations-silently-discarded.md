---
id: IK-0318
title: 許容されない種別の候補注釈が黙って捨てられ、生成側にも利用者にも伝わらない
status: open
recorded_at: 2026-07-20
resolved_at: null
sources:
  - docs/architecture/user_assistant_agents_survey_2026-07.md §8
  - docs/features/element_deliberation_workspace_design.md
feature_context:
  realizing: 要素について AI と検討し、その解釈を候補として残す
  layers: [theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [contract]
    governance: [completion]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 生成も破棄も、それぞれの仕様どおりに動いている。

    構造: 対象の種別ごとに許容する注釈の種別という制約が、生成側の指示にも表示にも現れない表現になっている点が表現に当たる。契約の不一致と表裏なので中。

    接続: 前段の出力契約と後段の入力契約が両立しない点が契約に当たる。

    統制: 保存されなかったのに保存されたように見えるため、この操作の「済み」の定義が実態と合っていない点が完了に当たる。契約不一致の結果とも読めるため中。
generalization:
  level: repo_pattern
  general_form: 後段が受け取れない種別の出力を前段が作り続け、捨てられた事実が誰にも伝わらない
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [inventory, trace_walk]
  note: >-
    支援エージェント横断調査で、対話から候補注釈が生まれるまでを辿り、対象の種別ごとに
    許容される注釈の種別と、生成側の指示に書かれている種別を突き合わせた。
resolution:
  perspective: [pending]
  note: >-
    未解決。解くには、①許容種別を生成側の指示に反映する（作らせない）か、②受け口を
    広げる（受け取って候補のまま持つ）か、③捨てた事実を利用者に見せる、のいずれかを
    選ぶ必要がある。どれを採るかは、要素の種別ごとの注釈の意味づけ（何を確定の対象に
    するか）を決めてからでないと選べない。
  landed_in: []
related: [IK-0304]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.contract, structure.representation]
    to: axes=processing=[none]; structure=[representation]; connection=[contract]; governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
  - date: '2026-09-19'
    field: classification.axes
    from: axes=processing=[none]; structure=[representation]; connection=[contract]; governance=[none]
    to: axes=processing=[none]; structure=[representation]; connection=[contract]; governance=[completion]
    reason: 軸ごとの再判定で、無言の破棄が「済み」の定義を実態から外している点を統制軸の要素として認めた
---

## 課題

図について AI と検討すると、意味・内訳・解釈にあたる候補注釈が生成される。しかし受け口は
対象の種別ごとに許容する注釈の種別を絞っており、図に対するそれらは保存されずに捨てられる。
捨てられた事実は、生成側の指示にも利用者の画面にも現れない。

原因は、前段の出力契約（何を作ってよいか）と後段の入力契約（何を受け取るか）が両立して
いないこと。どちらも単体では筋が通っており、間の不一致だけが問題である。

4 軸で見直すと、統制軸にも要素がある。保存されなかったにもかかわらず保存されたように見えるので、この操作の「済み」の定義が実態と合っていない。

## 発見の観点

支援エージェントの横断調査（`inventory`）で、対話から候補注釈が生まれて保存されるまでを
辿り（`trace_walk`）、許容種別の表と生成側の指示に書かれている種別を突き合わせた。

## 解決の観点

未解決（`pending`）。選択肢は三つあり、どれを採るかは「図という要素について、何を確定の
対象にするか」を決めてからでないと選べない。受け口を広げれば候補は残るが、確定できない
候補が溜まる。作らせなければ対話の自由度が下がる。捨てた事実を見せるのは最小の対処だが、
利用者には理由が分からない。

## 一般化

「後段が受け取れないものを前段が作る」構造は、対象の種類が増えるたびに生じる。捨てるなら
捨てたことを言う、作らせないなら作らせない、のどちらかに倒す。無言の破棄だけは避ける。
辞書の型 `information-dropped-as-unrepresentable` に対応する。
