---
id: IK-0211
title: 取り消せない操作の確認が経路ごとに非対称で、無確認のまま全教材の再抽出が始まる
status: resolved
recorded_at: 2026-07-17
resolved_at: 2026-07-18
sources:
  - docs/architecture/vision_ux_gap_survey_2026-07.md §2 G5-3
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §2 P3
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §4
feature_context:
  realizing: 取り消せない操作の前に、何が起きるかを人へ示して合意を取る
  layers: [frontend_admin_ui, versioning_v, field_atlas_s]
classification:
  primary: structure
  facets: [structure.aggregation, governance.review]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    各画面の確認処理はそれぞれ動き、実行される操作自体も正しい。原因は、確認の正本が
    ひとつの画面モジュールの内側に閉じていて他モジュールから使えず、同じ重大度の操作に
    別々の確認水準が付くこと。共通部品の置き場所を変えない限り、新しい画面で再発する。
    重大度と確認水準を対応づける手続が無い点は副次の統制の問題。
generalization:
  level: repo_pattern
  general_form: >-
    取り消せない操作の確認が共通部品として公開されておらず、経路ごとに水準がばらつき、無確認の経路が残る
discovery:
  perspective: [inventory, invariant_audit]
  note: >-
    取り消せない操作を全て列挙し、それぞれの確認の水準を突き合わせた。削除には名前の入力を
    求める丁寧な確認があるのに、不可逆の凍結や全教材の再抽出は素の確認だけ、あるいは確認ゼロ
    という逆転が見えた。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    確認の部品をモジュールの外へ公開し、他モジュールの取り消せない操作をそこへ寄せた。
    あわせて、失われるものを具体的に述べる文面（何件の未レビュー分類が消えるか等）へ改めた。
    重大度の判定を各画面に任せる案は採らなかった — 判定が分散すると同じ逆転が再発する。
  landed_in:
    - frontend/public/js/admin.js
    - frontend/public/js/versioning.js
    - docs/architecture/vision_ux_gap_survey_2026-07-17.md §4
related: [IK-0209, IK-0212]
view_of: []
pattern: destructive-action-without-confirmation
history: []
---

## 課題

**症状**: 取り消せない操作の確認が重大度と対応していない。削除は名前の入力まで求めるのに、
不可逆の骨格凍結・ライブラリの廃止・ステージの上書き実行は素の確認だけ。最悪の例では、
スキーマ提案の「システム全体へ適用」が**確認ゼロで全教材の再抽出を即時に開始**していた。

**原因**: 確認の共通部品が一枚の画面モジュールの内側に閉じており、他モジュールから呼べない。
そのため統一が及んだのは同じモジュール内の九箇所だけで、別モジュールの操作は素の確認や
無確認のまま残った。共通化が「同じファイルの中」でしか成立していなかった。

## 発見の観点

`inventory`（取り消せない操作の全列挙）と `invariant_audit`。個別の画面を見ている限りは
どれも「確認している」ように見えるため、操作を横に並べて水準を比べないと逆転に気づけない。

## 解決の観点

`canonical_source` — 確認の部品を外へ公開し、全モジュールがそれを使う形にした。文面も
「何が失われるか」を具体的に述べるものへ変えた。各画面が重大度を判断する案は退けた
（判断が分散すると同じ逆転が戻る）。

## 一般化

共通化した安全装置が「同じモジュールの中」でしか共有されていないと、境界の外に無防備な経路が
残る。確認・監査・権限チェック・レート制限など、横断して効くべき装置すべてで再発する。
辞書に同型が無いため新しい型として提案する（`destructive-action-without-confirmation`）。
