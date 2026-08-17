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
