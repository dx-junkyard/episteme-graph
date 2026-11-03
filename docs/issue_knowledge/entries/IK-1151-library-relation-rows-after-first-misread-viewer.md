---
id: IK-1151
title: "ライブラリの関係の一覧で、2 行目以降の相手の名前・向きの文・種類の札（上位/下位）が取り違わる — 第 10 波で足した「確定の理由の書き足し」が _describe_relations のループ内で変数 viewer（見ているエントリ）を閲覧者のユーザー ID で上書きし、2 行目から is_subject が偽になる（te-03 の画面で「関連: 状態方程式」の下に「この概念と「TOV residual loss at collocation points」は関連します」）"
status: resolved
recorded_at: 2026-10-31
resolved_at: 2026-10-31
sources:
  - "docs/architecture/persona_enactment_testing_design.md"
  - "docs/features/concept_registry_design.md"
feature_context:
  realizing: "教員がエントリの詳細で、この概念と他の概念の関係（上位・下位・関連）を、相手の名前と向きで正しく読んで確定・見送りする"
  layers: [concept_registry, frontend_admin_ui]
classification:
  axes:
    processing: [logic, regression]
    structure: [none]
    connection: [none]
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
    処理軸 logic + regression: backend/api/routes/library.py _describe_relations は viewer = 見ているエントリ id を置いてから行のループに入り、
    ループの末尾（第 10 波の can_add_decision_reason の判定）で viewer = str(viewer_user_id) と同じ名前に閲覧者のユーザー id を代入する。
    2 行目以降の is_subject = (not viewer) or subject_entry_id == viewer は常に偽になり、counterpart は subject_name（= 見ている概念自身）、
    kind_label は broader なら「下位」、direction_line は逆向きになる。1 行目は正しい。.venv で同じ 2 行（broader → related）を渡して再現した
    （1 行目「「TOV方程式」がこの概念の上位です」・2 行目「この概念と「TOV residual」は関連します」）。砂場 DB の library_entry_relations は 3 行とも
    subject = 67727cb0 で、データは正しい。
generalization:
  level: repo_pattern
  general_form: "行ごとの装飾ループの途中に追加した判定が、ループの外で決めた比較用の変数を同じ名前で上書きし、2 行目以降の判定だけが静かに変わる"
pattern: same-name-different-referents
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    第 25 周 面 A 第 11 周（judge_share_r11）。te-03 の頭脳が「開いているのが当の TOV residual loss なのに、相手の名前ではなく自分の名前が入る」と書いた
    （uxsim/runs/c-astro-r25-share-r11/20261031T101202Z-98535 seq 3・17・18）。生応答の 2 行目 counterpart_name が subject_name と同じことを確かめ、
    _describe_relations を読み、.venv で関数を直接呼んで再現した。第 10 周（judge_share_r10）の同じ行は「この概念と「状態方程式」は関連します」だった。
resolution:
  perspective: [single_point_fix, guardrail_fix]
  note: >-
    閲覧者のユーザー ID をループの外で別名の viewer_uid として 1 回だけ作り、理由の書き足しの判定はそれと比べる（見ているエントリの viewer を上書きしない）。2 行以上（上位・関連・相手が subject の上位）をループに通し、各行の相手の名前・向きの文・札・書き足しの可否を固定する単体テストと、viewer の再代入の不在をソースで固定する検査を足した。
  landed_in:
    - "backend/api/routes/library.py"
    - "backend/tests/test_share_round25_r11_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "関係が 2 本以上あるエントリの詳細を砂場の画面で読むこと（第 12 周 te-03 × (c)）"
related: [IK-1063]
view_of: []
history: []
---

## 課題

関係の一覧の 1 行目は正しく、2 行目以降で相手の名前が自分の名前になり、上位の関係は「下位」と逆向きに出る。第 10 波の是正（確定者本人が理由を
書き足せる）の副作用。

## 発見の観点

頭脳の違和感（reproduction）から、生応答 → route の装飾関数 → 変数の流れを辿った（trace_walk）。

## 解決の観点

未着手。変数の取り違えの是正と、ループを通す回帰テスト。

## 一般化

装飾ループの途中で比較用の変数名を再利用すると、1 行目だけ正しい不具合になる。見分け方: 2 行以上ある一覧で 2 行目以降だけがおかしい。確かめ方:
2 行以上の入力を関数に通して全行を検査する。

## 第 11 波の是正（2026-10-31）

変数を分けて直した（resolution.note）。2 行以上の入力で退行を再現する単体テストを足した。
