from datetime import datetime, timedelta

import pytest

from app.services.rules import sla_due, ticket_assignment, ticket_priority


@pytest.mark.parametrize(
    ('category', 'vip_level', 'complaint', 'stale_hours', 'expected'),
    [
        ('complaint', 'vip', True, 0, 'P1'),
        ('complaint', 'normal', True, 0, 'P3'),
        ('logistics_abnormal', 'normal', False, 24, 'P2'),
        ('logistics_abnormal', 'normal', False, 23, 'P3'),
        ('refund_failed', 'normal', False, 0, 'P2'),
        ('payment_issue', 'vip', False, 0, 'P2'),
        ('refund_review', 'normal', False, 0, 'P3'),
    ],
)
def test_ticket_priority_table(category, vip_level, complaint, stale_hours, expected):
    assert ticket_priority(category, vip_level, complaint, stale_hours) == expected


@pytest.mark.parametrize(
    ('priority', 'minutes'),
    [('P0', 15), ('P1', 30), ('P2', 120), ('P3', 480)],
)
def test_sla_due_table(priority, minutes):
    now = datetime(2026, 11, 6, 10, 0, 0)
    assert sla_due(priority, now=now) == (now + timedelta(minutes=minutes)).isoformat(
        timespec='seconds'
    )


@pytest.mark.parametrize(
    ('category', 'expected'),
    [
        ('logistics_abnormal', 'logistics_support'),
        ('refund_review', 'after_sales'),
        ('refund_duplicate', 'after_sales'),
        ('complaint', 'complaint_specialist'),
        ('unknown_category', 'customer_service'),
    ],
)
def test_ticket_assignment_table(category, expected):
    assert ticket_assignment(category) == expected
