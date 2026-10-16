"""The packaged 70-query list must be byte-for-byte the Google Sheet content (QRY-02, QRY-03)."""

from __future__ import annotations

import hashlib
from importlib import resources

import pytest

PINNED_SHA256 = "2cf16309128e916d3e280b3faf90034277e29467e75a99adfaff249628ecda5e"

SPECIAL_LINES = {
    13: "постельное бельё",
    14: "пастельный комплект 2-спальный",
    19: "постельное белье евро вареный хлопок dr. hygge",
    21: "постельное белье 2 спальное хлопок 100%",
    33: "постельное белье 160х200 на резинке",
    37: "постельное белье евро на резинке 200*220",
    53: "пастельный комплект 1,5",
    56: "постельное белье 2 спальное бязь 70х70",
    57: "постельное белье на резинке 160х200",
    66: "постельное белье 2 спальное на резинке 160x200",
    67: "постельное бельё евро",
    70: "постельное белье 1,5",
}


def _raw() -> bytes:
    return resources.files("wbmon").joinpath("data/queries_bundled.txt").read_bytes()


def _lines() -> list[str]:
    text = _raw().decode("utf-8")
    assert text.endswith("\n")
    return text[:-1].split("\n")


def test_file_is_pinned_byte_for_byte():
    raw = _raw()
    assert len(raw) == 3984
    assert hashlib.sha256(raw).hexdigest() == PINNED_SHA256


def test_file_shape():
    raw = _raw()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r" not in raw
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    lines = _lines()
    assert len(lines) == 70
    assert len(set(lines)) == 70
    assert all(line for line in lines)


@pytest.mark.parametrize(("number", "expected"), sorted(SPECIAL_LINES.items()))
def test_special_lines_are_exact(number, expected):
    assert _lines()[number - 1] == expected


def test_cyrillic_and_latin_x_are_kept_distinct():
    lines = _lines()
    for number in (33, 56, 57):
        assert "х" in lines[number - 1]
        assert "x" not in lines[number - 1]
    assert "x" in lines[65] and "х" not in lines[65]
