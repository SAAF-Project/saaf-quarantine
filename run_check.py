"""Trusted reusable-workflow entry point; never import the caller repository."""
import pathlib
import subprocess
import sys


def resolve_target(checkout, requested):
    root = pathlib.Path(checkout).resolve(strict=True)
    relative = pathlib.Path(requested)
    if relative.is_absolute():
        raise ValueError('target must be relative to the caller checkout')
    target = (root / relative).resolve(strict=True)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError('target escapes the caller checkout') from exc
    return target


def main():
    if len(sys.argv) != 3:
        print('usage: run_check.py CHECKOUT RELATIVE_TARGET', file=sys.stderr)
        return 2
    try:
        target = resolve_target(sys.argv[1], sys.argv[2])
    except (OSError, ValueError) as exc:
        print(f'quarantine: NOT ASSESSED: {str(exc)!r}', file=sys.stderr)
        return 2
    checker = pathlib.Path(__file__).resolve().with_name('quarantine_check.py')
    return subprocess.run([sys.executable, '-I', str(checker), '--format', 'github', str(target)],
                          cwd=checker.parent).returncode


if __name__ == '__main__':
    sys.exit(main())
