---
id: IK-0804
title: "TeX 取り込みで本体に $…$ を含む地の文マクロ（\\lcdm → $\\Lambda$CDM\\xspace）を数式として $…$ で包み、$$\\Lambda$CDM$ が display math の開きと読まれて数百字が 1 数式になる"
status: resolved
recorded_at: 2026-10-17
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/overview.md"
feature_context:
  realizing: "TeX 原文の論文マクロを展開し、本文のインライン数式を $…$ のまま後段に渡す"
  layers: [pipeline_a]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [none]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確定: tex_archive._text_mode_macro_math は展開結果に制御語か _ ^ があれば数式とみなして外側を $…$ で包んでいた。本体が自分で $ を区切るマクロ（$\\Lambda$CDM）や書体命令だけのマクロ（{\\tt COMET}）も包まれ、$$ の衝突が生じた。数式の中で同じマクロを展開しても $ が入れ子になる。処理軸 input_handling = マクロ本体の種類（地の文 / 数式）を見ずに一律の規則で包んだ。他の軸は none。原本 2610.08751 で 79 箇所の $$ を確認し、是正後 0。
generalization:
  level: repo_pattern
  general_form: "ソースの展開で展開先の文脈（地の文か数式か）を見ずに区切り記号で包み、区切りが入れ子・衝突して後段の区間判定が崩れる"
pattern: rule-enforced-per-surface
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 U1 が document 1471ff7f（2610.08751）の rhetorical_role 修復要求 4 件（seq 3022・3076・3091・3112）を読み、$$\\Lambda$CDM space$ の先頭 $$ が display math と解釈され span が数式内に落ちて invalid_char_range になることを見た。出所: uxsim/runs/radar_loop/BRAIN_NOTES_U1.md。"
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: "地の文では本体に $ を含むマクロを外側で包まず本体を地の文として整形（_clean_prose に退避）、書体命令だけのマクロは数式にしない（_TEXT_MODE_COMMANDS）。数式の中では _math_mode_macros が本体の $ を剥がし外側の語を \\text{} に包む。マクロ定義の \\xspace は取り込み時に除く。隣り合う $ + $ は空白で離す。原本 4 本を読み直し 2610.08751 の $$ 79→0、他 3 本はテキスト不変。"
  landed_in:
    - "backend/core/document_pipeline/tex_archive.py"
    - "backend/tests/test_latex_display.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: extracted
      note: "展開の正本（_expand_simple_macros）に数式用のマクロ表を渡す 1 点で、インライン・表示式の両経路を揃えた。設計前に原理は読んでいない。"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "再取り込み後の rhetorical_role で invalid_char_range 修復が消えること（実 LLM）"
related: [IK-0782, IK-0696]
view_of: []
history: []
---

## 課題

症状: `\lcdm` 系マクロの展開で本文に `$$\Lambda$CDM\xspace$` が残り、先頭の `$$` が display math と読まれて 7..597 のような数百字が 1 数式扱いになる。rhetorical_role の span が数式内に落ち修復 1 回分の LLM 呼び出しが毎回余計にかかる（この論文で 4 件）。

原因: 地の文マクロの展開結果を一律に `$…$` で包んでいた。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_U1.md（seq 3022・3061・3076・3079・3091・3112）。

是正前の出所（2610.08751・頭脳 W0）: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3177・3189 — $$\Lambda$CDM\xspace$ 等の $ 二重化。seq 3180・3195・3237 — $ の偶奇がずれて span 境界が数式区切りの内側で切れる。seq 3282 — その断片主張が dsl_linking の accepted_claims まで流れる）。TeX マクロ是正の前に取り込んだ素材。

是正前の出所（2610.08751・頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3178・3187 — 全模型名で $$X$CDM\xspace$ の $ 二重化。seq 3181・3196・3235・3238 — $ の偶奇がずれて span 境界が数式の内側で切れ、tex_b158 では 4 span 中 3 span が数式内境界）。TeX マクロ是正の前に取り込んだ素材。

是正前の出所（2610.08751・頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3179・3194・3236 — span 境界が崩れた $$w_0w_a$CDM\xspace$ や $\gls{rsd}\xspace$ の内側に落ち、中心的な解釈文が 2 span に分断。seq 3266 — 序論 tex_b31 の役割付き span も数式内で切れる。seq 3281 — 数式片で始まる・切れる主張が thesis_reconstruction の accepted_claims まで届く）。TeX マクロ是正の前に取り込んだ素材。

切り分けの所見（頭脳 X1・全是正入りの再解析 run 40ecf8df（seq 3313 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3397 — 役割判定の入力のブロック本文では「$w_0w_a$CDM」「${\rm Mpc}$」の $ の偶奇が正しく、是正前の run で見えた「span が数式の内側で切れる」は役割判定の入力ではなく、その後の span の文分割で生じていたと読める）。

## 発見の観点

修復要求の offset と本文の $ 区間を突き合わせた（data_inspection）。

## 解決の観点

マクロ本体の種類で包み方を分けた（単一点の是正 + 回帰テスト）。

実機の再現確認（reproduction_rerun）: 2026-10-17 03:49 に 2610.08751（document 1471ff7f）を是正入りの api-server で全再解析（run 40ecf8df）し、再パース後の chunks.text の全チャンクで `$$` の残骸ゼロを確かめた（是正前の同じ論文には fixTexMacros の計測で多数あった）。

解消の確認（reproduction_rerun・頭脳 X0・全是正入りの再解析 run 40ecf8df（seq 3315 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3354・3396 — 役割判定の材料で $\Lambda$CDM が 1 組の $ で出て、$ の二重化が消えた）。

解消の確認（reproduction_rerun・頭脳 X2・再解析 run 40ecf8df（seq 3317 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3353・3365・3368 — $\Lambda$CDM / $K$CDM / $w$CDM / $w_0w_a$CDM が 1 組の $ で描かれ、是正前に span が「(in particular $$」で割れた tex_b111 も文単位で割れる）。

解消の確認（reproduction_rerun・頭脳 Y1/Y2・再解析 run 40ecf8df）: uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3409・3469 — 「VDG$_\infty$」「$w_0w_a$CDM」の $ の偶奇が正しく渡る。seq 3476 — 是正前に数式内で割れた tex_b111 の span 境界が正しい）。

## 一般化

展開先の文脈を見ずに区切りで包むと区切りが衝突する。辞書の型 rule-enforced-per-surface の近傍。

