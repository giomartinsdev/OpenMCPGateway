Feature: Calling a tool from the panel

  As an operator
  I want to run one tool with arguments I typed, through the gateway itself
  So that "does this tool work" is answered by a call, not by asking someone to
  paste a curl command into Slack

  Background:
    Given an operator "play@example.dev" with password "correct-horse-battery"

  # The call goes through `/g/<slug>/mcp`, not straight to the target: it must
  # pay the rate limit, the allowlist and the audit that a real client pays. A
  # playground with its own path is a second, unguarded way into a production
  # server.

  Scenario: Calling an allowed tool returns its result
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the operator calls tool "echo" with arguments {"text": "hello"}
    Then the call succeeds
    And the result contains "hello"

  Scenario: Calling a tool outside the allowlist is refused by the guard
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And the allowlist exposes only "nothing_like_this"
    And an API key was issued for the gateway
    When the operator calls tool "echo" with arguments {"text": "hello"}
    Then the call is refused as forbidden
    And the refusal names the tool allowlist

  Scenario: Calling a tool on a gateway with no live key is refused
    # The playground uses a key the gateway already has, so an operator without
    # `keys:write` cannot use it to mint one. No key, no call, and the message
    # says why instead of a bare 401.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    When the operator calls tool "echo" with arguments {"text": "hello"}
    Then the call is refused as unauthenticated

  Scenario: Calling a tool that does not exist reports the upstream's answer
    # The error belongs to the upstream and is passed through: pretending the
    # gateway does not know the name would hide a misconfigured target_url.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the operator calls tool "no_such_tool" with arguments {}
    Then the call fails
    And the failure mentions "no_such_tool"

  Scenario: Malformed arguments are refused before the upstream sees them
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the operator calls tool "echo" with arguments "not-json"
    Then the answer is 422

  Scenario: The playground call shows up in the traffic log
    # If it did not, an operator debugging at 03:00 would see a mystery call
    # from the panel's own address in the log and no way to explain it.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When the operator calls tool "echo" with arguments {"text": "hello"}
    Then the traffic log records a call to tool "echo"
