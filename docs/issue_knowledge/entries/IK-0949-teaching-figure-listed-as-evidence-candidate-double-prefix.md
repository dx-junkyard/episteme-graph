---
id: IK-0949
title: "教員が教材図スタジオで作って採用した説明図が、原稿スタジオの根拠リンクで論文由来の根拠と同じ「[要確認] 根拠候補はありますが…教員確認が必要」の枠に並び（根拠の状態の判定が自作図を根拠に数える）、見出しは項目の題名「図: …」に種類の印「図:」が重なって「図: 図: …」になる"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.5"
  - "docs/features/teaching_figure_studio_design.md"
feature_context:
  realizing: "教員が自作の説明図を採用・保存した後、根拠リンクで論文の根拠と自分の図を区別して読む"
  layers: [teaching_figures, lecture_studio, frontend_admin_ui]
classification:
  axes:
    processing: [wording]
    structure: [representation]
    connection: [meaning]
    governance: [none]
  axis_confidence:
    processing: high
    structure: medium
    connection: medium
    governance: high
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    構造軸 representation: 採用は topic.evidence_links に extra.teaching:true の図を足す（教材図スタジオ §7.1b — 書き換え後の復元のため）が、
    backend/core/lecture_studio_state.py topic_evidence_state（164 行〜）は _real_evidence_items でこれを論文の根拠と区別せず数え、coverage の無いトピックを
    weak「根拠候補はありますが…教員確認が必要」にする。接続軸 meaning: 根拠（論文の裏付け）と教材（教員の説明）の出所の区別が根拠リンクの読み手に渡らない。
    処理軸 wording: core/course_content_builder.py 3161 行と admin-lecture-studio.js 3524 行が title を「図: 」+ キャプションで作り、一覧は種類の印「図」を前に
    付けるので「図: 図: …」になる。統制軸 none。
generalization:
  level: repo_pattern
  general_form: "復元用に同じ置き場へ入れた利用者自作の項目を、出所を区別しない判定と一覧がほかの由来の項目と同じ扱いで数え・並べる"
pattern: projection-mistaken-for-source
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    te-03 が見出しだけのトピックに自作の模式図を採用して保存すると、根拠リンクが「[要確認] 根拠候補…」「図: 図: 超新星と…」になり「論文の図ではなく私が作った
    説明図なのに論文の根拠のように見える」と書いた。共有コースの根拠一覧にも「図: 図: ダストの偏光観測で…」。run:
    uxsim/runs/c-astro-r25-studio-r3/20261023T071024Z-19157（seq20）/ uxsim/runs/c-astro-r25-studio-r3/20261023T071920Z-61773（seq13・14）。
resolution:
  perspective: [carry_through, canonical_source]
  note: "extra.teaching の図は topic_evidence_state の判定から外して根拠に数えず、根拠の項目に teaching: true / origin_label（TEACHING_FIGURE_ORIGIN_LABEL「教員が作った説明図」）を付けた。題名の「図: 」の前置をやめ、種類の印は lsEvidenceItemKindText の 1 箇所で付ける。"
  landed_in:
    - "backend/core/course_content_builder.py"
    - "backend/core/lecture_studio_state.py"
    - "backend/core/label_vocab.py"
    - "frontend/public/js/admin-lecture-studio.js"
    - "backend/tests/test_lecture_studio_round25_r3.py"
  verification:
    methods: [guardrail]
    unverified:
      - "自作図を採用して保存した後の根拠リンクは実画面で見ていない"
related: [IK-0910, IK-0875]
view_of: []
history: []
---

## 課題

症状: 自分で作った説明図が、論文の根拠と同じ「確認が必要な根拠候補」として出る。見出しの「図:」が二重。

原因: 採用は復元のために根拠の置き場へ図を入れ、根拠の状態の判定と一覧はその出所を見ない。題名と一覧の両方が種類の前置を付ける。

## 発見の観点

採用 → 保存 → 根拠リンクの読み直しを 1 本で歩いた。

## 解決の観点

第 25 周 面 D 第 3 周の是正（2026-10-23）で解決。extra.teaching の図は topic_evidence_state の判定から外して根拠に数えず、根拠の項目に teaching: true / origin_label（TEACHING_FIGURE_ORIGIN_LABEL「教員が作った説明図」）を付けた。（以下は起票時の見立て）出所（teaching）を判定と一覧で区別し、前置は 1 箇所に寄せる。

## 一般化

置き場を共有するなら出所の印も共有する。確かめ方は、根拠の無いトピックに自作図を採用して保存し、根拠の状態が変わらず、一覧で自作と分かることを見ること。
