Feature: Declarative configuration
  As an operator
  I want to manage gateways from a YAML file
  So that my setup is reviewable and repeatable

  Scenario: A file creates gateways idempotently
    Given a YAML document:
      """
      gateways:
        - slug: demo
          name: Demo
          target_url: http://backend.test/mcp
          rate_limit: 30
          allow: [octocat]
      """
    When the document is applied
    Then gateway "demo" exists with limit 30
    When the same document is applied again
    Then nothing changed

  Scenario: A gateway missing from the file survives
    Given a YAML document creating "kept"
    When the file is applied
    And a document without "kept" is applied
    Then gateway "kept" still exists

  Scenario: Pruning removes what the file omits
    Given a YAML document creating "doomed"
    When the file is applied
    And a document without "doomed" is applied with prune enabled
    Then gateway "doomed" is gone

  Scenario: Unset environment variables are an error, not an empty secret
    Given a YAML document referencing "${NOT_SET_ANYWHERE}"
    Then parsing fails with a clear message

  Scenario: OAuth configuration requires the IdP credentials it needs
    Given a YAML document with oauth but no client_id
    Then applying fails because the gateway is incomplete

  Scenario: The upstream token travels through the config file
    # Secrets must come from the environment, never from the file itself.
    Given the environment "UP_TOKEN" is set to "up-secret"
    And a YAML document:
      """
      gateways:
        - slug: proxy
          target_url: http://127.0.0.1:1/mcp
          auth:
            mode: api_key
            upstream_token: ${UP_TOKEN}
      """
    When the document is applied
    Then the gateway "proxy" has an upstream token
