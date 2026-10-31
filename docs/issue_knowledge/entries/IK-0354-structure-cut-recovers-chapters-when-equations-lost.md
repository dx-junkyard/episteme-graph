---
id: IK-0354
title: PDF 経路で式が全件導出から除外されると、代替の claim チェーンが出現順で依存を埋めるため、構造層が実質空のまま埋まって見え、接点の狭さで構造を切っても章分けしか出ない
status: open
recorded_at: 2026-09-23
resolved_at: null
sources:
  - docs/features/theory_module_layer_design.md §3.1
  - docs/features/theory_module_layer_design.md §7.3
  - docs/architecture/knowledge_reproduction_review_2026-09-19.md
feature_context:
  realizing: 論文の理論構造を式の依存から読み取り、共通構造を抽象化して他の論文・分野へ転用する
  layers: [pipeline_a, theory_artifacts, graph_review]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [meaning]
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
    接続軸: 式が使えないときの代替である claim チェーンは、前の主張を次の step の
    required_claim_ids に置いて依存の顔をしているが、代替であることは validation_issues の
    info（derivation_equation_only_fallback）にしか残らず、グラフ・画面・構造解析には伝わらない。
    下流は出現順を依存として読み、構造で切ると章の path がそのまま一つずつ潰れる（meaning）。
    確認手段: 2609.15375v1 で式レコード 64 個がすべて derivation_excludes_inconsistent_equation で
    除外され、主張の DAG が 470 ノード・455 辺・連結成分 15・分岐ゼロであること、同じ DAG に接点の
    狭さで切り出しを掛けると 15 モジュール = 15 章に戻ったこと。処理軸: 代替が位置（出現順）で
    依存を埋める処理（derivation_chain agent の _build_claim_chains）が依存の捏造に当たる
    （logic。仕様どおりの動作なので medium）。構造軸: 「この教材では式の層が再現されていない」を
    グラフの側で持つ状態が無く、空の層が主張の列で埋まって見える（representation。受け皿の欠如か
    接続の欠落かで迷ったので medium）。統制軸: 順序・予算・担当の統制は関係しないので none
    （式が除外されること自体は信頼の方針どおりで、IK-0328 の範囲）。
generalization:
  level: general
  general_form: 主たる情報源が全滅したときの代替が出現順で関係を埋め、代替であることが下流に伝わらないため、構造の解析は原文の並び順しか取り出せないのに構造が見つかったように見える
pattern: fallback-fabricates-missing-link
discovery:
  perspective: [data_inspection, reproduction]
  note: >-
    理論モジュール層の外枠の試作で、式チェーンを持つ TeX 経路の論文では理論の区切りと一致する
    モジュールが出たのに、PDF 経路の論文に同じ規則を掛けると章の数と同じモジュールに戻った。
    実データを読むと PDF 経路 10 本はすべて式チェーン 0 本で、詳細層は主張の出現順の列だった。
resolution:
  perspective: [pending]
  note: >-
    未解決。設計書は、モジュールの成員を入力式と出力式の両方を持つ step に限り（TM3）、claim
    チェーンを式の依存に数えない。式の step が無い教材では理論モジュールを組まず、その事実を
    事実文で出す（available:false）。claim チェーンが順序を required_claim_ids に書くこと自体を
    やめる案（(b-A)）は、TeX 再取り込み（実測 3）の後にオーナー判断で問う。式の層そのものの再現は
    IK-0328（PDF 由来の式）と再現性レビュー §5.2（vision OCR）の範囲。
  landed_in: []
related: [IK-0353, IK-0328, IK-0114]
view_of: []
history: []
---

## 課題

**症状**: 理論モジュール層（接点の狭さで式の依存に境目を入れる中間層）の規則を PDF 経路の論文に
掛けると、理論の区切りではなく章分けが出る。2609.15375v1 では 15 モジュール = 15 章だった。

**原因**: PDF 経路では式の抽出が信用できず（本文テキスト層から式が取れず散文から推測した記録が残る）、
式レコードはすべて信頼の方針で導出から除外される。derivation_chain は代替として claim チェーンを
作り、章ごとに主張を出現順に並べて前の主張を次の step の依存に置く。代替であることは
validation_issues の info にしか残らないため、グラフの上では式の層が埋まっているように見え、
構造の解析は原文の並び順（章ごとの一直線の path）しか取り出せない。

## 発見の観点

外枠の試作を TeX 経路と PDF 経路で同じ規則で動かし（`reproduction`）、結果の違いの原因を実データで
確かめた（`data_inspection`）。

## 解決の観点

未解決。設計書 §5.2 と TM3 で、代替の順序を構造の入力にしない線引きを先に置く。代替を埋めない側に
倒し（fail_closed に近い）、欠落を事実文で出す。代替の生成そのものを変える案は A層の契約変更なので、
式の層がどこまで再現されるかを見てから判断する。

## 一般化

主たる情報源が失われたとき、代替が位置や出現順で関係を埋めると、その上の構造解析は「代替の形」
（原文の順序）を構造として取り出してしまう。辞書の `fallback-fabricates-missing-link` に当たる。
代替であることを後段まで運ぶか、代替を構造の入力から外すか、どちらかがないと、解析結果が正しいのか
入力が空だったのかを区別できない。
