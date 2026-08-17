Feature: Controlled mixed-media advertising production
  Paid creative generation must follow a validated storyboard and a shared iteration budget.

  Scenario: [STORY-1.2] Invalid scene timing is rejected before execution planning
    Given an English advertising storyboard with overlapping scenes
    When comparison execution plans are requested
    Then storyboard preflight is rejected

  Scenario: [STORY-1.3] The first slice resolves an English narration voice
    Given the first advertising slice has no selected voice
    And the interface locale is Russian
    When narration settings are resolved
    Then the content language is en-US
    And the narration voice is compatible with en-US

  Scenario: [ADPIPE-1.2] Stock and Runway plans change only the hook source
    Given a valid three-scene English advertising storyboard
    When comparison execution plans are requested
    Then the baseline hook uses stock media
    And the candidate hook uses Runway generated media
    And all controlled scene intent remains equivalent

  Scenario: [RUNWAY-1.2] The shared iteration budget blocks overspend
    Given a ten dollar iteration budget with nine dollars and eighty cents charged
    When a sixty cent generation is reserved
    Then the reservation is rejected before provider submission
