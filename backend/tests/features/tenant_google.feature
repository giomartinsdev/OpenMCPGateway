Feature: Joining a tenant by signing in with Google
  As someone an administrator invited
  I want to sign in with my own Google account
  So that I reach the tenant without a second password

  Background:
    Given the installation has an administrator

  Scenario: An allowlisted address joins the tenant as a member
    # The admin already exists, so this login must not create a second admin:
    # the tenant would then have two owners and no way to tell them apart.
    Given the provider will vouch for "member@example.dev"
    When the member starts a Google login
    Then the member is signed in
    And the member joins the installation's tenant
    And the member is not an administrator

  Scenario: An address outside the allowlist is still refused
    # Being able to sign in at the provider is not permission to join a tenant.
    Given the provider will vouch for "stranger@elsewhere.test"
    When the member starts a Google login
    Then the login is refused with a reason
