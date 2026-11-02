Feature: Roles and permissions
  As the owner of a tenant
  I want to define roles and hand them out
  So that people get exactly the access their job needs

  Background:
    Given the owner registered

  Scenario: The owner starts with the owner role
    When the owner reads who it is
    Then the owner has the "role_owner" role
    And the owner may write settings

  Scenario: An administrator defines a role with a subset of permissions
    When the administrator defines the role "Auditor" with "traffic:read, audit:read"
    Then the role "Auditor" grants exactly "traffic:read, audit:read"

  Scenario: A permission outside the catalog is refused, naming it
    # A typo would otherwise be a role that grants nothing and says nothing; the
    # member finds out as a 403 on a button the panel offered.
    When the administrator defines the role "Typo" with "traffic:reed"
    Then defining the role fails naming "traffic:reed"

  Scenario: A member gets the viewer role by default
    When the administrator adds the member "member@example.dev"
    Then the member has the "role_viewer" role

  Scenario: A member with only read permissions cannot change anything
    Given the administrator added the member "member@example.dev"
    When the member tries to create a gateway
    Then the attempt is forbidden
    And the member may read traffic

  Scenario: An administrator can grant write access to one member
    Given the administrator defined the role "Operator" with "gateways:read, gateways:write"
    When the administrator adds the member "ops@example.dev" with the role "Operator"
    And that member creates a gateway
    Then the gateway exists

  Scenario: A member with members:write can add people but not define roles
    Given the administrator defined the role "Recruiter" with "members:read, members:write"
    When the administrator adds the member "hr@example.dev" with the role "Recruiter"
    And that member adds a new member "hire@example.dev" with no role
    Then the new member has the "role_viewer" role
    And that member cannot define a role

  Scenario: Changing a member's roles takes effect on the next sign-in
    Given the administrator added the member "member@example.dev"
    When the administrator gives that member the role "role_owner"
    And that member reads who it is
    Then the member may write settings

  Scenario: A built-in role cannot be deleted
    When the administrator deletes the role "role_owner"
    Then the deletion fails with a reason

  Scenario: A role in use cannot be deleted
    Given the administrator defined the role "Operator" with "gateways:write"
    And the administrator added the member "ops@example.dev" with the role "Operator"
    When the administrator deletes the role "Operator"
    Then the deletion fails with a reason

  Scenario: A member cannot grant more than it holds
    # Otherwise `members:write` is a path to `settings:write`: add an account
    # holding the owner role, sign in as it, done.
    Given the administrator defined the role "Recruiter" with "members:read, members:write"
    When the administrator adds the member "hr@example.dev" with the role "Recruiter"
    And that member adds a new member "hire@example.dev" with the role "role_owner"
    Then the attempt fails naming "you cannot grant"
