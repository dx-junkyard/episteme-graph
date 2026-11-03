---
id: IK-0807
title: "dsl_linking に渡る accepted_claims の大半が span 単位の親主張で atomic を持つのは 48 件中 3 件だけになり、プロンプトの「atomic な主張だけを根拠に」の規則と材料が両立しない"
status: resolved
recorded_at: 2026-10-17
resolved_at: 2026-10-17
sources:
  - "docs/pipeline/agents.md"
  - "CLAUDE.md"
feature_context:
  realizing: "採択された主張・式・中心命題を DSL グラフに接続し、各辺を atomic な主張で裏付ける"
  layers: [pipeline_a]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [information, contract]
    governance: [none]
  axis_confidence:
    processing: medium
    structure: medium
    connection: medium
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認済み（2026-10-17）: dsl_linking の input_builder は qualified span を軸に canonical_claim_id_for_span で claim object を引いていたが、span_id はブロックごとに振り直されるため同じ節に同じ span_id が複数あると突合が曖昧（""）になり、legacy ID（claim:tex_bN:span_k）の親 span 行がそのまま渡っていた。atomic な子は親と同じ span_id を持つので後段の追加ループでも拾えず、48 件中 3 件しか atomicity を持たなかった。IK-0719（ID 二系統）と同根で、ここで直したのは dsl_linking の入力だけ。 以下は起票時の仮説:
    仮説: dsl_linking の input_builder が accepted_claims を claim_qualification の qualified span（親）から組み、ClaimQualification の
    atomic_claims（と ClaimObjectBuilder の claim objects）を渡していない。接続軸 information = 前段で作った atomic 主張が後段の材料に
    届かない。接続軸 contract = プロンプトは「atomic のみを evidence に」を要求するのに、入力は親主張でその規則を満たせない。
    構造軸 none / 処理軸 none / 統制軸 none。確定には dsl_linking の input_builder が accepted_claims を何から組むかを読む。
    thesis_nodes が accepted_claims に無い ID を引く件（ID 二系統）は IK-0719 の側面。
generalization:
  level: general
  general_form: "前段で細かい単位を作ったのに後段の入力を粗い単位から組むため、後段の規則（細かい単位だけを根拠に）が材料で満たせない"
pattern: available-but-unwired
discovery:
  perspective: [data_inspection]
  note: "論文レーダー自律周回で頭脳 W0 が dsl_linking（2610.08751・seq 3282）の材料を読み、accepted_claims 48 件のうち atomicity を持つのが 3 件だけで、span 破断の断片主張（tex_b158:span_002）もそのまま入っていることに気付いた（出所: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md）。TeX マクロ是正（IK-0804〜0806）前に取り込んだ素材。"
resolution:
  perspective: [representation_change, canonical_source]
  note: "accepted_claims を claim object を母集合に組み直し、ID を claim object の claim_id 一系統に揃えた。atomic な子を持つ親（parent_claim_id / subclaim_ids、リンクの無い旧 artifact は split_suggestions の ID・本文一致）は子に置き換え、親は子が無いときだけ渡す。全行に atomicity / is_atomic を明示し、claim object に対応の無い採択 span は atomicity=unknown で落とさず渡す。block_id・役割・tier は qualified span と（節, span_id）→ 本文 → 親の突合先の順で突合して補い、決まらなければ空（推測しない）。thesis_nodes の claim_ids も同じ名前空間に正規化し、原子化された親は子へ展開する（選抜の thesis 優先も展開後の ID で）。上限は従来値（48）。IK-0719 の他段（thesis_reconstruction・component_assembly の入力）は未是正。"
  landed_in:
    - "src/episteme_graph/agents/dsl_linking/input_builder.py"
    - "src/tests/agents/dsl_linking/test_atomic_claim_input.py"
    - "docs/pipeline/agents.md"
  verification:
    methods: [guardrail]
    unverified:
      - "seq 3282 の論文（2610.08751）の再解析で accepted_claims の atomicity 内訳と thesis_nodes の参照解決を実測すること"
      - "thesis_reconstruction・component_assembly の入力の ID 二系統（IK-0719 の残り）"
related: [IK-0719, IK-0733, IK-0804]
view_of: []
history: []
---

## 課題

症状: dsl_linking の accepted_claims 48 件のうち atomicity を持つのは 3 件だけで、他は span 単位の親主張。プロンプトの「atomic のみを evidence に」と
材料が矛盾する。span 破断の断片（「{\rm Mpc}$ units extends…」）も主張として入る。thesis_nodes は accepted_claims に無い claim_span_005_52 等を引く（IK-0719）。
原因（仮説）: 入力が atomic 子主張ではなく親 span から組まれている。

出所: uxsim/runs/radar_loop/BRAIN_NOTES_W0.md（seq 3282）。

同じ症状の別段（頭脳 W2）: uxsim/runs/radar_loop/BRAIN_NOTES_W2.md（seq 3281 — thesis_reconstruction の accepted_claims にも atomic 子主張が無く、親 span の本文で渡る。脚注主張の本文に「（脚注:」の印が残る）。

追加の出所（第 2 波 deploy 後の run・頭脳 Z0）: uxsim/runs/radar_loop/BRAIN_NOTES_Z0.md（seq 3612 — dsl_linking の入力で from_equations が equations に無い inline 式 ID（tex_b44_207・b48_126・b50_6・b71_718・b73_6）を指し、thesis_nodes の claim_ids が accepted_claims に無い ID を指し、accepted_claims に split_required の主張が 3 件混じる。thesis_nodes の本文は日本語・claim は英語）。是正後の run での残存の観察として記録。

## 発見の観点

dsl_linking の accepted_claims の単位と、プロンプトの根拠の規則を突き合わせた（data_inspection）。

## 解決の観点

accepted_claims を claim object を母集合に組み直し、ID を claim object の claim_id 一系統に揃えた。atomic な子を持つ親（parent_claim_id / subclaim_ids、リンクの無い旧 artifact は split_suggestions の ID・本文一致）は子に置き換え、親は子が無いときだけ渡す。全行に atomicity / is_atomic を明示し、claim object に対応の無い採択 span は atomicity=unknown で落とさず渡す。block_id・役割・tier は qualified span と（節, span_id）→ 本文 → 親の突合先の順で突合して補い、決まらなければ空（推測しない）。thesis_nodes の claim_ids も同じ名前空間に正規化し、原子化された親は子へ展開する（選抜の thesis 優先も展開後の ID で）。上限は従来値（48）。IK-0719 の他段（thesis_reconstruction・component_assembly の入力）は未是正。

## 一般化

細かい単位を作っても後段に粗い単位を渡すと、後段の規則が満たせない。辞書の型 available-but-unwired。
