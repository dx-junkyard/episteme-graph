---
id: IK-0847
title: "論文の海の地図は、論文が配置されている領域でも全領域が「霧」になり、厚みの塗りと概念への円が描かれない（霧の判定が分野の地図の被覆、厚みが論文の配置と、2 つの「被覆」で決まり、霧が厚みを上書きする）"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/features/corpus_roaming_design.md"
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
feature_context:
  realizing: "学習者が論文の海で、分野のどの領域に閲覧できる論文がどれだけ厚くあるかを段階の塗りと円で読む"
  layers: [corpus_roaming, field_atlas_s, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 aggregation: 「この領域に論文があるか」の判定が 2 つある。霧（kind=fog）は atlas_state.py:1214-1221 の
    被覆（theory_components の配置・台帳の記帳・binding）から決まり（atlas_view.py:418）、論文の海の厚みは
    landscape_placements から決まる（corpus-sea.js:368 densityIndex）。corpus-sea.js:452 は霧の領域を厚みで塗らないので、
    砂場（台帳未記帳・コーパス概念の配置なし）では 10 領域すべてが霧になり、8 行の配置があるのに凡例の段階が一度も出ない。
    さらに atlas_view.py:447 は霧の領域の概念ノードを L1 に出さないため、corpus-sea.js:346 anchorPoint が概念アンカーの
    位置を引けず円が描かれない（領域そのものへの配置の円だけが残る）。接続軸 meaning（medium）: 地図の「霧＝論文未投入」の
    意味と海の「厚み」の意味が同じ画面で食い違う。処理軸 none（各関数は自分の定義どおり）。統制軸 none。
    確認: run 20261022T124315Z-36565 seq4 の /api/atlas 応答（regions の kind）と /api/learning/corpus/landscape 応答
    （placements の region_id / anchor_node_id）。
generalization:
  level: repo_pattern
  general_form: "同じ「ここに資料があるか」を 2 つの別の材料から判定し、一方の否定が他方の肯定を上書きして表示するため、資料があるのに無いと描かれる"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction, trace_walk]
  note: "学生ペルソナが「全部霧なのに丸は出ている」と記し、/api/atlas の region kind と corpus/landscape の配置、corpus-sea.js の renderMap を順に辿って、霧の判定と厚みの判定が別材料であることを確かめた。 run: uxsim/runs/c-astro-r25-atlas-r1/20261022T124315Z-36565（頭脳の所見 uxsim/runs/round25/BRAIN_NOTES_atlas.md）。"
resolution:
  perspective: [single_point_fix]
  note: >-
    論文の海（corpus-sea.js）の霧を骨格の被覆（r.kind）ではなく海自身の配置から決める（配置の無い領域だけを霧に描く）。L1 に概念ノードが無い配置（骨格側で霧の領域の概念は出ない）は配置の region_id の箱に円を並べる。コース側の分野の地図の霧の意味は変えない（CR2）。
  landed_in:
    - "frontend/public/js/corpus-sea.js"
    - "frontend/public/index.html"
    - "backend/tests/test_corpus_roaming_ui_static.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "海の画面の中では霧と厚みを同じ材料（配置）の 1 つの判定から出す。コース側の地図には手を入れない。"
  verification:
    methods: [guardrail]
    unverified:
      - "ブラウザでの描画（円の並び・凡例の段階）は未確認"
      - "atlas_state 側の霧の判定と海の霧を 1 つに寄せるかは判断していない"
related: [IK-0846]
view_of: []
history: []
---

## 課題

症状: 論文の海の地図で全 10 領域が「霧」、概念アンカーの円が出ず、凡例「閲覧できる論文の厚み」の段階が一度も見えない。
論文の一覧には配置済みの論文が並ぶ。原因: 霧は分野の地図の被覆、厚みは論文の配置と、別の材料で判定し、霧が厚みを
上書きする。霧の領域では概念ノードも出ないので、概念への配置の円が置けない。

## 発見の観点

ペルソナの通し受講で学生が地図と一覧の食い違いを記し（reproduction）、API 応答と描画関数を辿った（trace_walk）。

## 解決の観点

海の画面では霧と厚みを配置の 1 つの材料から決める。

## 一般化

同じ問い（資料があるか）に 2 つの判定があり、片方の否定が表示上優先されると、資料がある所が空に見える。
確かめ方は、骨格の被覆ゼロ・配置ありの分野で海を開き、配置のある領域に段階の塗りと円が出ることを見ること。

## 第 2 周の再演（2026-10-23・campaign c-astro-r25-atlas-r2）

- 画面では未確認。学生ペルソナは「全領域が霧のまま・円は小」と書いたが（run 20261022T220616Z-78776 seq 9）、ハーネスの
  論文の海の投影（`uxsim/runner/atlas_exec.py::sea_map_lines`）が霧を骨格の `r.kind == "fog"` で決めており、この是正
  （配置から霧を決める `corpus-sea.js densityIndex`）を写していない。審判 B13 の検出も同じ投影を読んでいる。
  偽所見として `uxsim/runs/round25/harness_fixes_atlas.md`（第 2 周 H12）に書いた。
- 応答からの読み替え: /api/learning/corpus/landscape の placements は各行に region_id（stars / high_energy / cosmology /
  ism_star_formation / observation_methods 等）を持ち、/api/atlas の L1 の region id と一致する。是正後の `densityIndex` は
  これで領域ごとの論文の集合を作るので、配置のある領域は霧にならず段階の塗りになる（製品は是正済み・投影の遅れ）。
  ブラウザでの描画は引き続き未確認。

## 第 3 周の再演（2026-10-23・campaign c-astro-r25-atlas-r3）

- GONE（論文の海）: 領域が「少しある」「蓄積あり」「霧」に分かれ、概念と領域に円（小・中）が出て、
  どこが厚いかが 1 操作で読めた（run 20261023T070912Z-18387 seq 4。投影は同期済み）。
- 残り半分は新規に起票: コースの分野の地図 L1 は同じ時刻に全領域が霧（IK-0940）。海の円の名前に骨格側の霧が残る（IK-0941）。
