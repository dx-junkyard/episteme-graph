---
id: IK-0317
title: 保存の失敗を握りつぶして成功を返し、利用者には別の症状として現れる
status: resolved
recorded_at: 2026-07-20
resolved_at: 2026-07-20
sources:
  - docs/architecture/user_assistant_agents_survey_2026-07.md §8
  - docs/features/assistant_common_infra_design.md
feature_context:
  realizing: コース構築の対話履歴を保存し、再訪時に続きから設計できるようにする
  layers: [course_builder, shared_infra]
classification:
  axes:
    processing: [resource]
    structure: [none]
    connection: [none]
    governance: [completion]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    原因は「保存の例外を握りつぶし、呼び出し元へ成功として返していること」。入力も契約も
    妥当で、その一箇所の例外処理を直せば周囲に波及しないため局所。調査記録 §8 が
    「書き込み失敗でもチャット API は 200、再読込で履歴消失に見える」と実装位置つきで
    確認している。
generalization:
  level: general
  general_form: 失敗を握りつぶして成功を返すため、利用者は別の症状（消えた・保存されていない）として経験する
pattern: failure-reported-as-success
discovery:
  perspective: [inventory, trace_walk]
  note: >-
    支援エージェント横断調査で、各機能の失敗時の振る舞いを比較した。保存の戻り値と
    応答の状態コードを辿ると、失敗経路が応答に一切現れないことが分かった。
resolution:
  perspective: [first_class_state, single_point_fix]
  note: >-
    失敗を呼び出し元へ伝え、利用者に事実として見せる。応答本体を止めるかどうかは
    機能ごとの縮退規約に従う（対話本体は縮退して 200、明示の保存操作は失敗を返す）。
    握りつぶしを残したまま再試行を足す案は、失敗の事実が依然として見えないため採らない。
  landed_in:
    - backend/api/services.py
    - backend/api/routes/admin.py
    - docs/features/assistant_common_infra_design.md
related: [IK-0307]
view_of: []
history:
  - date: '2026-09-19'
    field: classification
    from: primary=local facets=[local.resource, governance.completion]
    to: axes=processing=[resource]; structure=[none]; connection=[none]; governance=[completion]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

症状は「チャットで設計したのに、再読込すると履歴が消えている」。利用者からは消失に見えるが、
実際には保存が失敗していただけで、失敗は応答のどこにも現れていなかった。

原因は、保存処理の例外を握りつぶして呼び出し元へ成功として返していたこと。呼び出し元は
正常応答を返すので、利用者は「保存されなかった」ではなく「消えた」と経験する。

## 発見の観点

支援エージェント横断調査で、各機能の失敗時の振る舞いを並べて比較した（`inventory`）。
保存の戻り値から応答の状態コードまで辿り（`trace_walk`）、失敗経路が応答に現れないことを
確認した。

## 解決の観点

保存の成否を握りつぶさず、戻り値と応答の一級のフィールドにして利用者へ事実として見せた
（`first_class_state`）。応答本体を止めるかどうかは機能ごとの縮退規約に従う（対話の本体は縮退して返し、明示の
保存操作は失敗を返す）。握りつぶしたまま再試行を足す案は、失敗の事実が依然として
見えないので採らなかった。

## 一般化

「止めないための握りつぶし」は、止めないという目的自体は正しいことがあるが、事実を消して
よい理由にはならない。止めないなら、縮退したことを利用者に見せる。
新パターン `failure-reported-as-success` として辞書へ提案する。
