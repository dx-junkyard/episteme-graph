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
    governance: [none]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「受け口が対象の種別ごとに許容する注釈の種別を絞っているのに、その制約が
    生成側の指示にも利用者への表示にも現れないこと」。生成側は妥当な候補を作り、
    受け口は妥当に弾いているので、両者の契約が両立していない。調査記録 §8 が
    「黙って破棄される・LLM にも教員にも制約が伝わらない」と実装位置つきで確認している。
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
---

## 課題

図について AI と検討すると、意味・内訳・解釈にあたる候補注釈が生成される。しかし受け口は
対象の種別ごとに許容する注釈の種別を絞っており、図に対するそれらは保存されずに捨てられる。
捨てられた事実は、生成側の指示にも利用者の画面にも現れない。

原因は、前段の出力契約（何を作ってよいか）と後段の入力契約（何を受け取るか）が両立して
いないこと。どちらも単体では筋が通っており、間の不一致だけが問題である。

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
