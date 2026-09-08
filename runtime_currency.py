"""Ephemeral read-only observation of the application release branch.

Never fetch, write refs, install, request restart or hold workflow/board locks.
A bounded background observation keeps ordinary API reads independent of GitHub.
"""
from datetime import datetime, timezone
import os
import re
import subprocess
import threading
import time


class RuntimeCurrency:
    interval = 60
    max_age = 120

    def __init__(self, repository, running, enabled=True, clock=time.monotonic):
        self.repository, self.running, self.enabled = repository, running, enabled
        self.clock = clock
        self.lock = threading.Lock()
        self.worker = None
        self.next_check = 0
        self.observed = None
        self.result = dict(state='unknown', target_commit='', checked_at=None,
                           message='Checking published application revision.' if enabled and running else 'Published application revision unavailable.')

    def view(self):
        with self.lock:
            result = dict(self.result)
            if self.observed is not None and self.clock() - self.observed > self.max_age:
                result.update(state='unknown', message='Published revision observation expired; checking again.')
            return result

    def tick(self):
        if not self.enabled or not isinstance(self.running, str) or not re.fullmatch(r'[0-9a-f]{40,64}', self.running):
            return
        with self.lock:
            if self.clock() < self.next_check or (self.worker and self.worker.is_alive()):
                return
            self.next_check = self.clock() + self.interval
            self.worker = threading.Thread(target=self.check, daemon=True)
            self.worker.start()

    def check(self):
        try:
            # The managed Unfertig release/update contract selects origin/main.
            result = subprocess.run(['git', '-C', str(self.repository), 'ls-remote', '--exit-code',
                                     'origin', 'refs/heads/main'], capture_output=True, text=True, timeout=8,
                                    env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
            fields = result.stdout.strip().split()
            if result.returncode or len(fields) != 2 or fields[1] != 'refs/heads/main' or not re.fullmatch(r'[0-9a-f]{40,64}', fields[0]):
                raise ValueError('Cannot confirm the published application revision.')
            target = fields[0]
            value = dict(state='current' if target == self.running else 'outdated', target_commit=target,
                         checked_at=datetime.now(timezone.utc).isoformat(),
                         message='Running the published application revision.' if target == self.running else 'A different application revision is published; instance update is needed.')
        except (OSError, ValueError, subprocess.TimeoutExpired):
            value = dict(state='unknown', target_commit='', checked_at=None,
                         message='Published application status unavailable; currency cannot be confirmed.')
        with self.lock:
            self.observed = self.clock()
            self.result = value
