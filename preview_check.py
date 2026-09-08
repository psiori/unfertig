"""One-action, one-use verification evidence. Never serialized into a run."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

from storage import digest


def inputs(workflow, run):
    cwd = Path(run['worktree']).resolve()
    if workflow.git('status', '--porcelain', cwd=cwd):
        raise ValueError('Preview verification requires a clean worktree.')
    argv = workflow.argv('test', run)
    files = {Path(sys.executable).resolve()}
    executable = shutil.which(argv[0]) if argv else None
    if executable:
        files.add(Path(executable).resolve())
        if Path(executable).name == 'uv':
            # Bind the interpreter selected by this recipe, not just the uv binary.
            version = argv[argv.index('--python') + 1] if '--python' in argv else None
            command = [executable, 'python', 'find', *([version] if version else [])]
            files.add(Path(subprocess.check_output(command, cwd=cwd, text=True, timeout=10).strip()).resolve())
    roots = {Path(workflow.processing['working_directory']) / 'scripts'}
    for arg in argv:
        path = Path(arg) if Path(arg).is_absolute() else cwd / arg
        try:
            is_file = path.is_file()
        except OSError:
            is_file = False  # Inline commands can exceed filesystem name limits.
        if is_file:
            files.add(path.resolve())
            if path.suffix == '.py':
                roots.add(path.parent)
    # Host helpers and their local modules are mutable independently of app HEAD.
    # Include additions/removals as well as edits. Ignore runtime/cache directories.
    for root in roots:
        if root.is_dir():
            for directory, children, names in os.walk(root):
                children[:] = [name for name in children if not name.startswith('.')
                               and name not in {'__pycache__', 'node_modules'}]
                files.update((Path(directory) / name).resolve() for name in names
                             if Path(name).suffix in {'.py', '.sh', '.json', '.toml', '.lock'}
                             or name == '.python-version')
    hashes = [(str(p), hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(files)]
    return digest(dict(head=workflow.git('rev-parse', 'HEAD', cwd=cwd),
                       tree=workflow.git('rev-parse', 'HEAD^{tree}', cwd=cwd),
                       cwd=str(cwd), argv=argv, env=dict(os.environ),
                       python=sys.version, files=hashes))


class PreviewCheck:
    """Opaque success token: constructed only after a successful stable check."""
    def __init__(self, fingerprint):
        self.__fingerprint = fingerprint
        self.__used = False

    @classmethod
    def verify(cls, workflow, run, ident):
        before = inputs(workflow, run)
        workflow.command(workflow.argv('test', run), run['worktree'], ident,
                         purpose='verification')
        if inputs(workflow, run) != before:
            raise ValueError('Verification inputs changed during the primary check.')
        return cls(before)

    def consume(self, workflow, run):
        if self.__used:
            return False
        self.__used = True
        return inputs(workflow, run) == self.__fingerprint
