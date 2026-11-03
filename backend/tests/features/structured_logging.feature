Feature: Logs a dashboard can query
  As the operator of a deployment
  I want application logs as structured JSON
  So that Grafana can alert on fields instead of grepping strings

  Scenario: A log line is one JSON object
    When the application logs an event named "gateway.installed"
    Then every line is valid JSON
    And the line has level "info" and event "gateway.installed"

  Scenario: Fields bound to a unit of work follow the lines
    When the application logs inside a request for gateway "rancher"
    Then both lines carry the gateway "rancher"
    And the field is gone after the request ends

  Scenario: A value that cannot be serialised does not break the caller
    When the application logs an event carrying an unserialisable value
    Then the call returns normally
    And the line still names the event "weird.value"

  Scenario: A field named like a logging attribute still reaches the line
    # `logging` raises `KeyError` for `name`, `msg` and friends, and the logger
    # suppresses it so a log call can never break a request. The line then simply
    # vanishes — a migration that ran with no record of it having run.
    When the application logs an event carrying a field called "name"
    Then the call returns normally
    And the line has a "name" field with the value "readmodels_schema"
