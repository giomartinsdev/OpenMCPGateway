"""Database schema, expressed once as SQLAlchemy metadata.

Everything in this module is a declaration, not a query: the event store and the
projections both build on these tables, so a column can only be wrong in one
place. No string SQL appears here either — descending indexes and partial
indexes use column expressions, which SQLAlchemy renders for the dialect.

`JSONB` rather than `JSON` because the read models genuinely need it: indexable,
and it round-trips lists instead of returning the text `"[]"`.
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    false,
    func,
    true,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

# --------------------------------------------------------------------------
# Event log
# --------------------------------------------------------------------------

events = Table(
    "events",
    metadata,
    Column("global_seq", BigInteger, primary_key=True, autoincrement=True),
    Column("event_id", Text, nullable=False, unique=True),
    Column("aggregate_id", Text, nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("event_type", Text, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    Column("actor", Text, nullable=False),
    Column("payload", JSONB, nullable=False),
    # Optimistic concurrency: two writers folding the same version collide here.
    Index("events_stream_uniq", "aggregate_id", "sequence", unique=True),
    Index("events_aggregate_idx", "aggregate_id", "sequence"),
    Index("events_type_idx", "event_type", "global_seq"),
    # A total order across aggregates, newest first, is what the audit view reads.
    Index("events_occurred_idx", "occurred_at", postgresql_using="btree"),
)

projection_checkpoints = Table(
    "projection_checkpoints",
    metadata,
    Column("name", Text, primary_key=True),
    Column("global_seq", BigInteger, nullable=False),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

gateway_slugs = Table(
    "gateway_slugs",
    metadata,
    Column("slug", Text, primary_key=True),
    Column("aggregate_id", Text, nullable=False, unique=True),
)

operator_emails = Table(
    "operator_emails",
    metadata,
    Column("email", Text, primary_key=True),
    Column("aggregate_id", Text, nullable=False, unique=True),
)

rm_roles = Table(
    "rm_roles",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("permissions", JSONB, nullable=False, default=list),
    Column("deleted", Boolean, nullable=False, default=False, server_default=false()),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    Column(
        "updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
)

rm_operators = Table(
    "rm_operators",
    metadata,
    Column("id", Text, primary_key=True),
    Column("email", Text, nullable=False, unique=True),
    Column("role_ids", JSONB, nullable=False, default=list),
    Column("is_admin", Boolean, nullable=False, default=False, server_default=false()),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
)

# --------------------------------------------------------------------------
# Read models
# --------------------------------------------------------------------------
#
# Every column that a projection does not always supply has a Python-side
# default, so inserts stay small and the DDL needs no string literals.

rm_gateways = Table(
    "rm_gateways",
    metadata,
    Column("id", Text, primary_key=True),
    Column("slug", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("target_url", Text, nullable=False),
    Column("description", Text, nullable=False, default=""),
    Column("auth_mode", Text, nullable=False),
    Column("idp", Text),
    Column("client_id", Text),
    Column("config_url", Text),
    Column("required_scopes", JSONB, nullable=False, default=list),
    Column("requests_per_minute", Integer, nullable=False, default=0),
    Column("enabled", Boolean, nullable=False, default=True, server_default=true()),
    Column("deleted", Boolean, nullable=False, default=False, server_default=false()),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    Column(
        "updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
)

rm_principals = Table(
    "rm_principals",
    metadata,
    Column("gateway_id", Text, primary_key=True),
    Column("subject", Text, primary_key=True),
    Column("note", Text, nullable=False, default=""),
    Column(
        "added_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    Column("revoked", Boolean, nullable=False, default=False, server_default=false()),
)

rm_api_keys = Table(
    "rm_api_keys",
    metadata,
    Column("gateway_id", Text, primary_key=True),
    Column("key_id", Text, primary_key=True),
    Column("label", Text, nullable=False),
    Column("key_hash", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column(
        "issued_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    Column("revoked", Boolean, nullable=False, default=False, server_default=false()),
    Index("rm_api_keys_hash_idx", "key_hash"),
)

rm_request_stats = Table(
    "rm_request_stats",
    metadata,
    Column("gateway_id", Text, primary_key=True),
    # Minute granularity: finer would grow without bound for no extra insight.
    Column("bucket", DateTime(timezone=True), primary_key=True),
    Column("outcome", Text, primary_key=True),
    Column("ip", Text, primary_key=True, default=""),
    Column("count", BigInteger, nullable=False, default=0),
    Column("total_ms", BigInteger, nullable=False, default=0),
    Column("max_ms", Integer, nullable=False, default=0),
)

rm_requests_tail = Table(
    "rm_requests_tail",
    metadata,
    Column("event_id", Text, primary_key=True),
    Column("gateway_id", Text, nullable=False),
    Column("subject", Text, nullable=False),
    Column("outcome", Text, nullable=False),
    Column("status", Integer, nullable=False),
    Column("duration_ms", Integer, nullable=False),
    Column("ip", Text),
    Column("tool", Text),
    Column("method", Text, nullable=False),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
)

# Indexes that only make sense on an expression are declared after the tables.
# Partial: a deleted gateway keeps its slug for the audit trail but must not
# block a new gateway from reusing the name.
Index(
    "rm_gateways_live_slug_idx",
    rm_gateways.c.slug,
    unique=True,
    postgresql_where=rm_gateways.c.deleted == false(),
)
Index("rm_stats_bucket_idx", rm_request_stats.c.bucket.desc())
Index("rm_tail_time_idx", rm_requests_tail.c.occurred_at.desc())

__all__ = [
    "events",
    "gateway_slugs",
    "metadata",
    "operator_emails",
    "projection_checkpoints",
    "rm_api_keys",
    "rm_gateways",
    "rm_operators",
    "rm_principals",
    "rm_request_stats",
    "rm_requests_tail",
    "rm_roles",
]
