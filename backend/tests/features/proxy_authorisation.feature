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

  # The gate used to be a per-gateway list of subjects where *empty* meant
  # "anyone who authenticates" — a fail-open default behind a card that looked
  # like a gate. Now it is belonging: the caller's account has to be in the
  # tenant that owns the gateway. Same rule the panel already applies to
  # managing it, so there is one answer to "who may use this", not two that
  # drift.

  Scenario: A member of the gateway's tenant passes
    Given a gateway "demo" with oauth github auth
    And the account "octocat@example.dev" belongs to the gateway's tenant
    When the identity "octocat@example.dev" is resolved against the gateway
    Then the identity is accepted
    And the resolved subject is "octocat@example.dev"

  Scenario: An authenticated identity from outside every tenant is forbidden
    # Someone who signed up and joined nothing still gets a valid token. The
    # tenant is what turns an identity into access, and an account without one
    # reaches no gateway — including `rancher`, whose tools restart pods.
    Given a gateway "demo" with oauth github auth
    When the identity "stranger@example.dev" is resolved against the gateway
    Then the identity is refused

  Scenario: A member of another tenant is forbidden
    Given a gateway "demo" with oauth github auth
    And the account "outsider@example.dev" belongs to another tenant
    When the identity "outsider@example.dev" is resolved against the gateway
    Then the identity is refused

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

  # The plaintext used to be unrecoverable the moment the response was rendered:
  # "shown once" meant "if you closed the tab, rotate". Sealing it with the
  # deployment secret keeps that promise — the operator can read it exactly once
  # more — without turning the panel into a secret store.
  Scenario: A key that was never seen can be revealed once
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the operator reveals that key
    Then the revealed key matches the issued one
    And revealing it again is refused with a conflict

  Scenario: A rotated key can be revealed, because it was never seen either
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the operator rotates that key
    Then the new key can be revealed once

  Scenario: A revoked key cannot be revealed
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the operator revokes every key
    Then revealing the revoked key is refused

  # A gateway allowlist is a second gate on *which* tools, not just who. Both
  # halves matter: hiding a tool from the list without refusing the call is a
  # suggestion, and refusing the call without hiding it makes a client retry.
  Scenario: A gateway exposes only the tools it allows
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the operator allows only the tool "echo"
    Then a caller with the issued key sees the tool "echo"
    And calling the allowed tool succeeds
    And calling a tool outside the allowlist is refused with 403

  Scenario: The panel can read the upstream's tool catalog
    # The allowlist is ticked, not typed. Asking the gateway would answer with
    # what it already allows, so this path goes to the upstream directly.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    When the operator asks which tools the upstream serves
    Then the catalog is available and lists "echo"

  Scenario: An unreachable upstream says so instead of failing
    Given a gateway "demo" with api_key auth
    When the operator asks which tools the upstream serves
    Then the catalog is unavailable and names the reason

  Scenario: An empty allowlist means every tool
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the operator allows no tools
    Then a caller with the issued key sees the tool "echo"
    And calling the allowed tool succeeds

  # A checkpoint is a promise that everything before it was applied. Wiping
  # `rm_*` without wiping the checkpoint breaks that promise silently: the boot
  # replays nothing and the panel looks like a fresh install with all the
  # gateways missing. The empty read model is the evidence that it happened.
  Scenario: A wiped read model is rebuilt even though the checkpoint survived
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the read model is wiped but the checkpoint is kept
    And a second container starts against the same database
    Then the "demo" gateway is advertised as live

  Scenario: Rotating a key returns the new one and kills the old one
    # Rotation exists because the plaintext is unrecoverable: a key that leaked,
    # or one whose owner forgot it, has to be replaced without a window where
    # both work or neither does.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the operator rotates that key
    Then the rotation returns a new key and the old one is refused with 401
    And a caller with the rotated key lists the tools
