Feature: Configuring admin sign-in from the panel
  As an administrator
  I want to set the Google client for the panel in the UI
  So that I do not have to redeploy with new environment variables

  Scenario: Sign-in is off until an administrator configures it
    Given an administrator "ops@example.dev" with password "correct-horse-battery"
    When the administrator reads the sign-in settings
    Then sign-in with Google is off

  Scenario: An administrator sets the client and sign-in turns on
    Given an administrator "ops@example.dev" with password "correct-horse-battery"
    When the administrator saves a sign-in client
    Then sign-in with Google is on

  Scenario: The secret never comes back out
    # The panel shows enough to recognise a client, never enough to impersonate
    # one. A secret that round-trips to the browser ends up in a screenshot, a
    # HAR file and an issue report.
    Given an administrator "ops@example.dev" with password "correct-horse-battery"
    When the administrator saves a sign-in client
    And the administrator reads the sign-in settings
    Then the settings expose the callback and the client id
    And the settings do not expose the secret

  Scenario: Saving again without typing the secret keeps the stored one
    # Otherwise every unrelated edit — changing the callback, or the allowlist —
    # silently breaks sign-in until someone notices the button stopped working.
    Given an administrator "ops@example.dev" with password "correct-horse-battery"
    When the administrator saves a sign-in client
    And the administrator saves the same client with no secret
    Then sign-in with Google is on

  Scenario: The allowlist decides who may become an operator
    Given an administrator "ops@example.dev" with password "correct-horse-battery"
    When the administrator saves a sign-in client allowing two addresses
    And the administrator reads the sign-in settings
    Then the allowlist holds both addresses

  Scenario: A missing callback is refused, not guessed
    # A guessed callback is a redirect URI mismatch at the provider, which is the
    # slowest possible failure to diagnose: the error arrives from Google, in a
    # browser, with no mention of this panel.
    Given an administrator "ops@example.dev" with password "correct-horse-battery"
    When the administrator saves a sign-in client with no callback
    Then the settings are refused with a message naming the callback

  Scenario: A non-administrator cannot configure sign-in
    Given an administrator "ops@example.dev" with password "correct-horse-battery"
    And an operator "plain@example.dev" with password "correct-horse-battery"
    When a non-administrator tries to save a sign-in client
    Then the attempt is forbidden
    And sign-in with Google is off
