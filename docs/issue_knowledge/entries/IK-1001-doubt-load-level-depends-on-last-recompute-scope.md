---
id: IK-1001
title: "依存の広がり（load_score）は最後に再計算した範囲で値が変わり（パイプライン完了時は論文 1 本の依存グラフ・前提の地図の［再計算］はコースの論文全体のグラフ）、同じコースで［再計算］を押すと散布図と未検証合意リストの中身が式の頭から英語の生成文の主張へ丸ごと入れ替わる"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/features/stakes_ledger_design.md §16.8"
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
feature_context:
  realizing: "教員が前提の地図と未検証合意リストで、依存の広がりが大きく検証の記録が無い対象を探す"
  layers: [doubt_d, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [condition]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 condition（第 5 波で確認: グラフのノード ID は論文の中でしか一意でなく、論文の集合を 1 枚に混ぜると同じ ID が繋がる — compare_load_scopes の合成データで再現）— orchestrator は解析完了時に recompute_load_scores(document_ids=[その論文]) を呼び、POST …/load/recompute はコースの論文集合で同じ関数を呼ぶ。build_dependency_graph は渡された document 集合でグラフを作るので、同じ台帳行の load_score が最後に呼んだ範囲で変わる。段の閾値（p50/p90/p99）は値の分布から取る（IK-0970）ため、範囲の違う値が混ざると段の並びが入れ替わる。te-03 で［再計算］前の合意リストは式（dependent_count 18 の段『最高位』）、後は英語の生成文の主張（同じ段で dependent_count 0）が先頭だった。起票時は確定に 2 範囲の比較の読み取りが要るとした（第 5 波で実装・テストで固定）。
generalization:
  level: repo_pattern
  general_form: "導出値を計算のたびに違う範囲で作り直して同じ列に上書きするため、値の意味が最後に計算した経路で変わり、並べた一覧が操作のたびに入れ替わる"
pattern: threshold-reused-across-regimes
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    te-03 が前提の地図で［依存の広がりを再計算］を押すと「再計算しました」の後、散布図・合意リストの材料が式の頭（「番号なし: $\varepsilon_{\max} = 8\varepsilon_0$」）から英語の主張（「In an equation of this paper, $c_s^2$ depends on $\rho_0$」）へ入れ替わった。run: uxsim/runs/c-r25-doubt-r5/20261027T071425Z-60143（seq8〜10）。
resolution:
  perspective: [single_point_fix, canonical_source]
  note: >-
    オーナー判断（推奨どおり）: 依存の広がりは論文単位で保存する。recompute_load_scores は範囲の論文ごとに、その論文 1 本の依存グラフで台帳行を計算する（グラフのノード ID は論文の中でしか一意でなく、論文を 1 枚に混ぜると別の論文の同じ ID が繋がる — 仮説の機構）。同じ行を論文 1 本とコース全体で計算して並べる読み取り compare_load_scopes を足し、ID が衝突する 2 論文の合成データで値が違うこと・再計算後はパイプラインとコースの［再計算］で同じ値になることをテストで固定した。段の閾値は読み時にコースの母集団（IK-0970 の除外後）で取るまま。
  landed_in:
    - "backend/core/doubt/load_calculator.py"
    - "backend/tests/test_doubt_round25_wave5.py"
    - "docs/manual/teacher/18-admin-doubt-atlas.md"
  verification:
    methods: [guardrail]
    unverified:
      - "compare_load_scopes を砂場の実データ（te-03 のコース）で走らせていない（砂場 DB 禁止の運転）"
      - "台帳詳細の段（論文の母集団）と地図の段（コースの母集団）の違いの言い分けは未対応"
      - "行動上の合意の再計算は未決"
related: [IK-0970, IK-0933]
view_of: []
history: []
---

## 課題

症状: 同じコースで［再計算］を押す前と後で、前提の地図と未検証合意リストの中身が別物になる。教員はどちらを信じてよいか分からない。

原因（仮説）: 依存の広がりの値が、最後に計算した範囲（論文 1 本かコース全体か）で変わる。

## 発見の観点

再現（te-03 の再計算の前後）と、合意リストの先頭行の段と依存数の照合。

## 解決の観点

第 25 周 面 B 第 5 波（2026-10-27・docs/features/stakes_ledger_design.md §16.8）で解決。オーナー判断（推奨どおり）: 依存の広がりは論文単位で保存する。recompute_load_scores は範囲の論文ごとに、その論文 1 本の依存グラフで台帳行を計算する（グラフのノード ID は論文の中でしか一意でなく、論文を 1 枚に混ぜると別の論文の同じ ID が繋がる — 仮説の機構）。同じ行を論文 1 本とコース全体で計算して並べる読み取り compare_load_scopes を足し、ID が衝突する 2 論文の合成データで値が違うこと・再計算後はパイプラインとコースの［再計算］で同じ値になることをテストで固定した。段の閾値は読み時にコースの母集団（IK-0970 の除外後）で取るまま。

起票時の是正案: 確かめた後の是正案: 依存の広がりの計算範囲を 1 つに決める（論文単位で保存し、コースの地図は読み時に論文単位の値を並べる・閾値も読み時にそのコースの母集団で取る）。パイプライン完了時と［再計算］が同じ範囲で計算することをテストで固定する。範囲を変えるなら列を分ける。

## 一般化

同じ列に違う前提で計算した値が混ざると、並べた結果は計算の順序で変わる。確かめ方は、2 つの経路で計算した後に同じ行の値が一致することを見ること。

第 6 周の再演: 残存（形を変えて・2026-10-27）。te-04 の［負荷再計算］（第 5 波の論文単位の計算）の直後、散布図と合意リストの中身が式の並びから Cepheus の序論の英語の主張（段「最高位」・dependent_count 0）に丸ごと入れ替わった。再計算前の値は第 5 波より前の範囲で計算された古い値だった可能性が高く（デプロイ後に一度も再計算していない論文の値）、序論が上位に来る理由は出現順の辺を依存と数える計算（IK-1030 に起票）。是正後は全論文で一度再計算する運用が要る。学生の一覧と開幕にも同じ文が届いた。run: uxsim/runs/c-r25-doubt-r6/20261027T122831Z-7319（seq3・7）/ uxsim/runs/c-r25-doubt-r6/20261027T123134Z-9179（seq11・15）。

第 6 波の是正（2026-10-27）: 残り（再計算で一覧が序論の英語文で埋まる）は IK-1030 の是正で根を外した — 出現順の辺を依存に数えない。保存済みの値は全論文の再計算まで残るので、運転側で一度再計算する。設計書 §16.9。

第 7 周の再演: GONE（2026-10-28）。全論文の再計算の後、te-02 の［負荷再計算］（POST …/load/recompute 200・状態行「依存の広がり（負荷の段階）を再計算しました。」）の前後で前提の地図と合意リストの先頭が入れ替わらなかった。run: uxsim/runs/c-r25-doubt-r7/20261027T172037Z-54876（seq5・7）。
