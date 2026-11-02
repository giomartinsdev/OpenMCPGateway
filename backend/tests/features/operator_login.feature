Feature: Operator login with Google
  As an operator
  I want to sign in to the admin UI with my Google account
  So that I do not have to hold a separate password

  Background:
    Given Google sign-in is configured for the gateway

  Scenario: Starting the login sends the browser to Google
    When an operator starts a Google login
    Then the browser is redirected to the identity provider
    And the redirect carries the gateway's callback URL
    And the redirect carries a state value

  Scenario: Returning from Google signs the operator in
    Given the identity provider will vouch for "ops@example.dev"
    When the operator completes a Google login
    Then the operator has a session
    And the session belongs to "ops@example.dev"

  Scenario: A callback that did not start the same login is refused
    # The state value is the only thing binding the callback to a browser the
    # gateway itself started. Without it an attacker can hand a victim a code
    # that lands in the attacker's session.
    Given the identity provider will vouch for "ops@example.dev"
    When a callback arrives with a state the gateway never issued
    Then the callback is refused
    And the operator has no session

  Scenario: A token minted for another application is refused
    # Checking the signature is not enough: Google signs tokens for every
    # client in the project, so a missing audience check accepts a token meant
    # for someone else.
    Given the identity provider will vouch for "ops@example.dev"
    And the identity provider mints tokens for another application
    When the operator completes a Google login
    Then the callback is refused
    And the operator has no session

  Scenario: An unverified address cannot become an operator
    Given the identity provider will vouch for "ops@example.dev"
    And the identity provider reports the address as unverified
    When the operator completes a Google login
    Then the callback is refused
    And the operator has no session

  Scenario: A stranger with a valid Google account is refused
    Given the identity provider will vouch for "stranger@elsewhere.test"
    When the operator completes a Google login
    Then the callback is refused
    And the operator has no session

  Scenario: Signing in twice reuses the same operator
    Given the identity provider will vouch for "ops@example.dev"
    When the operator completes a Google login
    And the operator completes another Google login
    Then the operator has a session
    And the operator was not duplicated

  Scenario: Only the configured addresses may be created
    Given the identity provider will vouch for "ops@example.dev"
    When the operator completes a Google login
    Then the operator has a session
    And "ops@example.dev" was created by the allowlist

  Scenario: Sign-in uses the client saved in the panel, not the environment
    # The panel is the source of truth after boot. If the environment kept
    # winning, setting it in the UI would appear to work and change nothing.
    When an administrator saves the panel client for the fake provider
    And an operator starts a Google login
    Then the redirect carries the panel's client id
