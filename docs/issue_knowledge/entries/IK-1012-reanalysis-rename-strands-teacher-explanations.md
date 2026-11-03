---
id: IK-1012
title: "再解析で部品の名前（label）が変わると、その部品は supersede されて新しい行になり、教員が書いて共有・査読・支持した独自解釈（C層 component_explanations）は旧い行に残ったまま live な部品から消える — KO8 の再係留は agent ID を持つ参照だけを書き換え、部品の UUID を持つ C層の表を対象にしていない"
status: resolved
recorded_at: 2026-10-27
resolved_at: 2026-10-27
sources:
  - "docs/features/knowledge_objects_design.md"
  - "docs/architecture/layer_registry.md"
feature_context:
  realizing: "論文を再解析しても、教員が部品に付けた説明・査読・支持・引用を新しい解析結果の同じ部品に引き継ぐ"
  layers: [knowledge_objects, endorsement_c]
classification:
  axes:
    processing: [none]
    structure: [representation]
    connection: [version]
    governance: [resume]
  axis_confidence:
    processing: medium
    structure: medium
    connection: high
    governance: medium
  proposals: []
  cause_status: confirmed
  review: candidate
  reviewed_by: null
  reviewed_at: null
  basis: >-
    確認（コードと砂場の実測）: component の stable_key は label を材料に含む（core/knowledge_objects/stable_key.py::component_stable_key）。
    再解析で LLM が部品名を言い換えると（旧「TOV network map」→ 現「TOV residual loss at collocation points」ほか）キーが一致せず、
    sync_live_rows は旧行を superseded にして新しい UUID の行を INSERT する。第 2 段突合（fallback_match_column = name の 1 対 1 完全一致）も
    名前が変わったので働かない。KO8 の record_and_reanchor（core/knowledge_objects/remap.py）は「UUID を直接持つ参照は書き換え不要」の
    前提で agent ID を持つ 6 表だけを書き換え、component_id（UUID・FK）を持つ component_explanations（とそれに吊られた支持・引用）は対象外。
    UUID が保たれる前提は stable_key が一致した場合にしか成り立たない。構造軸 representation = 「同じ部品」を版をまたいで指す受け皿が
    stable_key の一致にしか無い。接続軸 version = 版の付け替えに人の記録が運ばれない。統制軸 resume = 再実行が前回の人の判断を引き継がない。
    処理軸 none = 個々の処理は設計どおり動く。
generalization:
  level: general
  general_form: "再生成で対象の同一性を内容由来のキーでしか結ばないと、内容が言い換えられた対象に付いていた人の記録が旧い版に取り残される"
pattern: reexecution-overwrites-human-decision
discovery:
  perspective: [reproduction, data_inspection]
  note: "第 25 周 第 6 周の前に PINN の TeX 版を第 5 波のコードで再解析したところ部品名が 15 件とも変わり、te-04 が第 5 周に「TOV network map」へ書いて共有した独自解釈が live な TeX 版の部品一覧のどこにも出なくなった。remap.py を読み、再係留の対象表に component_explanations が無いことを確かめた（出所: uxsim/runs/round25/r6_prereqs.md「再解析の実測」）。"
resolution:
  perspective: [carry_through, explicit_contract]
  note: >-
    オーナー判断（推奨どおり）で、再解析で superseded になった旧部品と新しく INSERT された部品を出典（evidence_claims / source_chunks の block_id 集合）の重なりで突き合わせ、相互に唯一の最良で 1 対 1 に決まる組だけ人の記録を付け替える（remap.match_superseded_components — Jaccard 0.5 以上か両側とも重なりの相手が唯一・同点は結ばない）。reanchor_superseded_components が element_id_remap に remap_kind=superseded_component で記録し、component_explanations（支持・引用は説明に吊られて付いていく）/ element_identity_links の部品側 / epistemic_ledger の component 行 / W層の注釈・対話を同じトランザクションで書き換える（一意制約に当たる行は動かさず skipped に残す・DELETE なし）。persist_components は sync の superseded_ids / inserted_ids を受けて savepoint の内側で呼び、失敗しても同期は止めず監査に残す。決まらなかった説明は旧行に残し、教員の説明一覧が X-Explanation-Fact で「再解析前の部品に付いたままの説明があります（部品名・説明の題名の列挙）」と言う。名前を保護列にする案は誤った名前まで固定するので採らない。
  landed_in:
    - "backend/core/knowledge_objects/remap.py"
    - "backend/core/knowledge_objects/sync.py"
    - "backend/core/document_pipeline/persistence.py"
    - "backend/api/routes/theory_components.py"
    - "docs/features/knowledge_objects_design.md"
    - "backend/tests/test_share_round25_r6_fixes.py"
  principles:
    - principle: sync-by-stable-key-and-protect-human-decisions
      use: consulted
      note: "参照の結び直しを agent ID から出典の重なりへ広げた（派生形「内容キーが一致しない場合の第 2 段突合」）。固有の差分: 運ぶ対象が人の記録で、決まらないときは推測で結ばず事実文で旧行に残す。"
  verification:
    methods: [guardrail]
    unverified:
      - "実 DB の再解析で付け替えが起きる組と残る組の分かれ方（砂場での再解析・再演は第 7 周）"
      - "付け替え先が決まらなかった説明を教員が手で移す操作は作っていない"
