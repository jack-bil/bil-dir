import argparse
import sys
import time
from playwright.sync_api import sync_playwright

DEFAULT_PROMPT = (
    "Write 6 paragraphs about why concurrency matters in AI tooling. "
    "Include a concrete example. End the last paragraph with the word done."
)


def main():
    parser = argparse.ArgumentParser(description="UI smoke test for session status and output.")
    parser.add_argument("--url", default="http://127.0.0.1:5025", help="Base URL of the app")
    parser.add_argument("--session", default="ui-check", help="Session name to use")
    parser.add_argument("--provider", default="gemini", choices=["codex", "copilot", "gemini"], help="Provider")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Prompt to send")
    parser.add_argument("--timeout", type=int, default=120, help="Timeout seconds")
    args = parser.parse_args()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{args.url}/chat/{args.session}")

        page.get_by_label("Provider").select_option(args.provider)
        page.get_by_role("textbox", name="Message").fill(args.prompt)
        page.get_by_role("button", name="Send").click()

        # Wait for status dot to indicate running.
        try:
            page.wait_for_function(
                """
                () => {
                  const dot = document.querySelector(`li[data-session-name='" + args.session + "'] .status-dot`);
                  return dot && dot.classList.contains('running');
                }
                """,
                timeout=10000,
            )
            status_running = True
        except Exception:
            status_running = False

        # Wait for send to be enabled again (done).
        page.wait_for_function(
            """
            () => {
              const btn = document.querySelector("button[type='submit']");
              return btn && !btn.disabled;
            }
            """,
            timeout=args.timeout * 1000,
        )

        # Read last assistant message.
        text = page.eval_on_selector(
            ".messages .msg.assistant:last-of-type",
            "el => (el ? el.textContent : '')",
        )
        trimmed = (text or "").strip()
        paragraphs = [p for p in (t.strip() for t in trimmed.split("\n\n")) if p]
        ends_with_done = trimmed.lower().endswith("done")

        browser.close()

    print("status_running=", status_running)
    print("paragraphs=", len(paragraphs))
    print("ends_with_done=", ends_with_done)

    if not status_running:
        print("FAIL: status dot did not enter running state")
        sys.exit(2)
    if len(paragraphs) != 6:
        print("FAIL: expected 6 paragraphs")
        sys.exit(3)
    if not ends_with_done:
        print("FAIL: response does not end with 'done'")
        sys.exit(4)


if __name__ == "__main__":
    main()
