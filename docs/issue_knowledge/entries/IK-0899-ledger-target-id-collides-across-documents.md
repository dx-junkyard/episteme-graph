---
id: IK-0899
title: "認識的地位台帳の行キーが (target_id, target_type) だけで document を含まないため、理論操作グラフのノード ID（theory_op_* / eq_op_*）や TeX 由来の式ラベルが論文をまたいで 1 行に衝突し、ある論文で記帳したスコープ・検証状態が別の論文の同名ノードにも出る（台帳行の document_id は最初に作った論文のまま）"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.3"
  - "docs/architecture/persona_enactment_testing_design.md §22.8"
  - "docs/features/doubt_layer_issues.md"
  - "docs/features/stakes_ledger_design.md"
  - "docs/features/personal_map_nearby_design.md"
feature_context:
  realizing: "教員が論文ごとに台帳へ検証スコープ・検証状態を記帳し、学生が自分の受講する論文の検証状態を読む"
  layers: [doubt_d, stakes_ledger_sl, personal_network, migrations_db, theory_artifacts]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [target]
    governance: [none]
  axis_confidence:
    processing: high
    structure: high
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation: backend/db/029_epistemic_ledger.sql の UNIQUE(target_id, target_type) と core/doubt/ledger_builder.py::_collect_targets が、グラフの node id（論文内でしか一意でない theory_op_0001 / eq_op_0001…）と式の
    agent ID（TeX ラベル由来の eq_dcf 等）をそのまま target_id にし、upsert は ON CONFLICT (target_id, target_type) で document_id を『空なら埋める』だけ。キーの表し方が document を含まない。接続軸 target:
    読み手（routes/doubt.py::_fetch_ledger_row・personal_graph/queries.py::fetch_component_ledger_statuses・学習者の台帳 GET）は target_id だけで行を引くので、どの論文のノードとして開いても同じ 1 行（＝別の論文の行）に着地する。処理軸 none（各関数は自分の仕様どおり動く）。統制軸
    none（記帳の帰属・監査は効いている）。確信度 governance medium は、衝突行に人の記帳が既に入っている状態をどう扱うかが手続の問題でもあるため。
generalization:
  level: general
  general_form: "集約ノードの ID が親単位の中でしか一意でないのに、それを全体一意のキーとして状態を記帳するため、別の親の同名ノードが同じ記帳を共有する"
pattern: id-unique-only-within-inner-scope
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    te-04 が Cepheus（TeX 版）のグラフのノード 1 番目の台帳を開くと、応答の document_id は中性子星の論文（PDF 版）だった。そこへ記帳したスコープ・直接検証は、te-01 が暗黒エネルギー論文の同じノードを document_id 指定で開いたときにもそのまま出て、学生 st-03 / st-09 の『わたしの地図』でも Cepheus
    の『理論の土台』に直接検証の札として出た。run:
    uxsim/runs/c-r25-doubt-r2/20261022T215750Z-73306（seq14・17・18・24）・uxsim/runs/c-r25-doubt-r2/20261022T220507Z-77990（seq11・14・16・20）・uxsim/runs/c-r25-doubt-r2/20261022T221137Z-82302（seq13）・uxsim/runs/c-r25-doubt-r2/20261022T221543Z-84621（seq13）。
resolution:
  perspective: [representation_change, required_argument]
  note: >-
    第 2 波で是正（2026-10-23）。台帳の行キーを (document_id, target_type, target_id) にした（migration 092・UNIQUE NULLS NOT DISTINCT）。claim / assumption と UUID 形の id は全体で一意なので行の突き合わせは従来どおり。行キーの WHERE 句の正本は core/doubt/ledger_targets.ledger_row_filter で、読み手（_fetch_ledger_row / learner_ledger_line / resolve_component_ledger_target / open_assumptions.target_label / personal_graph.queries.fetch_component_ledger_statuses / W層 positioning / SB層 store / D層 worker 2 本）は document_id を必須のキーワード引数で受け、論文の中でしか一意でない対象で分からなければ閉じる（API は 422、学習者の一行は出さない）。既存の衝突行は自動で振り分けず、旧キーが残る DB で 1 回だけ人の記帳が入った行に attribution_check_needed を立て、台帳 GET が attribution_note で「どの論文の記帳か確かめてください」と出す。監査の metadata に document_id。画面は台帳区画の data-doubt-document と ledgerPath 1 関数で論文を添える（地図の点・合意リスト・原稿スタジオのノード詳細）。
  landed_in:
    - "backend/db/092_epistemic_ledger_document_key.sql"
    - "backend/core/doubt/ledger_targets.py"
    - "backend/api/routes/doubt.py"
    - "backend/core/doubt/ledger_builder.py"
    - "backend/core/personal_graph/queries.py"
    - "backend/core/personal_graph/nearby.py"
    - "backend/core/simulation_bridge/store.py"
    - "backend/core/deliberation/positioning.py"
    - "backend/core/knowledge_objects/remap.py"
    - "frontend/public/js/doubt-atlas.js"
    - "backend/tests/test_doubt_ledger_document_key_round25.py"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "document_id を必須のキーワード引数にし、WHERE 句を ledger_row_filter の 1 箇所で組む（論文が分からなければ閉じる）。"
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "既存の記帳は振り分けも削除もせず、印を立てて人に確かめさせる（行は残す）。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB への migration 092 の適用（旧キーの制約名の検出と印の立て方）は scratch DB で未確認"
      - "画面での表示（attribution_note・地図の点から論文付きで開く）はブラウザで未確認"
