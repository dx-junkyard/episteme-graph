---
id: IK-1118
title: "有向の関係（依存）の向きが、学生の地図 L2 の「ここには描いていないもの:」の名前の行（「惑星形成 — 星形成」）と、教員の反映の知らせ（「A — B」を次版の下書きに追加しました）で落ちる — IK-1072 の是正が描いた実線・凍結の影響・パネルには届いたが、この 2 面に届いていない"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
feature_context:
  realizing: "教員が確定した有向の関係（依存）を、学生の分野の地図で描けなかった分も含めて、どちらがどちらに依存するかとして読む"
  layers: [field_atlas_s, frontend_learning_ui, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [none]
    connection: [information]
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
    接続軸 information: 関係の向き（from → to・種別）は骨格の edges にあり、L2 の描いた実線は directed_edges と relations_note で、
    凍結の影響は edge_change_label で、パネルは atlas_panel_relations_line で向きの句（label_vocab.edge_direction_phrase）を運ぶ。
    ところが描けなかった確定済みの関係は routes/atlas_view.py が l2_undrawn_relations を (ラベル, ラベル) の組だけで
    label_vocab.atlas_l2_undrawn_relations_line に渡し（docstring「向きは言わない — 選出は無向」）、種別と向きが落ちる。
    反映の知らせ（incorporate の結果文）も「A — B」の組の名前だけ。処理軸 wording: 「—」の並びは向きの代わりにならない。
    構造軸 none（向きの正本は 1 つで、句の関数も 1 つある — 呼ぶ面が足りない）、統制軸 none。
generalization:
  level: repo_pattern
  general_form: "保存された関係の属性（向き）を言う正本の関数を作り、列挙する面の多くがそれを通るようになっても、別の目的（選び残しの列挙・操作結果の知らせ）で同じ関係を名前だけで並べる面が取り残される"
pattern: rule-enforced-per-surface
discovery:
  perspective: [reproduction, trace_walk]
  note: "第 10 周、凍結 2026.7 の後に確定済みの関係が 7 本になり、学生 st-03（run uxsim/runs/c-astro-r25-atlas-r10/20261031T053524Z-69161 seq 6）と st-01（20261031T053658Z-69836 seq 6）の L2 で、描いた有向 3 本は「A → B（依存）」+ 向きの一文だったが、描けなかった有向の 1 本は「ここには描いていないもの: 惑星形成 — 星形成」と向きなしで出た（st-03 の頭脳「描いていない「惑星形成 — 星形成」には向きが書いてありません」・審判 C）。te-02 の反映（20261031T052922Z-65581 seq 8）の知らせも「「初期宇宙・インフレーション — 初期宇宙の物理」を次版の下書きに追加しました。」。atlas_view.py の l2_undrawn_relations と atlas_l2_undrawn_relations_line を読んで確かめた。"
resolution:
  perspective: [canonical_source, guardrail_fix]
  note: >-
    同じ型の 4 回目なので面を足すのをやめ、関係を名前で言う出口を全部挙げて label_vocab.edge_relation_phrase /
    edge_direction_phrase（凍結の影響は atlas_lifecycle.edge_change_label）に通した。新たに通したのは L2 の
    「ここには描いていないもの」（l2_undrawn_relations を (kind, from_label, to_label) に・atlas_l2_undrawn_relations_line が句）・
    反映の知らせ（atlas_review_display.incorporated_edge_result_line — 下書きに保存された辺の種別と記載順）・採用の結果文
    （edge_decide_result_line の kind）・凍結を止めている採用の一覧（routes/atlas.py _edge_pair_label → edge_change_label）・
    JSON Patch の after。「A — B」を f-string で組む関数は同じ関数で句の関数を呼ぶことを AST 走査で固定
    （種別が引けないときだけ fallback）。設計書 field_atlas_overlay_spec §21。
  landed_in:
    - "backend/core/label_vocab.py"
    - "backend/core/atlas_review_display.py"
    - "backend/core/atlas_edges/patching.py"
    - "backend/api/routes/atlas_view.py"
    - "backend/api/routes/atlas_edges.py"
    - "backend/api/routes/atlas.py"
    - "backend/tests/test_atlas_review_round25_r10.py"
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: "句の正本は既にあったので、出口の列挙と『— を組む関数は句を呼ぶ』の AST 走査（atlas 系 route/core と label_vocab 全体）で迂回を塞いだ。"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場で st-03 の L2 の『ここには描いていないもの』に「惑星形成」が「星形成」に依存する、と出ること（第 11 周の再演）"
      - "教員が有向の関係を反映したときの知らせの文面（砂場の教員の場面）"
related: [IK-1072, IK-1047, IK-1045]
view_of: []
history: []
---

## 課題

症状: 第 9 波で、有向の関係の向きを言う句の正本（`label_vocab.edge_direction_phrase`）ができ、L2 の描いた実線に矢じりと向きの一文、凍結の影響、
右パネル、カードの文がそれを通るようになった。第 10 周で確定済みの関係が 7 本（うち有向 4 本）になると、L2 の上限で 1 本（2026.3 の
「惑星形成 → 星形成」）が描けず、`omitted_note` 末尾の「地図の版の改訂で加わった関係のうち、ここには描いていないもの: 惑星形成 — 星形成。」に
回った。この行だけ向きが無い。教員側でも、反映の結果文は「A — B」の組で、直下のカードは向きの一文を持つ。

原因: 描けなかった関係の一覧は選出（無向の組）の副産物として作られ、種別を持たずに文の関数へ渡る。反映の結果文も組の名前だけで組み立てる。
句の正本を通らない面が 2 つ残っている。

## 発見の観点

IK-1072 の確かめ方（全ての面で向きが読めること）を、確定済みの関係が上限を超える状態で再演した（reproduction）。描かなかった関係の組の作り方を
読んだ（trace_walk）。

## 解決の観点

未解決。案は frontmatter の resolution.note。同じ型が 4 回目（IK-1047 → IK-1072 → 本件）なので、是正のときは「関係を名前で言う全ての面」を
列挙したガードレール（各面の文の関数が edge_relation_phrase を通る）を確かめ方に入れる。

## 一般化

型 rule-enforced-per-surface: 正本の関数があっても、主目的が別（選び残しの報告・操作結果の知らせ）の面は「関係を言う面」として数えられず
取り残される。確かめ方: 同じ関係を名前で出す面を grep で全部挙げ、正本の関数を呼んでいるかを並べる。

## 第 11 周の再演（2026-10-31・judge_atlas_r11）

**GONE（知らせ）／ 名前の行の依存の句は未観測**。te-05 の反映の知らせ「「分子雲」が「惑星形成」に依存する関係を次版の下書きに追加しました。」・採用の結果文も同じ句（run uxsim/runs/c-astro-r25-atlas-r11/20261031T101202Z-98536 seq 4・8）。学生の L2 は「分子雲」が加わって惑星形成が描かれる側に入り、前周の名前の行「惑星形成 — 星形成」は実線「惑星形成 → 星形成（依存）」になった。名前の行に回ったのは関連の 1 本で「「ブラックホール」と「超大質量ブラックホール」（関連）」（句の関数を通った形、run uxsim/runs/c-astro-r25-atlas-r11/20261031T102103Z-2572 seq 6・uxsim/runs/c-astro-r25-atlas-r11/20261031T102321Z-3474 seq 6）。依存が名前の行に回る状態は今周出ず、テスト test_undrawn_line_keeps_direction のみで裏取り。全ての面で句が揃った結果、句が言っている向きそのものを教員が選んでいないことが見えた → IK-1149。
