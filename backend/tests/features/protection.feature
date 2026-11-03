Feature: Rate limiting and traffic accounting
  As an operator
  I want to cap usage and see what happened
  So that one noisy client cannot starve the rest, and I can debug

  Background:
    Given an operator "ops@example.dev" with password "correct-horse-battery"

  Scenario: A configured limit rejects excess requests with 429
    # One MCP call costs two counted JSON-RPC requests (initialize + tools/list);
    # notifications are free. Four per minute therefore allows two calls.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    And the usage limit is 4 requests per minute
    When a caller with the issued key calls it 3 times
    Then the third call receives 429
    And the 429 response asks to retry later

  Scenario: Each subject gets its own budget
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    And the usage limit is 2 requests per minute
    When a second key is issued
    And each key calls once
    Then neither call is rate limited

  Scenario: An observation is recorded for a rejected call
    Given a gateway "demo" with api_key auth proxying a real MCP server
    When an anonymous caller probes the gateway
    Then the traffic log records outcome "unauthenticated" with status 401
    And the observation carries the caller IP

  Scenario: Allowed traffic is counted in the stats
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key calls it 2 times
    Then the stats report at least 2 requests
    And the stats include a per-IP breakdown

  Scenario: The breakdown names the gateway a reader recognises
    # The stats table keys on the gateway id, but a dashboard that shows `gw_…`
    # cannot be compared with the gateway list, which shows slugs.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key calls it 2 times
    Then the stats break the traffic down by gateway slug "demo"

  Scenario: The stats name the busiest minute
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key calls it 2 times
    Then the stats name the busiest minute, and it is one of the series points

  Scenario: A window is compared against the one before it
    # "12 rejected" says nothing on its own; "12 rejected, up from 3" is the
    # sentence an operator is looking for. The previous window is the same
    # length, immediately before this one.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key calls it 2 times
    Then the stats report no traffic in the previous window
