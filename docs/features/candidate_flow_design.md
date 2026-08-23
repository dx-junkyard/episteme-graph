# candidate → confirm 共通プリミティブ（`core/candidate_flow.py`）

**状態:** 実装済み（2026-08-14）— 正本・凍結。本書が `backend/core/candidate_flow.py` の正本。
以後の変更は §7 実装記録への追記で行う。

**位置づけ:** [機能整備提案 2026-08-13](../architecture/feature_consolidation_proposals_2026-08-13.md) §2-1
（★★★）の実装。`core/revision_store.py`（draft/freeze の共通制御フロー）・`core/privacy.py`
（k-匿名の閾値正本）と同じ「制御フローだけを共有する横断基盤」の3本目。

---

## §1 目的

「非LLM prefilter → 非同期 LLM 候補 → 人間 confirm / dismiss → 状態遷移で保持・監査記帳」という
同型パイプラインが、少なくとも8系統（§4 の表）で個別に再実装されている。LLM 呼び出し側は
`core/llm_worker/` に共通化済みで、残っているのは**確定側のワークフロー**（遷移可否の判定・
却下理由の要否・actor の必須性・再解析時の supersede セマンティクス・監査記帳の順序）である。

本モジュールはその**制御フローだけ**を引き受ける。

共有するもの:

- 遷移の可否判定（許可されるのは 3 遷移だけ）
- actor 必須性（KN-3）と却下理由の要否
- 「検証 → apply → 監査」の順序と、apply 失敗時に監査へ載せないこと
- 再解析で置換してよい行の抽出（`candidate` のみ）
- 空集合入力で書き込み callable を一度も呼ばないこと（SQL 非発行の慣行）

**共有しないもの**（意図的にドメイン側へ残す）:

- status 語彙そのもの（`confirmed` / `accepted` / `committed` / `teacher_approved` …）
- 粒度（1行 / JSONB 配列の1要素 / payload の1キー）
- トリガ（worker の起動条件・冪等マーカー・コスト上限）
- SQL・テーブル・DTO キー名・HTTP ステータスコードへの写像
- 監査 entity_type / action 語彙の選択（`core/schema.py` の `AUDIT_ENTITY_*` を使うのは
  ドメイン側の責務。本モジュールはカタログを参照しない）
- k-匿名集約（`core/privacy.py`）・段階ラベル変換（提案 §2-2）

## §2 不変条項

| 条項 | 実装での担保 |
|---|---|
| **P4 情報を落とさない** | 行削除に相当する API を持たない（`delete` / `purge` / `remove` を名前に含むメソッドが無いことをテストで固定）。却下は `dismissed` への遷移で保持し、`restore` で候補へ戻せる |
| **KN-3 確定は人間** | `confirm` / `dismiss` / `restore` は `actor_id` 必須（空文字・空白のみ・None・非文字列は `CandidateTransitionError`）。LLM / worker が人間確定状態へ遷移させる経路が無い |
| **監査必須** | 人間の3アクション（confirm / dismiss / restore）は `CandidateFlow` が apply と `record_audit` を一体で呼ぶ。監査 callable の例外は握らない。**supersede は例外**（既定は detect 側記帳に委ねる。`audit_supersede=True` で1行ずつ記帳） |
| **LS3 再生成は候補のみ supersede** | `select_supersedable` は `candidate` だけを返す。人間確定済みの `accepted` / `dismissed`、既に `superseded` の履歴行、語彙外の未知状態は返さない（fail-closed） |
| **DB へ触らない** | sqlalchemy / psycopg2 / `core.postgres` を import しない純 Python。書き込みはドメインが注入する callable |
| **FastAPI 非 import** | 開発ルール2（`core/` のテスタビリティ） |

## §3 API

### 3.1 `CandidateVocabulary`（frozen dataclass）

その系統の状態語彙の宣言。

| フィールド | 意味 |
|---|---|
| `candidate` | AI / prefilter が書ける唯一の状態 |
| `accepted` | 人間が確定した状態 |
| `dismissed` | 人間が却下した状態（行削除の代わり, P4） |
| `superseded` | 再生成で置換された履歴状態。この概念を持たない系統では `None` |

構築時の検証（違反は `CandidateFlowConfigError`）: 各語彙は非空文字列・前後空白なし・
互いに重複しないこと。派生プロパティは `statuses` / `human_decided`（= accepted, dismissed）/
`is_candidate(status)`。

### 3.2 例外

- `CandidateFlowConfigError(ValueError)` — 語彙・構成の誤り（構築時、および `superseded`
  未定義での supersede 要求時）
- `CandidateTransitionError(ValueError)` — 許されない遷移、`actor_id` 欠落、却下理由欠落

