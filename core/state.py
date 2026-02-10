"""Shared mutable state and locks."""
import threading

_SESSION_LOCK = threading.RLock()
_JOB_LOCK = threading.Lock()
_TASK_LOCK = threading.RLock()
_ORCH_LOCK = threading.RLock()
_PENDING_LOCK = threading.RLock()

_SESSION_STATUS = {}
_JOBS = {}
_SESSION_SUBSCRIBERS = set()
_TASK_SUBSCRIBERS = set()
_MASTER_SUBSCRIBERS = set()
_SESSION_VIEWERS = {}  # {session_name: {queue, queue, ...}}
_ORCH_STATE = {}
_PENDING_PROMPTS = {}
