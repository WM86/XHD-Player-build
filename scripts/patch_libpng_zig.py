"""Force libpng to build against its prebuilt pnglibconf.h under the zig toolchain.

libpng normally generates pnglibconf.h at build time by running the C
preprocessor and then a pile of awk scripts (options.awk, dfn.awk,
checksym.awk).  With the zig toolchain CMAKE_C_COMPILER is zig.exe and the
real compiler lives in CMAKE_C_COMPILER_ARG1=cc, so the template expands to a
bare "zig -E" and the build dies at "Generating pnglibconf.out".  The awk
generators are also a second hazard on Windows (line endings, path separators).

libpng ships an officially supported fallback for exactly this situation: when
no awk is available it copies scripts/pnglibconf.h.prebuilt instead
(CMakeLists.txt: "if(NOT AWK OR (ANDROID OR IOS))").  That prebuilt header is
the full feature set, which is what shadPS4 needs.

So blank out the awk probe and let libpng take the prebuilt path.  Only that
one line is touched, and only inside the generated build tree.
"""

import re
import sys
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('work/XHD-Player-source')
EXT = ROOT / 'reference/shadPS4/externals'
LIBS = ('libpng', 'ext-libpng')

PROBE = 'find_program(AWK NAMES gawk nawk awk)'
REPLACEMENT = 'set(AWK "AWK-NOTFOUND")  # zig toolchain: use prebuilt pnglibconf.h'


def find_libpng():
    for name in LIBS:
        candidate = EXT / name
        if (candidate / 'CMakeLists.txt').is_file():
            return candidate
    raise SystemExit('libpng not found under ' + str(EXT))


def main():
    lib = find_libpng()
    prebuilt = lib / 'scripts/pnglibconf.h.prebuilt'
    if not prebuilt.is_file():
        raise SystemExit('missing ' + str(prebuilt) + ' - cannot take the prebuilt path')
    cmakelists = lib / 'CMakeLists.txt'
    text = cmakelists.read_text(encoding='utf-8', errors='replace')
    if REPLACEMENT in text:
        print('libpng already set to the prebuilt configuration')
        return 0
    count = text.count(PROBE)
    if count != 1:
        found = re.findall(r'^.*\bAWK\b.*$', text, re.M)
        raise SystemExit('unexpected libpng CMakeLists (probe x%d): %r' % (count, found[:8]))
    cmakelists.write_text(text.replace(PROBE, REPLACEMENT), encoding='utf-8')
    print('patched ' + str(cmakelists))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
