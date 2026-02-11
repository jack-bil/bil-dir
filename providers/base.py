"""Provider shared helpers."""
import queue


def _filter_debug_messages(text):
    """Filter out debug messages from CLI output."""
    if not text:
        return text
    lines = text.split("\n")
    filtered_lines = [
        line
        for line in lines
        if "reading prompt from stdin" not in line.lower()
        and "codex_core::rollout::list: state db missing rollout path for thread" not in line
    ]
    return "\n".join(filtered_lines)


def _enqueue_output(pipe, q, label):
    """Enqueue output lines while filtering debug messages."""
    for line in iter(pipe.readline, ""):
        if "reading prompt from stdin" not in line.lower():
            q.put((label, line))
    pipe.close()
