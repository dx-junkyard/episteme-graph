---
id: IK-0990
title: "論文の海の縁は、教員が論文由来の候補（星間磁場）を地図に取り込んで凍結した後も「この領域の先に、まだ地図に置かれていない主題を扱う論文があります: Cep B の論文」と言い続ける — 学生には地図に増えた項目と縁の主題が同じか別か読めない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/corpus_roaming_design.md §6.1"
  - "docs/features/category_gap_candidates_design.md §5.6"
feature_context:
  realizing: "学生が凍結で版の上がった地図と論文の海を読み比べ、地図の外に残る主題を知る"
  layers: [corpus_roaming, category_gaps, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 version: core/corpus_view.py _visible_gap_signals / _build_fringe は landscape_gap_signals（論文単位の信号・status active）を現行凍結版の region に実在するかだけで絞り、信号の主題が現行凍結版に項目として既にあるか（教員側のレビューキューが atlas_gaps/store.py の resolved 集合で外している条件）を見ない。信号は再解析まで active のまま残るので、取り込み・凍結の後も縁は旧版の事実を言う。教員の判断は学習者に開示しない（§5.6）ので decisions 表ではなく、凍結版の項目との照合で足りる。構造軸 none、処理・統制軸 none。
generalization:
  level: repo_pattern
  general_form: "同じ信号から導出する 2 つの面のうち、一方だけが現行版で解消済みの行を除外し、もう一方は古い版の事実を言い続ける"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: "凍結 2026.2（星間磁場を星間物質・星形成に追加）の直後、学生が論文の海で縁の Cep B の論文と地図の星間磁場を見比べ、『磁場の主題ならもう地図にあるのでは』と記した。corpus_view の縁の SQL と教員側の resolved の除外を突き合わせた。run: uxsim/runs/c-astro-r25-atlas-r5/20261027T071949Z-96124 seq 4、凍結は uxsim/runs/c-astro-r25-atlas-r5/20261027T070623Z-23509 seq 15。"
resolution:
  perspective: [canonical_source]
  note: >-
    解消済みの判定を atlas_gaps/store.resolved_label_set（教員側の候補キューが使う _skeleton_index の resolved をそのまま公開）に寄せ、core/corpus_view.py の縁は信号の normalized_label がその集合に入る行を出さない（SQL に s.normalized_label を足しただけ・decisions は読まない = §6.1）。全行が解消済みの領域は縁を出さない。信号の行は残る（P4）。
  landed_in:
    - "backend/core/atlas_gaps/store.py"
    - "backend/core/corpus_view.py"
    - "backend/tests/test_atlas_review_round25_r5.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "同じ信号から導出する 2 面（教員の候補キューと学習者の縁）が同じ解消判定の関数を通る形を採った。固有の差分: 縁は教員の判断を読まない規律（§6.1）があるので、共有するのは凍結版の項目との照合だけ。"
  verification:
    methods: [guardrail]
    unverified:
      - "凍結直後の実データで縁が消えることは再演していない"
      - "配置の無い新しい項目に学習者向けの『再解析で論文が並ぶ』の一文は足していない"
related: [IK-0937, IK-0846, IK-0963]
view_of: []
history: []
---

## 課題

症状: 版 2026.2 で「星間磁場」（根拠は Cep B の論文）が地図の項目になった後も、論文の海の縁は星間物質・星形成について
「まだ地図に置かれていない主題を扱う論文があります」と Cep B の論文を挙げる。

原因: 縁は論文単位の信号（再解析まで active）を、現行版に領域があるかだけで絞る。教員側のレビューキューは現行凍結版の項目で
解消済みの主題を外すが、学習者の縁はその判定を共有していない。

## 発見の観点

凍結の後に初めて学生が海を開いた周で見えた（reproduction）。縁の導出と候補キューの導出を突き合わせた（trace_walk）。

## 解決の観点

第 5 波（2026-10-27）で解決。解消済みの判定を 1 つの関数に寄せ、縁もそれを通る。

## 一般化

同じ材料から複数の面を導出するときは、版で解消された行を外す条件を 1 か所に置く。確かめ方: 候補を取り込んで凍結した直後に、
教員の候補区画と学生の縁の両方からその主題が消えることを見る。



## 第 6 周の再演（第 25 周・面 C 第 6 周、2026-10-27・campaign c-astro-r25-atlas-r6）

GONE。凍結 2026.2 で星間磁場を取り込んだ後も第 5 周は星間物質・星形成に縁（Cep B の論文）が出ていたが、第 6 周の海の縁は高エネルギー天体・コンパクト天体と観測・装置・データ解析の 2 領域だけで、星間物質・星形成の縁は消えた（解消済みの主題の行が外れた）。2026.3 は概念を足さない版上げなので残る 2 件は版の中身と整合。残り（設計の範囲）: 縁は主題名を言わず論文題名だけを挙げるため、学生には「どの主題が置かれていないか」は読めない（候補の主題名は学習者に出さない設計 = カテゴリギャップ §5.6）。run: uxsim/runs/c-astro-r25-atlas-r6/20261027T123339Z-10273 seq 4。

## 第 7 周の再演（第 25 周・面 C 第 7 周、2026-10-28・campaign c-astro-r25-atlas-r7）

判定できない。2026.4 は項目が加わらない版上げで、海の縁は第 6 周と同じ 2 領域（高エネルギー天体・コンパクト天体 / 観測・装置・データ解析）。霧 3 領域は地図 L1 と一致。run: uxsim/runs/c-astro-r25-atlas-r7/20261027T173037Z-59717 seq 4、uxsim/runs/c-astro-r25-atlas-r7/20261027T172454Z-57525 seq 5。
