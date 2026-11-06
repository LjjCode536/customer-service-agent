from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.db.database import session_scope
from app.db.models import Ticket
from app.main import app


def _ticket(ticket_id, priority, due_at, status='open'):
    return Ticket(
        id=ticket_id,
        tenant_id='TENANT-DEMO',
        user_id='U1001',
        order_id='O10001',
        category='logistics_abnormal',
        priority=priority,
        status=status,
        description='sla dashboard fixture',
        assigned_group='logistics_support',
        sla_due_at=due_at.isoformat(timespec='seconds'),
        source='agent',
    )


def test_dashboard_reports_sla_risk_for_open_tickets(seeded_db):
    now = datetime.now()
    with session_scope() as session:
        # Keep this test independent of the demo tickets created by seed().
        session.execute(delete(Ticket))
        session.add_all(
            [
                _ticket('ticket_breached', 'P1', now - timedelta(minutes=5)),
                _ticket('ticket_at_risk', 'P2', now + timedelta(minutes=20)),
                _ticket('ticket_on_track', 'P3', now + timedelta(minutes=300)),
                # Resolved tickets are not operational risk.
                _ticket(
                    'ticket_resolved',
                    'P0',
                    now - timedelta(hours=1),
                    status='resolved',
                ),
            ]
        )

    body = TestClient(app).get('/api/v1/dashboard').json()

    risk = body['sla_risk']
    assert risk['window_minutes'] == 60
    assert risk['breached'] == 1
    assert risk['at_risk'] == 1
    assert risk['on_track'] == 1

    by_id = {item['id']: item for item in risk['tickets']}
    assert by_id['ticket_breached']['state'] == 'breached'
    assert by_id['ticket_breached']['remaining_minutes'] < 0
    assert by_id['ticket_at_risk']['state'] == 'at_risk'
    assert 0 <= by_id['ticket_at_risk']['remaining_minutes'] <= 60
    assert by_id['ticket_on_track']['state'] == 'on_track'
    assert 'ticket_resolved' not in by_id

    # Breached tickets surface first so the dashboard highlights the worst cases.
    assert [item['id'] for item in risk['tickets']][:2] == [
        'ticket_breached',
        'ticket_at_risk',
    ]


def test_dashboard_sla_risk_ignores_unparseable_due_dates(seeded_db):
    row = _ticket('ticket_no_due', 'P2', datetime.now())
    row.sla_due_at = 'not-a-date'
    with session_scope() as session:
        session.execute(delete(Ticket))
        session.add(row)

    risk = TestClient(app).get('/api/v1/dashboard').json()['sla_risk']

    assert risk['breached'] == 0
    assert risk['at_risk'] == 0
    assert risk['on_track'] == 0
    assert risk['tickets'] == []
