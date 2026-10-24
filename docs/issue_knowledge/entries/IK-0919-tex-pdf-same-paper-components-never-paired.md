---
id: IK-0919
title: "同じ論文を TeX と PDF の両方で取り込んでも、両版の部品は名前が違う（「TOV network map」⇄「Structure network map」）ため結び付きの候補が作られず、名前によらない材料（同じ本文のチャンクの近さ）で探す規則は部品の代表チャンクが空なので働かない"
status: resolved
recorded_at: 2026-10-23
resolved_at: 2026-10-23
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が同じ論文の別の版（TeX / PDF）の部品を共通部品に揃える"
  layers: [concept_registry, knowledge_objects, pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information]
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
    接続軸 information: core/library/identity_candidates.py の規則 ② は正規化名の完全一致で、LLM が版ごとに別の名前を付けると当たらない。規則 ③（chunk-proxy
    近傍、:781-818 _component_chunk_ids / _chunk_proxy_twin）は部品の primary_chunk_id を起点にするが、砂場の部品は primary_chunk_id が
    null・source_chunks が空（/api/admin/courses/{id}/theory-components の生応答）で、規則が一度も走らない。部品の根拠 claim は chunk_id を持つ（overview の
    inputs / outputs の claim_ids）のに、規則の入力に運ばれていない。文書単位の「同じ論文の別の版」（題名・arXiv ID の一致）も候補づくりの入力に無い。処理・構造・統制は none（規則自体の判定は正しい —
    入力が来ない）。
generalization:
  level: repo_pattern
  general_form: "名前によらない照合の規則があるのに、その入力（代表の位置）が上流で埋められず、名前が揺れる対が永久に照合されない"
pattern: available-but-unwired
discovery:
  perspective: [reproduction, data_inspection]
  note: >-
    教員 te-02 が PDF 版の検出要素に「TOV network map」が無いと記し（uxsim/runs/c-astro-r25-share-r3/20261023T032742Z-99528
    seq8）、同一性の候補が空（seq12）。PDF 版の検出要素の生応答（「Structure network map — constraint」等）と、部品一覧の primary_chunk_id・source_chunks
    の空を突き合わせた（uxsim/runs/c-astro-r25-share-r3/20261023T033629Z-52380
    seq2・uxsim/runs/c-astro-r25-share-r3/20261023T033729Z-59479 seq3 の theory-components 応答）。
resolution:
  perspective: [carry_through]
  note: >-
    core/library/identity_candidates.py: ① 規則③（chunk-proxy）の起点を代表チャンクだけでなく出典チャンクと根拠の主張（evidence_claims / linked_claim_ids / inputs・outputs の claim_ids を theory_claims_live で 1 回解決・同じ document の中だけ）の chunk_id から決定論で導く（component_origin_chunk_ids・起点は最大 4）。② 新規則②b: documents の arXiv ID（source_url → filename → source_path を normalize_arxiv_id / arxiv_id_from_filename で正規化）か題名の正規化完全一致で「同じ論文の別の版」を先に解き、その版の部品とだけ、根拠の式の正規化 LaTeX・主張の正規化本文の一致で対を選ぶ（名前を使わない・lexical_match・事実文 SAME_PAPER_REASON_*・数値なし）。③ 候補が空の事実文に SAME_PAPER_VERSIONS_NOTE。LLM 0 回・embedding 0 回・live ビューのみ。
  landed_in:
    - "backend/core/library/identity_candidates.py"
    - "backend/api/routes/library.py"
    - "backend/tests/test_concept_registry_identity_candidates.py"
  verification:
    methods: [guardrail]
    unverified:
      - "砂場の TeX 版と PDF 版で主要な部品の対が実際に出るか（PDF 版の式は復元由来で LaTeX が一致しないことがある）"
      - "実 DB での SQL（uuid 配列の受け渡し・id::text の照合）と部品が多いときの性能"
related: [IK-0864, IK-0916]
view_of: []
history: []
---

## 課題

症状: PINN の TeX 版と PDF 版を両方取り込んでいるのに、同一性の候補が空で、PDF 版の部品を TeX 版の名前で探しても見つからない。原因: 版ごとに部品名が違い名前の規則が当たらず、本文の近さの規則は部品の代表チャンクが空で走らない。

## 発見の観点

教員ペルソナの通し受講（reproduction）と、生応答の部品の欄の点検（data_inspection）。

## 解決の観点

是正済み（第 3 波）。同じ論文の別の版を文書単位で先に解き、その中で根拠の式・主張の本文の一致から部品の対を候補にする規則を足し、本文の近さの規則の起点を根拠の主張のチャンクにも広げた。

## 一般化

規則はあるが入力が来ない型（available-but-unwired）。確かめ方は、同じ論文の 2 つの版を取り込み、候補づくりの段を走らせて、少なくとも主要な部品の対が名前によらず候補に出ることを砂場で見ること。
