Feature: Creating or joining a tenant
  As someone who just signed up
  I want to create my own tenant or join one I was invited to
  So that I manage my own MCP connections without seeing anyone else's

  # A deployment used to be exactly one tenant, so signing up handed you the
  # installation. That works for a self-hosted panel for one team and fails the
  # moment a second person wants their own gateways: they either see everyone
  # else's or see nothing. A tenant is now something an account owns or joins.

  Scenario: A new account has no tenant yet
    When someone registers as "new@example.dev"
    Then the new account has no tenant
    And it is offered the choice of creating or joining a tenant

  Scenario: Creating a tenant makes that account its owner
    Given someone registered as "founder@example.dev"
    When the founder creates the tenant "Acme"
    Then the founder owns the tenant "Acme"
    And the founder holds the "role_owner" role

  Scenario: An owner gets a join code to hand out
    Given someone registered as "founder@example.dev"
    When the founder creates the tenant "Acme"
    Then the tenant has a join code the founder can share

  Scenario: A second account joins with the code and lands on the viewer role
    Given someone registered as "founder@example.dev"
    And the founder created the tenant "Acme"
    When someone registered as "teammate@example.dev" joins with the code
    Then the teammate belongs to the tenant "Acme"
    And the teammate holds the "role_viewer" role

  Scenario: A wrong join code is refused
    # The error must not say whether the code was close to a real one: that
    # turns guessing a code into a search.
    Given someone registered as "founder@example.dev"
    And the founder created the tenant "Acme"
    When someone registered as "stranger@example.dev" joins with the code "NOPE0000"
    Then the join is refused

  Scenario: Joining a tenant you are already in is refused
    Given someone registered as "founder@example.dev"
    And the founder created the tenant "Acme"
    And someone registered as "teammate@example.dev" joined the tenant "Acme"
    When the teammate joins the tenant "Acme" again
    Then the join is refused

  Scenario: An account without a tenant cannot touch gateways
    Given someone registered as "lone@example.dev"
    When the lone account creates a gateway
    Then the request is refused with "onboarding"

  Scenario: Two tenants cannot see each other's gateways
    # The whole point. A shared listing here is not a cosmetic bug: it is one
    # customer reading another's upstream URLs.
    Given someone registered as "alice@example.dev"
    And Alice created the tenant "Alice Co" with the gateway "alice-mcp"
    And someone registered as "bob@example.dev"
    And Bob created the tenant "Bob Co" with the gateway "bob-mcp"
    When Alice lists her gateways
    Then Alice sees only "alice-mcp"
    And Bob sees only "bob-mcp"

  Scenario: Two tenants cannot see each other's members
    Given someone registered as "alice@example.dev"
    And Alice created the tenant "Alice Co"
    And someone registered as "bob@example.dev"
    And Bob created the tenant "Bob Co"
    When Alice lists her members
    Then Alice sees only herself

  Scenario: Roles are per tenant
    # A role is a tenant's policy. Listing another tenant's roles leaks what
    # they are allowed to do, and editing one would change their access.
    Given someone registered as "alice@example.dev"
    And Alice created the tenant "Alice Co"
    And someone registered as "bob@example.dev"
    And Bob created the tenant "Bob Co"
    When Alice defines the role "Auditor" with "traffic:read"
    Then Bob's roles do not include "Auditor"

  Scenario: Another tenant's gateway cannot be read by id
    Given someone registered as "alice@example.dev"
    And Alice created the tenant "Alice Co" with the gateway "alice-mcp"
    And someone registered as "bob@example.dev"
    And Bob created the tenant "Bob Co"
    When Bob reads Alice's gateway by id
    Then the gateway is not found
