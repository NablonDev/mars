"""A batch that enqueues nothing because every order is DELIVERED looks
identical in the logs to a broken batch. These pin the diagnostic that tells
the two apart.
"""

from app.repositories.order import describe_no_open_orders


def test_returns_none_when_orders_are_open():
    assert describe_no_open_orders({"OPEN": 2, "DELIVERED": 1}) is None


def test_returns_none_when_there_are_no_orders_at_all():
    """An empty table is a different problem (nothing seeded), and the caller
    should not be told to reopen orders that don't exist."""
    assert describe_no_open_orders({}) is None


def test_explains_when_orders_exist_but_none_are_open():
    note = describe_no_open_orders({"DELIVERED": 4})

    assert note is not None
    assert "4 order(s) exist" in note
    assert "DELIVERED=4" in note


def test_breakdown_lists_every_status_sorted():
    note = describe_no_open_orders({"DELIVERED": 3, "CANCELLED": 1})

    assert note is not None
    assert "CANCELLED=1, DELIVERED=3" in note
    assert "4 order(s) exist" in note


def test_zero_open_count_is_treated_as_no_open_orders():
    """`count_by_status` omits absent statuses, but an explicit OPEN=0 must
    not be read as truthy-by-presence."""
    note = describe_no_open_orders({"OPEN": 0, "DELIVERED": 2})

    assert note is not None
    assert "DELIVERED=2" in note
