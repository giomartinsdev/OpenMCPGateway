Feature: More than one way in
  As an operator
  I want one gateway to accept an API key and tokens from several OAuth issuers
  So that I do not have to publish a separate gateway per credential type

  Background:
    Given an operator "multi@example.dev" with password "correct-horse-battery"

  # The whole point: one URL, one rate limit, one allowlist, several ways to
  # prove who you are.
  Scenario: One gateway admits both a key and an OAuth login
    Given a gateway "demo" with oauth github auth proxying a real MCP server
    And an API key was issued for the gateway
    And the gateway also accepts API keys
    When a caller with the issued key lists the tools
    Then the backend tool "echo" is visible

  Scenario: The OAuth login still works while keys are accepted
    # Two credential paths on one route is exactly where the metadata chain
    # breaks: the guard must keep advertising the login it still hosts.
    Given a gateway "demo" with oauth github auth proxying a real MCP server
    And an API key was issued for the gateway
    And the gateway also accepts API keys
    When an anonymous caller probes the gateway
    Then the caller receives 401
    And the challenge names a resource metadata URL

  Scenario: A revoked key stops working even though OAuth does not
    Given a gateway "demo" with oauth github auth proxying a real MCP server
    And an API key was issued for the gateway
    And the gateway also accepts API keys
    When the operator revokes every key
    Then the revoked key is refused with 401

  # The second issuer is not a login flow: the client already holds a token
  # from it. Accepting it means the gateway is the single resource server for
  # every provider the operator runs.
  Scenario: A token from a second authorization server is admitted
    Given a gateway "demo" accepting tokens from a second authorization server
    When a caller presents a token issued by that authorization server
    Then the call is admitted

  Scenario: A token signed by an issuer nobody configured is refused
    Given a gateway "demo" accepting tokens from a second authorization server
    When a caller presents a token from an unrelated issuer
    Then the caller receives 401

  Scenario: Two verifiers cannot share a label
    # The label is what the token's issuer is logged as; two sources with one
    # name makes "which provider rejected this" unanswerable.
    Given a gateway "demo" with oauth github auth
    When the operator adds two verifiers with the label "corp"
    Then configuring it fails with "verifier labels must be unique"

  Scenario: A public gateway cannot be made to demand credentials
    # "Anyone may call" and "you must present a key" are contradictory; a
    # gateway that silently picks the stricter one is a gateway that breaks
    # callers who were told it was open.
    Given a gateway "open" with no auth
    When the operator enables API keys on it
    Then configuring it fails with "cannot be combined"

  Scenario: Accepting keys before one exists is refused
    # Otherwise the route flips to "accepts nothing" and looks broken to the
    # owner, who has no key to present yet.
    Given a gateway "demo" with oauth github auth
    When the operator enables API keys on it
    Then configuring it fails with "issue a key first"
