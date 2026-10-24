---
id: IK-0852
title: "D層のコース単位の読み出しが「成果の行に焼かれた course_id 列」との一致で範囲を決めるため、コースを作らずに解析した教材（現行の標準経路）から作ったコースでは、前提の地図・未検証合意リスト（教員・学生）・観測系の主張・コーパス監査・負荷再計算・反実仮想・開幕の「この論文が確かめていないこと」がすべて空になる"
status: resolved
recorded_at: 2026-10-22
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/stakes_ledger_design.md"
feature_context:
  realizing: "教員が自分のコースの前提の地図で暗黙の前提を確かめ、反実仮想で依存の範囲を見る／学生が受講コースの未検証の前提と開幕の「確かめていないこと」を読む"
  layers: [doubt_d, stakes_ledger_sl, discuss, theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 target: 入口はコース（course_id）だが、D層の読み出しはコースの教材（sources）から document を解決せず、成果の行が
    持つ course_id 列との一致で範囲を決める — 前提の地図 `routes/doubt.py::get_assumption_atlas`（`WHERE l.course_id = :course`）、
    コーパス監査 `core/doubt/assumption_mining/corpus_audit.py::run_corpus_audit`（theory_component_graphs の `course_id = :course`）、
    負荷 `core/doubt/load_calculator.py::recompute_load_scores`・依存グラフ `core/doubt/dependency.py::build_dependency_graph`
    （course_id と document_id を AND で両方絞る）、未検証合意リスト `core/doubt/open_assumptions.py`（`WHERE course_id = :course`）、
    観測系の主張 `core/doubt/observation_targets.py::_fetch_graph_rows`、開幕 `core/discuss/opening.py` の `_compile_open_assumptions(course_id)`。
    構造軸 representation: その course_id 列は解析 run に course_id が渡ったときだけ値を持つ（orchestrator.py の
    `backfill_document_ledger(document_id=ctx.document_id, course_id=ctx.course_id or "")`、persistence.py の theory_component_graphs
    INSERT）。現行は教材を解析してからコースビルダーでコースを作るので値は常に空で、1 document が複数コースの教材になる関係も
    1 列では表せない。砂場の実測: corpus-audit が `{"skipped": true, "documents": 0}`、assumption-atlas / open-assumptions（教員・学生とも）
    / observation-targets が空、counterfactual/compute が `グラフ上に対応するノードが見つからなかった前提: theory_op_0001`。
    一方、学習者の台帳 GET（target 単位・course で絞らない）は同じ論文の claim 行を返す。処理軸 none（各クエリは宣言どおり）。
    統制軸 none。IK-0531 はゼミ前ブリーフ 1 面だけを course_id 空の台帳で合成するよう直した（面ごとの是正）。
generalization:
  level: repo_pattern
  general_form: "入口が束ねる範囲（コース）を、束ねる関係の正本から解決せず、下流の行に一度だけ焼いた所属列との一致で絞るため、所属列を書かない作り方で作った束では下流の一覧がすべて黙って空になる"
pattern: entry-scope-mismatch
discovery:
  perspective: [reproduction, trace_walk]
  note: "教員 2 人（te-03 / te-02）が種まきで作った自分のコースで前提の地図を開くと、散布図・前提候補・未検証合意リストがすべて空で、コーパス監査・前提マイニング・反実仮想も何も返さなかった。学生 2 人（st-06 / st-02）は出典タブでは各主張に台帳の一行が出るのに「未検証の前提」一覧が空だった。各応答と D層の WHERE 句を突き合わせた。 run: uxsim/runs/c-r25-doubt-r1/20261022T123418Z-31805（te-03 seq10〜16）・uxsim/runs/c-r25-doubt-r1/20261022T124129Z-35838（te-02 seq10〜19）・uxsim/runs/c-r25-doubt-r1/20261022T125348Z-41947（st-06 seq9）・uxsim/runs/c-r25-doubt-r1/20261022T125655Z-43791（st-02 seq9・12）。頭脳の所見 uxsim/runs/round25/BRAIN_NOTES_doubt.md 00009〜00015・00023〜00031・00042・00052・00055。"
resolution:
  perspective: [canonical_source, required_argument]
  note: >-
    D層のコース範囲の正本を「コースの教材（sources）から求めた document 集合」に切り替えた。route 層の routes/doubt.py::_course_scope_document_ids（編集権限ゲート + services.list_course_source_document_ids）が範囲を求め、core の範囲関数（build_dependency_graph・observation_claim_targets・build_support_context・compute_support_lines・compile_open_assumptions・recompute_load_scores・load_percentiles・run_corpus_audit・detect_gap_clusters・compute_counterfactual・discuss.opening._compile_open_assumptions）は document_ids を必須キーワード引数で受けて document_id::text = ANY(:doc_ids) で絞る（WHERE 断片の正本は core/doubt/scope.py・空集合は SQL を出さない）。行の course_id 列は記帳の出所に退いた。反実仮想は document_id があればその 1 件。スコープ／反証条件候補の worker と教員トリアージの負荷段階も同じ範囲にした。ゼミ前ブリーフは「この論文」1 件で読み可否は台帳行の有無で決める（IK-0531 の面ごとの是正をこの正本へ寄せた）。manual teacher/18 §overview を範囲の説明と一致させた。
  landed_in:
    - "backend/core/doubt/scope.py"
    - "backend/api/routes/doubt.py"
    - "backend/core/doubt/dependency.py"
    - "backend/core/doubt/open_assumptions.py"
    - "backend/core/doubt/load_calculator.py"
    - "backend/core/discuss/opening.py"
    - "backend/core/doubt/seminar_brief.py"
    - "backend/core/teacher_triage.py"
    - "backend/tests/test_doubt_course_scope_round25.py"
    - "docs/features/doubt_layer_issues.md"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "範囲を document_ids の必須引数で受け、問い合わせの内側（ANY）で強制し、空なら閉じる。"
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "面ごとに course_id 空を合成せず（IK-0531 型）、コース → document の解決を route の 1 関数に置いた。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の実データ（コースを作らずに解析した教材から作ったコース）で地図・一覧・開幕に成果が現れることは再演で未確認"
      - "同じ論文を教材に持つ別コースに同じ記帳が見えることの画面での確認は未実施"
related: [IK-0531, IK-0752]
view_of: [IK-0861]
history: []
---

## 課題

症状: 教員がコースビルダーで作った直後の自分のコースで前提の地図を開くと、散布図・前提候補・未検証合意リスト・反実仮想の
依存範囲・観測系の主張がすべて空になる。学生の受講コースでも「未検証の前提」一覧と開幕の「この論文が確かめていないこと」
が空。原因: D層のコース単位の読み出しがコースの教材から document を解決せず、成果の行に焼かれた course_id 列（解析 run に
course_id が渡ったときだけ書かれる）で範囲を決めている。現行の作り方では列が常に空で、どのコースからも成果が届かない。

## 発見の観点

ペルソナの通し受講（reproduction）で教員・学生の両側が空を見た。応答（corpus-audit の documents 0・compute の warnings）から
WHERE 句を辿った（trace_walk）。

## 解決の観点

解決済み（2026-10-23）。コース → document の解決を 1 箇所に置き、core は document 集合を必須引数で受ける（面ごとに course_id 空を合成する
IK-0531 型の是正を増やさない）。

## 一般化

束（コース・グループ・プロジェクト）単位の一覧を、束の構成の正本からではなく下流の行の所属列で絞ると、所属列を書かない
作り方が増えた時点で一覧が黙って空になる。確かめ方は、解析時にコースを渡さなかった教材からコースを作り、そのコースの
全一覧に成果が現れることを見ること（対象が 1 件の環境では範囲のずれが隠れる）。
