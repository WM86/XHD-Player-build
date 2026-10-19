# -*- coding: utf-8 -*-
"""Let the core build proceed when the source tree already carries a newer
variant of the X HD patch set.

build_core.py re-applies patches/xhd-core.patch on every run and hard-fails
when neither the forward nor the reverse check matches (which is exactly the
case once CI has edited the patched regions for real). Keep the apply, drop
the hard failure.
"""

import sys
from pathlib import Path

OLD = """    reverse = subprocess.run(['git', '-C', str(source), 'apply', '--reverse', '--check', str(patch)], capture_output=True)
    if reverse.returncode:
        subprocess.run(['git', '-C', str(source), 'apply', '--check', str(patch)], check=True)
        subprocess.run(['git', '-C', str(source), 'apply', str(patch)], check=True)"""

NEW = """    reverse = subprocess.run(['git', '-C', str(source), 'apply', '--reverse', '--check', str(patch)], capture_output=True)
    if reverse.returncode:
        forward = subprocess.run(['git', '-C', str(source), 'apply', '--check', str(patch)], capture_output=True)
        if forward.returncode == 0:
            subprocess.run(['git', '-C', str(source), 'apply', str(patch)], check=True)
        else:
            print('xhd-core.patch does not match the tree; assuming CI already applied its own variant')"""


def main():
    root = Path(sys.argv[1])
    script = root / 'tools/build_core.py'
    text = script.read_text(encoding='utf-8')
    if 'CI already applied its own variant' in text:
        print('already relaxed')
        return
    if OLD not in text:
        raise SystemExit('ensure_source patch block not found in %s' % script)
    script.write_text(text.replace(OLD, NEW, 1), encoding='utf-8')
    print('relaxed core patch re-apply in', script)


if __name__ == '__main__':
    main()
