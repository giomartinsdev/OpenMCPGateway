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

from gateway.domain.events import TENANT_OF_DEPLOYMENT

metadata = MetaData()

#: Where the read models live. Not `public`: a projection can be rebuilt from the
#: log, so `DROP SCHEMA readmodels CASCADE` followed by a boot is a safe reset —
#: and that is only true if nothing the log depends on is in there. `events`,
#: `schema_migrations` and the uniqueness tables stay in `public`.
READMODELS_SCHEMA = "readmodels"

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

# The join code is looked up by value, so it needs its own unique index: a
# tenant list scan per join is a full table read, and two tenants sharing a code
# would make "join" ambiguous.
rm_tenants = Table(
    "rm_tenants",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("join_code", Text, nullable=False, unique=True),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    schema=READMODELS_SCHEMA,
)

rm_roles = Table(
    "rm_roles",
    metadata,
    Column("id", Text, primary_key=True),
    # Roles are a tenant's policy: two tenants may both have an "Auditor" with
    # different permissions, and one must never see the other's.
    Column(
        "tenant_id", Text, nullable=False, default=TENANT_OF_DEPLOYMENT,
        server_default=TENANT_OF_DEPLOYMENT,
    ),
    Column("name", Text, nullable=False),
    Column("permissions", JSONB, nullable=False, default=list),
    Column("deleted", Boolean, nullable=False, default=False, server_default=false()),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    Column(
        "updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    schema=READMODELS_SCHEMA,
)

rm_operators = Table(
    "rm_operators",
    metadata,
    Column("id", Text, primary_key=True),
    Column("email", Text, nullable=False, unique=True),
    # Nullable: a new account has no tenant until it creates or joins one.
    Column("tenant_id", Text),
    Column("role_ids", JSONB, nullable=False, default=list),
    Column("is_admin", Boolean, nullable=False, default=False, server_default=false()),
    #: Offboarding. A signed cookie is a claim about the past, so "stop this
    #: person now" cannot be answered by deleting a row: the token is still
    #: valid until it expires. This flag is read on every request instead.
    Column("disabled", Boolean, nullable=False, default=False, server_default=false()),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    schema=READMODELS_SCHEMA,
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
    Column(
        "tenant_id", Text, nullable=False, default=TENANT_OF_DEPLOYMENT,
        server_default=TENANT_OF_DEPLOYMENT,
    ),
    Column("slug", Text, nullable=False),
    Column("name", Text, nullable=False),
    Column("target_url", Text, nullable=False),
    Column("description", Text, nullable=False, default=""),
    Column("auth_mode", Text, nullable=False),
    Column("idp", Text),
    Column("client_id", Text),
    Column("config_url", Text),
    Column("required_scopes", JSONB, nullable=False, default=list),
    #: Accept keys as well as the OAuth login. One route, two ways in.
    Column("allow_api_key", Boolean, nullable=False, default=False,
           server_default=false()),
    #: Extra issuers verified without hosting their login. `[{label, issuer,
    #: audience, jwks_uri}]`.
    Column("verifiers", JSONB, nullable=False, default=list),
    Column("requests_per_minute", Integer, nullable=False, default=0),
    #: Monthly ceiling, 0 = unlimited. A per-minute limit bounds a burst, not a
    #: bill; an integration that calls politely all month is invisible to it.
    Column("monthly_quota", Integer, nullable=False, default=0),
    #: Which calendar month `requests_this_month` counts. `YYYY-MM` in UTC, so a
    #: counter that was never rolled over cannot silently outlive its month.
    Column("quota_month", Text, nullable=False, default=""),
    Column("requests_this_month", Integer, nullable=False, default=0),
    #: Upstream tools this gateway exposes. Empty means all of them.
    Column("tools", JSONB, nullable=False, default=list),
    #: Per credential source overrides: `{"api_key": ["issue_write"]}`. A source
    #: with no entry inherits `tools`.
    Column("tool_scopes", JSONB, nullable=False, default=dict),
    Column("enabled", Boolean, nullable=False, default=True, server_default=true()),
    Column("deleted", Boolean, nullable=False, default=False, server_default=false()),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    Column(
        "updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    schema=READMODELS_SCHEMA,
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
    schema=READMODELS_SCHEMA,
)

rm_api_keys = Table(
    "rm_api_keys",
    metadata,
    Column("gateway_id", Text, primary_key=True),
    Column("key_id", Text, primary_key=True),
    Column("label", Text, nullable=False),
    Column("key_hash", Text, nullable=False),
    Column("subject", Text, nullable=False),
    # The plaintext, sealed with the deployment secret. Not a hash: the operator
    # who issued a key can read it back once, and that event wipes this column.
    # Empty for keys minted before sealing existed.
    Column("sealed", Text, nullable=False, default=""),
    # When the plaintext was last handed out. NULL means "never seen", which is
    # what the panel's eye icon keys on.
    Column("revealed_at", DateTime(timezone=True)),
    Column("revealed_by", Text),
    # NULL means the key never expires, which is every key minted before this
    # column existed.
    Column("expires_at", DateTime(timezone=True)),
    Column(
        "issued_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    Column("revoked", Boolean, nullable=False, default=False, server_default=false()),
    Index("rm_api_keys_hash_idx", "key_hash"),
    schema=READMODELS_SCHEMA,
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
    schema=READMODELS_SCHEMA,
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
    Column("reason", Text, nullable=False, default="", server_default=""),
    #: The id the caller chose (or the guard minted) for this call. Chosen at the
    #: edge so it exists before the row does, which is what makes "which call was
    #: that" a question support can ask.
    Column("request_id", Text, nullable=False, default="", server_default=""),
    Column("occurred_at", DateTime(timezone=True), nullable=False),
    schema=READMODELS_SCHEMA,
)

#: What the upstream served for one tool, last time it was probed.
#:
#: A tool that disappeared is visible from the catalog. A tool whose description
#: or `inputSchema` changed is not — and that is the one that breaks a client that
#: worked yesterday. The fingerprint is a hash of the parts a caller depends on.
rm_tool_fingerprints = Table(
    "rm_tool_fingerprints",
    metadata,
    Column("gateway_id", Text, primary_key=True),
    Column("tool", Text, primary_key=True),
    Column("fingerprint", Text, nullable=False),
    Column(
        "checked_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    schema=READMODELS_SCHEMA,
)

#: A URL to POST observations to. One row per hook, owned by a gateway.
rm_webhooks = Table(
    "rm_webhooks",
    metadata,
    Column("id", Text, primary_key=True),
    Column("gateway_id", Text, nullable=False),
    Column("url", Text, nullable=False),
    #: Sealed with the deployment secret, like an API key's plaintext: the hook
    #: needs it to sign, and the panel must be able to show it once without the
    #: database holding it in the clear.
    Column("secret", Text, nullable=False),
    #: Empty means every outcome. A hook that asked for failures only must not
    #: receive a success, or the filter is decoration.
    Column("outcomes", JSONB, nullable=False, default=list),
    Column("enabled", Boolean, nullable=False, default=True, server_default=true()),
    Column(
        "created_at", DateTime(timezone=True), nullable=False,
        server_default=func.now(),
    ),
    schema=READMODELS_SCHEMA,
)

#: One row per delivery attempt, because "the hook is silent" has two causes: no
#: matching event, or every POST failing. Only a stored attempt tells them apart.
rm_webhook_deliveries = Table(
    "rm_webhook_deliveries",
    metadata,
    Column("id", Text, primary_key=True),
    Column("webhook_id", Text, nullable=False),
    Column("event", Text, nullable=False),
    Column("status", Text, nullable=False),  # delivered | failed
    Column("attempts", Integer, nullable=False, default=0),
    Column("status_code", Integer),
    Column("error", Text, nullable=False, default=""),
    Column(
        "created_at", DateTime(timezone=True), nullable=False, server_default=func.now()
    ),
    schema=READMODELS_SCHEMA,
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
Index("rm_operators_tenant_idx", rm_operators.c.tenant_id)
Index("rm_stats_bucket_idx", rm_request_stats.c.bucket.desc())
Index("rm_tail_time_idx", rm_requests_tail.c.occurred_at.desc())

__all__ = [
    "READMODELS_SCHEMA",
    "TENANT_OF_DEPLOYMENT",
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
    "rm_tenants",
    "rm_tool_fingerprints",
    "rm_webhook_deliveries",
    "rm_webhooks",
]
