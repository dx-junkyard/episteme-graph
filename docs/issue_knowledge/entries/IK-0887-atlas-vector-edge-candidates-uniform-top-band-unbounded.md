---
id: IK-0887
title: "ベクトル索引を作ると、関係（辺）の候補と学習者の「推定の糸」がアンカーの近さだけで大量に立ち、全件が同じ最上位の段階ラベル・根拠なし・近さ順でない並びで出て、教員も学習者も読み分けられない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
  - "docs/features/atlas_vector_anchoring_design.md"
feature_context:
  realizing: "分野の地図のアンカーの近さから関係の候補を教員に提示し、学習者には推定の糸として見せる"
  layers: [relation_edges_re, vector_anchoring_va, frontend_admin_ui, frontend_learning_ui]
classification:
  axes:
    processing: [logic]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: low
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation: 辺候補の vector 由来は「最上位帯（ANCHOR_NEARNESS_THRESHOLD_NEAR）以上」だけを通すので
    （atlas_edges/derive.py derive_vector_pairs）、nearness_label は定義上全件「かなり近い」になり、段階ラベルが候補どうしを
    区別する情報を持たない。根拠は共起由来にしか付かず（IK-0849）、vector 由来には論文も逐語も無い。
    処理軸 logic: derive_vector_pairs は cosine 降順に並べるが、derive_edge_candidates が最後に (from_id, to_id) の
    英字 id 順で並べ直すので、教員の一覧から近さの順が消える。教員の一覧に上限が無い（学習者の糸だけ
    THREADS_MAX_PER_NODE / THREADS_MAX_TOTAL で切る）。
    接続軸 meaning（medium）: 最上位帯の閾値は gap の表記×アンカーの言語間レジームで校正した値で（label_vocab
    ANCHOR_NEARNESS_SCALE）、プロトタイプ同士（label + 「領域: …」の同じ書式の短文。confirmed 配置が無いと
    根拠の行が付かない — atlas_vectors/schema.py build_anchor_source_text）の比較に流用している。同じ書式を
    共有する短文どうしは cosine が底上げされやすく、天体物理として無理な組（ブラックホール — 暗黒エネルギー、
    大規模構造形成 — 惑星形成 等）が通る。これは仮説で、確定は保存済みベクトルの cosine 分布を見ること。
    統制軸 none（low: 候補は candidate のまま教員の判断を待つので確定は人間に残っている）。
    確認: run 20261022T215750Z-73308 seq 2・20261022T220051Z-74929 seq 2〜7（全件「プロトタイプ近傍（かなり近い）」・
    共起由来なし）・20261022T220438Z-77657 seq 6/8（糸が全件「かなり近い」）。第 1 周は索引が空で vector 由来は 0。
generalization:
  level: repo_pattern
  general_form: "最上位の段階だけを通す閾値で候補を立て、その段階ラベルを候補に付けるため、ラベルが全件同じになって判断材料にならず、さらに並べ直しで近さの順も失われる"
pattern: threshold-reused-across-regimes
discovery:
  perspective: [reproduction, data_inspection]
  note: "教員ペルソナが「全部『かなり近い』で同じに見える。どれを採っても大差ない」と上から押し、学生ペルソナが糸の組を「学習者を誤導しうる」と書いた。edge-candidates 応答の origins / nearness_label と derive の並べ直しを突き合わせた。 run: uxsim/runs/c-astro-r25-atlas-r2/20261022T220051Z-74929・uxsim/runs/c-astro-r25-atlas-r2/20261022T220438Z-77657。"
resolution:
  perspective: [representation_change, order_and_budget]
  note: >-
    辺候補の並べ方・上限・ラベルの意味を直した。derive_edge_candidates の最終整列を「共起を持つ組 → 近さの順位（derive_vector_pairs の近い順。値は持ち出さない）→ (from, to)」にし、教員の一覧は derive.cap_for_review（近さだけの組は 1 ノード 2 組・全体 30 組。共起と判断済みは畳まない）で絞って folded_note（件数なし）と［すべて表示］（show_all=true）に畳む。全件同じになる近さの段階ラベルは教員カードで描かず、何が近いと言っているかの basis_line（「両方の概念の説明文どうしが近いという推定です。論文の配置の根拠はありません。」等）を出す。閾値の校正は未実施（実 DB に触れないため）で、保存済みベクトルを読むだけの分位スクリプトを置いた。
  landed_in:
    - "backend/core/atlas_edges/derive.py"
    - "backend/core/atlas_edges/schema.py"
    - "backend/api/routes/atlas_edges.py"
    - "backend/core/atlas_review_display.py"
    - "backend/scripts/atlas_anchor_pair_distribution.py"
    - "frontend/public/js/admin.js"
    - "backend/tests/test_atlas_review_round25_r2.py"
  verification:
    methods: [guardrail]
    unverified:
      - "閾値（ANCHOR_NEARNESS_THRESHOLD_NEAR）をプロトタイプ同士の分布で校正していない — scripts/atlas_anchor_pair_distribution.py の実測が要る"
      - "学習者の推定の糸の閾値と一文は未変更"
