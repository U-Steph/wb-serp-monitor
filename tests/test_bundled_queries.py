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


def test_crlf_checkout_yields_the_same_70_queries():
    """IN-04: a core.autocrlf checkout must not put a trailing CR on every query."""
    from wbmon.queries import load_bundled, split_bundled

    crlf_text = _raw().decode("utf-8").replace("\n", "\r\n")
    assert "\r\n" in crlf_text

    assert split_bundled(crlf_text) == load_bundled()
    assert len(load_bundled()) == 70
    assert not any("\r" in query for query in load_bundled())


def test_split_bundled_only_removes_line_terminators():
    from wbmon.queries import split_bundled

    assert split_bundled("a b \nc\r\n") == ("a b ", "c")
    assert split_bundled("x\r\r\n") == ("x\r",)  # a lone inner CR is not ours to touch


def test_gitattributes_pins_lf_for_the_data_file():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / ".gitattributes").read_text(encoding="utf-8")
    assert "src/wbmon/data/*.txt text eol=lf" in text
