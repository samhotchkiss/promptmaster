# 0001 Account Home Recovery And Duplicate Add Fix

## Goal

Fix the case where deleting an account from config still leaves a stale isolated home behind, which then blocks re-adding the same account later.

## Completed

- `add_account_via_login()` now distinguishes between:
  - a true duplicate account already present in config
  - an orphaned existing home that matches the same email and can be reused
  - a stale/broken orphaned home that should be replaced by the fresh login
- Cleaned orphaned Claude homes from the current repo state.
- Added automated tests covering reuse, replacement, and duplicate rejection.

## Validation

- Automated tests cover the duplicate/orphan cases.
- The repo’s stale ghost Claude home was removed.
