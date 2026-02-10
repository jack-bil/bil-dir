"""Test orchestrator notification when session goes idle.

This test verifies that:
1. When a job completes and session goes idle
2. The orchestrator event processor is triggered
3. The orchestrator makes a decision
"""
import requests
import time
import json

BASE_URL = "http://localhost:5025"

def test_orchestrator_notification():
    print("=" * 60)
    print("Testing Orchestrator Notification on Session Idle")
    print("=" * 60)

    # Step 1: Create a test session
    print("\n[1] Creating test session...")
    session_name = f"test-orch-notify-{int(time.time())}"

    # Create session by sending a prompt (this will create the session record)
    # We'll use a very short prompt so it completes quickly
    resp = requests.post(f"{BASE_URL}/stream", json={
        "prompt": "Say 'Hello' and nothing else.",
        "session": session_name,
        "provider": "claude",
        "cwd": "C:/Users/jackb"
    })

    if resp.status_code != 200:
        print(f"[FAIL] Failed to create session: {resp.status_code}")
        return False

    print(f"[OK] Session created: {session_name}")

    # Wait for job to start
    time.sleep(2)

    # Step 2: Create orchestrator managing this session
    print("\n[2] Creating orchestrator...")
    orch_resp = requests.post(f"{BASE_URL}/orchestrators", json={
        "name": f"test-notifier-{int(time.time())}",
        "provider": "claude",
        "managed_sessions": [session_name],
        "goal": "Monitor the session and respond appropriately",
        "enabled": True
    })

    if orch_resp.status_code != 200:
        print(f"[FAIL] Failed to create orchestrator: {orch_resp.status_code}")
        print(orch_resp.text)
        return False

    orch_data = orch_resp.json()
    orch_id = orch_data["orchestrator"]["id"]
    orch_name = orch_data["orchestrator"]["name"]
    print(f"[OK] Orchestrator created: {orch_name} (ID: {orch_id})")

    # Step 3: Wait for the initial job to complete
    print("\n[3] Waiting for session job to complete...")
    max_wait = 60  # 60 seconds timeout
    start_time = time.time()

    while time.time() - start_time < max_wait:
        # Check session status
        sessions_resp = requests.get(f"{BASE_URL}/sessions")
        if sessions_resp.status_code == 200:
            sessions_data = sessions_resp.json()
            status_map = sessions_data.get("status", {})
            session_status = status_map.get(session_name, "unknown")

            print(f"  Session status: {session_status}")

            if session_status == "idle":
                print("[OK] Session went idle")
                break

        time.sleep(2)
    else:
        print(f"[FAIL] Timeout waiting for session to go idle")
        return False

    # Step 4: Give orchestrator event processor time to react
    print("\n[4] Waiting for orchestrator to process the idle event...")
    time.sleep(5)  # Give it 5 seconds to process

    # Step 5: Check if orchestrator made a decision
    print("\n[5] Checking orchestrator decision history...")
    orch_check_resp = requests.get(f"{BASE_URL}/orchestrators")
    if orch_check_resp.status_code != 200:
        print(f"[FAIL] Failed to get orchestrators: {orch_check_resp.status_code}")
        return False

    # Response is {"count": N, "orchestrators": [...]}
    orchestrators_data = orch_check_resp.json()
    orchestrators_list = orchestrators_data.get("orchestrators", [])
    orch = None
    for o in orchestrators_list:
        if o.get("id") == orch_id:
            orch = o
            break

    if not orch:
        print(f"[FAIL] Orchestrator not found")
        return False

    print(f"\nOrchestrator state:")
    print(f"  - Last action: {orch.get('last_action')}")
    print(f"  - Last decision at: {orch.get('last_decision_at')}")
    print(f"  - History entries: {len(orch.get('history', []))}")

    history = orch.get("history", [])
    if history:
        print(f"\n  Recent history:")
        for entry in history[-3:]:  # Show last 3 entries
            action = entry.get("action")
            at = entry.get("at", "unknown time")
            print(f"    - {action} at {at}")

    # Check if orchestrator made a decision after kickoff
    decision_made = False
    for entry in history:
        action = entry.get("action")
        if action in ("continue", "done", "ask_human"):
            decision_made = True
            print(f"\n[OK] Orchestrator made decision: {action}")
            break

    if not decision_made:
        print(f"\n[WARN]  Orchestrator did not make a decision yet")
        print(f"   This could mean:")
        print(f"   1. Session hasn't responded yet")
        print(f"   2. Orchestrator is still processing")
        print(f"   3. Event processor didn't trigger")

        # Check if there was a kickoff at least
        kickoff_found = False
        for entry in history:
            if entry.get("action") == "kickoff":
                kickoff_found = True
                print(f"\n[OK] Orchestrator did send kickoff")
                break

        if not kickoff_found:
            print(f"[FAIL] No kickoff found - orchestrator didn't activate")
            return False

    # Step 6: Clean up
    print("\n[6] Cleaning up...")
    try:
        # Delete orchestrator
        requests.delete(f"{BASE_URL}/orchestrators/{orch_id}")
        print(f"[OK] Deleted orchestrator")
    except Exception as e:
        print(f"[WARN]  Failed to delete orchestrator: {e}")

    print("\n" + "=" * 60)
    if decision_made:
        print("[PASS] TEST PASSED: Orchestrator was notified and made a decision")
    else:
        print("[WARN]  TEST PARTIAL: Orchestrator was notified but no decision yet")
    print("=" * 60)

    return decision_made


if __name__ == "__main__":
    try:
        success = test_orchestrator_notification()
        exit(0 if success else 1)
    except Exception as e:
        print(f"\n[FAIL] Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
