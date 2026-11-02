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