### 3.3 `resolve_transition(current_status, action, *, vocab, actor_id, reason="", require_dismiss_reason=True) -> str`

DB に触らない純粋な遷移解決。許されるのはこの 3 遷移だけで、ほかは全て
`CandidateTransitionError`。

| アクション | 遷移 |
|---|---|
| `confirm` | `candidate` → `accepted` |
| `dismiss` | `candidate` → `dismissed`（既定で `reason` 必須） |
| `restore` | `dismissed` → `candidate` |

`accepted` からの再確定・`superseded` の復活・`candidate` の restore・未知のアクション・
語彙外の現在状態は全て拒否する。`require_dismiss_reason=False` は理由を求めない系統
（例: 学習者自身の dismiss）のための緩和。

### 3.4 `select_supersedable(rows, *, vocab, status_key="status", status_of=None) -> list`

再解析で置換してよい行（`candidate` のみ）を返す。行は Mapping（`row[status_key]`）でも
オブジェクト（`getattr`）でもよく、`status_of` で任意の取り出し方を注入できる。
`vocab.superseded` が未定義なら `CandidateFlowConfigError`。

### 3.5 `CandidateFlow`（frozen dataclass）

`vocab` + `audit_entity_type` + 注入 callable（`apply_status` / `record_audit`）を束ね、
「検証 → apply → 監査」を一本化する。

注入 callable のキーワード契約:

```
apply_status(entity_id=, old_status=, new_status=, actor_id=, reason=, metadata=)
record_audit(entity_type=, entity_id=, action=, old_status=, new_status=,
             actor_id=, reason=, metadata=)
```

メソッド:

| メソッド | 内容 |
|---|---|
| `confirm(entity_id, *, current_status, actor_id, reason="", metadata=None)` | `candidate` → `accepted` |
| `dismiss(...)` | `candidate` → `dismissed`（理由必須。`require_dismiss_reason=False` で緩和） |
| `restore(...)` | `dismissed` → `candidate` |
| `supersede_candidates(rows, *, actor_id=None, reason="", metadata=None, status_key, id_key, status_of, id_of)` | 候補のみ `superseded` へ。対象0件なら callable を**一度も呼ばない**。`actor_id` は任意（worker 実行） |

戻り値は `{"entity_id", "action", "old_status", "new_status", "applied"}`（supersede は
`{"action", "new_status", "entity_ids", "count", "applied"}`）。`metadata` は複製して渡す
（呼び出し側の dict を書き換えない）。`apply_status` が例外を投げたら監査は記帳しない
（書き込めていない遷移を監査に載せない）。supersede の監査は既定 off
（`audit_supersede=True` で1行ずつ記帳する）— 多くの系統は「候補の入れ替え」を detect 側で
記帳しているため。

トランザクション境界（commit / rollback）は呼び出し側の責務（`revision_store` と同じ）。

## §4 8系統の語彙対応表（参考・2026-08-14 時点）

**巻き取りはしない。**この表は「新系統が語彙を宣言するとき、どの既存系統に倣うか」を
選ぶための参考であり、既存8系統のコードは一切変更していない（§6 非スコープ）。

| 系統 | 格納先 | candidate | accepted | dismissed | superseded | 監査 entity_type |
|---|---|---|---|---|---|---|
| tension（B層） | `interest_traces`（kind=`tension`） | `candidate` | `open` / `articulated` / `connected` / `abstracted`（= `TENSION_OWNED_STATUSES`） | `dismissed` | `superseded`（メッセージ書き直し・削除時） | `tension` |
| structure_anchor（B層） | `interest_traces.payload.structure_anchor` | `attribution_source='llm_candidate'` | `confirmed`（明示アンカーは `learner_selected` で即確定） | `status='dismissed'` | —（行 status は `superseded`） | `structure_anchor` |
| D層 scope_candidates | `epistemic_ledger.scope_candidates` JSONB | `candidate` | 確定は候補を昇格させず `verification_scopes` へ記帳 | `dismissed` | — | `ledger` |
| D層 assumption_nodes | `assumption_nodes.status` | `candidate` | `confirmed`（さらに `operationalized`） | `dismissed` | — | `assumption` |
| W層 element_annotations | `element_annotations.status` | `candidate` | `committed` | `dismissed` | — | `deliberation` |
| C層 explanations | `component_explanations.review_status` | `teacher_review_required` | `teacher_approved` | `rejected`（`needs_revision` は差し戻し） | — | `explanation` |
| ランドスケープ placements | `landscape_placements.status` | `inferred` | `confirmed` | `rejected`（`review_required` は再検討） | `superseded` | `landscape_placement` |
| カテゴリギャップ decisions | `atlas_gap_decisions.status` | `candidate` | `accepted` | `dismissed`（`merged` は統合） | 信号側 `landscape_gap_signals.status='superseded'` | `category_gap` |

