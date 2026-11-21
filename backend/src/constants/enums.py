from enum import StrEnum

class Role(StrEnum):
    ADMIN = 'admin'
    ANALYST = 'analyst'
    VIEWER = 'viewer'

class Status(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETE = 'complete'
    FAILED = 'failed'

class Review(StrEnum):
    PENDING = 'pending'
    ACCEPTED = 'accepted'
    RETURNED = 'returned'

class ChangeAction(StrEnum):
    ADD = 'add'
    CORRECT = 'correct'
    REVOKE = 'revoke'

class OrderState(StrEnum):
    HELD = 'held'
    READY = 'ready'
    APPLIED = 'applied'
    VOID = 'void'

class CycleState(StrEnum):
    COLLECTING = 'collecting'
    SWITCHING = 'switching'
    PUBLISHED = 'published'
    FAILED = 'failed'
