---
id: IK-1074
title: "前提の地図の「依存の広がりを計算できない前提」（IK-1058 の読み時の段）の行が前提の文だけのボタンで、DTO が持つ検証スコープの被覆の段・論文・記帳の日時を描かないため、スコープを記帳した前提と記帳していない同文の前提 3 件が同じ行として並び、スコープの記帳が地図のどこにも現れない"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/features/doubt_layer_issues.md"
feature_context:
  realizing: "教員が手で記帳した前提にスコープを書いたとき、その記帳が前提の地図の見え方に反映されて、どの前提がどこまで確かめられたかを地図から読み分ける"
  layers: [doubt_d]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（コード + 読み取り）: routes/doubt.py の assumption-atlas は unplotted_points[] に scope_coverage / unscoped / verification_status /
    document_id を載せるが、frontend/public/js/doubt-atlas.js の unplotted の描画（data-doubt-unplotted-point）はボタンに p.label だけを描く。
    散布図の点は横軸（被覆）で記帳の有無が位置に出、同じ文面の点は「ほか、同じ文面の点」と畳まれるのに、この区画は畳まず被覆も描かない。
    砂場の読み取り（episteme-uxsim api コンテナ・書き込みなし）: 1bad7d9f の範囲の手の前提 3 件（9332f0e6 / ec587be0 / fcccdc54・同文・
    Cepheus TeX に結び済み・load_score NULL）のうち ec587be0 だけが第 8 周に記帳したスコープ 1 件を持つ（scope_coverage=single）。
    te-02 の画面（seq3・8・10）は 3 行とも「前提N: ［磁場の乱れが小さく…］（押すと台帳）」で区別できない。
    接続軸 contract = サーバが渡す区別の手掛かりを画面が描かず、地図の「被覆」の約束がこの区画では切れている。
generalization:
  level: general
  general_form: "一覧の主表示に載らない行を別区画へ逃がすとき、主表示が位置や印で運んでいた属性（記録の有無・出所）を別区画で落とすと、記録を足しても見え方が変わらず、同じ文面の行を区別できない"
pattern: last-mile-missing
discovery:
  perspective: [trace_walk, data_inspection]
  note: "第 8 波の是正（IK-1058）の後、te-02 の前提の地図の区画と DTO・描画関数を並べ、砂場の台帳行を読み取りで数えた。run: uxsim/runs/c-r25-doubt-r9/20261031T003210Z-30688（seq3・8・10）。"
resolution:
  perspective: [carry_through]
  note: >-
    assumption-atlas の unplotted_points[] の各行に detail_line を足した（routes/doubt.py::_attach_unplotted_detail_lines・
    正本 label_vocab.assumption_unplotted_detail_line）:「検証スコープの被覆: {散布図の横軸と同じ段の語} ／ 論文『{題名}』 ／
    記帳: YYYY-MM-DD HH:MM（前提の記帳日時・日本時間）」。散布図では位置が運ぶ被覆を文で運び、同じ文面の前提を論文と日時で
    見分ける（IK-1060 と同じ形）。doubt-atlas.js はボタンの下に素通しで描き（data-doubt-unplotted-detail）、runner の
    unplotted_lines も同じ 1 行を写す。件数・生数値は出さない。日時・題名が引けなければ固定文で縮退（行は出す）。
  landed_in:
    - "backend/api/routes/doubt.py"
    - "backend/core/label_vocab.py"
    - "frontend/public/js/doubt-atlas.js"
    - "frontend/public/admin.html"
    - "uxsim/runner/doubt_exec.py"
    - "backend/tests/test_doubt_round25_wave9.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の固定コースの同文の前提の画面（te-02）を再演していない（第 10 周）"
      - "畳み（同文を 1 行にまとめる）はしていない — 日時と論文で見分ける形を選んだ"
related: [IK-1058, IK-1060, IK-1028]
view_of: []
history: []
---

## 課題

症状: 第 9 周 te-02 の前提の地図（固定コース 1bad7d9f）に、第 8 波の「依存の広がりを計算できない前提」区画が初めて出た（IK-1058 の読み時の段は成立）。
しかし 3 行とも同じ文「磁場の乱れが小さく、乱流と磁場のエネルギー等分配が成り立つという前提で…」だけで、第 8 周にスコープを記帳した前提も記帳していない前提も
同じ見え方。前提 → 台帳 → スコープ → 地図の最後の区間で、記帳が地図に現れない。

## 発見の観点

画面の区画・サーバの DTO・描画関数・台帳行の 4 つを突き合わせた。

## 解決の観点

主表示が位置で運ぶ属性（被覆）と区別の手掛かり（論文・日時）を別区画でも文で運ぶ（上の resolution.note）。

## 一般化

型 last-mile-missing。主表示から外した行の区画を作るときは、主表示が運ぶ属性の一覧を先に挙げ、別区画でもどの形で運ぶかを決める。

## 第 10 周の再演（第 25 周・面 B 第 10 周、2026-10-31・judge_doubt_r10 — 第 9 周と同じコース 1bad7d9f・同じ前提 3 件の前後比較）

**GONE（画面で 1 本通った）**。区画の 3 行の下に「検証スコープの被覆: 記帳なし / 1件 / 記帳なし ／ 論文『…Cepheus B…』 ／ 記帳: 2026-10-28 02:04 / 07:18 / 2026-10-31 09:25」が出て、第 8 周にスコープを記帳した ec587be0 だけが「1件」（第 9 周は 3 行とも文だけ）。区画のボタン（harness H52 の是正後）→ 前提1（9332f0e6）の台帳 → スコープ記帳 → 再読込で前提1 が「記帳なし」→「1件」に変わった（砂場の読み取りでも 9332f0e6 / ec587be0 がスコープ 1 件）。前提 → 台帳 → スコープ → 地図が 10 周目で初めて画面で通った。残り（軽）: ①行の「記帳: 日時」は前提の記帳日時で、スコープを記帳した日時ではない — スコープを記帳した直後も前提1 は「記帳: 2026-10-28 02:04」のまま、台帳のスコープ行には日時が無い（教員は「記帳」をどちらと読むか迷う）②同文の行は畳まない（散布図側は「ほか、同じ文面の点」で畳む — 是正記録のとおり選んだ形）。合意リスト末尾の同じ前提の行（81 行目）は観察の上限で切れた（harness H59）。run: uxsim/runs/c-r25-doubt-r10/20261031T052922Z-65583（seq3・8・9・10・11）。

## 第 11 周の再演（第 25 周・面 B 第 11 周、2026-10-31・judge_doubt_r11 — 固定コース 1bad7d9f・3 周連続の同じ対象）

H59 の投影の是正で、合意リスト末尾の手の前提 3 行（81〜83 行目）が属性ごと写った: スコープ 1 件の前提1・2 は「検証状態（教員の記帳）: 未検証」、記帳なしの前提3 は「未検証（スコープの記帳なし）」で、上の区画の被覆（1件 / 1件 / 記帳なし）と一致（GONE の維持）。STILL（軽）: 同文の前提 3 件のどれを開いたかは、台帳の見出し・対象の行からは区別できない（区画の行の「記帳: 日時」は台帳側に無い — te-02・te-03 の頭脳がともに挙げた）。run: 98534 seq3・9 / 99946 seq3。
