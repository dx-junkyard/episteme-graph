---
id: IK-0213
title: 権限の判定結果が応答に無く、画面が過剰な隠蔽と過小な隠蔽に分かれる
status: resolved
recorded_at: 2026-07-17
resolved_at: 2026-07-18
sources:
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §2 P4
  - docs/architecture/vision_ux_gap_survey_2026-07-17.md §4
feature_context:
  realizing: 共有された教材・コースについて、見られる人と操作できる人を画面に正しく反映する
  layers: [versioning_v, frontend_admin_ui, image_library_l]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [assignment]
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    サーバ側の権限判定は正しく、画面側の描画も与えられた情報に対しては妥当。原因は、判定の
    結果が応答に含まれず後段の画面へ渡らないため、画面が手元の情報から推測して分岐すること。
    条件が段階間で伝わらないという接続の定義に当たる。判断の主体が画面側へ滑っている点は
    副次の統制の問題。
generalization:
  level: general
  general_form: >-
    権限の判定結果が表示側へ渡らず、表示側が推測で分岐して、できる操作が隠れたりできない操作が見えたりする
discovery:
  perspective: [inventory, invariant_audit]
  note: >-
    同じ「所有者だけができる操作」を持つ画面を横に並べたところ、一方はボタンごと消して
    閲覧者が自分の状態を確認できず、もう一方は全員にボタンを見せて押すと素のエラーになる、
    という正反対の対処に分化していた。設計書が「選択肢自体を出さない」と書いた箇所が
    条件の有無だけで判定されている例も同じ形だった。
resolution:
  perspective: [carry_through, fail_closed]
  note: >-
    応答に所有者かどうかの判定結果を載せ、画面はそれを読む。フラグが取れないときは権限が
    無い側に倒す（推測しない）。方針としては「見せて 404」でも「隠して不可視」でもなく
    「見せて、できない操作は理由付きで無効化する」へ統一した。
  landed_in:
    - backend/api/routes/figure_presentation.py
    - frontend/public/js/versioning.js
    - docs/architecture/vision_ux_gap_survey_2026-07-17.md 追補
related: [IK-0206]
view_of: []
pattern: permission-and-affordance-asymmetric
history:
  - date: '2026-09-19'
    field: classification
    from: primary=connection facets=[connection.condition, governance.assignment]
    to: axes=processing=[none]; structure=[none]; connection=[condition]; governance=[assignment]
    reason: 排他の主分類を廃し、副分類を 4 軸の座標に写す（2026-09-19 座標化）。旧 facets を全て保持
---

## 課題

**症状**: 版の管理で、コース側は所有者以外にボタンごと非表示（閲覧者は自分がいまどの版を
見ているかを確認できない）。文書側は逆に全員へ操作ボタンを見せ、押すと素のエラーが返る。
例示画像を含めるかの確認も、設計書は「所有者以外には選択肢自体を出さない」と書いているのに、
実装は別の条件の有無だけで表示していた。

**原因**: 応答に所有者かどうかの判定結果が含まれないため、画面側が手元の情報から推測して
分岐するしかない。推測の仕方は画面ごとに違い、過剰な隠蔽と過小な隠蔽という正反対の実装に
分化した。

## 発見の観点

`inventory` と `invariant_audit`。同じ性質の操作を持つ画面を横に並べ、権限の扱いを比べると、
分化していること自体が見える。個別の画面だけを見ると、どちらも「それなりに妥当」に見える。

## 解決の観点

`carry_through`（判定結果を応答で運ぶ）+ `fail_closed`（取れないときは権限の無い側へ倒す）。
表示方針としては、閲覧者にも状態を見せつつ、できない操作を理由付きで無効化する形に統一した。
隠す・見せるの二択で揃える案は、どちらに揃えても片方の利用者が損をするため採らなかった。

## 一般化

権限を持つ側と表示する側が分かれているあらゆる画面で再発する。判定の材料を渡さないと、
表示側は必ず推測を始める。辞書の型は `permission-and-affordance-asymmetric`。
