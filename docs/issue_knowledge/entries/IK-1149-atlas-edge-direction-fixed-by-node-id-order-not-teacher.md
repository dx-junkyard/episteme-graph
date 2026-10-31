---
id: IK-1149
title: "分野の地図の有向の関係（依存）の向きを教員が選べず、node_id の辞書順で決まる — 採用メモと逆の向き「「分子雲」が「惑星形成」に依存する」が採用・反映・凍結 2026.8 を素通りして学生に届いた"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.4"
  - "docs/features/atlas_relation_edges_design.md"
feature_context:
  realizing: "教員が関係の候補を「依存」として採用するときに、どちらがどちらに依存するかを自分で決め、その向きが下書き・公開版・学生の地図まで運ばれる"
  layers: [field_atlas_s, frontend_admin_ui, frontend_learning_ui]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [none]
    governance: [assignment]
  axis_confidence:
    processing: medium
    structure: high
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation: 辺の判断の一意キーは無向（core/atlas_edges/schema.py の build_edge_key = edge|{domain}|{min}|{max}・
    版非依存のゾンビ候補防止）で、判断の行にも DecideEdgeCandidateRequest にも向きの欄が無い。parse_edge_key が返す正規化順
    （min, max）が incorporate-preview の from_id / to_id になり、そのまま骨格の edges の from → to として保存される。
    つまり「依存」の向きは表現の上に居場所が無く、キーの並び順が代わりに使われている。統制軸 assignment: 向きは関係の意味の判断
    （どちらを理解するのに他方が要るか）で人が決めるべきだが、実際に決めているのは文字列の比較。マニュアルは「向きは記載の順」と言うが、
    記載の順を選ぶ手が画面に無い。第 9〜10 波（IK-1047 → IK-1072 → IK-1118）で向きを全ての面で言うようにした結果、選ばれていない向きが
    確定した事実として学生に見えるようになった。既存の有向 4 本（2026.3〜2026.7）は辞書順が偶然妥当だった。
generalization:
  level: repo_pattern
  general_form: "対称な一意キー（無向の組・正規化した並び）で判断を記録したまま、その組に向きのある意味を足すと、キーの正規化順が人の選ばなかった向きとして保存・表示される"
pattern: default-hides-choice
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 25 周 面 C 第 11 周（judge_atlas_r11）。te-05 が「分子雲 — 惑星形成」を依存・メモ「惑星形成は分子雲（原始惑星系円盤の母体）に依存する」で採用
    （run uxsim/runs/c-astro-r25-atlas-r11/20261031T101202Z-98536 seq 4）→ 結果文とカードが「「分子雲」が「惑星形成」に依存する」で、メモと同じカードに
    逆向きで並んだ。頭脳「採用の時に向きを選ぶ欄は無かった…向きを直す方法が画面からは分からない」。反映の確認（seq 7）も［キャンセル］［下書きに追加する］
    だけ。te-04 は下書きの一覧で食い違いに気づきつつ凍結 2026.8（uxsim/runs/c-astro-r25-atlas-r11/20261031T101612Z-580 seq 2・10、凍結メモに「次版で確かめる」）。
    学生 st-03 / st-01 は L2 と右パネルで「逆に見える」（uxsim/runs/c-astro-r25-atlas-r11/20261031T102103Z-2572 seq 6・8、
    uxsim/runs/c-astro-r25-atlas-r11/20261031T102321Z-3474 seq 6・8）。schema.py の build_edge_key / parse_edge_key と routes/atlas_edges.py の
    decide・incorporate-preview を読んで、向きが node_id の辞書順（molecular_clouds < planet_formation）で決まることを確かめた。
