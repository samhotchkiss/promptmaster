Description: Specialized bug fixing process
Trigger: when fixing bugs or debugging

# Bugfix Rule

1. Reproduce the bug yourself before changing code.
2. Add or identify a failing test that captures the bug.
3. Fix the bug with the smallest clear change.
4. Verify the fix with the failing test and a user-visible check.
5. Add regression coverage for nearby failure paths.
6. Audit the touched area for unintended regressions.
7. Run the broader relevant test suite before declaring done.