related: [IK-0002, IK-0108]
view_of: []
history: []
---

## 課題

症状: PINN の TeX 版（document ce667c63）を再解析した後、te-04 の原稿スタジオの部品一覧に「TOV network map」が無く、te-04 が第 5 周に
その部品へ書いて共有・査読・自己支持した独自解釈も、live な TeX 版のどの部品の説明一覧にも出ない（superseded の旧行に残っている）。
第 6 周の (b2)（他の教員がその説明を支持・引用する場面）の材料が失われた。

原因: 部品の stable_key が label を材料に含み、LLM が名前を言い換えると別の部品として新しい行になる。KO8 の再係留は agent ID を持つ参照
（element_explanations / epistemic_ledger / challenges / element_annotations / deliberation_sessions / element_identity_links）だけを書き換え、
部品の UUID を FK で持つ component_explanations（と支持・引用）は「UUID は保たれる」前提で対象外。

出所: uxsim/runs/round25/r6_prereqs.md（再解析の実測）・uxsim/runs/round25/BRAIN_NOTES_product_A_r6pre.md / BRAIN_NOTES_product_B_r6pre.md（再解析時の所見）。

## 発見の観点

再解析の前後で同じ教員の部品一覧と説明一覧を製品 API で読み比べ（reproduction）、remap.py の対象表と突き合わせた（data_inspection）。

## 解決の観点

是正済み（第 25 周・面 A 第 6 波・2026-10-27）。

旧案: (1) superseded にする旧部品と新部品の対応（出典 block の重なり・式の重なり）が 1 対 1 で決まるときだけ component_explanations を新 UUID へ付け替え、element_id_remap に記録する（推測で結ばない — 決まらなければ旧行に残し、教員に「前の解析の部品に付いていた説明」として事実文で見せる）。(2) 人の記録が付いた部品は label を保護列に入れ supersede しない。どちらを採るかは人の記録の扱いの解釈を含む。運転側は説明を新しい部品に書き直して材料を作り直した。

## 一般化

内容由来のキーは「同じ内容なら同じ対象」を保証するが、「言い換えられた同じ対象」を拾えない。人の記録が UUID に吊られている表は、
キーが変わる経路でも運ばれるかを確かめる。型 reexecution-overwrites-human-decision（消去ではなく取り残し）。

## 第 6 周の再演（第 25 周・面 A 第 6 周、2026-10-27）

第 6 周の再演（同じ根の別の面）: ①同一性の候補 — 再解析で TeX 版の部品が superseded になり、その部品へのリンクが一覧から隠れ、PDF 版のリンク 1 本だけの半端な候補が 10 件残った（候補エントリの名前は旧 TeX 版の部品名）→ IK-1020。②凍結したコースの ⚓ — agent ID（comp_003）で埋めた参照が再解析後の別の部品へ解決された → IK-1021。③教員の説明は運転側で新部品に書き直した材料が学生 2 人・教員 te-01 に届いた（取り残しは書き直しで回避しただけ）。run: uxsim/runs/c-astro-r25-share-r6/20261027T122225Z-3503 seq2、uxsim/runs/c-astro-r25-share-r6/20261027T122930Z-7994 seq7

## 第 7 周の再演（第 25 周・面 A 第 7 周、2026-10-28）

第 7 周の再演: 説明の側は事実文が GONE 候補 — te-04 の説明一覧に「この論文には、再解析前の部品に付いたままの説明があります（「TOV network map」の標準の説明・…「AI と下書きした説明 — TOV 方程式」）。…記録は消さずに残しています。」（uxsim/runs/c-astro-r25-share-r7/20261027T173130Z-60105 seq3）。残り: その記録を開く手・置き場がこの画面に無い（「残しています」と言うが読めない）。学習者側の顔は凍結コースの ⚓ が全部開けない形で残る → IK-1051。

## 第 7 波の是正（第 25 周・面 A、2026-10-28）

第 7 波の是正（2026-10-28・残り）: 説明一覧の［再解析前の説明を開く（読み取り専用）］→ GET /api/admin/theory-components/{id}/stranded-explanations（UUID を返さない）。作成者の行には新しい部品の［追加］から書き直す案内、ほかの行には「書き直せるのは作成者だけ」、標準の説明には作り直しの文。凍結コースの ⚓ は IK-1051 の第 7 波で読み時につながる。

## 第 8 周の再演（第 25 周・面 A 第 8 周、2026-10-28・judge_share_r8）

置き場の入口は出た（「…［再解析前の説明を開く（読み取り専用）］」— uxsim/runs/c-astro-r25-share-r8/20261027T224703Z-1148 seq3）。開いていないので中身は未評価。
