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

  Scenario: The request log scopes to one gateway by its id
    # The gateway's own page shows that gateway's calls. The `gateway` filter is
    # a substring match on slug and name — right for a search box, wrong for a
    # page scope, because "console" would also match "console-2".
    Given the owner has 3 gateways
    And 5 requests were recorded, 2 of them refused
    When the owner lists requests of the first gateway by its id
    Then the request list has 5 rows
    And every row belongs to that gateway

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

  Scenario: The console answers over HTTP, not just in-process
    # FastMCP's streamable-HTTP app needs its lifespan entered before it serves,
    # and `http_app()` returns a new object each call. Serving a different one was
    # a 500 on every request — and the seeded `console` gateway pointed at it, so
    # the "works on day one" gateway was dead on day one.
    Given the owner has 3 gateways
    When the owner mints a console token
    And an MCP client lists tools over HTTP with that token
    Then the console answers with its tool list

  Scenario: The manual is data, served to the panel and to an agent
    When anyone reads the manual
    Then the manual has at least 10 sections
    And the console searches the manual for "oauth"

  # The manual is the one page a stranger reads before signing in, in the
  # language they arrived in. It is fetched per language rather than translated
  # in the browser, so the paragraphs an agent searches are the paragraphs a
  # reader sees.
  Scenario: The manual is served in the reader's language
    When anyone reads the manual in "pt"
    Then the manual is served in "pt"
    And the manual is not the English one

  # A language tag carries a region; the manual is per language, so the region
  # is dropped rather than treated as an unknown code.
  Scenario Outline: A regional tag resolves to its language
    When anyone reads the manual in "<language>"
    Then the manual is served in "<resolved>"

    Examples:
      | language | resolved |
      | pt-BR    | pt       |
      | zh-CN    | zh       |
      | es-419   | es       |

  Scenario Outline: An unknown language falls back to English
    When anyone reads the manual in "<language>"
    Then the manual is served in "en"

    Examples:
      | language |
      | xx       |
      | klingon  |

  # A translated manual is only worth shipping if the console can search it:
  # both consumers read the same file, per language.
  Scenario: The console searches the translated manual
    When anyone reads the manual in "pt"
    Then the console searches the "pt" manual for "gateway"

  Scenario: A token signed with another secret is refused
    When anyone presents a console token signed with another secret
    Then the console refuses the request
