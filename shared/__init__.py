"""Shared infrastructure for Conductor services.

DB engine/session, ORM models (single source of truth for table schemas),
EventBus, outbox relay, and environment config — used by ALL services.
"""
