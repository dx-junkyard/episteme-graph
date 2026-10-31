---
id: IK-0701
title: "論文レーダー・ディスカバリーの「取り込み済み」候補は、こちらで解析済みの教材へのリンクが無く、判定に使う集合も閲覧者の可視性で絞られないため、見えない教材を理由に取り込めない候補が出る"
status: resolved
recorded_at: 2026-10-16
resolved_at: 2026-10-16
sources:
  - "docs/features/paper_radar_design.md §15.5"
  - "docs/features/paper_discovery_design.md"
feature_context:
  realizing: "候補のうち既に取り込まれた論文を示し、教員がこちらの解析結果を読むか、自分の教材として取り込むかを選べるようにする"
  layers: [paper_radar, paper_discovery, auth_visibility]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [condition]
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
    確認（backend/core/paper_discovery/search.py の ingested_arxiv_ids）: 判定集合は全 documents の source_url から arXiv ID を
    正規化した集合で、閲覧者の可視性で絞らず、どの document かも返さない。接続軸 condition = 閲覧者の可視性という条件が判定に
    渡らないため、他の教員が private で取り込んだ論文も「取り込み済み」になり、チェックボックスが消えて自分では取り込めず、
    開くこともできない（存在だけが漏れる）。構造軸 representation = 判定が ID の集合（真偽）だけを表し、対応する教材を指す
    手掛かりを持たないので、見える場合でもリンクを作れない。処理軸 none = 正規化と照合は正しい。統制軸 none（medium）=
    取り込みの承認・担当の問題ではない。
generalization:
  level: repo_pattern
  general_form: "「既にある」の判定が全域の集合で行われ、閲覧者に見えない対象も既存扱いになって、作成も閲覧もできない状態になる"
pattern: scope-widened-silently
discovery:
  perspective: [inventory, invariant_audit]
  note: "論文レーダー UX の棚卸し（uxsim/runs/radar_loop/UX_BASELINE.md §3）で取り込み済み候補の表示と判定経路を突き合わせ、document 可視性の fail-closed 原則（PR8・CR1）と判定集合が食い違うことを見た。"
resolution:
  perspective: [required_argument, carry_through]
  note: "第 2 波（paper_radar_design.md §19 C）。レーダーの「取り込み済み」を search.visible_ingested_documents(session, *, visible_document_ids) に置き換えた。可視性は必須のキーワード引数で受け、問い合わせの内側（id::text = ANY(:visible_ids)）で強制し、空・None は SQL を発行せず空（fail-closed — 見えない教材を取り込み済みにしない）。route が services.list_visible_document_ids を注入（core はユーザーを知らない）。見える教材は arXiv ID → {document_id, material_id, analysis_status} を返し、候補カードのリンク（教材の行・グラフレビュー・位置づけ・連鎖）に使う。他の教員の非公開の取り込みは new のまま（本人が取り込める・存在を漏らさない）。分野購読・基盤論文・引用検索の ingested_arxiv_ids は従来どおり（次の波）。"
  landed_in:
    - backend/core/paper_discovery/search.py
    - backend/core/paper_discovery/radar.py
    - backend/api/routes/paper_discovery.py
    - frontend/public/js/admin-paper-radar.js
    - "docs/features/paper_radar_design.md §19"
  verification:
    methods: [guardrail]
    unverified:
      - "教員 2 人の実データ（片方が非公開で取り込んだ論文）で、もう片方に new と出ることを実 DB・実画面で見ていない（FakeSession と route の注入検査のみ）"
      - "分野購読モーダル・基盤論文・引用検索の取り込み済み判定は全域のまま（本解決の範囲外）"
  principles:
    - principle: enforce-condition-in-query-with-required-argument
      use: consulted
      note: "可視性を必須引数で受けて問い合わせ内で強制・空は SQL 非発行。ただし旧関数は他の 3 経路が使うため残し、レーダーだけを新関数に寄せた（全経路の置換は次の波）"
related: [IK-0700]
view_of: []
history: []
---

## 課題

症状: 候補に「取り込み済み」バッジと「この論文はすでに教材として取り込まれています。」が出るが、その教材へのリンクが無い。さらに、
他の教員が非公開で取り込んだ論文も取り込み済みと判定され、チェックボックスが出ないので、閲覧者は読めも取り込めもしない。
原因: 判定集合が全 documents の source_url から作られ、可視性で絞らず、document を指す情報を持たない。

## 発見の観点

UX の棚卸し（inventory）で、表示と判定経路を不変条項（document 可視性の fail-closed）に照らした（invariant_audit）。

## 解決の観点

未解決。判定を閲覧者に見える document への対応に変え（条件を問い合わせ内で強制）、見える場合だけリンクを出す。

## 一般化

重複作成を防ぐための「既にある」判定を全域で行うと、見えない対象が作成を妨げ、存在だけを漏らす。確かめ方は、別の教員が非公開で
取り込んだ論文を候補に含め、閲覧者には取り込み可能な通常候補として出ること、自分に見える取り込み済みには教材へのリンクが
出ることを実データで見る（教員が 1 人だけの環境では検出できない）。
