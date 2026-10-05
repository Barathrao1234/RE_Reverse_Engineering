# =============================================================================
# utils.py
# =============================================================================
# PURPOSE:
#   Shared stateless utility/helper functions used across the entire tool.
#   No business logic. No Java-specific knowledge. No external dependencies
#   beyond Python stdlib.
#
# WHAT IT CONTAINS:
#   - log_time()                      : writes timestamped messages to log file
#   - strip_top_level_comments()      : removes top-level // and /* */ comments
#   - is_commented_declaration()      : checks if a given line is commented out
#   - is_declaration_line_commented() : checks if a source index is in a comment
#   - _strip_comments_and_literals()  : strips comments AND string literals
#   - strip_generics()                : removes <...> generics from type names
#   - _strip_source_comments()        : full comment stripper preserving strings
#
# WHEN TO EDIT THIS FILE:
#   Almost never. These are very stable low-level helpers.
#   Only if a comment-stripping edge case is found.
#
# DEPENDS ON: re, datetime (stdlib only)
# USED BY:    All other modules
# =============================================================================

import re
from datetime import datetime


def log_time(message):
    with open("execution_log_service.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} - {message}\n")


def strip_top_level_comments(code):
    """
    Remove top-level comments (// ... and /* ... */) but leave comments
    inside method/class bodies untouched.
    """
    code = re.sub(r'^\s*//.*$', '', code, flags=re.M)

    def replacer(match):
        if '{' not in match.group(0) and '}' not in match.group(0):
            return ''
        return match.group(0)

    code = re.sub(r'/\*.*?\*/', replacer, code, flags=re.S)
    return code


def is_commented_declaration(code, line_no):
    """
    Return True if the line corresponding to line_no is fully commented out.
    """
    lines = code.splitlines()
    if line_no < 0 or line_no >= len(lines):
        return False
    line = lines[line_no].strip()
    return line.startswith("//") or line.startswith("/*") or line.startswith("*")


def is_declaration_line_commented(src, decl_start_idx):
    """
    Return True if the line where decl_start_idx occurs is commented out.
    """
    line_start = src.rfind('\n', 0, decl_start_idx) + 1
    line = src[line_start: src.find('\n', line_start)]
    stripped = line.lstrip()

    if stripped.startswith("//"):
        return True

    before = src[:decl_start_idx]
    last_block_start = before.rfind("/*")
    last_block_end = before.rfind("*/")

    if last_block_start != -1 and last_block_end < last_block_start:
        return True

    return False


def _strip_comments_and_literals(text):
    if not isinstance(text, str):
        return ""
    return re.sub(
        r'//.*?$|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])\'',
        '',
        text,
        flags=re.MULTILINE | re.DOTALL
    )


def strip_generics(name):
    if not isinstance(name, str):
        return name
    name = re.sub(r'\s*<[^&gt]+>\s*', '', name)
    name = re.sub(r'\s*<[^>]+>\s*', '', name)
    return name


def _strip_source_comments(src: str) -> str:
    """Remove // and /* */ comments while preserving string literals."""
    result = []
    i = 0
    n = len(src)
    in_string = False
    string_char = None

    while i < n:
        ch = src[i]
        nxt = src[i + 1] if i + 1 < n else ""

        if in_string:
            result.append(ch)
            if ch == '\\':
                i += 1
                if i < n:
                    result.append(src[i])
            elif ch == string_char:
                in_string = False
                string_char = None
            i += 1
            continue

        if ch == '/' and nxt == '/':
            while i < n and src[i] != '\n':
                i += 1
            continue

        if ch == '/' and nxt == '*':
            i += 2
            while i < n - 1:
                if src[i] == '*' and src[i + 1] == '/':
                    i += 2
                    break
                i += 1
            continue

        if ch in ('"', "'"):
            in_string = True
            string_char = ch

        result.append(ch)
        i += 1

    return ''.join(result)
