---
id: IK-1161
title: "ペルソナ体験試験のハーネスが、ペルソナ LLM の行為引数 document_id を形の検査なしに URL へ入れるため、教材ヒントの題名の断片（「Cepheus B」）が文書 id として送られグラフ対話が 404 になる"
status: resolved
recorded_at: 2026-11-03
resolved_at: 2026-11-03
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/graph_dialogue_review_design.md"
feature_context:
  realizing: "ペルソナ体験試験で、教員ペルソナがグラフレビュー画面から論文のグラフ全体対話を始める"
  layers: [tests_guardrails, graph_review]
classification:
  axes:
    processing: [input_handling]
    structure: [none]
    connection: [contract]
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
    確定（コードの読みと再現）: uxsim/runner/actions_exec.py の _graph_chat ほか 5 箇所が ctx.args.get("document_id") or <画面の文書> で、
    ペルソナ LLM の引数を形の検査なしに優先して URL に埋めていた。処理軸 input_handling（外から来た値を検査せずに使う）。接続軸 contract
    （行為の引数は「画面に出ている文書 id」という暗黙の契約が、ペルソナ LLM 側に伝わらず境界で検査もされない）。構造軸は引数の置き場所の
    問題ではないので none（確信度 medium）。統制軸は手続の欠落ではないので none。PE9（行為レジストリの id だけを送る）と同系で、
    「ペルソナが画面に無い値を送れてしまう」型。
generalization:
  level: repo_pattern
  general_form: "利用者を模す生成器が出す引数を、画面に出ている値で置き換えずにそのまま要求へ入れるため、画面に無い値で要求が失敗し、製品の失敗と見分けにくくなる"
pattern: unchecked-type-contract-at-boundary
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    sim-sandbox E-0006（campaign c-sandbox-p03-dcf・対象版 1d1929d8 ura-dev）の試行 1 手 4。直前の admin.graph_review.open は正しい UUID で 200 だったが、
    次の graph chat が POST /api/admin/deliberation/documents/Cepheus B/graph-sessions → 404 "Document not found"、審判 C blocked。
    transcript の行為引数に material_hint の語「Cepheus B」が document_id として入っていることを確かめた。
    run: uxsim/runs/c-sandbox-p03-dcf/20261103T045702Z-84733/transcript.jsonl・report.md。
resolution:
  perspective: [fail_closed, guardrail_fix]
  note: >-
    _doc_arg(ctx) を足し、引数が UUID の形のときだけ採用し、それ以外は画面の文書 id に倒す。5 箇所に適用した。
    uxsim/tests/test_document_arg.py（3 件）を追加し、test_harness_wave15.py::test_graph_chat_sends_screen_context の引数を UUID に更新。
    引数を行為レジストリの id に限る（PE9 型の徹底）案は今回は見送り。
  landed_in:
    - "uxsim/runner/actions_exec.py"
    - "uxsim/tests/test_document_arg.py"
    - "uxsim/tests/test_harness_wave15.py"
  verification:
    methods: [guardrail, reproduction_rerun]
    unverified:
      - "UUID の形だが画面に無い文書 id（別論文の id）を送るケースは弾かない"
      - "修正は未コミット（コミット番号は後で付く）"
related: [IK-1162]
view_of: []
history: []
---

## 課題

症状: ペルソナがグラフ全体対話を始めると 404 "Document not found" で止まり、審判が blocked と判定する。製品の不具合に見えるがハーネスの不具合。

原因: ハーネスが行為引数 document_id をペルソナ LLM の出力どおり URL に入れていた。ペルソナは教材ヒントの題名の断片「Cepheus B」を
document_id に書き、`/api/admin/deliberation/documents/Cepheus B/graph-sessions` が送られた。画面に出ている文書 id で置き換える・形を検査する処理が無かった。

## 発見の観点

試行 1 の再現で、正しい UUID で開いた画面の直後の対話要求だけが 404 になった（reproduction）。transcript の行為引数を読み、題名の断片が id として送られていることを確かめた（data_inspection）。

## 解決の観点

判定できない引数は画面の文書 id に倒す（fail_closed）。`_doc_arg(ctx)` が UUID の形のときだけ引数を採用する。`uxsim/tests/test_document_arg.py` で再発を防ぐ（guardrail_fix）。
uxsim テスト 1437 件中、修正前から失敗している無関係の 1 件（test_round25_r5_harness_fixes::test_prereqs_round6_profile_names_item）を除き pass。試行 2（20261103T045922Z-85083）で graph chat が 200。

## 一般化

利用者を模す生成器（ペルソナ LLM）の出力を要求へ渡す箇所すべてで再発しうる。PE9（行為レジストリの id だけを送る）と同系。
確かめ方: 引数に画面に無い値（題名・略称）を入れたとき、ハーネスが画面の値へ倒すか、少なくとも製品の失敗と区別して記録するかを見る。
