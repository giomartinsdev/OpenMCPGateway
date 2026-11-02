Feature: Proxy authorisation
  As an operator
  I want the proxy to decide who gets through
  So that only approved callers reach the backend MCP server

  Background:
    Given an operator "ops@example.dev" with password "correct-horse-battery"

  Scenario: An unauthenticated caller is challenged, not forbidden
    Given a gateway "demo" with api_key auth
    When an anonymous caller probes the gateway
    Then the caller receives 401
    And the challenge names no bearer realm

  Scenario: A wrong API key is rejected
    Given a gateway "demo" with api_key auth
    When a caller presents an API key that was never issued
    Then the caller receives 401

  Scenario: A revoked key stops working immediately
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the operator revokes every key
    Then the revoked key is refused with 401

  Scenario: A valid key reaches the backend
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key lists the tools
    Then the backend tool "echo" is visible

  Scenario: An allowlisted principal passes
    Given a gateway "demo" with oauth github auth
    And the principal "octocat" is allowed
    When the identity "octocat" is resolved against the gateway
    Then the identity is accepted
    And the resolved subject is "octocat"

  Scenario: A principal outside the allowlist is forbidden, not challenged
    Given a gateway "demo" with oauth github auth
    And the principal "octocat" is allowed
    When the identity "stranger" is resolved against the gateway
    Then the identity is refused

  Scenario: An empty allowlist admits any authenticated principal
    Given a gateway "demo" with oauth github auth
    When the identity "anyone" is resolved against the gateway
    Then the identity is accepted

  Scenario: A disabled gateway is not routed at all
    Given a gateway "demo" with api_key auth proxying a real MCP server
    When the operator disables the gateway
    Then the gateway endpoint is unreachable

  Scenario: A gateway awaiting credentials does not break its neighbours
    # Creating a gateway and configuring its OAuth client are two steps. Between
    # them, the gateway is "enabled" but unusable — and it must be the only thing
    # that suffers. Before this test, one such draft raised KeyError out of
    # _install and returned 500 for every route, including unrelated gateways.
    Given a gateway "ready" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    And an enabled gateway "draft" with oauth github auth and no credentials
    When a caller with the issued key lists the tools
    Then the backend tool "echo" is visible
    And the "draft" gateway answers 503 explaining it is not ready

  Scenario: The gateway presents the upstream's own token, not the caller's
    # Servers like rancher-mcp and db-mcp demand a bearer of their own. If the
    # gateway cannot hold it, the only options are leaving the upstream open or
    # keeping the token in every client config — which is what the gateway is for.
    Given a gateway "protected" proxying an MCP server that requires the token "up-secret"
    And the gateway is told the upstream token
    And an API key was issued for the gateway
    When a caller with the issued key lists the tools
    Then the backend tool "echo" is visible
    And the backend saw a bearer on every request

  Scenario: Gateways are serving again after a restart
    # Boot must end with the same route table it had before: a gateway that only
    # becomes reachable after the first request to its own URL is down for anyone
    # whose client reconnects first — and /healthz lies about it in the meantime.
    Given a gateway "restarted" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a second container starts against the same database
    Then the "restarted" gateway is advertised as live

  Scenario: Rotating a key returns the new one and kills the old one
    # Rotation exists because the plaintext is unrecoverable: a key that leaked,
    # or one whose owner forgot it, has to be replaced without a window where
    # both work or neither does.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the operator rotates that key
    Then the rotation returns a new key and the old one is refused with 401
    And a caller with the rotated key lists the tools
