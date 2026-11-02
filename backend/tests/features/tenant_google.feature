Feature: Joining a tenant by signing in with Google
  As someone who signs in with Google
  I want to reach my tenant without a second password
  So that the account I already have is enough

  Background:
    Given the installation has an administrator

  Scenario: An allowlisted address lands in the installation's tenant
    # The admin already exists, so this login must not create a second admin:
    # the tenant would then have two owners and no way to tell them apart.
    Given the provider will vouch for "member@example.dev"
    When the member starts a Google login
    Then the member is signed in
    And the member joins the installation's tenant
    And the member is not an administrator

  Scenario: An address outside the allowlist signs up with no tenant
    # Being able to sign in at the provider is permission to have an identity,
    # not permission to see anyone's gateways: the new account starts with no
    # tenant and is offered the choice like any other signup.
    Given the provider will vouch for "stranger@elsewhere.test"
    When the member starts a Google login
    Then the member is signed in
    And the member has no tenant
