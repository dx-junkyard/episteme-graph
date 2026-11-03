---
id: IK-0940
title: "学習者のコースの分野の地図（L1）は、コースの論文が「論文の位置」に置かれていても全領域を「霧 — 論文未投入」と描き、同じ画面の L2 と論文の海の濃淡と食い違う（霧の判定が解析成果の概念配置と台帳だけを数え、論文の配置を数えない）"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/field_atlas_overlay_spec.md"
  - "docs/features/knowledge_landscape_design.md"
feature_context:
  realizing: "学習者がコースの分野の地図を開き、どの領域にコースの論文があるかを読む"
  layers: [field_atlas_s, knowledge_landscape, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [aggregation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 aggregation: 「この領域に論文があるか」の判定が 2 つ残っている。L1 の霧（atlas_view.py の kind=fog）は
    atlas_state.compute_overlay_rows の被覆 = theory_components の骨格配置（placed_count_by_region）と概念の
    has_corpus_state / has_binding だけから決まり（atlas_state.py 1214-1221）、landscape_placements を数えない。
    論文の海は IK-0847 の是正で配置から霧を決めるようになり、学習者の L2「論文の位置」レイヤーと出典タブも配置を読む。
    砂場（cartridge_id を付けずに解析した TeX 教材 = コーパス概念の骨格配置なし・台帳未記帳）では L1 が全領域霧、
    同じ時刻の論文の海は複数の領域を「少しある」「蓄積あり」と塗る。接続軸 meaning: 霧の固定文「この領域には、まだ
    コーパスの論文が関係づけられていません」（FOG_VERIFY_LINE / pill）は配置がある事実と矛盾する。
    IK-0847 は海側だけ直し「atlas_state 側の霧と海の霧を 1 つに寄せるかは判断していない」と残した。処理軸 none・統制軸 none。
    確認: run 20261023T070701Z-16885 seq 5（/api/atlas の regions の kind が全件 fog）・seq 6 / 7（同じコースの
    landscape に論文ごとの配置）、run 20261023T070912Z-18387 seq 4（同時刻の論文の海の濃淡）。
generalization:
  level: repo_pattern
  general_form: "同じ「ここに資料があるか」の判定の正本を 2 つのうち片方の画面でだけ直し、もう片方の画面が古い材料で否定を描き続けるので、同じ利用者が 2 つの画面で逆のことを読む"
pattern: duplicate-canonical-sources
discovery:
  perspective: [reproduction, trace_walk]
  note: "学生ペルソナが L1 の全領域霧と L2 の論文の位置の多数の配置を同じ画面で並べ、矛盾を記した。/api/atlas の regions の kind と landscape の placements、atlas_state の被覆の材料を辿った。 run: uxsim/runs/c-astro-r25-atlas-r3/20261023T070701Z-16885・20261023T070912Z-18387。"
resolution:
  perspective: [canonical_source]
  note: >-
    霧の判定を 1 つにした（指揮者の判断・推奨案）。atlas_state.load_corpus_snapshot が landscape_placements の生きている配置（status ∈
    LEARNER_VISIBLE_STATUSES・旧版の node_id は atlas_correspondence の resolver で現行版へ読み替え）を
    CorpusSnapshot.placed_node_ids に読み、compute_overlay_rows
    が領域そのもの・その概念に配置があれば灯す。論文の海と同じ状態集合。配置は真偽だけを使い記帳の件数には足さない（CR3）。配置だけで灯った領域の検証行は
    REGION_PLACED_ONLY_VERIFY_LINE（台帳はまだ無いと言う）。キャッシュの陳腐化検知（_SIGNATURE_SQL）に landscape_placements.updated_at
    を足した。CR2 は霧の定義の是正として抵触しないと判断（field_atlas_overlay_spec.md §15）。残課題: L1 はドメイン単位の判定、海は本人が閲覧できる論文での判定という差は残る。
  landed_in:
    - "backend/core/atlas_state.py"
    - "docs/features/field_atlas_overlay_spec.md"
    - "backend/tests/test_atlas_review_round25_r3.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "「この領域に論文があるか」の材料を海と L1 で同じ集合にした（IK-0847 で片方だけ直した残り半分）。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場のコースで L1 の霧と海の塗りが一致することは再演で未確認"
      - "霧が晴れた領域の「学ぶ」導線先の挙動は未確認（領域行は詳細パネルに載らない）"
related: [IK-0847, IK-0888, IK-0941]
view_of: []
history: []
---

## 課題

症状: コースの論文が L2 の「論文の位置」で多くの場所に置かれているのに、同じオーバーレイの L1 は全領域が
「霧のハッチ — 論文未投入の地形」。同じ時刻の論文の海は複数の領域を「少しある」と描く。学生は「どちらが正しいのか」と止まる。

原因: L1 の霧は解析成果の概念配置と台帳だけを数え、論文の配置（landscape_placements）を数えない。海の側だけが配置に寄せられた。

## 発見の観点

通し受講（reproduction）で学生が 2 つの層の食い違いを記し、/api/atlas と landscape の応答と atlas_state の材料を辿った（trace_walk）。

## 解決の観点

第 3 波（2026-10-23）で是正。霧の判定を 1 つにした（指揮者の判断・推奨案）。

## 一般化

IK-0847 と同じ二重判定の残り半分。「霧」「未投入」「未検証」のような否定の状態を描く面が複数あるとき、正本を 1 つに
寄せないと、直した面と直していない面が逆のことを言う。確かめ方: 配置あり・概念配置なしの分野で L1 と海を同時に開き、
霧の領域が一致することを見る。
