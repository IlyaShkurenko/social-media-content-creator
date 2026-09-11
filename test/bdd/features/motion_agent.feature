Feature: Agent-authored motion projects
  Scenario: [ADPIPE-3.3] A failed render is repaired without changing the user request
    Given a motion request for ten seconds and a renderer that fails once
    When the motion agent authors and renders the project
    Then the author receives the actual build error
    And both source attempts are retained under the same request
