Feature: Gateway lifecycle
  As an operator
  I want to register MCP servers behind the gateway
  So that clients reach them through one authenticated entrypoint

  Background:
    Given an operator "ops@example.dev" with password "correct-horse-battery"

  Scenario: Registering a gateway exposes a proxied endpoint
    When the operator registers a gateway "demo" pointing at "http://backend.test/mcp"
    Then gateway "demo" is listed
    And its public endpoint is "https://gateway.test/g/demo/mcp"

  Scenario: Slugs are unique
    Given a gateway "demo" pointing at "http://backend.test/mcp"
    When the operator registers another gateway with slug "demo"
    Then the command is rejected because the slug is taken

  Scenario: Slugs must be URL safe
    When the operator attempts a gateway with slug "Demo Gateway!" at "http://backend.test/mcp"
    Then the command is rejected because the slug is invalid

  Scenario: A gateway must have a reachable URL shape
    When the operator attempts a gateway with slug "demo" at "file:///etc/passwd"
    Then the command is rejected because the target is not an http URL

  Scenario: Deleting a gateway frees its slug
    Given a gateway "demo" pointing at "http://backend.test/mcp"
    When the operator deletes gateway "demo"
    Then gateway "demo" is not listed
    And the slug "demo" can be reused
