"""TeX アーカイブの展開量の上限（gzip 爆弾で資源を使い切らない — 2026-10-01 レビュー M3）。

``_is_readable_tex`` は URL 取得・``/ingest``・取り込み worker の同期経路で走るため、
``gzip.decompress`` を丸ごと呼ぶと数百 KB の gzip で数百 MB〜GB を確保してしまう。
ここでは RSS を測らず、**打ち切りの経路が取られること**（``gzip.decompress`` を呼ばず、
``_MAX_MEMBER_BYTES + 1`` で止まる）を固定する。外部には一切接続しない。
"""

from __future__ import annotations

import gzip
import io
import sys
import tarfile
import zlib
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
for _p in (str(BACKEND), str(BACKEND / "api"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.document_pipeline import tex_archive as ta  # noqa: E402


def _bomb(size: int, head: bytes = b"\\documentclass{article}\n") -> bytes:
    """``size`` バイトのゼロ（先頭に LaTeX の骨格）を gzip した小さなバイト列。"""
    return gzip.compress(head + b"\0" * size, compresslevel=9)


class TestSingleGzipBound:
    def test_bomb_over_the_member_limit_raises_value_error(self, monkeypatch):
        data = _bomb(ta._MAX_MEMBER_BYTES + 1024)
        assert len(data) < 64 * 1024  # 圧縮後は小さい = 爆弾の形
        # 丸ごと展開する API を使っていないこと（打ち切りの経路が取られること）。
        monkeypatch.setattr(
            ta, "gzip", None, raising=False,
        )
        with pytest.raises(ValueError):
            ta._read_single_gzip_member(data)
        with pytest.raises(ta.TexArchiveTooLargeError):
            ta._read_single_gzip_member(data)

    def test_decompression_stops_at_limit_plus_one(self, monkeypatch):
        seen = []
        real = zlib.decompressobj

        class _Spy:
            def __init__(self, *a):
                self._d = real(*a)

            def decompress(self, data, max_length=0):
                out = self._d.decompress(data, max_length)
                seen.append((max_length, len(out)))
                return out

            @property
            def eof(self):
                return self._d.eof

            @property
            def unconsumed_tail(self):
                return self._d.unconsumed_tail

        monkeypatch.setattr(ta.zlib, "decompressobj", _Spy)
        with pytest.raises(ValueError):
            ta._read_single_gzip_member(_bomb(ta._MAX_MEMBER_BYTES * 4))
        assert seen == [(ta._MAX_MEMBER_BYTES + 1, ta._MAX_MEMBER_BYTES + 1)]

    def test_load_tex_archive_rejects_the_bomb(self):
        with pytest.raises(ValueError):
            ta.load_tex_archive(_bomb(ta._MAX_MEMBER_BYTES + 1), source_file="x.gz")

    def test_small_single_file_submission_still_reads(self):
        body = b"\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n"
        members = ta._read_single_gzip_member(gzip.compress(body))
        assert members == {"main.tex": body.decode()}

    def test_truncated_gzip_is_unreadable_not_an_error(self):
        data = gzip.compress(b"\\documentclass{article}\n" * 1000)
        assert ta._read_single_gzip_member(data[: len(data) // 2]) == {}

    def test_not_gzip_is_unreadable(self):
        assert ta._read_single_gzip_member(b"%PDF-1.7 not gzip") == {}

    def test_readable_check_falls_back_on_the_bomb(self):
        import source_resolution as sr
        from core import url_fetch

        fetched = url_fetch.FetchedSource(
            content=_bomb(ta._MAX_MEMBER_BYTES + 1), source_kind="tex_archive",
            filename="a.tar.gz",
        )
        assert sr._is_readable_tex(fetched) is False


class TestTarBounds:
    def _tar(self, members: list[tuple[str, bytes]]) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name, body in members:
                info = tarfile.TarInfo(name)
                info.size = len(body)
                tar.addfile(info, io.BytesIO(body))
        return buf.getvalue()

    def test_member_read_is_bounded(self):
        """巨大な .tex member は ``read(limit + 1)`` で打ち切られて読まれない。"""
        data = self._tar([
            ("big.tex", b"\\documentclass{article}" + b"\0" * (ta._MAX_MEMBER_BYTES + 10)),
            ("main.tex", b"\\documentclass{article}\n\\begin{document}x\\end{document}"),
        ])
        tex, _bib = ta._read_archive_members(data)
        assert list(tex) == ["main.tex"]

    def test_too_many_members_is_rejected(self, monkeypatch):
        monkeypatch.setattr(ta, "_MAX_ARCHIVE_MEMBERS", 3)
        data = self._tar([(f"f{i}.txt", b"x") for i in range(5)])
        with pytest.raises(ta.TexArchiveTooLargeError):
            ta._read_archive_members(data)

    def test_scan_total_is_bounded(self, monkeypatch):
        monkeypatch.setattr(ta, "_MAX_TAR_SCAN_BYTES", 4096)
        data = self._tar([("pad.bin", b"\0" * 8192), ("main.tex", b"\\documentclass{a}")])
        with pytest.raises(ta.TexArchiveTooLargeError):
            ta._read_archive_members(data)

    def test_source_does_not_call_unbounded_gzip_decompress(self):
        src = Path(ta.__file__).read_text(encoding="utf-8")
        code = "\n".join(
            line for line in src.splitlines()
            if not line.lstrip().startswith("#") and "``gzip.decompress``" not in line
        )
        assert "gzip.decompress(" not in code
        assert ".getmembers()" not in code
