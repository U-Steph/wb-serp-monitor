"""Pager state machine: stop rules, position numbering, dedupe gaps, ad slot comparison."""

from __future__ import annotations

import pytest

from factories import PURE_FORBIDDEN_IMPORTS, SRC_DIR, make_product, make_products, module_imports
from wbmon.pager import MIN_FINGERPRINT_IDS, STOP_REASONS, WRAP_FINGERPRINT_LEN, Pager, Slot

T1, T2, T3 = "2026-10-16T07:00:01Z", "2026-10-16T07:00:03Z", "2026-10-16T07:00:05Z"
BASE = 900000000


def _feed(pager: Pager, *pages: list) -> None:
    """Feed pages in order (stamped T1, T2, then T3) until the pager is done."""
    for index, page in enumerate(pages):
        if pager.done:
            break
        pager.add_page(page, received_at=(T1, T2, T3)[min(index, 2)])


def _page(first: int, n: int, **kw) -> list[dict]:
    return make_products(BASE + first, n, **kw)


def _positions(pager: Pager) -> list[int]:
    return [slot.position for slot in pager.slots]


def test_constants():
    assert (WRAP_FINGERPRINT_LEN, MIN_FINGERPRINT_IDS) == (10, 3)
    assert STOP_REASONS == ("top_n", "empty", "wrap", "wrap_subset", "cap")


def test_two_full_pages_reach_top_n():
    pager = Pager(200)
    _feed(pager, _page(1, 100), _page(101, 100))
    assert _positions(pager) == list(range(1, 201))
    assert (pager.pages_seen, pager.stop_reason) == (2, "top_n")
    assert pager.is_complete and pager.done and pager.next_page is None
    assert [s.nm_id for s in pager.slots] == [BASE + i for i in range(1, 201)]


def test_next_page_before_done():
    pager = Pager(200)
    assert pager.next_page == 1 and not pager.done and not pager.is_complete
    pager.add_page(_page(1, 100), received_at=T1)
    assert pager.next_page == 2 and not pager.done


def test_page_two_identical_to_page_one_is_a_wrap():
    pager = Pager(200)
    page = _page(1, 100)
    _feed(pager, page, page)
    assert len(pager.slots) == 100
    assert (pager.stop_reason, pager.pages_seen, pager.is_complete) == ("wrap", 2, True)


def test_empty_second_page_ends_results():
    pager = Pager(200)
    _feed(pager, _page(1, 100), [])
    assert len(pager.slots) == 100
    assert (pager.stop_reason, pager.pages_seen, pager.is_complete) == ("empty", 2, True)


def test_empty_first_page_is_complete_with_no_slots():
    pager = Pager(200)
    pager.add_page([], received_at=T1)
    assert (pager.stop_reason, pager.slots, pager.is_complete) == ("empty", (), True)


def test_sixty_then_empty():
    pager = Pager(200)
    _feed(pager, _page(1, 60), [])
    assert len(pager.slots) == 60
    assert (pager.stop_reason, pager.is_complete) == ("empty", True)


def test_sixty_then_the_same_sixty_wraps():
    pager = Pager(200)
    page = _page(1, 60)
    _feed(pager, page, page)
    assert len(pager.slots) == 60
    assert (pager.stop_reason, pager.is_complete) == ("wrap", True)


def test_page_size_48_needs_five_pages_and_stays_under_the_cap():
    pager = Pager(200)
    pages = [_page(1 + 48 * i, 48) for i in range(6)]
    _feed(pager, *pages)
    assert len(pager.slots) == 200
    assert (pager.pages_seen, pager.stop_reason, pager.is_complete) == (5, "top_n", True)


def test_duplicate_id_leaves_a_gap_and_keeps_later_positions():
    ids = [BASE + 101 + i for i in range(100)]
    ids[4] = BASE + 7  # page-2 index 5 repeats an id from page 1
    pager = Pager(200)
    _feed(pager, _page(1, 100), make_products(0, 100, ids=ids))
    assert len(pager.slots) == 199
    assert 105 not in _positions(pager)
    assert max(_positions(pager)) == 200
    assert pager.duplicates_dropped == 1
    assert (pager.stop_reason, pager.is_complete) == ("top_n", True)


def test_short_page_two_does_not_end_the_query():
    pager = Pager(200)
    _feed(pager, _page(1, 100), _page(101, 97))
    assert not pager.done and pager.next_page == 3 and pager.pages_seen == 2
    pager.add_page(_page(198, 100), received_at=T3)
    assert len(pager.slots) == 200
    assert (pager.pages_seen, pager.stop_reason, pager.is_complete) == (3, "top_n", True)
    assert _positions(pager) == list(range(1, 201))


