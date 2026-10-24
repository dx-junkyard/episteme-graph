---
id: IK-0944
title: "講義のスライドに渡す数式はそのスライドが引く式だけの部分集合なのに、学習画面の描画（app.js renderMaterialChunk）が式を id と「位置 FORMULA_i」の両方の鍵で表に入れ、位置の鍵が後から id の鍵を上書きするため、2 枚目以降のスライドの式が 1 つずつずれて別の式が差し込まれる（「読む」では正しい）"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/lecture_slide_sync_design.md"
feature_context:
  realizing: "学習者が数式を含むトピックを講義（スライドのタイマー送り）で読み、読む表示と同じ式を見る"
  layers: [lecture_player, frontend_learning_ui, frontend_admin_ui]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation: frontend/public/js/app.js renderMaterialChunk（7895〜7904 行）は formulas の各要素を id（"[[FORMULA_n]]" と正規化形）と
    位置（"FORMULA_" + idx / "[[FORMULA_" + idx + "]]"）の同じ名前空間の鍵で登録し、後の要素の位置の鍵が先の要素の id の鍵を上書きする。処理軸 logic:
    読む表示は全トピックの式を番号順に持つので位置と id が一致し差が出ないが、講義（7509 行 pseudoChunk の formulas = slide.formulas）は
    core/lecture.py _assign_slide_formulas（520 行）がそのスライドの参照だけに絞った部分集合で、位置 i と FORMULA_i が食い違う。st-04 の t11 スライド 2 は
    [[FORMULA_1]]〜[[FORMULA_7]] が [[FORMULA_2]]〜[[FORMULA_14]] の式に置き換わった（サーバの割当ては id どおり = 生応答で確認）。接続軸 target（medium）:
    スライド単位の式の束と描画の解決表で「どの式か」の対象がずれる。admin-lecture-studio.js 4349 行（lsRenderCourseMaterialPreview）も同じ登録順。統制軸 none。
    IK-0404 の解決記録は「病的な衝突例でだけ差が出る」と書いたが、スライドの部分集合ではすべての 2 枚目以降で起きる。
generalization:
  level: general
  general_form: "識別子と並び順の 2 系統の鍵を 1 つの表に同じ名前で入れるため、要素を部分集合にすると並び順の鍵が識別子の鍵を上書きし、別の要素が引かれる"
pattern: id-namespace-conflated
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    st-04 が講義のスライド 2 で「構造ネットワーク $r$ は、半径 $\hat\varepsilon_c$、…」と記号が 1 つずつずれていると報告し、読む表示とチャットの答えで正しい
    入力を確かめた。sequence の生応答（スライドの formulas は id どおり）と app.js の解決表を並べた。runner の state.resolve_formula_placeholders は app.js と
    同じ規則の写しなので投影は忠実。run: uxsim/runs/c-astro-r25-studio-r3/20261023T072502Z-354（seq8・13）。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: "読む表示と講義は build_topic_slides の戻り値を同じ契約で受け取る。式の解決表は id の鍵（[[FORMULA_n]]・正規化形）を優先し、並び順の鍵（FORMULA_i）は id の鍵がまだ空いているときの救済だけにした。app.js renderMaterialChunk と原稿スタジオの 3 箇所のプレビューを同じ規則にそろえ、部分集合の formulas で [[FORMULA_k]]（k ≠ 位置）が自分の式に解決されることをガードレールで固定した。"
  landed_in:
    - "frontend/public/js/app.js"
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/core/lecture.py"
    - "backend/tests/test_lecture_reading_lecture_contract.py"
  verification:
    methods: [guardrail]
    unverified:
      - "ハーネスの写し（uxsim の resolve_formula_placeholders）は投影同期係が揃える（この是正では触っていない）"
      - "講義のスライド 2 枚目以降の式の表示は実画面で見ていない"
related: [IK-0404, IK-0389]
view_of: []
history: []
---

## 課題

症状: 数式の多いトピックを講義で開くと、2 枚目以降のスライドで本文中の式が 1 つずつ後ろの式に入れ替わる（最後の式が 2 回出る）。同じ本文を「読む」で
開くと正しい。学習者は講義と読むのどちらが正しいか分からず、記号の意味を取り違える。

原因: スライドに付く式はそのスライドが引く式だけ（サーバの割当ては正しい）。学習画面の描画は式の表を id と並び順の両方の鍵で作り、並び順の鍵が
後から id の鍵を上書きする。部分集合では「並び順 1 番目 = FORMULA_1」が成り立たない。

## 発見の観点

学生の報告（講義と読むで式が違う）から、講義の列の生応答とフロントの解決表を辿った。

## 解決の観点

第 25 周 面 D 第 3 周の是正（2026-10-23）で解決。読む表示と講義は build_topic_slides の戻り値を同じ契約で受け取る。（以下は起票時の見立て）id の鍵を優先する（並び順は id を持たない要素の救済に限る）か、講義にもトピック全体の式を渡す。前者は 3 箇所の写し（学習画面・原稿スタジオ・
ハーネス）を同時に直す。

## 一般化

識別子と位置を同じ名前空間に入れる表は、要素を絞った途端に別物を引く。確かめ方は、式を 3 つ以上含む 2 枚目のスライドで各プレースホルダが自分の
式に解決されることを見ること。
