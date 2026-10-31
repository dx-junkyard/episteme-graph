---
id: IK-0714
title: "表示式の直前の導入句だけのブロック（'… are defined by'）や式をまたいで割れた文が、式本体も先行詞も無いまま主張採否に回り deferred になる"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/agents.md"
  - "docs/pipeline/extraction-units.md"
feature_context:
  realizing: "式を導入・説明する本文から、式と結びついた主張（定義・関係）を claim 候補として採る"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [decomposition]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（論文レーダー自律周回の claim_qualification 入力の実物）: 表示式は独立した式ブロックになり、その前の導入句（'We write' /
    '… is defined via' / 'defined by'）と後ろの続き（'where …' / 'which …' / 'respectively.'）は別の本文ブロックになる。
    claim_qualification の入力（span + Source Block + 前後の文脈）には隣接する式ブロックの本体が載らないため、導入句は内容を持たず、
    式の後ろの文は先行詞（式）を失う。構造軸 decomposition = 1 つの文が式の位置で 3 つのブロックに割れる。接続軸 information =
    式ブロックはパイプラインに存在するのに、主張採否の入力に渡っていない。処理軸 none / 統制軸 none。
generalization:
  level: repo_pattern
  general_form: "構造上の区切り（式・図・表）で文を別単位に割り、割れた片側だけを後段の判定に渡すため、判定の材料から文の中身が欠ける"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回の頭脳メモ uxsim/runs/radar_loop/BRAIN_NOTES_H0.md（seq 1386・1533・1539）・BRAIN_NOTES_H1.md（seq 1507・1531）・BRAIN_NOTES_I2.md（seq 1577・1580・1637・1688）・BRAIN_NOTES_I1.md（多数）で、式の導入句・後置き定義が単独ブロックとして回り、式本体が span にも Source Block にも無いため deferred にした例を繰り返し見た。"
resolution:
  perspective: [carry_through]
  note: "両側是正。rhetorical_role は直前の文脈ブロックが同じ小節の式で本文が続きのときだけ preceding_equation を渡す（前の是正）。claim_qualification は Source Block に隣接式 adjacent_equations（前 1・後 1・同じ小節・ラベルと印字番号付き・式本文は TeX 由来のみ）を添え、前はブロック先頭の span で本文が続き語か直前の式の名指し（The last factor / This expression）で始まるとき、後はブロック末尾の span で本文が文末記号で閉じていないときだけ渡す（判定の正本 rhetorical_role/input_builder.py の refers_to_preceding_equation / ends_as_equation_intro を両段が共有）。prompt に「隣接式を参照先として採否を判断・式そのものは主張にしない」を 1 文。LLM 呼び出し数は不変。単位の併合（案②）はしていない。"
  landed_in:
    - "docs/pipeline/agents.md"
    - "src/episteme_graph/agents/rhetorical_role/input_builder.py"
    - "src/episteme_graph/agents/rhetorical_role/schema.py"
    - "src/episteme_graph/agents/rhetorical_role/prompt.py"
    - "src/episteme_graph/agents/claim_qualification/input_builder.py"
    - "src/episteme_graph/agents/claim_qualification/schema.py"
    - "src/episteme_graph/agents/claim_qualification/prompt.py"
    - "src/tests/agents/rhetorical_role/test_math_boundary_and_preceding_equation.py"
    - "src/tests/agents/claim_qualification/test_adjacent_equations.py"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "X2 seq 3425・3428・3431 の材料を再解析で再生成し、隣接式が載って deferred が減ったことの実測"
      - "続き語・名指しの正規表現に当たらない書き出し（例: 「Here the first term」以外の言い回し）での取りこぼし"
