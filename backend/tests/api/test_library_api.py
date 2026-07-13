"""routes/library.py（ナレッジライブラリ L層 API, `/api/admin/library/...`）の
Pydantic リクエストモデル検証テスト。

DB / MinIO への実接続は行わない（``core.library.store`` / ``search`` はモジュール
import のみで、実際の DB クエリは発行しない）。楽観ロック用 ``expected_revision``
必須化など、リクエストボディのバリデーションに閉じたテスト。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HAS_FASTAPI = True
try:
    from fastapi.testclient import TestClient  # noqa: F401
except ImportError:
    _HAS_FASTAPI = False

_skip_no_fastapi = pytest.mark.skipif(
    not _HAS_FASTAPI, reason="FastAPI not installed (run inside Docker for full API tests)"
)


@pytest.fixture
def library_routes():
    if not _HAS_FASTAPI:
        pytest.skip("FastAPI not installed")
    backend_dir = str(Path(__file__).resolve().parents[2])
    api_dir = str(Path(__file__).resolve().parents[2] / "api")
    for p in (backend_dir, api_dir):
        if p not in sys.path:
            sys.path.insert(0, p)
    import routes.library as mod

    return mod


@_skip_no_fastapi
class TestExemplarImageIn:
    def test_requires_all_three_fields(self, library_routes):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            library_routes.ExemplarImageIn(figure_id="f1", minio_key="k1")

    def test_accepts_valid_payload(self, library_routes):
        img = library_routes.ExemplarImageIn(figure_id="f1", minio_key="k1", source_document_id="d1")
        assert img.figure_id == "f1"
        assert img.minio_key == "k1"
        assert img.source_document_id == "d1"


@_skip_no_fastapi
class TestLibraryEntryCreateRequest:
    def test_requires_domain_key_entry_type_and_name(self, library_routes):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            library_routes.LibraryEntryCreateRequest(entry_type="apparatus", name="n")
        with pytest.raises(ValidationError):
            library_routes.LibraryEntryCreateRequest(domain_key="d", name="n")
        with pytest.raises(ValidationError):
            library_routes.LibraryEntryCreateRequest(domain_key="d", entry_type="apparatus")

    def test_defaults_for_optional_fields(self, library_routes):
        req = library_routes.LibraryEntryCreateRequest(domain_key="d", entry_type="apparatus", name="n")
        assert req.aliases == []
        assert req.summary == ""
        assert req.body == {}
        assert req.source_component_ids == []
        assert req.source_document_ids == []
        assert req.exemplar_images == []

    def test_accepts_full_payload_with_exemplar_images(self, library_routes):
        req = library_routes.LibraryEntryCreateRequest(
            domain_key="particle_physics", entry_type="apparatus", name="Vacuum chamber",
            aliases=["chamber"], summary="A sealed chamber.",
            body={"typical_parts": ["flange"]},
            source_component_ids=["c1"], source_document_ids=["d1"],
            exemplar_images=[{"figure_id": "f1", "minio_key": "k1", "source_document_id": "d1"}],
        )
        assert req.aliases == ["chamber"]
        assert len(req.exemplar_images) == 1
        assert req.exemplar_images[0].figure_id == "f1"


@_skip_no_fastapi
class TestLibraryEntryUpdateRequest:
    """draft 編集（§6-3）: expected_revision は楽観ロックのため必須。"""

    def test_expected_revision_is_required(self, library_routes):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            library_routes.LibraryEntryUpdateRequest(name="new name")

    def test_expected_revision_alone_is_valid_no_op_shape(self, library_routes):
        # モデル自体はバリデーションを通す（「更新するフィールドが無い」判定は
        # ルート側の 422 で行う — モデルはここでは通す）。
        req = library_routes.LibraryEntryUpdateRequest(expected_revision=3)
        assert req.expected_revision == 3
        assert req.name is None
        assert req.exemplar_images is None

    def test_partial_update_fields_are_optional(self, library_routes):
        req = library_routes.LibraryEntryUpdateRequest(expected_revision=1, summary="updated")
        assert req.summary == "updated"
        assert req.name is None
        assert req.body is None

    def test_expected_revision_must_be_int(self, library_routes):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            library_routes.LibraryEntryUpdateRequest(expected_revision="not-an-int")


@_skip_no_fastapi
class TestFreezeRequest:
    def test_note_defaults_to_empty_string(self, library_routes):
        assert library_routes.FreezeRequest().note == ""

    def test_note_can_be_set(self, library_routes):
        assert library_routes.FreezeRequest(note="v2 corrections").note == "v2 corrections"


@_skip_no_fastapi
class TestSimilarEntriesRequest:
    def test_requires_domain_key_and_text(self, library_routes):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            library_routes.SimilarEntriesRequest(text="vacuum chamber")
        with pytest.raises(ValidationError):
            library_routes.SimilarEntriesRequest(domain_key="d")

    def test_defaults(self, library_routes):
        req = library_routes.SimilarEntriesRequest(domain_key="d", text="vacuum chamber")
        assert req.entry_type is None
        assert req.top_k == 5
