"""Make libpng's genout.cmake honour CMAKE_C_COMPILER_ARG1.

The zig toolchain is configured as CMAKE_C_COMPILER=zig.exe with
CMAKE_C_COMPILER_ARG1=cc, so every compiler invocation must be "zig cc ...".
libpng's scripts/cmake/genout.cmake.in builds the command from
${CMAKE_C_COMPILER} alone and therefore runs a bare "zig -E", which stops the
build at "Generating pnglibconf.out".  Inject ARG1 into that template only.

After patching, it prints (read only) every remaining place under externals
that interpolates ${CMAKE_C_COMPILER} without ARG1, so the next failure does
not need another 15 minute configure to discover.
"""

import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('work/XHD-Player-source')
EXT = ROOT / 'reference/shadPS4/externals'
TEMPLATE = EXT / 'libpng/scripts/cmake/genout.cmake.in'

ORIGINAL = 'set(CMAKE_C_COMPILER "@CMAKE_C_COMPILER@")\n'
PATCHED = (
    'if(NOT "@CMAKE_C_COMPILER_ARG1@" STREQUAL "")\n'
    '  set(CMAKE_C_COMPILER "@CMAKE_C_COMPILER@" "@CMAKE_C_COMPILER_ARG1@")\n'
    'else()\n'
    '  set(CMAKE_C_COMPILER "@CMAKE_C_COMPILER@")\n'
    'endif()\n'
)

USES = re.compile(r'\$\{CMAKE_C_COMPILER\}')
NAMES = ('CMakeLists.txt',)
SUFFIXES = ('.cmake', '.cmake.in')


def patch_template():
    if not TEMPLATE.is_file():
        raise SystemExit('missing ' + str(TEMPLATE))
    text = TEMPLATE.read_text(encoding='utf-8')
    if PATCHED in text:
        print('libpng genout template already patched')
        return
    if text.count(ORIGINAL) != 1:
        found = re.findall(r'^.*CMAKE_C_COMPILER.*$', text, re.M)
        raise SystemExit('unexpected genout template, C compiler lines: ' + repr(found))
    TEMPLATE.write_text(text.replace(ORIGINAL, PATCHED), encoding='utf-8')
    print('patched ' + str(TEMPLATE))


def scan():
    hits = []
    for path in EXT.rglob('*'):
        if not path.is_file():
            continue
        if path.name not in NAMES and not path.name.endswith(SUFFIXES):
            continue
        if '.git' in path.parts:
            continue
        try:
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        if not USES.search(text):
            continue
        if 'CMAKE_C_COMPILER_ARG1' in text:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if USES.search(line):
                hits.append('%s:%d: %s' % (path.relative_to(EXT).as_posix(), number, line.strip()))
    print('[scan] %d bare ${CMAKE_C_COMPILER} use(s) outside libpng patch scope' % len(hits))
    for hit in hits[:20]:
        print('[scan] ' + hit)


def main():
    patch_template()
    scan()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