related: [IK-0849, IK-0846]
view_of: []
history: []
---

## 課題

症状: ベクトル索引を作った後の関係（辺）の候補が大量に並び、全件が「プロトタイプ近傍（かなり近い）・AIによる推定（未確認）」で、
論文の題名も逐語も無い。並びは英字の id 順で、近い順でもない。学習者の推定の糸も全件「かなり近い」で、分野として無理な組が
点線で並ぶ。教員は「どれを採っても大差ない」と上から押した。

原因: 最上位帯だけを通す閾値で候補を立て、その段階ラベルを付けるのでラベルが情報を持たない。並べ直しで近さの順が消え、
一覧に上限が無い。閾値は別の比較レジームで校正した値の流用（仮説）。

## 発見の観点

再演（reproduction）で教員と学生の双方が判断材料の無さを書き、応答の origins / nearness_label と導出コードを読んだ（data_inspection）。

## 解決の観点

第 2 波（2026-10-23）で是正。並べ順・上限・ラベルの意味を直し、閾値はプロトタイプ同士の分布で校正し直す。

## 一般化

段階ラベルの最上位だけを候補化の条件にすると、どの候補にも同じラベルが付く。近さ・関連度で候補を立てる層（論文レーダー・
コーパスを補う論文・同一性の候補）で同じ形が出うる。確かめ方: 候補の段階ラベルが 2 種以上に分かれるか、分かれないなら
ラベルを出していないことと、一覧が近さ順・上限付きであることを見る。

## 第 3 周の再演（2026-10-23・campaign c-astro-r25-atlas-r3）

- GONE（教員側の並べ方）: 一覧は近さの上位で畳まれ「ほかにも…」+［すべて表示］、各カードに何が近いと言っているかの一文
  （「論文の配置の根拠はありません」）が出る（run 20261023T032742Z-99530 seq 9 / 10）。
- 第 3 周の再演: 残存（材料と校正）。候補はすべてプロトタイプ近傍で共起は 0、「ブラックホール — 暗黒エネルギー」
  「宇宙線 — 宇宙天気」のような語の近さだけの組が並び、精読型の教員は新規に採用しなかった（run 20261023T070217Z-13812
  seq 9）。閾値の校正（scripts/atlas_anchor_pair_distribution.py の実測）は未実施のまま。
- 第 3 周の再演: 残存（学習者の糸）。推定の糸はすべて「かなり近い」の一色で、糸ごとの根拠の一文が無い（教員側の
  basis_line は学習者に届いていない）。「重力レンズ ⋯ 重力波」を学生が「語の似かた」と読んだ（run 20261023T070701Z-16885
  seq 6 / 8）。是正の未着手分（学習者の糸の閾値と一文）がそのまま見えた。

## 第 3 波の是正（2026-10-23）

- 閾値の校正は**この波でも行っていない**（事実として残す）: 分位スクリプト
  `backend/scripts/atlas_anchor_pair_distribution.py` は砂場・実 DB で走らせない規律のため、コードの既定値
  （`ANCHOR_NEARNESS_THRESHOLD_NEAR`）は不変。「校正未実施」と、値を変えるときの手順（対象 DB で 1 回走らせ、
  分位を設計書に記録してから変える）を `docs/features/atlas_vector_anchoring_design.md` §13.1 に記録した。
- 教員側は、導出から外れた関係・凍結を止める関係・反映の確認にも採用時の出所（`mapping_justification`）から
  根拠の一文を出すようにした（IK-0937 / IK-0939）。学習者の糸の一色と根拠の一文の欠落は**残課題**のまま。
