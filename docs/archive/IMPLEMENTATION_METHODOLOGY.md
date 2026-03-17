# Implementation Methodology

## Overview

This document describes the structured, iterative implementation approach used for sqlery development. The methodology emphasizes quality, rigorous testing, and intentional decision-making through a 5-step cycle with built-in review and validation mechanisms.

## The 5-Step Cycle

### Step 1: Design & Planning
- Define scope and requirements for the feature/fix
- Identify acceptance criteria
- Plan implementation approach and identify potential risks
- Document dependencies and integration points

### Step 2: Implementation
- Write code following project standards (Python 3.13+, modern type annotations)
- Ensure adherence to naming conventions and code quality guidelines
- Implement core functionality and supporting utilities
- Keep changes focused and avoid scope creep

### Step 3: Manual Testing & Validation
- Execute manual tests against acceptance criteria
- Test edge cases and error conditions
- Verify integration with existing systems
- Document test results and any issues found
- Perform manual adversarial testing to identify weaknesses

### Step 4: Automated Testing & Integration
- Write unit tests for critical paths and pure functions
- Create integration tests for end-to-end flows
- Run full test suite to ensure no regressions
- Achieve target test coverage for new code
- Verify CI/CD pipeline passes

### Step 5: Review Analysis & Correction
- Conduct adversarial review: challenge assumptions, identify edge cases
- Analyze patterns and potential improvements
- Determine what to add to PLAN vs what to implement now
- Document decisions, trade-offs, and future considerations
- Create checklist of follow-up items

## Adversarial Review Process

The adversarial review is a critical quality gate that happens after each step. It involves:

### Questioning Phase
- Challenge every design decision: "Why this approach?"
- Identify assumptions: "What if this assumption is wrong?"
- Search for edge cases: "What scenarios aren't covered?"
- Look for performance concerns: "Will this scale?"
- Examine error handling: "What happens when things fail?"

### Testing Phase
- Attempt to break the implementation with unconventional inputs
- Test boundary conditions and extreme values
- Verify error messages are clear and helpful
- Check resource usage and cleanup
- Validate thread safety and concurrency if applicable

### Analysis Phase
- Document findings in a structured format
- Prioritize issues by severity (critical vs. nice-to-have)
- Identify patterns that should be standardized
- Note technical debt or follow-up work

## Manual Testing Approach

Manual testing occurs in Step 3 and is performed by the implementing agent:

### Test Categories
1. **Happy Path**: Core functionality under normal conditions
2. **Error Handling**: Invalid inputs, missing data, permission issues
3. **Edge Cases**: Boundary values, empty collections, null references
4. **Integration**: Interaction with other system components
5. **Adversarial**: Intentional attempts to break or misuse the feature

### Test Documentation
Each manual test should document:
- Test scenario description
- Steps to reproduce
- Expected outcome
- Actual outcome
- Pass/Fail status
- Any anomalies or unexpected behaviors

## Automated Testing Approach

Automated testing occurs in Step 4 and focuses on:

### Test Scope
- **Unit Tests**: Pure functions in isolation, mocking external dependencies
- **Integration Tests**: Component interactions, database operations
- **Regression Tests**: Verify no existing functionality is broken
- **Coverage**: Minimum 80% for new code, critical paths at 90%+

### Testing Principles
- Test behavior, not implementation details
- Use descriptive test names that explain the scenario
- Keep tests independent and repeatable
- Mock external services and I/O operations
- Use fixtures and factories to reduce duplication

## Review Analysis & Correction Phase

After each step completes and passes automated testing:

### 1. Adversarial Analysis
Review the implementation critically:
- What assumptions were made that could be wrong?
- What edge cases remain unhandled?
- Where could performance degrade?
- What errors could go undetected?

### 2. Pattern Recognition
Identify opportunities for:
- Code standardization
- Reusable utilities or components
- Simplified error handling
- Improved maintainability

### 3. Decision Capture
For each finding, decide:

**Implement Now**: Critical fixes, security issues, blocking problems
- Add to implementation immediately
- Re-test after changes
- Document in commit message

**Add to PLAN**: Future enhancements, nice-to-haves, optimization opportunities
- Document with rationale
- Estimate effort and priority
- Link to issue tracker if applicable
- Include any architectural notes

