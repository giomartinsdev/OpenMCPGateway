Feature: Cursor pagination and filtering on every list
  As an operator
  I want to ask for a page and narrow it by the column I am looking at
  So that a list of ten thousand rows is still something I can search

  # The two halves of one contract. A cursor without filters is a slow way to
  # scroll; filters without a cursor are a client-side `filter()` over whatever
  # the last page happened to contain — and the row you are looking for is the
  # one that did not fit.

  # Every tenant ships with one gateway of its own (the console), so the counts
  # below are always "the ones this scenario made, plus that one".
  Background:
    Given the owner registered

  Scenario: The gateway list answers with a cursor
    Given the owner has 3 gateways
    When the owner pages through the gateway list with a limit of 3
    Then the first page has 3 gateways
    And the first page offers a next cursor
    And the second page has 1 gateway
    And the second page offers no next cursor
    And the two pages are disjoint

  Scenario: A filter narrows the list on the server
    Given the owner has 3 gateways
    When the owner lists gateways filtered by name "rancher"
    Then the list has 1 gateway
    And the gateway is "rancher"

  Scenario: A text filter matches a fragment, not the whole value
    Given the owner has 3 gateways
    When the owner lists gateways filtered by name "anch"
    Then the list has 1 gateway

  Scenario: A filter nobody declared is refused, naming the ones that exist
    # Silently ignoring it would return every row while the reader believes the
    # filter applied, which is worse than a 422.
    Given the owner has 3 gateways
    When the owner lists gateways filtered by "outcame" "allowed"
    Then the filter is refused naming "outcame"

  Scenario: A filter by id finds a row whose id you only half remember
    Given the owner has 3 gateways
    When the owner lists gateways filtered by the first gateway's id
    Then the list has 1 gateway
    And the gateway is "rancher"

  Scenario: The gateway list sorts by name, both ways
    Given the owner has 3 gateways
    When the owner lists gateways sorted by name ascending
    Then the gateway names are in ascending order
    When the owner lists gateways sorted by name descending
    Then the gateway names are in descending order

  Scenario: A cursor is refused when the order changed under it
    # A cursor means "after this row, in this order". Silently reusing it under
    # another order pages through a different comparison and loses rows without
    # ever saying so.
    Given the owner has 3 gateways
    And the owner has a cursor from the gateway list sorted by name
    When the owner asks for the next page sorted by slug
    Then the cursor is refused

  Scenario: A sort nobody declared is refused, naming the ones that exist
    Given the owner has 3 gateways
    When the owner lists gateways sorted by "password"
    Then the sort is refused naming "password"

  Scenario: A cursor from another list is refused
    Given the owner has 3 gateways
    When the owner lists gateways with the cursor "not-a-cursor"
    Then the cursor is refused

  Scenario: The request log filters by outcome and by gateway name
    Given the owner has 3 gateways
    And 5 requests were recorded, 2 of them refused
    When the owner lists requests filtered by outcome "forbidden"
    Then the request list has 2 rows
    And every row is "forbidden"

  Scenario: The audit log filters by event type
    Given the owner has 3 gateways
    When the owner lists events filtered by type "gateway.created"
    Then the event list has at least 3 rows
    And every event is a "gateway.created"

  Scenario: The request log sorts by how slow the call was
    Given the owner has 3 gateways
    And 5 requests were recorded, 2 of them refused
    When the owner lists requests sorted by duration descending
    Then the request list has 5 rows

  Scenario: Members and roles page too
    Given the owner has 3 gateways
    And 3 members were added
    When the owner pages through the member list with a limit of 2
    Then the first member page has 2 members
    And the member page offers a next cursor

  Scenario: A new tenant gets a gateway to the deployment's own console
    When the owner lists gateways
    Then the list includes a gateway called "console"
    And its target is this deployment's console

  Scenario: A second tenant's console slug names the tenant, not a counter
    # A slug is global because `/g/<slug>/mcp` is a public URL, so the first
    # tenant gets `console` and everyone else needs a suffix. Counting
    # (`console-2`) would say nothing about whose gateway it is, and would move
    # if a tenant in between disappeared.
    Given another tenant is created by a different account
    Then its console slug is "console-" followed by its tenant id

  Scenario: A tenant that predates the seed gets one on the next boot
    # "The feature exists but mine is missing" is worse than no feature: a
    # deployment upgraded to this version has tenants that never saw `create`,
    # so no gateway row for the console exists at all.
    Given the owner has 3 gateways
    And the tenant's console gateway row is removed
    When the console backfill runs
    And the owner lists gateways
    Then the gateway list counts 4 gateways

  Scenario: A console gateway the owner deleted stays deleted
    # "Missing" has to mean missing, not soft-deleted: a backfill that resurrects a
    # gateway someone removed is a feature arguing with its owner.
    Given the owner has 3 gateways
    And the tenant's console gateway is deleted
    When the console backfill runs
    And the console backfill runs
    And the owner lists gateways
    Then the gateway list counts 3 gateways

  Scenario: A console token reads the deployment but cannot change it
    # The console MCP is what an agent plugs into; the token is the whole
    # boundary between "read the logs" and "operate the deployment".
    Given the owner has 3 gateways
    When the owner mints a console token
    Then the console token is returned once, with an expiry
    And the token can list the gateways
    And the token cannot create a gateway

  Scenario: The manual is data, served to the panel and to an agent
    When anyone reads the manual
    Then the manual has at least 10 sections
    And the console searches the manual for "oauth"

  Scenario: A token signed with another secret is refused
    When anyone presents a console token signed with another secret
    Then the console refuses the request
