# Multi-line Prompt Support Fixes - Applied 2026-02-07

## Summary

Successfully restored all multi-line prompt support and task execution fixes that were lost. These fixes enable proper handling of multi-line prompts in both interactive sessions and scheduled tasks.

## Fixes Applied

### 1. Codex Task Execution (`_run_codex_exec`)
**Location**: app.py lines 71-153
**Changes**:
- Changed from `subprocess.run` to `subprocess.Popen` with stdin pipe
- Prompt is now passed via stdin instead of command-line argument
- Added timeout handling with `communicate()`
- Returns mock `ProcResult` object for API compatibility

### 2. Codex Interactive Args (`_build_codex_args`)
**Location**: app.py lines 2651-2685
**Changes**:
- Function now returns tuple: `(args, prompt)` instead of just `args`
- Prompt is NO LONGER appended to args
- Prompt will be passed via stdin for multi-line support

### 3. Codex Interactive Job (`_start_codex_job`)
**Location**: app.py lines 2823-2840
**Changes**:
- Updated to unpack `(args, prompt)` from `_build_codex_args`
- Added `stdin=PIPE` to `subprocess.Popen`
- Writes prompt to stdin after process starts
- Closes stdin after writing

### 4. Copilot Interactive Job (`_start_copilot_job`)
**Location**: app.py lines 2931-2952
**Changes**:
- Removed `-p` flag from args
- Added `stdin=PIPE` to `subprocess.Popen`
- Writes prompt to stdin after process starts
- Closes stdin after writing

### 5. Copilot Task Execution (`_run_copilot_exec`)
**Location**: app.py lines 1543-1602
**Changes**:
- Changed from `subprocess.run` to `subprocess.Popen` with stdin pipe
- Removed `-p` flag from args
- Prompt is now passed via stdin instead of command-line argument
- Added timeout handling with `communicate()`
- Returns mock `ProcResult` object for API compatibility

### 6. Debug Message Filtering
**Location**: app.py lines 2613-2634
**Changes**:
- Added `_filter_debug_messages()` function to filter "Reading prompt from stdin"
- Updated `_enqueue_output()` to filter debug messages in interactive sessions
- Applied filtering in task execution for both Codex and Copilot

## Why These Fixes Were Needed

### Problem
Multi-line prompts passed as command-line arguments would fail or get corrupted due to shell escaping issues. Tasks would hang indefinitely when prompts contained newlines, quotes, or special characters.

### Root Cause
Two different execution paths existed:
1. **Interactive sessions** - Stream output in real-time
2. **Task execution** - Capture output and return result

Both were using command-line args for prompts, which breaks with multi-line text.

### Solution
Use stdin for ALL prompt input instead of command-line arguments. This:
- Eliminates shell escaping issues
- Supports multi-line prompts naturally
- Prevents task execution hangs
- Maintains consistent behavior across providers

## Testing Recommendations

1. **Test multi-line prompts in interactive sessions**:
   - Codex: Try prompts with newlines, quotes, special chars
   - Copilot: Same testing

2. **Test multi-line prompts in tasks**:
   - Create a task with multi-line prompt
   - Run manually and verify completion
   - Check output is correct

3. **Test debug message filtering**:
   - Verify "Reading prompt from stdin" doesn't appear in chat output
   - Check task outputs are clean

## Files Modified

- `app.py` - All changes in this single file

## Notes

- Claude task execution already correct (no --mcp-config flag found)
- Gemini uses command-line args (may need similar fix if multi-line issues occur)
- Debug filtering is case-insensitive for robustness