### 4. Correction Guidance
Document three categories:

**What To Do**:
- Critical improvements needed before merge
- Security or correctness issues
- Necessary error handling

**What Not To Do**:
- Anti-patterns to avoid in future similar work
- Decisions that proved ineffective
- Technical debt to prevent

**End-of-Cycle Steps**:
- Final validation checks before considering complete
- Documentation updates needed
- Metrics to track or monitor

## Plan vs Implementation Decision Framework

### Criteria for Immediate Implementation
✓ Blocking critical functionality
✓ Security or data integrity issues
✓ Would cause test failures otherwise
✓ Trivial to implement (< 15 minutes)
✓ Directly required by acceptance criteria

### Criteria for Adding to PLAN
✓ Enhancement or optimization
✓ Requires significant additional work
✓ Depends on external decisions/approvals
✓ Can be deferred without impacting current feature
✓ Represents a larger feature or refactoring
✓ Needs architectural review

## Step Review Document Template

After completing each step, create a summary using this template:

```markdown
# Step [N]: [Step Title] Review

## Completion Status
- [x] Implementation Complete
- [x] All Tests Passing
- [ ] Deployment Ready (if applicable)

## What Was Accomplished
[Brief summary of work done]

## Test Results

### Manual Testing
- **Status**: PASS / FAIL
- **Test Cases Executed**: [Number]
- **Issues Found**: [List or "None"]

### Automated Testing
- **Status**: PASS / FAIL
- **Coverage**: [Percentage]
- **Failed Tests**: [List or "None"]

## Adversarial Review Findings

### Critical Issues (Implement Now)
- [ ] [Issue description with fix approach]

### Follow-up Items (Add to PLAN)
- [Issue description with rationale]

### Technical Observations
[Any patterns, anti-patterns, or lessons learned]

## Review Decisions

### What To Do
1. [Action item with priority]
2. [Action item with priority]

### What Not To Do
- [Anti-pattern to avoid]
- [Decision to reconsider in future]

### End-of-Cycle Steps
- [ ] [Final validation check]
- [ ] [Documentation update]
- [ ] [Metric to establish baseline]

## Ready for Next Step?
**YES / NO** - [Reason if NO]

## Notes
[Any additional context or decisions]
```

## Executive Summary Requirements

At the end of each implementation cycle (all 5 steps), provide an executive summary:

```markdown
# Implementation Summary: [Feature/Fix Name]

## Overview
[1-2 sentence description of what was built]

## Key Achievements
- [Accomplishment 1 with metrics if applicable]
- [Accomplishment 2 with metrics if applicable]
- [Accomplishment 3 with metrics if applicable]

## Test Coverage
- Manual Tests: [X scenarios, all passing]
- Automated Tests: [X% coverage, Y test cases]
- Adversarial Review: [Key issues identified and resolved]

## Quality Metrics
- Code Review: [APPROVED / PENDING]
- Test Coverage: [X%]
- Performance Impact: [Baseline → New measurement]
- Issues Found & Fixed: [X critical, Y minor]

## Items for Future Work (Added to PLAN)
- [Item 1: Brief description]
- [Item 2: Brief description]

## Lessons Learned
- [Pattern or observation]
- [Recommendation for future work]

## Deployment Status
- Ready for: [Environment/Branch]
- Prerequisites: [Any required setup or dependencies]
- Rollback Plan: [If applicable]
```

## Key Principles

1. **Stop After Each Step**: Progress is shown and validated before moving forward
2. **Adversarial Mindset**: Actively seek to break and criticize the implementation
3. **Clear Decision Making**: Explicitly decide what goes into current release vs. future work
4. **Documentation First**: Record findings and decisions immediately while fresh
5. **Quality Over Speed**: Testing and review add robustness that pays dividends long-term

## Integration with Development Workflow

- Each step corresponds to a phase that should be documented in commits/PRs
- Adversarial review findings inform commit messages and PR descriptions
- PLAN additions inform issue creation or milestone planning
- Manual tests inform test strategy documentation
- Step reviews provide context for code review discussions

---

**Last Updated**: 2025-11-05
**Version**: 1.0
**Status**: Active Methodology
