Feature: Reason, usage, retention, key expiry and reachability

  As an operator
  I want the log to say why, the allowlist to know what is unused, and old
  traffic to age out on its own
  So that "why did that fail" is a sentence, not a guess, and a deployment
  that has been running for a year still fits on the same disk

  Background:
    Given an operator "ops@example.dev" with password "correct-horse-battery"

  # ------------------------------------------------------------------ reason
  # `outcome` says "forbidden"; it cannot tell an allowlist refusal from a tool
  # allowlist refusal from a bad token. The guard knows which one it was, and the
  # panel cannot ask it after the fact.

  Scenario: A refused subject records the allowlist as the reason
    # Keys bypass the allowlist (their subject embeds a key id that rotation
    # replaces), so the refusal is produced by an identity a provider vouched
    # for: that is the caller the list is about.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And the principal "someone-else" is allowed
    When an outsider calls the gateway
    Then the traffic log records reason "subject not in allowlist"

  Scenario: A refused tool records the tool's name as the reason
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And the allowlist exposes only "nothing_like_this"
    And an API key was issued for the gateway
    When a caller with the issued key calls tool "echo" once
    Then the traffic log records reason "tool not in allowlist"

  Scenario: An allowed call records no reason
    # A reason field that is always set is a field nobody reads. Allowed is the
    # normal case and it has nothing to explain.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key calls it once
    Then the traffic log records no reason

  # ------------------------------------------------------------------- usage
  # The allowlist is a set of decisions; a decision nobody can audit against
  # usage is a list that only grows. The count is per gateway and per tool, from
  # the same aggregation as the totals.

  Scenario: The stats count calls per tool
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key calls tool "echo" 2 times
    Then the stats report tool "echo" was called 2 times

  Scenario: Calls that named no tool leave the tool breakdown empty
    # A 401 never reaches tools/list, so it has no tool. Counting it under an
    # empty name would put "no tool" at the top of the usage table.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    When an anonymous caller probes the gateway
    Then the stats report no tool usage

  # --------------------------------------------------------------- retention
  # Only the traffic log is pruned. The audit log is the record of who changed
  # what and never ages out; a request log is a debugging aid with a horizon.

  Scenario: Old traffic observations are past the retention horizon
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an observation recorded 40 days ago
    When the retention window is 30 days
    Then the old observation is pruned
    And the recent observation is kept

  Scenario: Retention of zero keeps everything
    Given a gateway "demo" with api_key auth proxying a real MCP server
    When the retention window is 0 days
    Then the retention step prunes nothing

  Scenario: Retention never touches the audit log
    Given a gateway "demo" with api_key auth proxying a real MCP server
    When the retention window is 30 days
    Then the audit log still has the gateway's creation event

  # ---------------------------------------------------------------- key expiry
  # Rotation is manual and nobody remembers. An expiry date turns "this key has
  # been in a CI secret for three years" into a 401 with a date on it.

  Scenario: A key past its expiry is refused
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway that expired yesterday
    When a caller with the issued key calls it once
    Then the gateway answers 401

  Scenario: A key with no expiry never expires
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key calls it once
    Then the gateway answers 200

  Scenario: The key list names when each key expires
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the operator lists the gateway's keys over HTTP
    Then every key row carries an expiry field

  # ------------------------------------------------------------- reachability
  # "Is the upstream up" is not "is the gateway up". The panel could only answer
  # the second, so a dead upstream looked like a healthy gateway with no traffic.

  Scenario: Testing a dead upstream reports it is down
    Given a gateway "demo" pointing at "http://127.0.0.1:1/mcp"
    When the operator tests the gateway's upstream
    Then the test reports the upstream is down
    And the test names what went wrong

  Scenario: Testing a live upstream reports it is up
    Given a gateway "demo" with api_key auth proxying a real MCP server
    When the operator tests the gateway's upstream
    Then the test reports the upstream is up
    And the test counts the tools the upstream serves

  Scenario: The gateway detail carries the last health result
    Given a gateway "demo" with api_key auth proxying a real MCP server
    When the operator tests the gateway's upstream
    Then the gateway detail reports the upstream is up

  # --------------------------------------------------------------------- CSV
  # The log is what a SIEM and a spreadsheet want, and neither speaks JSON with
  # a cursor. Same filters, same tenant scope, one file.

  Scenario: The request log exports as CSV
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key calls it 2 times
    And the owner exports the request log as CSV
    Then the CSV has a header row
    And the CSV has a row per exported request
    And the CSV rows carry the outcome column

  Scenario: A CSV export cannot reach another tenant
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And another tenant's gateway "other" has traffic
    When the owner exports the request log as CSV
    Then the CSV does not mention gateway "other"

  # -------------------------------------------------------------------- plan
  # Applying a file is a blind write. `prune: true` plus a truncated file is a
  # delete nobody saw coming; the plan is the diff that makes it visible.

  Scenario: The plan lists what an apply would create
    Given a YAML document:
      """
      gateways:
        - slug: fresh
          name: Fresh
          target_url: http://backend.test/mcp
      """
    When the owner asks what applying it would do
    Then the plan would create "fresh"
    And the plan would delete nothing

  Scenario: The plan lists what prune would delete
    Given a gateway "kept" pointing at "http://backend.test/mcp"
    And a YAML document with prune enabled omitting "kept"
    When the owner asks what applying it would do
    Then the plan would delete "kept"

  Scenario: Asking for a plan changes nothing
    Given a gateway "kept" pointing at "http://backend.test/mcp"
    And a YAML document with prune enabled omitting "kept"
    When the owner asks what applying it would do
    Then gateway "kept" still exists
