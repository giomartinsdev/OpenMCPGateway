Feature: Signing in with a password cannot be guessed at
  As the operator of an installation
  I want repeated failed passwords to stop being worth trying
  So that the password form — the fallback when the identity provider is broken —
  is not the weakest door in the building

  Background:
    Given an operator "ops@example.dev" with password "correct-horse-battery"

  Scenario: A handful of wrong passwords is allowed, the next one is refused
    # Five is the number a person fumbling a password produces. A thousand is a
    # program, and the only thing politeness buys is the log line.
    When the caller signs in as "ops@example.dev" with a wrong password 5 times
    And the caller signs in as "ops@example.dev" with a wrong password
    Then the sign-in is refused with 429
    And the refusal asks to retry later

  Scenario: The right password is refused too while the throttle holds
    # Otherwise the throttle only slows the wrong answers, and the one guess
    # that matters still gets through.
    When the caller signs in as "ops@example.dev" with a wrong password 5 times
    And the caller signs in as "ops@example.dev" with the right password
    Then the sign-in is refused with 429

  Scenario: One address under attack does not lock out another
    Given an operator "other@example.dev" with password "correct-horse-battery"
    When the caller signs in as "ops@example.dev" with a wrong password 5 times
    And the caller signs in as "other@example.dev" with the right password
    Then the sign-in succeeds