related: [IK-0712, IK-0713]
view_of: []
history:
  - date: '2026-10-17'
    field: status
    from: open
    to: resolved
    reason: "claim_qualification 側を是正（頭脳 X2 seq 3425・3428・3431・全是正入り run 40ecf8df: 指す式が材料に無く deferred / weak）。uxsim/runs/radar_loop/BRAIN_NOTES_X2.md"
  - date: '2026-10-16'
    field: resolution.landed_in
    from: docs のみ
    to: rhetorical_role の preceding_equation を追加
    reason: "rhetorical_role 側は是正（直前 2 ブロック以内に式があるときだけ preceding_equation として式を渡す）。claim_qualification 側（導入句で終わる span の後ろの表示式が材料に無い件）は未是正のため status は open のまま"
  - date: '2026-10-17'
    field: resolution.note
    from: 直前 2 文脈ブロック以内（同じ節）の式を preceding_equation として渡す
    to: 直前の文脈ブロックが同じ小節の式で、本文が続き語で始まるときだけ渡す
    reason: "精密化（X1 seq 3334 / 3361）: 2 ブロック以内の規則は話題の変わった段落にも前の式を「continue from it」で渡し誤帰属を誘った。案内の誤帰属そのものは IK-0794 で解決。claim_qualification 側の未是正は変わらず status は open のまま"
---

## 課題

症状: 「The Galileon operators are defined by」のような導入句だけのブロックや、表示式の直後から始まる 'which …' / 'where …' のブロックが
主張採否に回り、式本体が材料に無いため deferred になる。観察の出所は uxsim/runs/radar_loop/BRAIN_NOTES_H0.md・BRAIN_NOTES_H1.md・
BRAIN_NOTES_I1.md・BRAIN_NOTES_I2.md。

原因: 文書構造で表示式が独立ブロックになり、式をまたぐ文が前後のブロックに割れる。主張採否の入力は隣接する式ブロックを持たない。

追加の出所（2609.35757・TeX 経路）: uxsim/runs/radar_loop/BRAIN_NOTES_S1.md（seq 2722・2725・2743・2770・2773・2788・2791・2824 — where / which / i.e. / This is because / In the second line で始まるブロック、「given by」「gives」で終わるブロック）、uxsim/runs/radar_loop/BRAIN_NOTES_S2.md（seq 2717・2723・2744・2771・2786・2792・2795・2822・2828・2858 — 「For example, we have that」「with the variance S …」のような断片だけの span にも LLM を 1 回使う例、本文が「Equivalently, at」で切れて式へ続く例を含む）。

追加の出所（2609.35757・TeX 経路・頭脳 S0）: uxsim/runs/radar_loop/BRAIN_NOTES_S0.md（seq 2721・2724・2742・2754・2766・2769・2772・2805・2823 — 「is given by」等の導入句で終わる span の後ろの表示式、「with X and Y independent.」の前の式が材料に無く deferred / merge 止まり）。

追加の出所（2610.08751・頭脳 W1）: uxsim/runs/radar_loop/BRAIN_NOTES_W1.md（seq 3142 — 前ブロックの display 式が Previous Span に入らず、「The last factor」の照応を解けず deferred。seq 3157 — 脚注が付く対象の式が文脈に無い。seq 3139 — span が「one obtains」で終わり導出結果が式側に委ねられる。seq 3250 — equation_semantics の経路は前後の display 式を文脈に含めるので、経路差がある）。

追加の出所（2610.08751・頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3137・3152 — 式の導入文「…nuisance parameters,」「…contribute to … as」が式抜きの span になり deferred。seq 3185 — 式の直後の地の文「up to an additive constant. Here, …」が独立ブロックになり式との繋がりが材料に無い）。

追加の出所（頭脳 X1・全是正入りの再解析 run 40ecf8df（seq 3313 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X1.md（seq 3325・3337 — 式の前置き文だけのブロックが 1 span になり、式本体は Next Context。Preceding Equation の誤帰属（seq 3334・3361・3376）は IK-0794）。

未是正の確認（頭脳 X0・全是正入りの再解析 run 40ecf8df（seq 3315 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X0.md（seq 3333 — ブロックが関係詞「which」で始まり主語が直前の式。seq 3360 — 「up to an additive constant.」で始まる。seq 3327 — 「one obtains」で終わり式へ続く。seq 3348 — 記号定義の断片「where $W(kR)$ is…」が是正前の run では独立の主張になり上位文脈に並んでいた（seq 3297））。

