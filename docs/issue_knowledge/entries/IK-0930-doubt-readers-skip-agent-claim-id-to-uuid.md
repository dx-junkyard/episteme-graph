---
id: IK-0930
title: "D層の読み手が、理論操作グラフの主張参照（synth_claim_* / claim_span_* の agent ID）を DB の主張（UUID）へ解決せず UUID 形だけを採るため、部品・ノードの台帳の「支える主張」が常に空で疑義の入口が死に、観測を倒す選択肢の先頭（主張の型で拾った UUID）はグラフで伝播できず、支持線の「観測からの支持根」は全件「名前を引けない対象」になる"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/architecture/persona_enactment_testing_design.md §22.9"
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/stakes_ledger_design.md"
  - "docs/features/knowledge_objects_design.md"
feature_context:
  realizing: "教員が部品・ノードの台帳から支える主張へ移って疑義を残し、観測を仮に倒して依存範囲を試算し、支持線の根を名前で読む"
  layers: [doubt_d, stakes_ledger_sl, knowledge_objects, theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [meaning, contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    接続軸 meaning / contract: 保存された graph_json のノードの linked_claim_ids は agent ID（component-graph の生応答に synth_claim_0003 等、support_lines の observation_roots に
    claim_span_001_142 等）で、D層の読み手は主張を UUID として扱う契約に立つ。ledger_targets.supporting_claims_for_target は is_uuid_text(cid) の主張だけを採るので
    常に空（challenge_note は CHALLENGE_TARGETS_NONE_NOTE になる）。observation_targets の C 段（主張の型）は theory_claims の UUID を返し、反実仮想の伝播はグラフの
    agent ID で種を引くので UNRESOLVED_WARNING_PREFIX になる。target_labels の claim 表示名は theory_claims_live の id 照合だけで、seminar_brief._ReadableLabels が既に持つ
    claim_object_builder の本文（agent ID → 本文）を使わない。theory_claims には agent_claim_id 列（migration 078・(document_id, agent_claim_id) の索引）があり、解決の手段は
    存在する。処理軸 none（各関数は UUID を前提にすれば正しい）。構造軸 none（表現は両方の ID を既に持つ）。
generalization:
  level: repo_pattern
  general_form: "同じ対象を二つの ID 空間（生成時の局所 ID と永続化後の大域 ID）で持つのに、後段の読み手が片方の形だけを受け、もう片方を黙って捨てるため、機能が空の結果で正常終了する"
pattern: id-namespace-conflated
discovery:
  perspective: [trace_walk, reproduction]
  note: >-
    te-05 が散布図の点（Ostriker の臨界線質量）の台帳で「支える主張は見つかりませんでした」・観測を倒して「グラフ上で見つからなかった」、te-02 のノード台帳で支持根が
    「名前を引けない対象」の反復。生応答の linked_claim_ids と observation_roots の claim_id が agent ID であることから supporting_claims_for_target の UUID 篩いへ辿った。
    run: uxsim/runs/c-r25-doubt-r3/20261023T032742Z-99529（seq8・9・15）/ uxsim/runs/c-r25-doubt-r3/20261023T033238Z-24957（seq8・9）。
resolution:
  perspective: [canonical_source, carry_through]
  note: >-
    主張参照の解決の正本 backend/core/knowledge_objects/claim_refs.py（ClaimRefResolver）を新設した。UUID はそのまま、生成時の局所 ID は theory_claims_live の (document_id, agent_claim_id) と source_scope.legacy_ids で UUID へ、どちらにも無ければ採用 run の claim_object_builder の本文で名前だけ返す（内部 ID は名前にしない）。綴りの正規化は既存 references.py の正本を使う。supporting_claims_for_target（支える主張）・observation_claim_targets（A/B 段のグラフ参照と C 段の UUID を UUID に揃えて重ね、名前は本文）・target_labels（claim の局所 ID）・build_dependency_graph（claim_refs に UUID ⇄ 局所 ID の別名を足し、UUID の観測で種を引ける）が通る。支持線の観測からの支持根の名前は observation_claim_targets の本文になる（IK-0902 の残り）。
  landed_in:
    - "backend/core/knowledge_objects/claim_refs.py"
    - "backend/core/doubt/ledger_targets.py"
    - "backend/core/doubt/observation_targets.py"
    - "backend/core/doubt/target_labels.py"
    - "backend/core/doubt/dependency.py"
    - "backend/tests/test_doubt_round25_wave3.py"
    - "backend/tests/test_doubt_ledger_document_key_round25.py"
  verification:
    methods: [guardrail]
    unverified:
      - "実データ（Cepheus B の graph_json と theory_claims_live）で支える主張・観測の伝播・支持根の名前が出ること"
      - "局所 ID が live 行にも artifact にも無い旧 run の見え方"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "D層の読み手 4 か所（支える主張・観測・表示名・依存グラフの種）を 1 つの解決器に通した。全経路走査の検査は置かず、各読み手のテストで固定した"
related: [IK-0904, IK-0902, IK-0903, IK-0901]
view_of: []
history: []
---

## 課題

症状: 部品・ノード（式の詳細層の導出ステップを含む）の台帳では、疑義の区画に「疑義は前提と主張に残せます。この対象を支える主張は、
このコーパスの中では見つかりませんでした。」だけが出て、主張の台帳へ移る道が無い。観測を仮に倒すと、選択肢の先頭の観測は
「グラフ上で見つからなかったため、依存する範囲を試算できなかった対象」になる。支持線の「観測からの支持根」は
「名前を引けない対象」が並ぶだけ。

原因: グラフの主張参照は agent ID、D層の読み手は UUID の形だけを主張とみなす。両者を結ぶ列（agent_claim_id）は DB にあるが
使われていない。IK-0904 の是正（支える主張の一覧）はこの篩いで常に空になり、標準の経路から疑義に届かないまま。

## 発見の観点

疑義 → 提案 → 取り下げの通しを 1 人に歩かせ（再現）、空で返った一覧の材料を生応答の ID の形まで辿った（経路の追跡）。

## 解決の観点

主張参照の解決を 1 か所（claim_refs.py）に集め（正本）、UUID と局所 ID の両方を受けて UUID へ運ぶ（carry_through）。名前だけ要る箇所は artifact の本文へ縮退し、内部 ID は出さない。依存グラフは別名を足すだけで既存のキーを書き換えない。（第 25 周 第 3 波・2026-10-23）

## 一般化

生成段の局所 ID と永続化後の大域 ID を併存させる系では、読み手ごとに片方の形だけを受ける篩いを書くと、結果が空のまま正常に
終わり、欠陥が「該当なし」の事実文に隠れる。確かめ方は、実データの参照の形（ID の形）を読み手の受ける形と突き合わせ、
少なくとも 1 件が両方の経路で解決することを見ること。
