Feature: Control — correlation, budgets, revocation and notifications

  As an operator
  I want a call I can name, a monthly ceiling that is not a per-minute one, a
  way to sign someone out, and a machine that tells me when a gateway breaks
  So that support has an id to ask for, a runaway integration stops on its own,
  an offboarded account stops within seconds, and nobody has to watch a screen
  at three in the morning

  Background:
    Given an operator "control@example.dev" with password "correct-horse-battery"

  # ------------------------------------------------------------- correlation
  # The event id exists only after the row is written, which is too late for the
  # caller on the phone. A request id is chosen at the edge, echoed back, and
  # stored, so "which call was that" is a header and a filter.

  Scenario: A caller's request id is echoed back and recorded
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key calls it with request id "trace-abc-123"
    Then the response carries request id "trace-abc-123"
    And the traffic log records request id "trace-abc-123"

  Scenario: A call without a request id still gets one
    # Two callers with no header must not share a row: the gateway mints one
    # rather than storing an empty string.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key calls it once
    Then the response carries a generated request id
    And the traffic log records a request id

  Scenario: A refused call also carries the id it was given
    # The interesting tickets are the refusals, so the id cannot be minted only
    # on the success path.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    When an anonymous caller probes the gateway with request id "trace-denied-1"
    Then the response carries request id "trace-denied-1"

  # ----------------------------------------------------------- monthly quota
  # A per-minute limit bounds a burst, not a bill. An integration that calls
  # politely all month is invisible to it.

  Scenario: A call over the monthly quota is refused with 429
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And the gateway's monthly quota is 1 call
    And an API key was issued for the gateway
    When a caller with the issued key calls it 2 times
    Then one call is refused for the monthly quota

  Scenario: A quota of zero means no quota
    # Same convention as the rate limit, because two spellings of "unlimited"
    # in one panel is a support call.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And the gateway's monthly quota is 0 calls
    And an API key was issued for the gateway
    When a caller with the issued key calls it 3 times
    Then no call is refused for the monthly quota

  Scenario: The quota resets when the month changes
    # The window is a calendar month in UTC. A counter that is never rolled over
    # is a gateway that stops working on the first of the month after it was
    # installed, which is the worst kind of bug: late and total.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And the gateway's monthly quota is 1 call
    And an API key was issued for the gateway
    And the quota counter was last rolled over in September
    When a caller with the issued key calls it once
    Then the call is allowed

  # ------------------------------------------------------------ revocation
  # Sessions are signed and stateless with a twelve-hour life. Offboarding
  # someone today meant rotating the deployment secret, which signs out every
  # tenant's every user.

  Scenario: Disabling a member signs them out immediately
    Given a member "gone@example.dev" with the viewer role
    And that member is signed in
    When the owner disables "gone@example.dev"
    Then the member's next request is 401

  Scenario: A disabled member can no longer sign in
    Given a member "gone@example.dev" with the viewer role
    When the owner disables "gone@example.dev"
    Then signing in as "gone@example.dev" is refused

  Scenario: Disabling a member keeps their audit trail
    # The events are the record of what they did while they had access.
    # Deleting them would rewrite history to make an offboarding look tidier.
    Given a member "gone@example.dev" with the viewer role
    When the owner disables "gone@example.dev"
    Then the audit log still names "gone@example.dev"

  Scenario: Re-enabling a member restores access without a new password
    Given a member "gone@example.dev" with the viewer role
    And the owner disabled "gone@example.dev"
    When the owner re-enables "gone@example.dev"
    Then signing in as "gone@example.dev" succeeds

  # -------------------------------------------------------------- webhooks
  # `test upstream` answers a question nobody asked at 03:00. A hook is the same
  # observation pushed, with a retry and a signature so the receiver can trust it.

  Scenario: A webhook delivers a failure observation as a signed POST
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And a webhook pointing at a real HTTP endpoint
    When the gateway refuses a call for an anonymous caller
    Then the endpoint receives a POST with a signature
    And the payload names the gateway and the outcome

  Scenario: A webhook that is unreachable is retried and then given up on
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And a webhook pointing at an endpoint that fails
    When the gateway receives a failure observation
    Then the delivery is retried
    And the delivery is recorded as failed

  Scenario: A webhook only receives what it subscribed to
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    And a webhook subscribed to "forbidden" only
    When the gateway receives an allowed observation
    Then the endpoint receives nothing

  Scenario: An unsigned webhook is refused
    # A receiver that cannot tell our POST from anyone's POST has not gained
    # anything. The secret is shown once, like an API key.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And a webhook with no secret
    Then the webhook is refused with 422

  Scenario: A webhook URL must be http or https
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And a webhook pointing at "file:///etc/passwd"
    Then the webhook is refused with 422
