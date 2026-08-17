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

  Scenario: [STORY-1.5] A planning-frustration hook permits a non-product phone screen
    Given a hook storyboard declaring a non-product contextual screen
    When the Runway hook request is compiled
    Then the generated hook may show a generic phone screen
    And the generated hook must not claim that screen is tict UI

  Scenario: [BRAND-1.4] Brand subtitles and synthesis pronunciation remain separate
    Given a storyboard with lowercase tict copy and its tickt pronunciation
    When narration settings are resolved from the storyboard
    Then the subtitle narration retains lowercase tict
    And the synthesis narration uses the tickt pronunciation

  Scenario: [RUNWAY-2.2] Only temporally passing generated hooks are selectable
    Given three generated hooks with one passing temporal screen
    When generated hook selection is requested
    Then only the passing generated hook is eligible

  Scenario: [RUNWAY-2.2] A confirmed temporal false positive can be selected
    Given a generated hook with a reviewed false-positive temporal event
    When generated hook selection is requested
    Then the reviewed hook is eligible
