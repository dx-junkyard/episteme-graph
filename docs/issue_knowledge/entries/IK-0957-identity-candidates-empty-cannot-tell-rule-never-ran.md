---
id: IK-0957
title: "同一性の候補が空のとき、候補づくりの段がいまの規則で既存の教材を探したのか（探して無かったのか、まだ探していないのか）を言わず、規則を足しても既存の教材は再解析しない限り探されない"
status: resolved
recorded_at: 2026-10-25
resolved_at: 2026-10-25
sources:
  - "docs/architecture/persona_enactment_testing_design.md §22.2"
feature_context:
  realizing: "教員が同じ論文の別の版の部品の対を、同一性の候補から確かめる"
  layers: [concept_registry, pipeline_a, frontend_admin_ui]
classification:
  axes:
    processing: [none]
    structure: [none]
    connection: [version]
    governance: [resume, completion]
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
    統制軸 resume: 同一性の候補は解析パイプラインの最後の段（identity_candidates）が書く行で、一覧（routes/library.py:1279-1398）は保存済みの行を読むだけ。IK-0919
    の新規則（同じ論文の別の版）は 2026-10-24 に入り、砂場の PINN の 2 版は 2026-10-22 に解析済み（教材一覧の analyzed_at）で、段を走らせ直す契機が無い。統制軸
    completion:
    空の事実文（IDENTITY_CANDIDATES_EMPTY・SAME_PAPER_VERSIONS_NOTE）は「版は名前が違っても候補になります」と言うが、その規則で探したかどうかを言わない。接続軸
    version: 規則の版と、行を書いた run の版を照らす手がかりが画面に無い。
generalization:
  level: repo_pattern
  general_form: "規則を足しても、その規則で過去の対象を探したかどうかが分からず、空の結果が「無い」と「まだ探していない」を区別しない"
pattern: completion-defined-by-proxy
discovery:
  perspective: [reproduction, trace_walk]
  note: >-
    te-05 が同一性の候補の空を見て「PDF
    版はまだ探されていないのか、同じものが見つからなかったのか分からない」と記した（uxsim/runs/c-astro-r25-share-r4/20261024T232812Z-98848
    seq12）。候補の出所（パイプラインの段）と教材の解析日・規則の着地日を突き合わせた。
resolution:
  perspective: [first_class_state]
  note: >-
    候補づくりの規則の版を一級の値にした。core/library/identity_candidates.py に IDENTITY_RULES_VERSION（r6-same-paper = IK-0919 の規則を含む版）を置き、段の artifact に rules_version を残す。一覧の空状態（routes/library.py _identity_rules_scan_facts）は閲覧できる document の採用 run（completed）の artifact の版を照らし、いまの規則で探していない教材を題名で列挙して「パイプラインを実行 ▼」から探し直す道を言う。全部探していれば「すべていまの規則で探しました。そのうえで候補は見つかっていません。」判定できないときは何も足さない。教材一覧の行の「同じ論文の別の版があります」は今回入れていない。
  landed_in:
    - "backend/core/library/identity_candidates.py"
    - "backend/api/routes/library.py"
    - "docs/admin_operations/library.md"
    - "backend/tests/test_share_round25_r4_fixes.py"
  verification:
    methods: [guardrail]
    unverified:
      - "規則の版が無い既存の解析済み教材を持つ実データでの空状態"
      - "教材一覧の行の別の版の告知（未実装）"
related: [IK-0919, IK-0864]
view_of: []
history: []
---

## 課題

症状: 同じ論文の TeX 版と PDF 版を取り込んでいても候補が空で、教員はそれが「対が無い」のか「まだ探していない」のかを区別できない。原因: 候補は解析時にしか作られず、規則の追加後に既存の教材を探し直す契機も、探した時点を示す事実も無い。

## 発見の観点

再現（te-05 seq12）と経路の追跡。

## 解決の観点

是正済み（第 25 周・面 A 第 4 波・2026-10-25）。

旧案: 是正案: ① 一覧の空の事実文に、閲覧できる教材のうち「いまの規則で候補づくりを走らせていない教材」の題名（`stage_outputs.identity_candidates` の規則版で判定・件数なし）と、その行の「パイプラインを実行 ▼」への道案内を添える。② 同じ論文の別の版の規則は文書の対だけで決まるので、教材一覧の行に「同じ論文の別の版があります（候補づくりを実行していません）」を出す。どちらも LLM 0 回。

## 一般化

完了を代理の信号（空）で判定する型。確かめ方は、規則を足した後に既存の 2 版について空の文が「まだ探していない教材」を名指しし、段を走らせ直すと候補が出ることを砂場で見ること。

## 第 5 周の再演（第 25 周・面 A 第 5 周、2026-10-27）

第 5 周の再演: 消滅（段の再実行の後、候補が 10 件出た。空状態の事実文そのものは候補があるため未評価）。run: uxsim/runs/c-astro-r25-share-r5/20261027T071135Z-43804 seq2
