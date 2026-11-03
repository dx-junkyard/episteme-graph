---
id: IK-0662
title: "学習画面の教材区画で、本文のスクロール領域と下端の印の帯の間に境界が無く、本文が帯の手前で切れて「帯が本文と開幕のボタン列を隠している」ように見える"
status: resolved
recorded_at: 2026-10-09
resolved_at: 2026-10-10
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/understanding_cycle_design.md"
feature_context:
  realizing: "学習者が教材区画を読みながら印を付け、議論の開幕で最初の一手を選ぶ"
  layers: [understanding_cycle, frontend_learning_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（指揮者の実機計測・視覚ベースライン V2''）: 教材区画は本文（material-body・スクロール）と下端の固定行
    （material-foot・印の帯と次へ）に分かれており、要素の矩形を JS で測ると重なりは無かった。本文はスクロール領域の
    下端で途中の行が切れて見えるが、foot との間に境界線も影も無く背景も同じだったため、切れた本文の上に帯が
    乗っているように読めた。処理軸 wording = 挙動（配置）は正しく、見え方が利用者の理解と一致しない。構造軸 none =
    領域の分割（スクロールする本文と固定行）自体は正しく、要素どうしの領域の割り当ての欠落ではなかった（起票時の
    decomposition の仮説は外れた）。接続軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "スクロールする領域と固定の行の境目が描かれず、領域の端で切れた内容が固定の行に隠されているように見える"
pattern: wording-mismatch
discovery:
  perspective: [reproduction]
  note: "第 23 周の視覚ベースラインで、指揮者が砂場の学習画面を拡大して重なりに見えるものを見た。第 2・3 波の後に要素の矩形を測って重なりではないと分かった。"
resolution:
  perspective: [single_point_fix]
  note: "下端の固定行（.material-region .material-foot）を flex:0 0 auto で縮まない行にし、上の境界線・上向きの影・背景を付けて、本文のスクロール領域とは別の行だと読めるようにした。帯が長いときは行の中で折り返す。"
  landed_in:
    - "frontend/public/css/styles.css"
  verification:
    methods: [manual_ui]
    unverified:
      - "スマートフォン幅と、議論の開幕の予想欄を開いた状態での見え方"
      - "重なりが無いことを固定する検査（矩形の重なりを測るガードレールは置いていない）"
related: [IK-0652, IK-0663]
view_of: []
history:
  - date: '2026-10-10'
    field: classification
    from: "title=印の帯が教材本文と開幕のボタン列に重なって隠す; axes=processing=[none]; structure=[decomposition]; connection=[unknown]; governance=[none]; cause_status=hypothesis; pattern=entry-point-proliferation"
    to: "title=境界の無いスクロール領域の端で本文が切れて隠れているように見える; axes=processing=[wording]; structure=[none]; connection=[none]; governance=[none]; cause_status=confirmed; pattern=wording-mismatch"
    reason: "指揮者の実機計測（視覚ベースライン V2''）で、要素の矩形に重なりが無く、境界の無いスクロール領域の下端の見え方だったと確かめたため"
---

## 課題

症状: 教材区画の下端で、印の帯（気になる・まだ分からない…）が本文と議論の開幕の「最初の一手」のボタン列を覆って隠しているように見える。原因: 実際の重なりではなく、本文のスクロール領域と下端の固定行の間に境界が描かれておらず、領域の端で途中の行が切れた本文の上に帯が乗っているように読めた。

## 発見の観点

砂場の実機で画面を拡大して見た（reproduction）。起票時は重なりと読んだが、後の計測で矩形は重なっていなかった。

## 解決の観点

固定行を縮まない別の行にし、境界線・影・背景で本文と分けた。見え方の問題だったので、配置の割り当ての作り直しではなく、境目を描くことで足りた。

## 一般化

スクロールする領域の端で内容が切れること自体は正常でも、境目が見えないと「隠されている」と読まれる。確かめ方は、幅と状態を変えて要素の矩形が重ならないことを測り、そのうえで境目が見えることを目で見ること（見た目の印象だけで重なりと判断しない）。
