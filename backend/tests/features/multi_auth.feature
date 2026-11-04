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

  # The allowlist names *people*: it is a second gate on an identity a provider
  # vouched for. A key has no identity to vet — it is a credential the operator
  # minted for this gateway on purpose — and its subject is derived from the key
  # id, so requiring it in the list would break on the next rotation.
  Scenario: An allowlisted gateway still admits its own key
    Given a gateway "demo" with oauth github auth proxying a real MCP server
    And the principal "octocat" is allowed
    And an API key was issued for the gateway
    And the gateway also accepts API keys
    When a caller with the issued key lists the tools
    Then the backend tool "echo" is visible

  Scenario: Rotating a key does not demand an allowlist edit
    # Rotation mints a new key id, which is a new subject. If the allowlist
    # applied to keys, every rotation would silently lock the caller out.
    Given a gateway "demo" with oauth github auth proxying a real MCP server
    And the principal "octocat" is allowed
    And an API key was issued for the gateway
    And the gateway also accepts API keys
    When the operator rotates that key
    Then the rotated key lists the tools

  # One route can serve callers with very different trust levels: a key minted
  # for a bot, a person signed in through the IdP. A single tool allowlist for
  # the whole gateway forced one gateway per credential type — exactly the
  # duplication this feature removes. The scope is keyed by *source*, so a
  # narrower list can name the bot without touching the human's.
  Scenario: A credential source can have a narrower list than the gateway
    Given a gateway "demo" accepting tokens from a second authorization server
    And an API key was issued for the gateway
    And the gateway also accepts API keys
    When the operator allows only the tool "echo" for the source "api_key"
    And a caller presents a token issued by that authorization server
    Then the listed tools include "delete_repo"
    And a caller with the issued key sees only "echo"

  Scenario: A tool outside the source's list is refused even though the gateway allows it
    # The guard, not just the listing: a caller that remembered the name from
    # before the scope existed must get a sentence, not a 200.
    Given a gateway "demo" accepting tokens from a second authorization server
    And an API key was issued for the gateway
    And the gateway also accepts API keys
    When the operator allows only the tool "echo" for the source "api_key"
    Then calling "delete_repo" with the issued key is refused with 403

  Scenario: A source without a list inherits the gateway's
    Given a gateway "demo" accepting tokens from a second authorization server
    When the operator allows only the tool "echo"
    And a caller presents a token issued by that authorization server
    Then the listed tools are exactly "echo"

  Scenario: Clearing a source's list makes it inherit again
    Given a gateway "demo" accepting tokens from a second authorization server
    And an API key was issued for the gateway
    And the gateway also accepts API keys
    When the operator allows only the tool "echo" for the source "api_key"
    And the operator clears the list for the source "api_key"
    And a caller with the issued key lists the tools
    Then the backend tool "delete_repo" is visible

  Scenario: A list for a source the gateway does not have is refused
    # A typo would otherwise be a scope nobody matches, which reads as "it
    # saved" and silently changes nothing.
    Given a gateway "demo" with no auth
    When the operator allows only the tool "echo" for the source "corp"
    Then setting the allowlist fails with "unknown source"

  Scenario: Keys do not widen the allowlist for people
    Given a gateway "demo" with oauth github auth
    And the principal "octocat" is allowed
    And an API key was issued for the gateway
    And the gateway also accepts API keys
    When the identity "stranger" is resolved against the gateway
    Then the identity is refused
