Feature: What the log can tell you after the fact

  As an operator
  I want the log to answer "how slow", "who still uses this key", "when did
  that change", and "has the upstream changed under me"
  So that a regression, a dead credential and a silent upstream release are
  read from the panel instead of reconstructed with curl

  Background:
    Given an operator "insight@example.dev" with password "correct-horse-battery"

  # ------------------------------------------------------------------ latency
  # `avg` hides a tail and `max` is one unlucky call. Neither answers "is the
  # p95 the thing that got worse". Percentiles are read from the retained rows
  # rather than from `rm_request_stats`: a bucket that keeps count/sum/max
  # cannot be un-aggregated, and inventing a distribution from three numbers
  # would be a percentile-shaped lie.

  Scenario: Percentiles describe the distribution of the window
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And 100 observations with durations 1 to 100 milliseconds
    When the operator reads the stats for the last hour
    Then the p50 latency is 50.5 milliseconds
    And the p95 latency is 95.05 milliseconds
    And the p99 latency is 99.01 milliseconds

  Scenario: A window with no traffic reports zero percentiles, not an error
    # A dashboard that divides by zero on a quiet gateway is a dashboard that
    # is red every morning before anyone calls anything.
    Given a gateway "quiet" with api_key auth proxying a real MCP server
    When the operator reads the stats for the last hour
    Then the p50 latency is 0 milliseconds
    And the p95 latency is 0 milliseconds

  # ------------------------------------------------------------- key last use
  # "Can this key be revoked?" is a question about evidence. Without a last-used
  # date the only safe answer is no, so keys outlive the person who made them.

  Scenario: A key that has served a call carries the time of its last call
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key calls it once
    Then the key's last-used time is recorded

  Scenario: A key nobody has called has no last-used time
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    Then the key has never been used

  # --------------------------------------------------------------- audit CSV
  # The request log exports; the audit log is the one a compliance review asks
  # for, and "select * from events" is not an answer anyone outside this team
  # can run.

  Scenario: The audit log exports as CSV within the caller's tenant
    Given a gateway "demo" with api_key auth proxying a real MCP server
    When the operator exports the audit log as CSV
    Then the CSV header starts with "occurred_at,event_type,aggregate_id"
    And the CSV contains the gateway's creation event
    And the CSV contains no event from another tenant

  Scenario: Exporting the audit log needs audit read permission
    # A viewer may read the audit log; a role built for the gateway page alone
    # may not. Exporting is the same permission as reading, not a second one.
    Given a custom role "no-audit" that may only read gateways
    And an operator "reader@example.dev" with the role "no-audit"
    When "reader@example.dev" exports the audit log as CSV
    Then the CSV export answers 403

  # ------------------------------------------------------- history and restore
  # Everything is event sourced, and the log was only readable as a flat feed.
  # "Who changed this target_url, and what was it before" is one aggregate's
  # stream, not a needle in the global list.

  Scenario: A gateway's history lists its versions oldest first
    Given a gateway "demo" pointing at "http://127.0.0.1:9/mcp"
    And the operator renames it to "first"
    And the operator renames it to "second"
    When the operator reads the gateway's history
    Then the history has a version for every change
    And the last entry's actor is the operator who made it

  Scenario: Restoring an earlier version brings the field back
    Given a gateway "demo" pointing at "http://127.0.0.1:9/mcp"
    And the operator renames it to "first"
    And the operator renames it to "second"
    When the operator restores the gateway to version 2
    Then the gateway is named "first"
    And the restore is recorded as its own event

  Scenario: Restoring a version that does not exist is refused
    Given a gateway "demo" pointing at "http://127.0.0.1:9/mcp"
    When the operator restores the gateway to version 999
    Then the answer is 422

  Scenario: Restoring the current version changes nothing
    # A no-op restore that writes an event is a log full of "nothing happened",
    # which costs a reader attention every time they audit a gateway.
    Given a gateway "demo" pointing at "http://127.0.0.1:9/mcp"
    When the operator restores the gateway to its current version
    Then the answer is 422

  # ----------------------------------------------------------- schema drift
  # A tool that disappeared is visible today. A tool whose description or input
  # schema changed is not, and that is the one that breaks a client that was
  # working yesterday.

  Scenario: A tool whose upstream schema changed is reported as changed
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And a stored fingerprint for tool "echo" that does not match the upstream
    When the operator tests the gateway's upstream
    Then the test reports the tool "echo" as changed

  Scenario: The first probe records fingerprints without calling them drift
    # A baseline that is itself reported as a change makes the badge red on
    # every fresh deployment, which is how a warning becomes wallpaper.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    When the operator tests the gateway's upstream
    Then the test reports no changed tools
