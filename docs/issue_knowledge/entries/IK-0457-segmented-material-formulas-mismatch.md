---
id: IK-0457
title: "教材が 2 区画以上に割れると、本文は build_topic_slides が振った [[FORMULA_N]] なのに formulas は content_blocks の式を渡し、$w$ が別の式に引かれるか「この数式は教材に載せられていません」に潰れる"
status: resolved
recorded_at: 2026-09-28
resolved_at: 2026-09-28
sources:
  - docs/architecture/persona_enactment_testing_design.md §17
feature_context:
  realizing: 受講画面が教材本文をレクチャーと同じページ境界の区画に分けて、区画ごとに数式を描く
  layers: [frontend_learning_ui, lecture_player]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [contract]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸: P2-R3 で区画の本文を build_topic_slides の display_text（$…$ と ![[equation:…]] を [[FORMULA_N]] に置き換え済み）に
    したが、get_topic_material は formulas に content_blocks の式を渡したままだった。学習画面は [[FORMULA_N]] を id か位置で引くので、
    N 番目の content_blocks の式（別の式）に引かれ、位置の外は replace_unresolved_formula_placeholders が事実文に置き換えた
    （contract）。砂場の 20 トピックすべてが 2 区画以上に割れ、事実文への置き換えが 214 か所・別の式への取り違えが 41 か所あった。
    あわせて、レクチャー側の ![[equation:…]] 解決は latex の無い式の英語の意味要約（「Equation semantics could not be
    inferred.」）を数式として描いていた。
generalization:
  level: repo_pattern
  general_form: 本文を置き換えた関数と、その本文が参照する索引を作る関数が別になっている
pattern: contract-changed-one-side
discovery:
  perspective: [reproduction, trace_walk]
  note: 砂場コースの t15 を配信と同じ関数で組み立て、「状態方程式 $w$」が「（この数式は教材に載せられていません）」、「$a$」が「z < 2」になるのを見た。
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    topic_material_delivery_segments（core/lecture.py）が区画の本文と、その本文が引く formulas を同じ build_topic_slides から
    返す（割れないときは従来どおり全文と content_blocks の式）。get_topic_material はこれに委譲する。レクチャー側の
    _resolve_equation_embeds は evidence_links の latex → content_blocks の latex → 短い原文（inline_formula_text）の順で本体を決め、
    summary は式に見えるときだけ使う（散文の要約は埋め込みごと外す）。
  landed_in:
    - backend/core/lecture.py
    - backend/api/routes/learning.py
    - backend/tests/test_ik0455_source_embed_delivery.py
    - backend/tests/test_lecture_equation_embeds.py
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - 砂場の受講画面（ブラウザ）での表示。確かめたのは砂場コースのデータを配信と同じ関数に通した結果（事実文への置き換えと別の式への取り違えが消えた）
      - 式の id が 0 から連続しないとき（本文に元から飛び番号の [[FORMULA_N]] がある古いコース）、学習画面の位置キーが別の式の id キーを上書きし得る
related: [IK-0389, IK-0404, IK-0454, IK-0455]
view_of: []
history: []
---

## 課題

区画に割った本文と、その本文が引く数式の索引が別の関数から来ていた。

## 発見の観点

配信と同じ関数で実データの区画を組み立て、プレースホルダーごとの行き先を追った。

## 解決の観点

本文と索引を同じ関数から受け取るようにし、配信はその関数へ委譲する。

## 一般化

プレースホルダーに置き換えた関数が、その索引も返す。
