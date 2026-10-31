---
id: IK-1044
title: "分野の地図で次版の下書きを作るボタンが論文由来の候補（採用済み）のカードにしか出ず、候補が 0 件の状態では関係を採用しても画面から下書きを作れない — 関係のカードの文は「「現在の版から次版の下書きを作る」を実行すると反映できます」と、画面に無いボタンを指す"
status: resolved
recorded_at: 2026-10-28
resolved_at: 2026-10-28
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
  - "docs/features/category_gap_candidates_design.md §5.5"
feature_context:
  realizing: "教員が関係（辺）の候補を採用し、自分で次版の下書きを作って反映し、凍結まで運ぶ"
  layers: [field_atlas_s, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [responsibility]
    connection: [information]
    governance: [none]
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
    構造軸 responsibility: 次版の下書きを作る操作（POST …/atlas/skeleton/draft/from-frozen）は分野単位の操作なのに、画面の入口
    （admin.js data-gap-action="draft-from-frozen"）が論文由来の候補のカード（採用済みか判断済みのカード）の責務として置かれ、分野の
    区画（公開中の版・下書きの欄）にも関係の候補のカードにも無い。接続軸 information: 関係のカードの文 EDGE_NO_DRAFT_TEXT は
    「「現在の版から次版の下書きを作る」を実行すると反映できます」と言うが、その文を出す側はボタンが画面にあるかを知らない。
    処理軸 none（各関数は宣言どおり）、統制軸 none（凍結のゲート・帰属は正しく働く）。
generalization:
  level: repo_pattern
  general_form: "上位の単位にかかる準備操作の入口が、たまたま最初に必要になった下位の部品のカードにだけ置かれ、その部品が無い状態では別の部品の文が指す入口がどこにも無い"
pattern: last-mile-missing
discovery:
  perspective: [trace_walk, reproduction]
  note: "第 7 周で te-04 が候補 0 件・下書きなしの分野の地図タブを開き、関係のカードに［採用］の後の反映が案内されるのに下書きを作る入口が見当たらないことを観察した（run uxsim/runs/c-astro-r25-atlas-r7/20261027T171357Z-51096 seq 2・3）。admin.js のボタンの出る条件（renderGapCard の accepted && !draftExists）と関係のカードの文を突き合わせた。7 周を通じて教員が画面から下書きを作った観察は無く、毎周、指揮者が API で作っている。"
resolution:
  perspective: [responsibility_move, guardrail_fix]
  note: >-
    分野単位の準備操作の入口を、論文由来の候補カードの部品から関係（辺）の区画へも置いた: admin.js _edgeDraftCreateHtml が関係の候補区画の先頭と
    「候補の条件から外れた採用済みの関係」の区画に、次版の下書きが無いときだけ事実文 + ［現在の版から次版の下書きを作る］（アンカー
    atlas.edge-draft-from-frozen・廃止済みは押せない理由の一文）を出す。動作は既存 gapCreateDraftFromFrozen(setStatusFn) の共有で、
    POST …/atlas/skeleton/draft/from-frozen を呼ぶだけ（既にあれば 409 の事実文を押した区画の結果欄へ）。新しい骨格の書き込み経路は無い（VA9 / RE3 / AB4）。
    関係カードの案内 EDGE_NO_DRAFT_TEXT は「この区画の先頭の［現在の版から次版の下書きを作る］を押すと反映できます」と画面にある導線を指す。
    マニュアル teacher/17-admin-atlas.md#edge-draft-from-frozen・ADMIN_UI_ANCHORS 登録（3 点セット）。
  landed_in:
    - "frontend/public/js/admin.js"
    - "backend/core/help_kb/admin_ui_anchors.py"
    - "docs/manual/teacher/17-admin-atlas.md"
    - "docs/features/atlas_relation_edges_design.md"
    - "backend/tests/test_atlas_review_round25_r7.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "下書きを作る動作は論文由来の候補と同じ関数 1 つを両区画から呼ぶ（二重実装しない）。固有の差分: 入口は区画ごとに置くが、結果文は押された区画の結果欄へ返す。"
  verification:
    methods: [guardrail]
    unverified:
      - "候補が無く下書きも無い砂場で教員が画面から下書きを作り、採用した関係を反映して凍結まで歩けること（次周の再演）"
      - "ブラウザでの描画（ボタンが 2 区画に出て、下書きを作った後に消えること）"
related: [IK-0991, IK-0886, IK-0939]
view_of: []
history: []
---

## 課題

症状: 分野の地図タブで、公開中の版はあるが次版の下書きが無く、論文由来の候補が 0 件のとき、画面のどこにも［現在の版から次版の下書きを作る］
が出ない。関係（辺）の候補は採用できるが、採用後のカードの文は「次版の下書きがまだありません。「現在の版から次版の下書きを作る」を実行すると
反映できます。」と言い、指す先のボタンは画面に無い。教員は採用した関係を自分で下書きに入れられず、凍結まで運べない。第 1〜7 周を通じて
教員が画面から下書きを作った観察は無く（論文由来の候補は 7 周連続 0 件）、各周とも指揮者が API で下書きを作った。

原因: 下書きを作る入口が論文由来の候補カード（判断済み・採用済み）の部品として実装され、分野の区画にも関係のカードにも無い。関係のカードの
文は入口の所在を確かめずに固定文で案内する。

## 発見の観点

通し受講（reproduction）で候補 0 件の画面を歩き、ボタンの出る条件と案内の文を突き合わせた（trace_walk）。campaign の notes（c-r25-atlas-r7）も
同じ欠けを「IK-0991 の作成の入口の欠け」として予告していた。

## 解決の観点

未解決。

## 一般化

型 last-mile-missing: 確定の装置（下書きへの反映・凍結）はあるのに、そこへ至る準備の入口が別の部品の有無に依存して消える。確かめ方: 論文由来の
候補 0 件・下書きなしの状態で関係を 1 本採用し、画面の操作だけで下書き作成 → 反映 → 凍結まで歩けること。

## 第 7 波の是正（2026-10-28）

分野単位の準備操作の入口を、論文由来の候補カードの部品から関係（辺）の区画へも置いた: admin.js _edgeDraftCreateHtml が関係の候補区画の先頭と 「候補の条件から外れた採用済みの関係」の区画に、次版の下書きが無いときだけ事実文 + ［現在の版から次版の下書きを作る］（アンカー atlas.edge-draft-from-frozen・廃止済みは押せない理由の一文）を出す。動作は既存 gapCreateDraftFromFrozen(setStatusFn) の共有で、 POST …/atlas/skeleton/draft/from-frozen を呼ぶだけ（既にあれば 409 の事実文を押した区画の結果欄へ）。新しい骨格の書き込み経路は無い（VA9 / RE3 / AB4）。 関係カードの案内 EDGE_NO_DRAFT_TEXT は「この区画の先頭の［現在の版から次版の下書きを作る］を押すと反映できます」と画面にある導線を指す。 マニュアル teacher/17-admin-atlas.md#edge-draft-from-frozen・ADMIN_UI_ANCHORS 登録（3 点セット）。

## 第 8 周の再演（2026-10-28・judge_atlas_r8）

**GONE（入口の存在）**。下書きなし・論文由来の候補 0 件の te-04（run uxsim/runs/c-astro-r25-atlas-r8/20261027T223243Z-92717 seq 2〜6）で、関係の区画の先頭に「編集中の次版の下書きはありません。…先に公開中の版を複製して次版の下書きを作ります（AI は使いません）。」と［現在の版から次版の下書きを作る］が出た。採用済みカードの文も「この区画の先頭の［現在の版から次版の下書きを作る］を押すと反映できます。」と実在のボタンを指す。te-01 の 1 回目（20261027T223540Z-94369 seq 2・6・7）にも出ていた。**押されてはいない** — 場面の steps に `admin.atlas.edges.draft_from_frozen` が無く（ハーネス H45）、下書きは 8 周目も指揮者が API で作った。教員が画面だけで一巡できることの確かめは第 9 周へ。残り（軽）: 未採用のカードの「[採用] を押すと、次版の下書きへ反映できます」は下書きなしでは採用だけでは反映できず、区画の先頭の文と少しずれる。

## 第 9 周の再演（2026-10-31・judge_atlas_r9）

**GONE（教員が画面だけで一巡した — 8 周未観察だった経路）**。下書きなし・論文由来の候補 0 件の te-04（run uxsim/runs/c-astro-r25-atlas-r9/20261031T003210Z-30687）が、採用（seq 4・「宇宙膨張 — 一般相対論」を依存で）→ 見送り（seq 5）→ 関係の区画の［現在の版から次版の下書きを作る］（seq 6・確認「現行版 2026.5 を複製して下書きを作ります。下書きは学習者には表示されません」→ `POST …/skeleton/draft/from-frozen` 200 はペルソナの手）→ 反映の確認（seq 7）→ 反映（seq 8）まで画面の操作だけで進み、続く te-03（20261031T015006Z-36651）が凍結 2026.6 まで運んだ。**指揮者は API で下書きを作っていない**（r9_prereqs・lane_atlas.log）。第 8 周の残り（未採用カードの案内文のずれ）も GONE: 下書きなしでは「[採用] を押し、この区画の先頭の［現在の版から次版の下書きを作る］で下書きを作ると反映できます。」、下書きありでは「[採用] を押すと、次版の下書きへ反映できます。」と言い分けた（seq 2・8）。

## 第 10 周の再演（2026-10-31・judge_atlas_r10）

**GONE（2 周連続の画面だけの一巡）**。te-02 が関係の区画の［現在の版から次版の下書きを作る］で下書きを作り（run uxsim/runs/c-astro-r25-atlas-r10/20261031T052922Z-65581 seq 6・`from-frozen` はペルソナの手・指揮者の API 介入なし）、反映し、別の教員 te-01 が凍結 2026.7（uxsim/runs/c-astro-r25-atlas-r10/20261031T053235Z-67481 seq 10）。第 9 周（te-04 → te-03・2026.6）に続く。

## 第 11 周の再演（2026-10-31・judge_atlas_r11）

**GONE（維持・3 周連続）**。te-05 が区画先頭の［現在の版から次版の下書きを作る］で下書きを作り（run uxsim/runs/c-astro-r25-atlas-r11/20261031T101202Z-98536 seq 6）、te-04 が凍結 2026.8（uxsim/runs/c-astro-r25-atlas-r11/20261031T101612Z-580 seq 10）。指揮者の API 介入なし。