related: [IK-0855, IK-0852, IK-0900, IK-0901, IK-0902]
view_of: []
history: []
---

## 課題

症状: 教員がある論文の主グラフのノード（理論の土台など）の台帳に記帳すると、同じ番号のノードを持つ他のすべての論文の
台帳にも同じスコープ・検証状態が出る。台帳の詳細が示す論文（document_id）は、開いた論文ではなく最初にその番号の行を
作った論文になる。学生の地図の検証の札、支持線の計算（行の document のグラフで解く）、依存の件数（最後に再計算した論文の値）
が、開いている論文と別の論文の値になる。前提の地図はコースの教材の document で絞るので、他の論文に「取られた」行は
その論文のコースの地図から消える。

原因: 台帳の行キーが (target_id, target_type) で、target_id に論文内でしか一意でない ID（グラフのノード ID・式の agent ID）
を使っている。claim は DB の UUID なので衝突しない。第 1 周の IK-0855（部品とノードの名前空間）を直したことで、ノード側の
台帳が実際に読まれるようになり、この衝突が表に出た。

第 2 周の頭脳ノートが「教員の直接検証が学生の地図に届いた（GONE）」と書いた所見は、この衝突の現れで、届いたのは
別の論文の行である（審判の裏取り。judge_doubt_r2.md）。

## 発見の観点

再現（reproduction）: 2 人の教員に別の論文を指定して同じ番号のノードの台帳を開かせ、応答の document_id と記帳者を
突き合わせた。実データ（data_inspection）: 台帳 GET の document_id と教材一覧の document_id を対応させた。

## 解決の観点

第 2 波で是正（2026-10-23。frontmatter の resolution）。以下は起票時の是正案。後戻りしにくい構造の変更なのでオーナー判断の候補（judge_doubt_r2.md §5）。推奨は次のとおり。

- 行キーを (document_id, target_type, target_id) にする。claim（UUID）は document_id を併記するだけで意味は変わらない。
- migration で UNIQUE を張り替える。既存の衝突行は、記帳（verification_scopes / falsification_conditions / evidence_lines /
  challenges の対象）が入っていないものは ledger_builder の再実行で論文ごとに作り直す。人の記帳が入った衝突行は、
  記帳時にどの論文を開いていたかが行に残っていない（監査の metadata にも document が無い）ため自動で振り分けない。
  行の現在の document_id に残し、教員が確かめられるよう「この記帳はどの論文のものか確かめてください」の事実文を出す。
- 読み手（_fetch_ledger_row・nearby の状態読み・学習者の台帳 GET・seminar_brief・open_assumptions）は document_id を必須の
  キーワード引数にする（enforce-condition-in-query-with-required-argument）。document が分からない呼び出しは閉じる。
- 監査の metadata に document_id を残す（今後の振り分けの手掛かり）。

代案: target_id に論文の名前空間を前置する（"{document_id}:{node_id}"）。キーの列は変えずに済むが、既存の参照（反実仮想の
toggled_assumption_ids・element_id_remap・challenges.target_id）の書き換えが要り、名前空間の付け忘れが再発の入口になるので採らない。

## 一般化

ノード ID が親（論文）の中でしか一意でないのに、それを全体一意のキーとして状態を持つと、親をまたいで状態が共有される。
確かめ方は、同じ番号の対象を持つ親を 2 つ用意し、片方に記帳してもう片方から読んで空であることを見ること。
