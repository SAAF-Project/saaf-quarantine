"""Offline synthetic boundary demo. Fixture source is read, never executed."""
import pathlib
import subprocess
import sys
import tempfile


def main():
    checker = pathlib.Path(__file__).with_name('quarantine_check.py').resolve()
    with tempfile.TemporaryDirectory(prefix='quarantine-demo-') as directory:
        source = pathlib.Path(directory) / 'agent.py'
        for label, value, expected in [('unwrapped', 'policy_text', 1),
                                       ('wrapped', "quarantine(policy_text, source='synthetic')", 0)]:
            source.write_text('import anthropic\nfrom saaf_quarantine import quarantine\n'
                              + 'user_prompt = f"Review {' + value + '}"\n')
            result = subprocess.run([sys.executable, '-I', str(checker), str(source)], capture_output=True, text=True)
            if result.returncode != expected:
                raise RuntimeError(f'{label}: expected {expected}, got {result.returncode}: {result.stderr}')
            print(f'Synthetic {label} source: exit {result.returncode} as expected')
        source.write_text('def broken(')
        result = subprocess.run([sys.executable, '-I', str(checker), str(source)], capture_output=True)
        if result.returncode != 2:
            raise RuntimeError('invalid source must remain not assessed')
        print('Synthetic invalid source: exit 2, not assessed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
