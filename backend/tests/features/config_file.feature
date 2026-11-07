Feature: Declarative configuration
  As an operator
  I want to manage gateways from a YAML file
  So that my setup is reviewable and repeatable

  Scenario: A file creates gateways idempotently
    Given a YAML document:
      """
      gateways:
        - slug: demo
          name: Demo
          target_url: http://backend.test/mcp
          rate_limit: 30
      """
    When the document is applied
    Then gateway "demo" exists with limit 30
    When the same document is applied again
    Then nothing changed

  Scenario: A gateway missing from the file survives
    Given a YAML document creating "kept"
    When the file is applied
    And a document without "kept" is applied
    Then gateway "kept" still exists

  Scenario: Pruning removes what the file omits
    Given a YAML document creating "doomed"
    When the file is applied
    And a document without "doomed" is applied with prune enabled
    Then gateway "doomed" is gone

  Scenario: Unset environment variables are an error, not an empty secret
    Given a YAML document referencing "${NOT_SET_ANYWHERE}"
    Then parsing fails with a clear message

  Scenario: OAuth configuration requires the IdP credentials it needs
    Given a YAML document with oauth but no client_id
    Then applying fails because the gateway is incomplete

  Scenario: A file defines a role and it grants what it says
    Given a YAML document:
      """
      roles:
        - name: Auditor
          permissions: [traffic:read, audit:read]
      members:
        - email: auditor@example.dev
          password: ${MEMBER_PASSWORD:-member-password-1234}
          roles: [Auditor]
      """
    When the document is applied
    Then the role "Auditor" exists with permissions "audit:read, traffic:read"
    And the member "auditor@example.dev" has the role "Auditor"

  Scenario: Reapplying the same file changes nothing
    Given a YAML document:
      """
      roles:
        - name: Auditor
          permissions: [traffic:read]
      members:
        - email: auditor2@example.dev
          password: ${MEMBER_PASSWORD:-member-password-1234}
          roles: [Auditor]
      """
    When the document is applied
    And the same document is applied again
    Then nothing changed

  Scenario: Editing a role in the file updates the role in place
    Given a YAML document:
      """
      roles:
        - name: Auditor
          permissions: [traffic:read]
      """
    When the file is applied
    And the file is applied again with:
      """
      roles:
        - name: Auditor
          permissions: [traffic:read, audit:read]
      """
    Then the role "Auditor" exists with permissions "audit:read, traffic:read"

  Scenario: A member may not be created without a password
    # Inventing one would be a credential nobody can sign in with, and a blank
    # one is worse. The file has to say where the password comes from — but only
    # for an account that does not exist yet, so an export (which never carries a
    # password) still applies cleanly.
    Given a YAML document:
      """
      members:
        - email: nobody@example.dev
          roles: [viewer]
      """
    When the document is applied
    Then applying fails naming "password"

  Scenario: A member naming a role that does not exist is refused
    Given a YAML document:
      """
      members:
        - email: ghost@example.dev
          password: ${MEMBER_PASSWORD:-member-password-1234}
          roles: [DoesNotExist]
      """
    When the document is applied
    Then applying fails naming "DoesNotExist"

  Scenario: A built-in role cannot be redefined by the file
    # `owner` and `viewer` are code, so the file can name them but not change
    # them: a file that redefined `viewer` would narrow every existing member
    # without touching a single member row.
    Given a YAML document:
      """
      roles:
        - name: viewer
          permissions: [gateways:read, roles:write]
      """
    When the document is applied
    Then applying fails naming "built-in"

  Scenario: Members left out of the file survive
    Given a YAML document:
      """
      members:
        - email: stays@example.dev
          password: ${MEMBER_PASSWORD:-member-password-1234}
          roles: [viewer]
      """
    When the file is applied
    And a document without members is applied with prune enabled
    Then the member "stays@example.dev" still exists

  Scenario: A role outside the caller's own permissions is refused
    # Same rule as the panel: config:write must not be a path to role_owner.
    Given a YAML document:
      """
      roles:
        - name: Escalation
          permissions: [roles:write]
      """
    When the document is applied by an account without "roles:write"
    Then applying fails naming "cannot grant"

  Scenario: The export round-trips through the parser
    # The export is the half of config-as-code that people trust without reading:
    # if it stops parsing, the file is a dead end that only fails later.
    Given a YAML document:
      """
      roles:
        - name: Auditor
          permissions: [traffic:read, audit:read]
      members:
        - email: round@example.dev
          password: ${MEMBER_PASSWORD:-member-password-1234}
          roles: [Auditor]
      """
    When the document is applied
    And the tenant is exported and applied again
    Then nothing changed
    And the exported document declares role "Auditor"

  Scenario: The upstream token travels through the config file
    # Secrets must come from the environment, never from the file itself.
    Given the environment "UP_TOKEN" is set to "up-secret"
    And a YAML document:
      """
      gateways:
        - slug: proxy
          target_url: http://127.0.0.1:1/mcp
          auth:
            mode: api_key
            upstream_token: ${UP_TOKEN}
      """
    When the document is applied
    Then the gateway "proxy" has an upstream token

  Scenario: Environment expansion outside a credential field is refused
    # The document is echoed back by /config/validate, so anything expanded
    # outside a credential turned that screen into a way to read the process
    # environment — `slug: ${OMG_SESSION_SECRET}` came back as text.
    Given a YAML document with a placeholder in the gateway "name"
    Then parsing fails naming "gateways[0].name"
