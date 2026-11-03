---
id: IK-0598
title: "教員向けの表示射影がフィールド名で禁止キーを落とすため、物理記号をキーにした写像から `k` などの記号が黙って消える"
status: resolved
recorded_at: 2026-10-02
resolved_at: 2026-10-02
sources:
  - docs/features/simulation_bridge_design.md §12.1
feature_context:
  realizing: "検証シミュレーションの理論出力・観測値・記号ごとの比較を、教員向け API で数値と内部 ID を落として返す"
  layers: [simulation_bridge_sb, shared_infra]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認: `backend/api/display_route.py` の教員向け射影（DP7）は `FORBIDDEN_KEYS_TEACHER`（`k` / `count` / `weight` /
    `score` 等）に当たる dict キーを位置を問わず落とす。検証シミュレーション層の DTO は `theory_outputs` / `observed` /
    `metrics.by_symbol` で物理記号を dict のキーに使うため、記号 `k` の項目が教員の画面から消える。構造軸
    representation = dict のキーという 1 つの置き場所に、スキーマのフィールド名と分野の値（記号名）が同居し、射影から
    区別できない。接続軸 meaning = 射影はキーをフィールド名と解釈し、生成側はキーを記号として渡している（同じキーの
    意味が段階間でずれる）。処理軸は射影の判定自体は仕様どおりなので none。統制軸は順序・予算・手続の問題では
    ないので none。
generalization:
  level: general
  general_form: "フィールド名で値を落とす横断の射影を、キーそのものが分野の値である写像に掛け、偶然禁止語と同じ名前の値が黙って消える"
pattern: id-namespace-conflated
discovery:
  perspective: [adversarial_review, boundary_walk]
  note: "API 担当の実装報告をレビューする段で、教員向け射影の禁止キー集合と本層 DTO の記号キーの写像を突き合わせ、記号 `k` が消えることに気づいた（利用者が見る前）。"
resolution:
  perspective: [representation_change, guardrail_fix]
  note: >-
    本層のルーターは教員向け射影を使わず、本層専用の `SimulationDisplayRoute` を掛ける。落とすのは
    `core/simulation_bridge/schema.py::FORBIDDEN_DTO_KEYS`（記号名と衝突しない数値・指紋のキー）と制御文字だけにした。
    記号 `k` と `count` を含む DTO で、記号キーが残り禁止キーだけが落ちることをテストで固定した。
  landed_in:
    - backend/api/routes/simulation_bridge.py
    - backend/tests/test_simulation_bridge_api.py
    - docs/features/simulation_bridge_design.md §12.1
  verification:
    methods: [guardrail]
    unverified:
      - "ブラウザでの表示"
      - "記号をキーにした写像を返す他の教員向け面（教員向け射影を掛けたままのもの）"
related: [IK-0592]
view_of: []
history: []
---

## 課題

検証シミュレーション層の新しい管理ルーターは、当初、教員向けの表示射影のルートクラスを使っていた。この射影は禁止キー集合に
当たる dict キーを位置を問わず落とす。本層の DTO は理論出力・観測値・記号ごとの比較で物理記号を dict のキーにしており、
記号の名前が禁止キー（`k` など）と同じだと、その記号の値ごと教員の画面から黙って消える。

## 発見の観点

API 担当の実装報告をレビューする段で、射影の禁止キー集合と DTO の記号キーを突き合わせた（2 機能の境界 = 射影 × 分野の値の
写像）。利用者が見る前に見つかった。

## 解決の観点

キーがフィールド名である前提を持たない、本層専用の射影に替えた。落とすキーは本層の契約で禁止したもの（記号名と衝突しない語）
だけにし、記号キーが残ることをテストで固定した。横断の射影の禁止キー集合から `k` を外す案は、他の面での件数・k-匿名の値の
遮断を弱めるため採らなかった。

## 一般化

名前で値を落とす横断の規則は「キーはフィールド名である」という前提に立つ。キーが分野の値（記号・カテゴリ名・利用者が付けた
名前）である写像に掛けると、禁止語と偶然同じ名前の値が消える。辞書の型は `id-namespace-conflated`（フィールド名と分野の値と
いう由来の異なる名前空間を同じキーの置き場所で扱う）。解決原理 `route-all-surfaces-through-one-point` の非適用（面ごとに意図的に
違う判定は 1 つに潰さない）に近い解き方だが、正本の中に系統別の定数を並べたのではなく層の中に専用の射影を置いたので、原理の
参照は付けない。
