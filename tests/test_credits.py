"""Smoke test for the credit-ledger regen math (no DB needed — pure logic check).

For full tests, spin up the app with an SQLite/Postgres test DB and use the
real service functions. This illustrates the lazy-regen formula.
"""
from datetime import datetime, timedelta, timezone


def lazy_regen(balance, last_event, max_credits, interval_hours, now=None):
    now = now or datetime.now(timezone.utc)
    if balance >= max_credits:
        return balance
    earned = int((now - last_event) // timedelta(hours=interval_hours))
    return min(max_credits, balance + max(earned, 0))


def test_no_regen_before_interval():
    last = datetime.now(timezone.utc) - timedelta(hours=10)
    assert lazy_regen(0, last, 3, 48) == 0


def test_one_credit_after_one_interval():
    last = datetime.now(timezone.utc) - timedelta(hours=49)
    assert lazy_regen(0, last, 3, 48) == 1


def test_capped_at_max():
    last = datetime.now(timezone.utc) - timedelta(hours=48 * 10)
    assert lazy_regen(0, last, 3, 48) == 3


def test_full_balance_unchanged():
    last = datetime.now(timezone.utc) - timedelta(hours=200)
    assert lazy_regen(3, last, 3, 48) == 3
