Feature: Tenants and their members
  As the owner of a tenant
  I want the people I add to land in that same tenant
  So that we share gateways but only I can change them

  Background:
    Given an owner registered and created a tenant

  Scenario: The account that creates the tenant owns it
    When the owner reads who it is
    Then the owner is the tenant's administrator

  Scenario: An account an administrator adds joins the same tenant
    # The other way in, besides the join code: an owner adds a colleague
    # directly, with the roles chosen up front.
    When the owner reads who it is
    And the administrator adds the member "member@example.dev"
    Then both accounts belong to the same tenant
    And the member is not an administrator

  Scenario: A member reads the tenant's gateways
    Given the administrator created a gateway "demo"
    When the administrator adds the member "member@example.dev"
    Then the member can list the tenant's gateways

  Scenario: A member cannot change the tenant's gateways
    # Read-only is enforced on the server: the panel hides the buttons, but a
    # hidden button is not an authorisation rule.
    Given the administrator created a gateway "demo"
    When the administrator adds the member "member@example.dev"
    Then the member cannot create a gateway
    And the member cannot read the tenant's sign-in settings
    And the member cannot configure a gateway

  Scenario: An administrator sees who belongs to the tenant
    When the administrator adds the member "member@example.dev"
    Then the tenant lists both accounts, with their roles
