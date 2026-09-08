"""Integration evidence and static migration-registry checks (no candidate imports)."""
import ast


class GitFailure(ValueError):
    def __init__(self, command, result):
        self.evidence = dict(command=list(command), returncode=result.returncode,
                             stdout=result.stdout[-12000:], stderr=result.stderr[-12000:])
        super().__init__('Git '+ ' '.join(command) + '\nstdout:\n' + result.stdout[-2500:] +
                         '\nstderr:\n' + result.stderr[-2500:])


class StaleCandidate(ValueError):
    pass


def registry(source):
    """Reject duplicate literal keys before Python can silently overwrite them."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'MIGRATIONS' for t in node.targets):
            if not isinstance(node.value, ast.Dict):
                raise ValueError('MIGRATIONS must remain an explicit registry for integration review.')
            result = {}
            for key, value in zip(node.value.keys, node.value.values):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str) or not isinstance(value, ast.Name):
                    raise ValueError('Migration registry needs explicit version/function entries.')
                if key.value in result:
                    raise ValueError('Competing migration successors for '+key.value)
                result[key.value] = value.id
            return result
    return {}


def migration_issues(candidate, parents):
    """Every parent feature must survive, even when Git merges without conflict."""
    try:
        combined = registry(candidate)
        expected = set()
        for source in parents:
            expected.update(registry(source).values())
        missing = expected - set(combined.values())
        return ['Migration features missing from combined registry: '+', '.join(sorted(missing))] if missing else []
    except (ValueError, SyntaxError) as error:
        return [str(error)]
