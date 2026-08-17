Feature: Video-quality experiment acceptance
  Candidate decisions must not claim more certainty than the evaluator has established.

  Scenario: [EVAL-1.2] Improved candidate with pending constraints requires review
    Given a candidate experiment improves its primary metric
    And one or more required goal constraints remain unverified
    When the experiment decision is recorded
    Then the decision is provisional and requires review

  Scenario: [EVAL-1.3] Verified improved candidate can be kept
    Given a candidate experiment improves its primary metric
    And all required goal constraints are verified
    When the experiment decision is recorded
    Then the decision keeps the candidate

  Scenario: [EVAL-2.4] A generic screen is judged against the declared scene policy
    Given a scene declares a non-product contextual screen
    And the observed screen is generic and does not claim tict identity
    When screen-policy compliance is calculated
    Then the scene screen policy passes

  Scenario: [EVAL-3.2] An experiment plan is frozen before candidate evaluation
    Given a reproduced comparable baseline
    When a new experiment is started with a problem, hypothesis, change, and expected impact
    Then the experiment is planned without candidate metrics
    And its engineering hypothesis and baseline evidence are frozen

  Scenario: [EVAL-3.3] A finished experiment retains its exact final MP4
    Given an evaluated experiment with a hash-matching local video artifact
    When final artifact retention is verified
    Then the experiment artifact is accepted as retained

  Scenario: [EVAL-4.2] Pairwise preference is balanced across reversed input order
    Given a baseline and candidate are judged in both A/B orders
    And the candidate is preferred in both judge passes
    When the order-balanced visual win rate is calculated
    Then the candidate visual win rate is 1.0

  Scenario: [EVAL-4.4] Visual preference cannot override timeline regression
    Given a candidate improves its visual judge primary metric
    And its timeline alignment regresses below the comparable baseline
    When the comparison-aware experiment decision is calculated
    Then the candidate is rejected for a constraint regression

  Scenario: [EVAL-5.3] A high-severity temporal hallucination vetoes a generated hook
    Given temporal evidence contains a high-severity screen visibility contradiction
    When temporal consistency is calculated
    Then temporal screening rejects the generated hook

  Scenario: [EVAL-6.2] Product-owner acceptance can override model preference
    Given a candidate has lower model preference but passes every enforced constraint
    And the product owner explicitly accepts the retained final video
    When the reviewed final decision is calculated
    Then the candidate is kept after human review

  Scenario: [EVAL-6.2] Product-owner acceptance cannot override an enforced failure
    Given a candidate has lower model preference and a failed enforced constraint
    And the product owner explicitly accepts the retained final video
    When the reviewed final decision is calculated
    Then the reviewed keep is rejected for a constraint regression
