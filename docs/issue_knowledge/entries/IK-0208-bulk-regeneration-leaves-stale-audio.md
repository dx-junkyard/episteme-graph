---
id: IK-0208
title: 一括の作り直しが派生キャッシュを無効化せず、古い読み上げが実質永続する
status: resolved
recorded_at: 2026-07-17
resolved_at: 2026-07-17
sources:
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §2 P2
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §4
  - docs/development_checklist.md §3
feature_context:
  realizing: コース本文を作り直したうえで、本文に対応した読み上げ音声を配信する
  layers: [lecture_studio, lecture_player, course_builder]
classification:
  primary: connection
  facets: [connection.version, structure.aggregation]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    個別編集の経路も一括生成の経路も、それぞれ単体では正しく動く。原因は、一括の経路で
    本文を作り直したあとに派生物である音声の世代が更新されず、段階の間で版がずれること。
    無効化の呼び出しが更新の入口ごとに散っているため、片方だけ直しても次の入口で再発する。
generalization:
  level: general
  general_form: >-
    更新の入口が複数あり、そのひとつが派生物の無効化を伴わないため、古い派生物が配信され続ける
discovery:
  perspective: [boundary_walk]
  note: >-
    「再実行とキャッシュ無効化」という境界を歩き、個別編集の経路にある無効化の呼び出しが
    一括の経路に無いことを突き合わせて見つけた。表示は新しく、聞こえる音声は古いという
    食い違いになるため、片方だけを見ていると気づけない。
resolution:
  perspective: [carry_through, canonical_source]
  note: >-
    一括の経路にも同じ無効化を同乗させ、二つの経路を対称にした。準備完了の判定を一箇所の
    正本に寄せる整理も同時期に行い、表示・音声・準備完了の三者が同じ分割規則を通るようにした。
    境界の回帰テストを完了条件に含める規律へ反映した。
  landed_in:
    - backend/core/course_content_builder.py
    - backend/api/routes/lecture_studio/topics.py
    - backend/core/lecture.py
    - docs/development_checklist.md §3
related: [IK-0207, IK-0209]
view_of: []
pattern: stale-derivative-served
history: []
---

## 課題

**症状**: コース内容を作り直しても、トピックに紐づく読み上げ音声が古いまま配信され続ける。
本文は新しいのに、聞こえるナレーションは前の版という食い違いが実質的に永続する。

**原因**: 本文を作り直す経路が二つある。個別に編集する経路には音声キャッシュを消す処理が
あるが、一括で作り直す経路には無い。無効化の責務が更新の入口ごとに分散しており、後から
足した入口が漏れた。

## 発見の観点

`boundary_walk`。「再実行」と「キャッシュ無効化」の境界を歩いて、同じ対象を更新する二つの
経路の処理を突き合わせた。片方の経路だけを見ていると正しく見える。

## 解決の観点

`carry_through`（無効化を更新の経路に同乗させる）+ `canonical_source`（準備完了の判定を
一箇所に寄せ、表示と音声が同じ規則を通るようにする）。「音声側で本文の更新時刻を見て判断する」
案もあったが、判断の正本が増えるため採らなかった。

## 一般化

キャッシュ・投影・索引・派生ファイルを持つあらゆる機能で再発する。とくに更新の入口が増える
たびに漏れやすく、入口の数だけ無効化を書く設計である限り構造的に残る。辞書の型は
`stale-derivative-served`。