一部解消と未是正（頭脳 X2・再解析 run 40ecf8df（seq 3317 以降））: uxsim/runs/radar_loop/BRAIN_NOTES_X2.md（seq 3326 — rhetorical_role に「Preceding Equation」区画が入り、where 節の主語（式 tex_b37）が材料で読める。未是正: seq 3425・3428・3431 — claim_qualification の Span Context には直前・直後の式が渡らず、「is the Hessian of …」「The last factor encodes …」「can be schematically written as」が主語・式の無い断片のまま主張候補になる。是正は次の波で予定。seq 3329 — 式の間の繋ぎ句「and, finally, … yields」が独立ブロックで LLM 1 回）。

残存の確認（頭脳 Y0/Y1/Y2・再解析 run 40ecf8df）（この是正がその run の取り込みより後に入ったかは照合していない）: uxsim/runs/radar_loop/BRAIN_NOTES_Y0.md（seq 3420・3441・3447・3432・3435 — 「can be written as」「of the form」「defined as」で終わる span、「where $\mu$ is …」の記号定義の断片が式なしで回る）、uxsim/runs/radar_loop/BRAIN_NOTES_Y1.md（seq 3421・3424・3433・3466・3440・3446・3452 — claim_qualification に Preceding Equation 欄も直後の式も無い）。役割判定と equation_semantics の側では式が見えている（seq 3526・3535）。

追加の出所（頭脳 Z2・2609.35757 の再解析 run 54e85a2a と 2610.08751 の再解析 run の後段）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3578・3647・3665・3677・3707・3713 ほか — 式と式をつなぐ 1 句だけのブロック（「such that … is」「with X and Y independent, we obtain the SPCF」）が役割判定で 1 回ずつ LLM を使う。2609.35757 の付録は式と 1 文が交互でこの型が多い（頭脳は 8 件と数えた））。

追加の出所（第 2 波 deploy 後の run・頭脳 Z0/Z1）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3600・3630・3675 — 「In this limit we have that」など式の導入句だけのブロックが 1 回ずつ LLM を使う）、uxsim/runs/radar_loop/BRAIN_NOTES_Z1.md（seq 3688 — 「where, to very good approximation,」の 4 語だけのブロック）。

## 発見の観点

主張採否の入力を実データで繰り返し読んだ（data_inspection）。

## 解決の観点

両側是正（2026-10-17）。claim_qualification の入力に隣接式 adjacent_equations を添えた（上の resolution.note）。以下は前段の記録: rhetorical_role の入力に、直前 2 ブロック以内に式があるときだけ preceding_equation としてその式を渡すようにした（式の後ろの続き文が先行詞を持つ）。claim_qualification の入力に隣接する式を添える件（導入句で終わる span の後ろの表示式・続き文の前の式）は未是正。単位の併合は他の消費者（学ぶ単位・出典）への影響を調べてから。

解消の確認（reproduction_rerun・頭脳 Z2・2609.35757 の再解析 run 54e85a2a と 2610.08751 の再解析 run の後段）: uxsim/runs/radar_loop/BRAIN_NOTES_Z2.md（seq 3572・3584・3590・3614・3644・3680・3707・3716 — 役割判定で継続文（where / i.e. / with など小文字始まり）の前の式が Preceding Equation 区画で渡り、where 節の対象が読める）。区画にならない大文字始まりの文では、前の式が Previous Context に入り長いと頭が「…」で切れる（seq 3602）。claim_qualification 側の隣接式は、この run の材料では確かめていない。

解消の確認（reproduction_rerun・頭脳 AA2・2609.35757 の再解析 run 54e85a2a）: uxsim/runs/radar_loop/BRAIN_NOTES_AA2.md（seq 3728・3737・3755・3797・3830・3836 — claim_qualification に Adjacent Equations の区画が入り、ラベル・印字番号（付録の (A.4)・(A.11) を含む）・TeX 全文で前後の式が渡る。where / such that / i.e. / 関係節 / 「can be written as」の参照先が読める）。残る面: ブロックの中間の span には付かない（IK-0826）。長い式は末尾「…」で切れる（seq 3761）。

## 一般化

構造上の区切りで文を割り、片側だけを判定に渡すと材料が欠ける。確かめ方は、式を導入する文を含む論文を通し、主張採否の入力に
隣接する式の本体が載ることと、導入句が deferred ではなく式と結びついた主張になることを見る。辞書の型 available-but-unwired。
