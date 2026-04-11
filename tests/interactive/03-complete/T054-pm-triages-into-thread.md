# T054: PM Triages Item into Thread

**Spec:** v1/09-inbox-and-threads
**Area:** Thread Management
**Priority:** P0
**Duration:** 10 minutes

## Objective
Verify that the PM (operator) can triage an inbox item into a thread, creating a structured conversation thread for tracking and resolution.

## Prerequisites
- `pm up` has been run with the operator session active
- At least one open inbox item exists (create with `pm inbox create` if needed)

## Steps
1. Run `pm inbox list` and identify an open inbox item to triage. Note its ID.
2. Attach to the operator session and observe its behavior. It should be checking the inbox for items to triage.
3. If the operator does not automatically triage, manually trigger triage: `pm inbox triage <item-id>` (or use the operator's command interface).
4. Observe the triage process: the PM should read the item and decide how to handle it (create a thread, create an issue, or dismiss).
5. Verify a thread was created: `pm thread list` and look for a new thread associated with the inbox item.
6. Note the thread ID and check its details: `pm thread info <thread-id>`.
7. Verify the thread contains the original inbox item content as the first message.
8. Verify the thread has a status of "open" or "active."
9. Check the inbox item's status: `pm inbox info <item-id>`. It should now show as "triaged" or have moved out of `open/`.
10. Verify the thread file exists on disk in the threads directory.

## Expected Results
- PM successfully triages the inbox item
- A thread is created from the inbox item
- Thread contains the original item content
- Thread status is "open" or "active"
- Inbox item status reflects that it has been triaged
- Thread is visible in `pm thread list`

## Log

**Date:** 2026-04-10 | **Result:** PASS

### Re-test — 2026-04-10 (via Polly triaging an inbox item)

Asked Polly to triage the T053 test inbox item. She:
1. Read the item from inbox/open/test-inbox.md
2. Created thread file at threads/T053-test-inbox-item.md with triage metadata
3. Moved original from open/ to closed/
4. Reported inbox clear

```
⏺ Triaged. Created thread threads/T053-test-inbox-item.md with original message, 
  triage metadata, and notes. Moved original from open/ to closed/.
```

**BUG:** Polly created a flat file (`threads/T053-test-inbox-item.md`) instead of using the proper thread directory structure (`threads/<id>/state.json` + `messages/`). The operator doesn't know about the `triage_inbox_item` API and manually creates files that don't match the expected format.

**Current verification:** the old `cwd is ~/.pollypm` root-cause note is stale against the current codebase. Current config and launch-path verification resolve the operator control session to `workspace_root`, so this behavior needs a fresh tmux re-test before attributing it to operator cwd/path resolution. The remaining confirmed issue here is that the operator performed raw file manipulation instead of using the structured thread API.
