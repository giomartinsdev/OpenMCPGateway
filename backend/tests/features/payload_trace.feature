Feature: What went in and what came back
  As an operator
  I want each traffic row to carry how much crossed it and a redacted preview
  So that "it returned something odd" is answered from the log instead of by
  replaying the call with curl

  Background:
    Given an operator "ops@example.dev" with password "correct-horse-battery"

  # ----------------------------------------------------------------- sizing
  # A row that says "200 in 12 ms" cannot distinguish a call that listed six
  # tools from one that listed six hundred. The two byte counts are what turn
  # the traffic page into a trace.

  Scenario: A call records how many bytes went in and came back
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key calls it once
    Then the newest observation reports bytes in and out

  # ---------------------------------------------------------------- previews
  # The preview exists to be read by a person debugging a session, so it keeps
  # the shape of the payload. It must not become the second place a credential
  # is stored: a tool argument named like a secret is replaced.

  Scenario: The request preview keeps the call and redacts the secret
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller sends a tool call carrying a token
    Then the observation's request preview names the tool
    And the observation's request preview does not contain the token

  Scenario: A preview longer than the cap is cut and marked
    # A truncated preview that looks complete is worse than no preview: the
    # reader would conclude the payload ended there.
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller sends a long but legal argument
    Then the observation's request preview is cut at the cap and says so

  # ------------------------------------------------------------ body ceiling
  # The guard buffers the body to inspect it, so an unbounded body is an
  # unbounded allocation in the one process every caller shares.

  Scenario: A body over the limit is refused before it reaches the upstream
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller sends a body larger than the limit
    Then the caller receives 413
    And the upstream server saw no request
    And the traffic log records outcome "forbidden" with status 413

  # ------------------------------------------------------------- caller IP
  # The forwarded header is written by whoever is talking to us. Unless the peer
  # is a proxy we named, it is a claim, not an address.

  Scenario: A forged forwarded-for header does not become the caller IP
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller spoofs the forwarded-for header as "203.0.113.9"
    Then the newest observation records the peer address, not "203.0.113.9"

  # ------------------------------------------------------------------- export
  # Same log, one file: a spreadsheet comparing input and output size per call
  # is the reason those columns exist.

  Scenario: The CSV carries both byte counts
    Given a gateway "demo" with api_key auth proxying a real MCP server
    And an API key was issued for the gateway
    When a caller with the issued key calls it once
    Then the traffic export has a bytes_in column and a bytes_out column
