from typing import Dict, List, Set, Tuple


def find_gap_violations(rows: Dict[str, List[Tuple[int, int]]], mine: Set[int], taken: Set[int]) -> List[int]:
    """Single-empty-seat rule.

    rows  : row_label -> [(seat_id, seat_number)] sorted by seat_number
    mine  : seats selected/held/booked by this customer
    taken : seats taken by anyone else (booked, locked by others, inactive)
    Returns ids of free seats that would be left alone between one of my seats and a taken seat.
    """
    bad = []
    for seats in rows.values():
        ids = [sid for sid, _ in seats]
        for i in range(1, len(ids) - 1):
            sid = ids[i]
            if sid in mine or sid in taken:
                continue
            left, right = ids[i - 1], ids[i + 1]
            if (left in mine and right in taken) or (left in taken and right in mine):
                bad.append(sid)
    return bad
