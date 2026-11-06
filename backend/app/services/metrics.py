"""Operational metrics derived from persisted business rows."""

from datetime import datetime

SLA_AT_RISK_MINUTES = 60
SLA_RISK_LIST_LIMIT = 10

_STATE_ORDER = {'breached': 0, 'at_risk': 1, 'on_track': 2}


def _parse_due(value):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def sla_risk(tickets, now=None, window_minutes=SLA_AT_RISK_MINUTES):
    """Classify open tickets into breached / at-risk / on-track.

    Unparseable or missing SLA due dates are ignored rather than counted as safe.
    """
    now = now or datetime.now()
    items = []
    for ticket in tickets:
        due = _parse_due(ticket.get('sla_due_at'))
        if due is None:
            continue
        exact = (due - now).total_seconds() / 60
        if exact < 0:
            state = 'breached'
        elif exact <= window_minutes:
            state = 'at_risk'
        else:
            state = 'on_track'
        remaining = round(exact)
        # Avoid showing "0 minutes overdue" for a ticket that just crossed the line.
        if state == 'breached':
            remaining = min(-1, remaining)
        elif state == 'at_risk':
            remaining = max(1, remaining)
        items.append(
            {
                'id': ticket['id'],
                'priority': ticket.get('priority'),
                'category': ticket.get('category'),
                'assigned_group': ticket.get('assigned_group'),
                'sla_due_at': ticket.get('sla_due_at'),
                'remaining_minutes': remaining,
                'state': state,
            }
        )

    items.sort(key=lambda item: (_STATE_ORDER[item['state']], item['remaining_minutes']))
    counts = {state: 0 for state in _STATE_ORDER}
    for item in items:
        counts[item['state']] += 1

    return {
        'window_minutes': window_minutes,
        'breached': counts['breached'],
        'at_risk': counts['at_risk'],
        'on_track': counts['on_track'],
        'tickets': items[:SLA_RISK_LIST_LIMIT],
    }
