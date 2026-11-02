Feature: Upgrading a running installation to tenancy
  As someone redeploying a gateway that has been running since before tenants
  I want the existing accounts and gateways to keep working
  So that shipping multi-tenancy does not strand my installation

  # Upgrading does not replay the log: the checkpoint says those events were
  # already projected, so the new tenant column arrives empty. Accounts written
  # before tenants have no `tenant_id` key in the log at all, while a new signup
  # stores `null` on purpose — that difference is what tells the backfill which
  # rows to adopt.

  Scenario: An account written before tenants is adopted on boot
    Given an account from before tenants existed
    When the container boots again
    Then that row is in the installation's tenant
    And the installation's tenant has a join code

  Scenario: An adopted account signs in to the panel it always had
    Given an account from before tenants existed
    And a gateway created before tenants existed
    When the container boots again
    And that account signs in
    Then it can sign in without being asked to onboard
    And it is listed for the installation's tenant

  Scenario: A new signup is not swept into the installation's tenant
    # Signing up is how someone deliberately gets an account with no tenant.
    # A backfill that adopts them would silently undo that choice on every boot.
    Given an account from before tenants existed
    When a new signup registers
    And the container boots again
    Then a new signup is still without a tenant
