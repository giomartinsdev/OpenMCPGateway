Feature: Copy-paste instructions for testing a gateway
  As an operator
  I want the exact requests to call my gateway
  So that I can prove the proxy works without guessing the auth steps

  # The panel shows this next to each gateway. The OAuth path is the one that
  # needs help: a 401 is the first step of a five-request flow, not a failure,
  # and a snippet that only shows the last request teaches the wrong thing.

  Scenario: A public gateway needs one request
    Given an owner with a public gateway "open-mcp"
    When the owner reads the connect guide for "open-mcp"
    Then the guide offers 1 curl step
    And the curl step posts an MCP initialize to the gateway endpoint
    And the guide offers a python step

  Scenario: An API-key gateway shows the bearer header
    Given an owner with an api_key gateway "keyed-mcp"
    When the owner reads the connect guide for "keyed-mcp"
    Then the guide offers 1 curl step
    And the curl step sends a bearer token
    # The plaintext is shown once at issue time and never stored, so the guide
    # must not pretend to have it.
    And the curl step does not contain "sk_"

  Scenario: An OAuth gateway shows the whole token path
    Given an owner with an oauth github gateway "oauth-mcp"
    When the owner reads the connect guide for "oauth-mcp"
    Then the guide offers 6 curl steps
    And the steps start with an unauthenticated challenge
    And a step fetches the protected resource metadata
    And a step registers a client
    And a step exchanges the code for a token
    And the last step sends a bearer token
    And the python guide runs the flow through the SDK

  Scenario: Another tenant's gateway has no guide
    # The guide names upstream URLs and scopes; it is not public information.
    Given an owner with a public gateway "private-mcp"
    And someone else in another tenant
    When the other tenant reads the connect guide for "private-mcp"
    Then the guide request is not found