同型の系統（表外）: SL層 反証条件（`epistemic_ledger.falsification_candidates`:
`candidate` / 確定は `falsification_conditions` へ記帳 / `dismissed`）、
図スタジオ提案（`teaching_figure_suggestions`）、地図の修正報告。

**この表から読める非対称**（共通化の対象外として残す判断）:

1. **確定が「別の器への記帳」になる系統がある**（D層 scope / SL層 反証条件）。候補行の
   status は `dismissed` 相当にしか動かず、確定内容は人間の記帳列へ移る。この場合は
   `accepted` を宣言しつつ、`apply_status` の中で昇格先への書き込みまで行う。
2. **中間状態を持つ系統がある**（`needs_revision` / `review_required` / `operationalized` /
   `merged`）。本プリミティブは 3 アクションだけを扱い、中間状態への遷移はドメイン側の
   別関数に残す（`resolve_transition` は語彙外の状態を hard に拒否するので、中間状態を
   `statuses` に載せない限り本プリミティブは通らない — これは意図的な設計）。

## §5 接続例（新系統のアダプタ）

新系統は「語彙の宣言 + 2つの callable」だけ書く（15行程度）。

```python
# core/<新系統>/store.py
from core import candidate_flow
from core.schema import AUDIT_ENTITY_LANDSCAPE_PLACEMENT

VOCAB = candidate_flow.CandidateVocabulary(
    candidate="inferred", accepted="confirmed",
    dismissed="rejected", superseded="superseded",
)

def make_flow(session):
    def apply_status(*, entity_id, old_status, new_status, actor_id, reason, metadata):
        return session.execute(sa_text("UPDATE ... SET status = :s WHERE id = :i"),
                               {"s": new_status, "i": entity_id})

    def record_audit(*, entity_type, entity_id, action, old_status, new_status,
                     actor_id, reason, metadata):
        services.record_review_event(entity_type, entity_id, old_status, new_status,
                                     actor_id, {**metadata, "action": action})

    return candidate_flow.CandidateFlow(
        vocab=VOCAB, audit_entity_type=AUDIT_ENTITY_LANDSCAPE_PLACEMENT,
        apply_status=apply_status, record_audit=record_audit,
    )
```

呼び出し側（route）は例外を HTTP へ写すだけ:

```python
try:
    result = make_flow(session).confirm(pid, current_status=row["status"],
                                        actor_id=str(current_user["id"]))
except candidate_flow.CandidateTransitionError as exc:
    raise HTTPException(status_code=422, detail=str(exc))
```

再解析 worker 側:

```python
flow.supersede_candidates(existing_rows)  # 0件なら SQL を発行しない
store.insert_candidates(new_candidates)
```

## §6 非スコープ

- **既存8系統の巻き取り**（提案 §2-1 の「次の新系統から適用」方針。既存コードは1行も
  変更していない）
- **SQL 生成 / セッション管理 / トランザクション境界**（`revision_store` と同じくドメイン側）
- **LLM ワーカー基盤**（`core/llm_worker/`: client / run_with_repair / CostGate）
- **段階ラベル変換の正本化**（提案 §2-2）・**k-匿名**（`core/privacy.py` が正本）
- **レビューキューのレジストリ化**（提案 §2-8。本プリミティブとセットで効くが別提案）
- **中間状態（`needs_revision` / `review_required` / `merged` / `operationalized`）の遷移**

## §7 実装記録（2026-08-14）

- `backend/core/candidate_flow.py` 新設（純 Python・sqlalchemy / FastAPI / LLM 非 import）
- `backend/tests/test_candidate_flow.py` 新設（48 tests）
  - 遷移マトリクス全網羅（語彙4状態 × アクション3種 = 12 組。許可3遷移以外は全て
    `CandidateTransitionError`）
  - actor 空・空白・None・非文字列の拒否 / dismiss 理由の必須と緩和
  - `select_supersedable` が人間確定行・履歴行・未知状態を返さないこと
  - 空集合 no-op（Mock 未呼び出しで検証）/ apply 失敗時に監査を呼ばないこと /
    監査例外の伝播
  - `guardrail_helpers` による fastapi・sqlalchemy 非 import と `DELETE` 文字列不在の
    ソース検査、不変条項（P4 / KN-3 / 監査必須 / LS3）の docstring 明記
- CLAUDE.md「横断基盤」節・`layer_registry.md` 横断基盤行・`docs/README.md` 索引への
  追記は同日（2026-08-14）の並行作業で実施済み（提案 §2-1 の「CLAUDE.md 横断基盤ルール
  への追記が本体」に対応）。
