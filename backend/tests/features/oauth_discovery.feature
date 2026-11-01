Feature: OAuth discovery
  As an MCP client
  I want to discover how to authenticate
  So that I can log in with GitHub instead of being handed a static token

  Background:
    Given an operator "ops@example.dev" with password "correct-horse-battery"

  Scenario: An unauthenticated OAuth caller is told where the metadata lives
    Given a gateway "demo" with oauth github auth
    When an anonymous caller probes the gateway
    Then the caller receives 401
    And the challenge names a resource metadata URL

  Scenario: The advertised metadata is actually served at that URL
    Given a gateway "demo" with oauth github auth
    When an anonymous caller probes the gateway
    And the client follows the discovery chain
    Then the protected resource metadata names the gateway endpoint
    And the authorization server metadata names the per-gateway issuer
    And the authorization server metadata advertises a registration endpoint

  Scenario: Discovery lives at the root, not under the gateway prefix
    Given a gateway "demo" with oauth github auth
    When an anonymous caller probes the gateway
    And the client follows the discovery chain
    Then the protected resource document is served from the root path
    And it is not served beneath the gateway prefix

  Scenario: An API-key gateway exposes no OAuth discovery
    Given a gateway "demo" with api_key auth
    When an anonymous caller probes the gateway
    Then the caller receives 401
    And the challenge names no resource metadata URL
    And the authorization server document is not served

  Scenario: Required scopes are advertised as a list, not as characters
    # The scopes column is jsonb. Without decoding, the string "[]" reached the
    # metadata and was iterated per character, so clients were told to request
    # the scopes "[" and "]".
    Given a gateway "demo" with oauth github auth requiring the scope "read:user"
    When an anonymous caller probes the gateway
    And the client follows the discovery chain
    Then the protected resource metadata advertises exactly the scope "read:user"
