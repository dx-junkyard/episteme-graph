---
id: IK-1009
title: "学習チャットの文脈に添える「この箇所の式」（formula_fact_line）が、番号の無い復元式を抜粋 1 つにつき「番号のない式［AI復元］」1 語に畳むため、同じ抜粋の中のどの式が復元でどれが原文どおりかが AI にも学生にも分からず、整った TeX の式まで復元扱いで語られる"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
feature_context:
  realizing: "学生がチャットで式の出所（原文か AI の復元か）を尋ね、式ごとの確からしさを答えてもらう"
  layers: [rag_chat, learner_experience_b]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [information]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: hypothesis
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    仮説: 構造軸 representation — backend/core/formula_facts.py::formula_fact_line は印字番号のある式だけを個別に「式 (N)［AI復元］」と書き、
    番号の無い復元式は数えずに 1 語へ畳む（「数えない」は数値非表示のため）。式を区別する手がかり（記号・式の冒頭）が行に無い。
    接続軸 information: その結果、抜粋の中の式ごとの印が文脈へ運ばれない。仮説: 出典 8（TeX 経路の別教材の抜粋）に付いた印が、
    その抜粋の中の崩れた PDF 由来の式によるのか、整った TeX の式そのものに付いた誤りなのかは確かめていない — 出典 8 は固定コースの
    sources 外で、学習者の source-chunk API は 404 を返し式の記録を読めなかった。確定には、その chunk の formulas の reconstructed と
    source_latex を見ることが要る。処理軸 none。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "要素ごとの確からしさの印を、要素を数えない要約にすると、どの要素の印かが失われる"
pattern: information-dropped-as-unrepresentable
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    学生 st-02 が「TeX 由来の抽出の式にも［AI復元］が付くのはなぜか」と尋ね、AI は「注記は式ごとではなく資料の抜粋ごとに付いている」と
    答えた。製品頭脳 A・B のノート（4055 / 4056 / 4062 / 4063）にも、原文 TeX の式まで復元扱いに見えるという同じ記述が繰り返し出た。
    run: uxsim/runs/c-astro-r25-studio-r5/20261027T072352Z-12975 seq16・17、uxsim/runs/round25/BRAIN_NOTES_product_{A,B}_r5.md。
resolution:
  perspective: [representation_change]
  note: >-
    IK-0997 と同じ是正（core/formula_facts.formula_fact_line の式単位化）。番号の無い復元式を 1 語に畳まず、式本文の先頭 $…$ か定める記号で名指す（数は書かない）。名指せないものだけ「番号のない式［AI復元］」を 1 度書く。仮説のうち、出典 8 の印が誤付与かどうかは chunk の formulas を読めず確かめていない。
  landed_in:
    - "backend/core/formula_facts.py"
    - "backend/tests/test_round25_wave5_chat_rag.py"
  verification:
    methods: [guardrail]
    unverified:
      - "出典 8（固定コースの sources 外）の chunk の formulas の reconstructed と source_latex"
      - "砂場での再演（st-02 の問い）"
related: [IK-0977, IK-0895, IK-0577, IK-1008]
view_of: []
history: []
---

## 課題

症状: チャットが「この抜粋の式は復元」としか言えず、整った TeX の式まで「原文として断定できない」と扱う。学生は式ごとの確からしさを得られない。

原因（仮説を含む）: 文脈の事実行が番号の無い復元式を 1 語に畳み、式を区別する手がかりを落とす。TeX の抜粋の式自体に印が誤って付いているかは未確認。

## 発見の観点

学生の通し受講（reproduction）と製品頭脳のノート（data_inspection）の両方で、同じ「抜粋ごとの印」の記述が出た。

## 解決の観点

第 25 周 第 5 波で解決（frontmatter の resolution）。当初の案: 番号の無い式も、数を言わずに式を区別できる手がかり（定義する記号・式の冒頭 20 字程度）を添えて個別に書く（数値を出さない規律は保てる）。
あわせて TeX 経路の chunk の formulas の印を確かめ、原文と一致する式に印が付いていれば IK-0895 の判定（source_latex との一致）の取りこぼしとして直す。

## 一般化

確からしさの印は要素の単位で運ぶ。確かめ方: 復元式と原文どおりの式が混ざる抜粋をチャットの文脈に入れ、事実行からどの式が復元かを言えることを見る。

第 6 周の再演（2026-10-27）: **GONE**（畳み込み）。チャットが「[出典5] では、式 (1)〜(4) すべてに［AI復元］の印」と式ごとに言った。印そのものが教材と食い違う点は IK-0895。run: uxsim/runs/c-astro-r25-studio-r6/20261027T123926Z-13979 seq15。

第 6 波（2026-10-27）: 式単位化の後も、同じ式 (1)〜(4) が表示中の教材では印なし・[出典5] では［AI復元］と食い違った（製品頭脳 A 4248 / B 4251）。判定の材料が版ごとに違う（TeX 版の記録と PDF 版のチャンクの記録）ことを IK-1040 に記帳し、同じ論文の別の版の原文の式で照合する是正を入れた。