resolution:
  perspective: [representation_change]
  note: >-
    第 25 周 第 11 波。判断の記録に向きの居場所を作った: migration 096 で atlas_edge_decisions に from_node_id / to_node_id（edge_key は無向のまま・
    既存行は NULL = 向きの記録なし・推測で埋めない）。採用 API は optional の from_node_id（2 端点のどちらかのみ・accept 以外は 422）を受け、
    to は store が導出する。読みは schema.decision_direction（記録があればそれ、無ければ辞書順 + recorded=False）に一本化し、incorporate-preview の
    patch・採用の結果文・候補カードの見出しと向きの文（下書きに入っていれば下書きの辺の向き）・凍結ゲートの列挙が読む。記録なしの依存は
    教員の面に EDGE_DIRECTION_UNRECORDED_NOTE、凍結の影響・学生の右パネル・L2 の列挙に label_vocab.EDGE_DIRECTION_UNRECORDED_SUFFIX を添える。
    凍結の影響に「次版で向きが変わる関係」（redirected_edges）を足し、公開済みの向きを次版の下書きで入れ替える手が影響に見えるようにした。
    管理 UI は種類が「依存」のとき「向き:」の 2 択（既定は提示順・アンカー atlas.edge-direction・マニュアル #edge-direction）。
    既定を提示順にしたのは指示どおり（IK 起票時の案は「既定なし」）で、選択は採用の確認の先頭に文で出る。
  landed_in:
    - backend/db/096_atlas_edge_decision_direction.sql
    - backend/core/atlas_edges/schema.py
    - backend/core/atlas_edges/store.py
    - backend/api/routes/atlas_edges.py
    - backend/core/atlas_lifecycle.py
    - backend/api/routes/atlas_view.py
    - backend/core/label_vocab.py
    - frontend/public/js/admin.js
    - docs/manual/teacher/17-admin-atlas.md
    - backend/tests/test_atlas_edge_direction.py
  verification:
    methods: [guardrail]
    unverified:
      - 実 DB での migration 096 の適用
      - ブラウザで向きを選んで採用 → 反映 → 凍結 → 学生の L2・右パネルまで通す再演（次の周の te-05 × edge-review）
      - 採用後に向きを変える専用の手（下書きの編集で入れ替える経路だけ）
  principles:
    - principle: route-all-surfaces-through-one-point
      use: consulted
      note: 向きの解決を schema.decision_direction 1 箇所に置き、記録なしの添え書きも label_vocab の 1 句（edge_direction_with_record）に通した。
related: [IK-1118, IK-1072, IK-1047, IK-0887]
view_of: []
history: []
---

## 課題

有向の関係「依存」の向きは、教員が選ぶのではなく、2 つの概念の node_id を辞書順に並べた順（前 → 後ろ）で決まる。第 11 周、te-05 が
「惑星形成は分子雲に依存する」とメモして「分子雲 — 惑星形成」を依存で採用すると、カード・反映の確認・反映の知らせ・下書きの一覧・凍結の影響・
学生の地図のすべてが「「分子雲」が「惑星形成」に依存する」と言い、凍結 2026.8 で公開された。教員 2 人・学生 2 人とも食い違いに気づいたが、
直す手は誰の画面にも無かった（反映後は［見送り…］も出ない — H54 の是正どおり）。

## 発見の観点

採用メモと記載の向きを同じカードで突き合わせた再演（reproduction）。向きがどこで決まるかを edge_key の正規化から preview の from/to まで辿った
（trace_walk）。

## 解決の観点

判断の行に記載の向きを持たせ（representation_change）、全ての面が同じ読み（decision_direction）を通る形にした。詳細は frontmatter の resolution.note。表示の面を足す是正ではなく、判断の記録に向きの居場所を作る（representation_change）。

## 一般化

型 default-hides-choice: 対称なキーの正規化順が、人の選ばなかった向きの「記録」になる。見分け方: 何も選ばずに採用したとき、どの向きが
記録されるか。それは判断か。確かめ方: 辞書順と逆の向きを意図する関係を採用し、記録と全ての表示がその向きになるか、選べないなら採用が
止まることを見る。
