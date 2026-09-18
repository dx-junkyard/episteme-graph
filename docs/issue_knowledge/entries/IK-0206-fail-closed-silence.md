---
id: IK-0206
title: 候補なし・未処理・権限なし・障害が同じ非表示に畳まれ、空と壊れが区別できない
status: resolved
recorded_at: 2026-07-16
resolved_at: 2026-07-16
sources:
  - docs/architecture/vision_ux_gap_survey_2026-07.md §2 G3
  - docs/architecture/vision_ux_gap_survey_2026-07.md §4
feature_context:
  realizing: 想定外や未成立のときに黙って隠すという安全側の原則を守りながら、利用者に現状を伝える
  layers: [frontend_learning_ui, personal_network, reconstruction_r]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    処理: 個々の判定は設計どおりに動く。ただし取得の失敗と例外を同じ経路で握りつぶす点は、
    後始末の不良と読む余地が残るため確信は中。
    構造: 候補が無い・まだ処理していない・権限が無い・取得に失敗した、という意味の異なる四つの
    状態が「表示しない」という単一の表現へ写され、必要な区別が表現上どこにも存在しない。
    接続: 状態が段階の間で落ちているとも読めるが、落ちる場所は表現への写像であって前後の
    受け渡しではない。
    統制: 隠すか見せるかの線引きが決まっていないのは設計判断の不在であり、担当・順序・予算・
    レビュー・完了判定のいずれにも当たらない。
generalization:
  level: general
  general_form: >-
    意味の異なる複数の状態が同じ無表示へ畳まれ、利用者が空と壊れを区別できない
discovery:
  perspective: [invariant_audit, symptom_report]
  note: >-
    安全側に倒す原則が要求する挙動と、初期状態・障害時に利用者が見る画面を突き合わせた。
    通信の失敗も例外も同じ経路で握りつぶされ、機能が存在すること自体に気づけないことが分かった。
resolution:
  perspective: [representation_change, explicit_contract]
  note: >-
    隠すことを選んだ以上は対になる表現が要る、という見立てで、空状態の説明・障害と空の区別・
    準備中の表示を足した。ただし設計上ここは隠すのが正しいと決まっている箇所（記帳の無い台帳・
    骨格の無い地図）は変えず、どちらに倒すかの線引き自体を決めごととして残した。
  landed_in:
    - frontend/public/js/app.js
    - frontend/public/js/reconstruction.js
    - frontend/public/js/personal-map.js
    - docs/architecture/vision_ux_gap_survey_2026-07.md §6
related: [IK-0204]
view_of: []
pattern: information-dropped-as-unrepresentable
history:
  - date: '2026-09-19'
    field: classification
    from: primary=structure facets=[structure.representation, connection.information]
    to: axes=processing=[none]; structure=[representation]; connection=[information];
      governance=[none]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 学習者の初期状態では、地図も気づきの候補も台帳も再構成も黙って消える。通信障害で
取得に失敗したときも同じように消える。利用者からは「まだ何も無い」のか「壊れている」のか
「自分には権限が無い」のかが区別できない。

**原因**: 想定外は例外ではなく非表示にする、候補は本人が確定するまで見せない、少人数では
集計ごと消す、といった安全側の原則を徹底した結果、意味の異なる状態がすべて同じ「何も出ない」
という表現に写像された。原則そのものは守られており、欠けているのは原則の代償を受け止める表現。

## 発見の観点

`invariant_audit`（原則が要求する挙動と実際の画面の照合）と `symptom_report`。原則に違反した
箇所を探すのではなく、**原則を守った結果として利用者に何が見えるか**を見る視点が要る。
違反を探す監査では見つからない類の課題である。

## 解決の観点

`representation_change` — 「隠す」を選んだ以上、空状態の説明・障害と空の区別・準備中の表示が
対になっていないと壊れたシステムと区別できない、という見立て。全部を表示する方向には
倒していない。設計として隠すのが正しい箇所は維持し、どちらに倒すかの線引きを明示した
（`explicit_contract`）。

## 一般化

安全側の既定（fail-closed・匿名化・候補非表示・押し付けない）を持つあらゆる機能で再発する。
原則の違反ではなく原則の副作用なので、ガードレールが緑のまま成立し続けるのが特徴。
辞書の型は `information-dropped-as-unrepresentable`。
