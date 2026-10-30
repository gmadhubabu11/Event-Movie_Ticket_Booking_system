from decimal import Decimal
from app.services.pricing import calc_bill, refund_percent, dynamic_price
from app.services.seat_rules import find_gap_violations
from app.services.ticket_service import qr_value, verify_qr


def test_bill():
    b = calc_bill([200, 200, 300])
    assert b["ticket_total"] == Decimal("700.00")
    assert b["convenience_fee"] == Decimal("90.00")
    assert b["gst_amount"] == Decimal("142.20")       # 18% of 790
    assert b["grand_total"] == Decimal("932.20")


def test_refund_policy():
    assert refund_percent(25 * 3600) == 100
    assert refund_percent(24 * 3600) == 50
    assert refund_percent(2 * 3600) == 50
    assert refund_percent(2 * 3600 - 1) is None


def test_dynamic_price():
    assert dynamic_price(100, 81, 100) == Decimal("110.00")
    assert dynamic_price(100, 80, 100) == Decimal("100.00")


def test_single_gap_rule():
    rows = {"A": [(i, i) for i in range(1, 9)]}
    # I take 1,2 ; seat 4 is taken -> seat 3 would be a lonely gap
    assert find_gap_violations(rows, mine={1, 2}, taken={4}) == [3]
    # I take 1,2,3 ; seat 4 taken -> fine
    assert find_gap_violations(rows, mine={1, 2, 3}, taken={4}) == []
    # gap next to the wall is fine
    assert find_gap_violations(rows, mine={2}, taken=set()) == []


def test_qr_signature():
    v = qr_value("BKG-20260101-0001")
    assert verify_qr(v) == "BKG-20260101-0001"
    assert verify_qr("BKG-20260101-0001|deadbeef") is None
    assert verify_qr("BKG-20260101-0002|" + v.split("|")[1]) is None
