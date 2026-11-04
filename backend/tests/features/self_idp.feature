Feature: The deployment as its own identity provider
  As an operator
  I want SSO on a gateway with nothing registered anywhere
  So that adding a gateway costs no per-slug callback URI

  The gateway is the authorization server here, and the human step is the
  operator session the panel already issues. There is no upstream to be lenient
  for us, so the negatives below matter as much as the happy path.

  Background:
    Given a gateway "demo" whose identity provider is this deployment
    And an operator signed in to the panel as "ops@example.dev"

  Scenario: A signed-in operator earns a token the gateway accepts
    When the client registers, is sent to consent, and approves
    Then the token endpoint issues an access token
    And the gateway accepts that token as "ops@example.dev"

  Scenario: A browser without a session is sent to the panel
    Given the operator is not signed in
    When the client registers and reaches the consent screen
    Then the browser is redirected to the panel's login
    And no code is issued

  Scenario: Denying the request returns access_denied
    When the client registers, is sent to consent, and denies
    Then the client is redirected with error "access_denied"
    And no code is issued

  Scenario: A forged consent form is refused
    # The form is signed per transaction; a page from somewhere else cannot
    # approve a request it did not render.
    When the client registers, is sent to consent, and posts a forged CSRF token
    Then the consent screen refuses the submission
    And no code is issued

  Scenario: An unknown transaction is refused
    When the browser opens a consent screen for a transaction that does not exist
    Then the consent screen refuses the submission

  Scenario: A code is spent once
    # Single use is the whole point of a code: a replay must not mint a second
    # token, even if the two token requests arrive together.
    When the client completes the flow and exchanges the code twice
    Then the second exchange is refused

  Scenario: A token from another gateway is refused
    # Same deployment, same signing material, different audience.
    Given a second gateway "other" whose identity provider is this deployment
    When the client earns a token on "demo"
    Then "demo" accepts the token
    And "other" refuses it

  Scenario: A resource for another gateway is refused at authorize
    # RFC 8707: asking for someone else's audience dies before consent, not
    # after the token is minted.
    When the client asks to authorize for another gateway's resource
    Then the authorization request is refused
    And no code is issued

  Scenario: An expired token is refused
    When the gateway is shown a token that expired
    Then the gateway refuses it

  Scenario: A refresh token renews the same subject
    When the client completes the flow and refreshes
    Then the refreshed token is accepted as "ops@example.dev"
