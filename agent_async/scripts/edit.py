#!/usr/bin/env python3
# Portable in-place editing helpers
from __future__ import annotations
import sys, re, json
from pathlib import Path

USAGE = """
Usage:
  edit.py replace <file> <old> <new>
  edit.py regex <file> <pattern> <replacement> [flags]
  edit.py insert_after <file> <anchor> <text>
  edit.py ensure_block <file> <start_marker> <end_marker> <block_text>

Notes:
- Operates on UTF-8 text files.
- For regex flags, combine letters (i=IGNORECASE, s=DOTALL, m=MULTILINE).
- Exits non-zero on no-op for replace/regex unless AGENT_EDIT_ALLOW_NOOP=1.
"""

def _read(p: Path) -> str:
    return p.read_text(encoding='utf-8') if p.exists() else ''

def _write(p: Path, s: str) -> None:
    p.write_text(s, encoding='utf-8')

def _flags(s: str) -> int:
    f = 0
    for ch in (s or ''):
        if ch == 'i': f |= re.IGNORECASE
        if ch == 's': f |= re.DOTALL
        if ch == 'm': f |= re.MULTILINE
    return f

def cmd_replace(path: str, old: str, new: str) -> int:
    p = Path(path)
    s = _read(p)
    if old not in s:
        if os.environ.get('AGENT_EDIT_ALLOW_NOOP') == '1':
            return 0
        print('no match for replace', file=sys.stderr)
        return 2
    s2 = s.replace(old, new)
    if s2 != s:
        _write(p, s2)
        return 0
    return 1

def cmd_regex(path: str, pat: str, repl: str, flags: str = '') -> int:
    p = Path(path)
    s = _read(p)
    r = re.compile(pat, _flags(flags))
    s2, n = r.subn(repl, s)
    if n == 0 and os.environ.get('AGENT_EDIT_ALLOW_NOOP') != '1':
        print('no match for regex', file=sys.stderr)
        return 2
    if s2 != s:
        _write(p, s2)
    return 0

def cmd_insert_after(path: str, anchor: str, text: str) -> int:
    p = Path(path)
    s = _read(p)
    idx = s.find(anchor)
    if idx == -1:
        if os.environ.get('AGENT_EDIT_ALLOW_NOOP') == '1':
            return 0
        print('anchor not found', file=sys.stderr)
        return 2
    insert_pos = idx + len(anchor)
    s2 = s[:insert_pos] + text + s[insert_pos:]
    if s2 != s:
        _write(p, s2)
    return 0

def cmd_ensure_block(path: str, start: str, end: str, block: str) -> int:
    p = Path(path)
    s = _read(p)
    if start in s and end in s and s.find(start) < s.find(end):
        # already present
        return 0
    # append at end with markers
    add = f"\n{start}\n{block}\n{end}\n"
    _write(p, s + add)
    return 0

def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(USAGE.strip())
        return 1
    cmd = argv[1]
    try:
        if cmd == 'replace' and len(argv) == 5:
            return cmd_replace(argv[2], argv[3], argv[4])
        if cmd == 'regex' and len(argv) in (5,6):
            return cmd_regex(argv[2], argv[3], argv[4], argv[5] if len(argv)==6 else '')
        if cmd == 'insert_after' and len(argv) == 5:
            return cmd_insert_after(argv[2], argv[3], argv[4])
        if cmd == 'ensure_block' and len(argv) == 6:
            return cmd_ensure_block(argv[2], argv[3], argv[4], argv[5])
    except Exception as e:
        print(f'error: {e}', file=sys.stderr)
        return 1
    print(USAGE.strip())
    return 1

if __name__ == '__main__':
    import os
    sys.exit(main(sys.argv))