def test_page_two_reversed_is_a_subset_wrap():
    pager = Pager(200)
    page = _page(1, 100)
    _feed(pager, page, list(reversed(page)))
    assert len(pager.slots) == 100
    assert (pager.stop_reason, pager.is_complete) == ("wrap_subset", True)


def test_two_item_page_repeated_needs_the_subset_rule():
    pager = Pager(200)
    page = _page(1, 2)
    _feed(pager, page, page)
    assert len(pager.slots) == 2
    assert pager.stop_reason == "wrap_subset"


def test_page_cap_stops_pagination_and_is_not_complete():
    pager = Pager(200)
    _feed(pager, _page(1, 100), _page(101, 30), _page(131, 30))
    assert (pager.pages_seen, pager.stop_reason) == (3, "cap")
    assert len(pager.slots) == 160
    assert pager.done and not pager.is_complete and pager.next_page is None


def test_unfinished_pager_is_not_complete():
    pager = Pager(200)
    pager.add_page(_page(1, 100), received_at=T1)
    assert not pager.done and not pager.is_complete and pager.stop_reason is None


def test_item_without_integer_id_consumes_its_slot():
    page = _page(1, 100)
    del page[2]["id"]
    pager = Pager(200)
    pager.add_page(page, received_at=T1)
    assert pager.skipped_no_id == 1
    assert 3 not in _positions(pager)
    assert pager.slots[2].position == 4 and pager.slots[2].nm_id == BASE + 4


def test_non_dict_items_are_skipped_like_missing_ids():
    page = [*_page(1, 2), "junk", None, *_page(5, 1)]
    pager = Pager(200)
    pager.add_page(page, received_at=T1)
    assert pager.skipped_no_id == 2
    assert _positions(pager) == [1, 2, 5]


def test_top_n_smaller_than_page_truncates():
    pager = Pager(5)
    pager.add_page(_page(1, 10), received_at=T1)
    assert _positions(pager) == [1, 2, 3, 4, 5]
    assert (pager.pages_seen, pager.stop_reason, pager.is_complete) == (1, "top_n", True)


def test_page_with_exactly_top_n_items_stops():
    pager = Pager(5)
    pager.add_page(_page(1, 5), received_at=T1)
    assert (pager.stop_reason, pager.next_page) == ("top_n", None)


@pytest.mark.parametrize(
    ("page", "promo", "mismatches"),
    [
        (1, 2, 0),
        (1, 5, 1),
        (2, 5, 0),  # page-relative numbering at global slot 105
        (2, 105, 0),  # global numbering
        (2, 9, 1),
    ],
)
def test_ad_slot_mismatch(page, promo, mismatches):
    pager = Pager(200)
    if page == 1:
        pager.add_page(_page(1, 100, ads={2: promo}), received_at=T1)
    else:
        _feed(pager, _page(1, 100), _page(101, 100, ads={5: promo}))
    assert pager.ad_slot_mismatch == mismatches


def test_ads_are_numbered_like_organic_items():
    pager = Pager(200)
    pager.add_page(_page(1, 5, ads={2: 2}), received_at=T1)
    assert _positions(pager) == [1, 2, 3, 4, 5]
    assert pager.slots[1].raw["log"]["promoPosition"] == 2


def test_each_slot_carries_its_own_page_metadata():
    pager = Pager(200)
    _feed(pager, _page(1, 100), _page(101, 100))
    first, last = pager.slots[0], pager.slots[-1]
    assert (first.page, first.page_index, first.received_at) == (1, 1, T1)
    assert (last.page, last.page_index, last.received_at) == (2, 100, T2)
    assert isinstance(first, Slot)
    assert pager.slots[104].page_index == 5 and pager.slots[104].position == 105


def test_slot_raw_is_the_original_item():
    product = make_product(BASE + 1)
    pager = Pager(10)
    pager.add_page([product], received_at=T1)
    assert pager.slots[0].raw is product


def test_invalid_top_n():
    with pytest.raises(ValueError):
        Pager(0)
    with pytest.raises(ValueError):
        Pager(-3)


def test_add_page_after_done_raises():
    pager = Pager(5)
    pager.add_page(_page(1, 5), received_at=T1)
    with pytest.raises(ValueError):
        pager.add_page(_page(6, 5), received_at=T2)


def test_pager_module_is_pure():
    assert not module_imports(SRC_DIR / "pager.py") & PURE_FORBIDDEN_IMPORTS
