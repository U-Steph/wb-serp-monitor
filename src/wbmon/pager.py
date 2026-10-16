"""Top-N pagination state machine. Pure: no I/O, no HTTP.

The caller fetches pages and feeds each raw ``products`` list to :meth:`Pager.add_page`; the pager
numbers positions, drops duplicates, and decides when to stop. Positions are the 1-based slot index
in the concatenated raw arrays (ads included). A dropped duplicate or an item without an id leaves
a gap rather than shifting later positions, so stored positions equal what the site displayed.
A short page is never an end signal.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from wbmon.normalize import promo_position, to_int

WRAP_FINGERPRINT_LEN = 10
MIN_FINGERPRINT_IDS = 3
STOP_REASONS = ("top_n", "empty", "wrap", "wrap_subset", "cap")
_COMPLETE_REASONS = frozenset({"top_n", "empty", "wrap", "wrap_subset"})


@dataclass(frozen=True, slots=True)
class Slot:
    """One accepted product: its SERP position and where it came from."""

    position: int
    page: int
    page_index: int
    nm_id: int
    raw: Mapping
    received_at: str


def _item_id(item: object) -> int | None:
    """Return the integer ``id`` of a raw product, or ``None``."""
    return to_int(item.get("id")) if isinstance(item, dict) else None


class Pager:
    """Accumulate pages of one query until the top-N, the end of results or the page cap."""

    def __init__(self, top_n: int) -> None:
        if top_n < 1:
            raise ValueError("top_n must be at least 1")
        self._top_n = top_n
        self._page_size = 0
        self._fingerprint: tuple[int, ...] = ()
        self._seen: set[int] = set()
        self._slot_counter = 0
        self._slots: list[Slot] = []
        self.done = False
        self.stop_reason: str | None = None
        self.pages_seen = 0
        self.duplicates_dropped = 0
        self.skipped_no_id = 0
        self.ad_slot_mismatch = 0

    @property
    def slots(self) -> tuple[Slot, ...]:
        """Accepted products in SERP order."""
        return tuple(self._slots)

    @property
    def next_page(self) -> int | None:
        """The page number to fetch next, or ``None`` once the pager is done."""
        return None if self.done else self.pages_seen + 1

    @property
    def is_complete(self) -> bool:
        """True when the results ended naturally or ``top_n`` slots were covered."""
        return self.done and self.stop_reason in _COMPLETE_REASONS

    def _stop(self, reason: str) -> None:
        self.done = True
        self.stop_reason = reason

    def _fingerprint_of(self, products: Sequence[object]) -> tuple[int, ...]:
        ids = (_item_id(item) for item in products)
        return tuple(i for i in ids if i is not None)[:WRAP_FINGERPRINT_LEN]

    def _is_wrap(self, products: Sequence[object]) -> str | None:
        """Return ``wrap``/``wrap_subset`` when the page repeats earlier results, else ``None``."""
        fingerprint = self._fingerprint_of(products)
        if len(fingerprint) >= MIN_FINGERPRINT_IDS and fingerprint == self._fingerprint:
            return "wrap"
        page_ids = [i for i in (_item_id(item) for item in products) if i is not None]
        if page_ids and all(i in self._seen for i in page_ids):
            return "wrap_subset"
        return None

    def _note_ad_mismatch(self, item: object, slot: int, page_index: int) -> None:
        promo = promo_position(item)
        if promo is not None and promo != slot and promo != page_index:
            self.ad_slot_mismatch += 1

    def add_page(self, products: Sequence[object], *, received_at: str) -> None:
        """Consume the raw ``products`` list of the next page."""
        if self.done:
            raise ValueError("pager is already done")
        self.pages_seen += 1
        page = self.pages_seen
        if not products:
            self._stop("empty")
            return
        if page == 1:
            self._page_size = len(products)
            self._fingerprint = self._fingerprint_of(products)
        else:
            wrapped = self._is_wrap(products)
            if wrapped is not None:
                self._stop(wrapped)
                return
        for page_index, item in enumerate(products, start=1):
            self._slot_counter += 1
            slot = self._slot_counter
            if slot > self._top_n:
                self._stop("top_n")
                return
            nm_id = _item_id(item)
            if nm_id is None:
                self.skipped_no_id += 1
                continue
            if nm_id in self._seen:
                self.duplicates_dropped += 1
                continue
            self._seen.add(nm_id)
            self._note_ad_mismatch(item, slot, page_index)
            self._slots.append(
                Slot(
                    position=slot,
                    page=page,
                    page_index=page_index,
                    nm_id=nm_id,
                    raw=item,
                    received_at=received_at,
                )
            )
        if self._slot_counter >= self._top_n:
            self._stop("top_n")
        elif page >= math.ceil(self._top_n / self._page_size) + 1:
            self._stop("cap")
