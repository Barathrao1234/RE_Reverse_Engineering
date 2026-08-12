# import os
# import re
# import csv
# import json
# import logging
# import traceback
# import threading
# import time
# from collections import Counter
# from datetime import datetime
# from pathlib import Path
# from typing import Dict, List, Optional, Tuple, Set, Sequence, Any
# from datetime import datetime
# import pandas as pd
# # import requests  # not needed anymore since we removed LLM calls

# def log_time(message):
#     with open("execution_log_service.txt", "a", encoding="utf-8") as f:
#         f.write(f"{datetime.now()} - {message}\n")

# # ==========================================================
# # Configuration
# # ==========================================================

# LOGGER_NAME = "copilotclient"
# LOG_FILE = "copilot_errors.log"

# logger = logging.getLogger(LOGGER_NAME)
# logger.setLevel(logging.INFO)
# if not logger.handlers:
#     ch = logging.StreamHandler()
#     ch.setLevel(logging.INFO)
#     logger.addHandler(ch)

# # ------------------ Global paths/state (initialized in main) ---------------
# SUMMARIES_DIR: Optional[Path] = None              # chunks YAML (not used in code-only mode)
# COMBINED_DIR: Optional[Path] = None               # chunks combined txt
# GROUP_SUMMARIES_DIR: Optional[Path] = None        # groups YAML (not used in code-only mode)
# GROUP_COMBINED_DIR: Optional[Path] = None         # groups combined txt
# REPORTS_DIR: Optional[Path] = None

# # ------------------ Memoized paths -----------------------------------------
# summary_path_by_chunk: Dict[str, str] = {}  # not used in code-only mode
# group_summary_path_by_gid: Dict[str, str] = {}  # not used in code-only mode

# # ------------------ NEW: Project/file metadata globals ----------------------
# PROJECT_ROOT: Optional[Path] = None
# FILE_ID_MAP: Dict[Path, str] = {}  # e.g., {Path(.../Foo.java): 'f33'}

# # Accumulate per-chunk/group file metadata (file ids, ranges, totals)
# EXTRACTION_META_BY_CHUNK: Dict[str, Dict[Path, Dict[str, object]]] = {}
# EXTRACTION_META_BY_GROUP: Dict[str, Dict[Path, Dict[str, object]]] = {}

# # Track the unit currently being built so we can accumulate metadata
# CURRENT_CHUNK_ID: Optional[str] = None
# CURRENT_GROUP_ID: Optional[str] = None

# # ------------------ NEW: Group mappings ------------------------------------
# GROUP_METHODS_MAP: Dict[str, List[str]] = {}  # gid -> [methods and/or nested groups]

# # ------------------ NEW: Called-files mappings -----------------------------

# # ------------------ App props (normalized & exact for COBOL) ---------------
# APP_PROPS_BY_METHOD_EXACT: Dict[str, List[Dict[str, str]]] = {}

# # ------------------ Edges (normalized & exact by imported_name) -------------
# EDGES_BY_IMPORTED_EXACT: Dict[str, List[Dict[str, str]]] = {}

# # ==========================================================
# # Token detection
# # ==========================================================
# CHUNK_ID_RE = re.compile(r"^C\d+", re.IGNORECASE)

# # Support both G<number> and GN<number>
# GROUP_ID_RE = re.compile(r"^G(?:N)?\d+$", re.IGNORECASE)

# # Extract a group id embedded in longer tokens (e.g., "GN1 no_of_lines")
# GROUP_ID_EXTRACT_RE = re.compile(r"\bG(?:N)?\d+\b", re.IGNORECASE)

# # Strip "no_of_lines : X" suffix from method tokens
# NO_OF_LINES_TOKEN_RE = re.compile(
#     r"^(?P<name>.+?)\s+no_of_lines\s*:\s*(?P<loc>\d+|Nil|None)\s*$",
#     re.IGNORECASE
# )


# def is_chunk_id(token: str) -> bool:
#     return bool(token) and bool(CHUNK_ID_RE.match(token.strip()))

# def is_group_id(token: str) -> bool:
#     """
#     Strict check: entire token is a canonical group id like 'G1' or 'GN1'.
#     """
#     return bool(token) and bool(GROUP_ID_RE.match(token.strip()))

# def normalize_group_token(token: Optional[str]) -> Optional[str]:
#     """
#     Return canonical group id if the token contains a group id anywhere.
#     """
#     if not token:
#         return None
#     t = token.strip()
#     if GROUP_ID_RE.match(t):
#         return t
#     m = GROUP_ID_EXTRACT_RE.search(t)
#     if m:
#         gid = m.group(0).strip()
#         return gid
#     return None

# def is_groupish(token: Optional[str]) -> bool:
#     return normalize_group_token(token) is not None

# def strip_no_of_lines_name(token: str) -> str:
#     if not token:
#         return ""
#     s = token.strip()
#     m = NO_OF_LINES_TOKEN_RE.match(s)
#     if m:
#         return m.group("name").strip()
#     return s

# def strip_no_of_lines_suffix(token: str):
#     if not token:
#         return "", None

#     s = token.strip()
#     m = NO_OF_LINES_TOKEN_RE.match(s)

#     if m:
#         name = m.group("name").strip()
#         loc = m.group("loc")

#         # Normalize loc
#         if loc.isdigit():
#             loc = int(loc)
#         else:
#             loc = None

#         return name, loc

#     # No suffix found
#     return s, None


# # ==========================================================
# # Utilities
# # ==========================================================
# def require_columns(df: pd.DataFrame, cols: List[str], name: str):
#     missing = [c for c in cols if c not in df.columns]
#     if missing:
#         raise ValueError(f"[ERROR] {name} missing required columns: {missing}")

# def split_names(raw) -> List[str]:
#     if raw is None:
#         return []
#     # Treat NaN-like values as empty
#     try:
#         if pd.isna(raw):
#             return []
#     except Exception:
#         pass

#     s = str(raw).strip().strip('"').strip("'")
#     parts = re.split(r"[,\n;]+", s)
#     return [p.strip() for p in parts if p.strip() and p.strip().lower() != "nan"]

# def name_parts(full_name: str) -> Tuple[Optional[str], str]:
#     if "." in full_name:
#         pre, post = full_name.rsplit(".", 1)
#         return pre, post
#     return None, full_name

# def list_source_files(src_root: Path, exts: Set[str]) -> List[Path]:
#     # print("src_root : ",src_root)
#     # print("exts : ",exts)
#     exts = {item.lower() for item in exts}
#     out: List[Path] = []
#     for root, _, files in os.walk(src_root):
#         for fn in files:
#             p = Path(root) / fn
#             # print(" P_1: ",p.suffix)
#             # print()
#             if p.suffix.lower() in exts:
#                 # print(" P : ",p)
#                 out.append(p)
#                 # print("outt : ",out)
#     return out

# def detect_language_by_ext(ext: str) -> str:
#     ext = ext.lower()
    
#     if ext in {".java", ".js", ".ts"}:
#         return "c_like"
    
#     return "text"


# # def _norm_ext(ext: Optional[str]) -> str:
# #     """Normalize extension for case-insensitive, whitespace-safe comparison."""
# #     return (ext or "").strip().lower()


# def _norm_ext(ext: Optional[List[str]]) -> List[str]:
#     """Normalize extensions for case-insensitive, whitespace-safe comparison."""
#     return [e.strip().lower() for e in ext or []]

# def _count_lines(s) -> int:
#     if not s:
#         return 0

#     # If s is a list of strings, count lines in each
#     if isinstance(s, list):
#         return sum(len(item.splitlines()) for item in s if isinstance(item, str))

#     # NEW: If s is a dict, count lines in string values
#     if isinstance(s, dict):
#         return sum(len(v.splitlines()) for v in s.values() if isinstance(v, str))

#     # If s is a string, count normally
#     return len(s.splitlines())   

# # ------------------ NEW: helpers for metadata header & ranges ----------------
# def _compute_snippet_line_range(full_text: str, snippet_text: str) -> Tuple[int, int]:
#     if not full_text or not snippet_text:
#         return (0, 0)

#     start_idx = full_text.find(snippet_text)
#     if start_idx == -1:
#         normalized_file = re.sub(r"\s+", " ", full_text)
#         normalized_snip = re.sub(r"\s+", " ", snippet_text)
#         start_idx = normalized_file.find(normalized_snip)
#         if start_idx == -1:
#             return (0, 0)
#         start_line = normalized_file[:start_idx].count("\n") + 1
#         end_line = start_line + len(snippet_text.splitlines()) - 1
#         return (start_line, end_line)

#     start_line = full_text[:start_idx].count("\n") + 1
#     end_line = start_line + len(snippet_text.splitlines()) - 1
#     return (start_line, end_line)

# def _relative_path_for_header(file_path: Path) -> str:
#     global PROJECT_ROOT
#     try:
#         if PROJECT_ROOT and PROJECT_ROOT.exists():
#             return str(file_path.relative_to(PROJECT_ROOT)).replace("/", "\\")
#     except Exception:
#         pass
#     return str(file_path).replace("/", "\\")

# def _display_file_id_from_snippet(snippet_text: str, fallback_file_id: str = "") -> str:
#     if not snippet_text:
#         return (fallback_file_id or "").lower()

#     for line in snippet_text.splitlines():
#         m = re.match(r"\s*(f\d+)_\d+\b", line.strip(), re.IGNORECASE)
#         if m:
#             return m.group(1).lower()   # 'f336'
#     return (fallback_file_id or "").lower()

# def _display_file_id_from_path(pth: Path) -> str:
#     try:
#         return (pth.stem or "").lower()
#     except Exception:
#         return ""

# def build_unit_metadata_block(meta_by_file: Dict[Path, Dict[str, object]],
#                               breakdown: Optional[Dict[str, int]] = None) -> str:
#     if not meta_by_file:
#         return ""

#     file_sections: List[str] = []

#     for pth, meta in meta_by_file.items():
#         snippet_text = str(meta.get("snippet_text") or "")

#         provided = str(
#             meta.get("extracted_file_id")
#             or meta.get("file_id")
#             or meta.get("display_file_id")
#             or ""
#         ).lower()

#         fallback_from_path = _display_file_id_from_path(pth)

#         file_id_display = _display_file_id_from_snippet(
#             snippet_text,
#             provided or fallback_from_path
#         )

#         file_name = pth.name
#         rel_path = _relative_path_for_header(pth)
#         total_lines = int(meta.get("total_lines") or 0)

#         ranges = meta.get("ranges") or []
#         ranges_display: List[str] = []

#         for rng in ranges:
#             if not isinstance(rng, (tuple, list)) or len(rng) < 2:
#                 continue
#             try:
#                 start = int(rng[0])
#                 end = int(rng[1])
#             except (TypeError, ValueError):
#                 continue

#             if end >= start:
#                 if file_id_display:
#                     ranges_display.append(f"[{file_id_display}_{start}:{file_id_display}_{end}]")
#                 else:
#                     ranges_display.append(f"[{start}:{end}]")

#         ranges_str = ", ".join(ranges_display) if ranges_display else "[]"

#         section_lines = [
#             f"** File Id : {file_id_display}",
#             f"** File name : {file_name}",
#             f"** File_Path : {rel_path}",
#             f"** Total number of lines in File : {total_lines}",
#             f"** Lines Used : {ranges_str}",
#         ]

#         if breakdown:
#             section_lines.extend([
#                 f"** Total lines in combined input : {int(breakdown.get('total_lines', 0))}",
#                 f"** Code lines : {int(breakdown.get('code_lines', 0))}",
#                 f"** Application properties lines : {int(breakdown.get('props_lines', 0))}",
#                 f"** Child summary lines : {int(breakdown.get('child_summary_lines', 0))}",
#                 f"** Empty lines : {int(breakdown.get('empty_lines', 0))}",
#                 f"** LLM reference lines : {int(breakdown.get('llm_ref_lines', 0))}",
#             ])

#         file_sections.append("\n".join(section_lines))

#     final_lines = [
#         "*****************************  Begin Header ***********************************",
#         ("\n-----------------------------------------------------------------------------\n").join(file_sections),
#         "*****************************  End Header ***************************************",
#         ""
#     ]

#     return "\n".join(final_lines).rstrip()

# _GROUP_HEADER_RE = re.compile(
#     r"^(=+\s*COMBINED CODE FOR GROUP:\s*[^\n]+?=+)\s*$",
#     re.IGNORECASE | re.MULTILINE
# )

# def inject_metadata_after_group_header(combined_text: str, header_text: str) -> str:
#     """
#     Insert metadata immediately after the first group header line.
#     """
#     if not header_text.strip():
#         return combined_text

#     m = _GROUP_HEADER_RE.search(combined_text or "")
#     if not m:
#         # Fallback: prepend if header line not found
#         return f"{header_text}\n{combined_text}"

#     insert_at = m.end()
#     return combined_text[:insert_at] + "\n\n" + header_text + "\n" + combined_text[insert_at:]

# def attach_file_metadata_to_combined_text(
#     extension,
#     combined_text: str,
#     meta_by_file: Dict[Path, Dict[str, object]]
# ) -> str:
#     breakdown = compute_document_line_breakdown(combined_text, extension)
#     header = build_unit_metadata_block(meta_by_file, breakdown=breakdown)
#     return inject_metadata_after_group_header(combined_text, header)

# # ---------------------------------------------------------------------------
# # STRICT and LOOSE signature regex builders (FIXED)
# # ---------------------------------------------------------------------------
# def build_java_signature_regex_strict(name_re: str, brace_on_same_line: bool) -> re.Pattern:
#     # Correct generic matcher using actual '<' and '>'
#     generic = r"(?:<[^<>\n]*?(?:<[^<>\n]*?>[^<>\n]*?)*?>)"

#     line_id_prefix = r"^\s*(?:f\d+_\d+\s+)?"

#     annotations = (
#         r"(?P<annotations>("
#         r"(?:@\s*[\w.$]+(?:\s*\([^)]*\))?\s*)|"
#         r"(?:\[\s*[\w.$,\s]+(?:\s*\([^)]*\))?\s*\]\s*)"
#         r")*)"
#     )

#     modifiers = (
#         r"(?P<modifiers>("
#         r"(?:public|protected|private|internal|static|final|abstract|"
#         r"synchronized|native|strictfp|default|sealed|virtual|override|"
#         r"extern|unsafe|async|new|partial)"
#         r"\s+"
#         r")*)"
#     )

#     type_params = rf"(?:{generic}\s+)?"

#     ret = rf"""
#         (?P<ret>
#             (?:
#                 [\w.$]+
#                 (?:\s*{generic})?
#             )
#             (?:\s*\[\s*\])*
#             (?:\s*\.\.\.)?
#         )
#         \s+
#     """

#     core = rf"""
#         {line_id_prefix}
#         {annotations}
#         {modifiers}
#         {type_params}
#         (?:
#             {ret}
#         )?
#         (?P<name>{name_re})
#         \s*
#         \(
#             (?P<params>[\s\S]*?)
#         \)
#         (?:\s*throws\s+(?P<throws>[^\){{;]+))?
#     """

#     if brace_on_same_line:
#         pattern = rf"""{core}\s*\{{"""
#     else:
#         pattern = rf"""{core}\s*$"""

#     return re.compile(pattern, re.MULTILINE | re.VERBOSE | re.DOTALL)


# def build_java_signature_regex_loose(name_re: str, brace_on_same_line: bool) -> re.Pattern:
#     line_id_prefix = r"^\s*(?:f\d+_\d+\s+)?"

#     annotations = (
#         r"(?P<annotations>("
#         r"(?:@\s*[\w.$]+(?:\s*\([^)]*\))?\s*)|"
#         r"(?:\[\s*[\w.$,\s]+(?:\s*\([^)]*\))?\s*\]\s*)"
#         r")*)"
#     )

#     modifiers = (
#         r"(?P<modifiers>("
#         r"(?:public|protected|private|internal|static|final|abstract|"
#         r"synchronized|native|strictfp|default|sealed|virtual|override|"
#         r"extern|unsafe|async|new|partial)"
#         r"\s+"
#         r")*)"
#     )

#     core = rf"""
#         {line_id_prefix}
#         {annotations}
#         {modifiers}

#         (?:
#             [\w.$]+
#             (?:\s*<[^>\n]+>)?
#             (?:\s*\[\])*
#         )
#         \s+

#         (?P<name>{name_re})
#         \s*
#         \(
#             (?P<params>[\s\S]*?)
#         \)
#         (?:\s*throws\s+(?P<throws>[^\){{;]+))?
#     """

#     if brace_on_same_line:
#         pattern = rf"""{core}\s*\{{"""
#     else:
#         pattern = rf"""{core}\s*$"""

#     return re.compile(pattern, re.MULTILINE | re.VERBOSE | re.DOTALL)


# def _rewind_to_method_start(text: str, sig_start: int) -> int:
#     pos = sig_start
#     keep_from = sig_start

#     while True:
#         prev_nl = text.rfind("\n", 0, pos)
#         if prev_nl == -1:
#             return 0

#         line_start = prev_nl + 1
#         line = text[line_start:pos]

#         if line.strip() == "":
#             keep_from = line_start
#             pos = prev_nl
#             continue

#         if re.match(r"^\s*}\s*$", line):
#             return keep_from

#         if re.match(r"^\s*(?:f\d+_\d+\s+)?@\w", line):
#             keep_from = line_start
#             pos = prev_nl
#             continue

#         return keep_from

# def _trim_to_signature_with_annotations(snippet: str, method_name: str) -> str:
#     if not snippet:
#         return snippet

#     lines = snippet.splitlines(True)

#     sig_pattern = re.compile(
#         rf"""(?mx)
#         ^\s*(?:f\d+_\d+\s+)?            
#         (?:public|protected|private|static|final|abstract|synchronized|native|strictfp|default|\s)*
#         [^\n]*\b{re.escape(method_name)}\s*\(   
#         """
#     )

#     sig_text = "".join(lines)
#     m = sig_pattern.search(sig_text)
#     if not m:
#         return snippet.lstrip()

#     upto = sig_text[:m.start()]
#     start_line_idx = upto.count("\n")

#     keep_from = start_line_idx
#     while keep_from - 1 >= 0:
#         prev_line = lines[keep_from - 1]
#         if re.match(r"^\s*(?:f\d+_\d+\s+)?@\w", prev_line) or prev_line.strip() == "":
#             keep_from -= 1
#             continue
#         break

#     trimmed = "".join(lines[keep_from:])
#     trimmed = re.sub(r"^(?:\s*}\s*\n)+", "", trimmed)
#     return trimmed

# COBOL_LINES_SUFFIX_RE = re.compile(r"\s+no_of_lines\s*:\s*\d+\s*$", re.IGNORECASE)

# # Support either numeric column ids and/or line-id tokens; allow both in sequence.
# LINE_ID_PREFIX_TOKEN   = r"[A-Za-z]{1,5}\d{0,4}_\d{1,7}"
# PREFIX_TOKEN_RE        = rf"(?:\d{{3,}}\s+|{LINE_ID_PREFIX_TOKEN}\s+)"
# LINE_OR_COL_PREFIX_RE  = rf"(?:{PREFIX_TOKEN_RE})*"


# def _cut_at_next_signature_line_with_line_ids(snippet: str) -> str:
#     if not snippet:
#         return snippet

#     new_sig_re = re.compile(
#         r'^\s*f\d+_\d+\s+(public|protected|private)\b.*\(',
#         re.MULTILINE
#     )

#     matches = list(new_sig_re.finditer(snippet))
#     if len(matches) <= 1:
#         return snippet

#     cut_pos = matches[1].start()
#     return snippet[:cut_pos].rstrip("\n")


# def _fallback_method_extract_c_like(text: str, method_name: str) -> Optional[str]:

#     sig = re.search(
#         rf"(?ms)^[^\n]*\b{re.escape(method_name)}\s*\([\s\S]*?\)\s*[^\n]*\{{",
#         text
#     )
#     if not sig:
#         return None

#     start = sig.start()
#     brace_start = text.find("{", sig.start())
#     if brace_start == -1:
#         return None

#     depth = 0
#     i = brace_start
#     if i < 0:
#         return None
#     end_index = None
#     while i < len(text):
#         ch = text[i]
#         if ch == "{":
#             depth += 1
#         elif ch == "}":
#             depth -= 1
#             if depth == 0:
#                 keep_from = _rewind_to_method_start(text, sig.start())
#                 return text[keep_from:i+1]
#         i += 1
#     return None

# def lookup_actual_value(
#     excel_path: str,
#     file_name: str,
#     method_name: str,
#     sheet_name: str = "standalone",
# ):

#     import os
#     import pandas as pd
#     from html import unescape

#     # ---------- Helpers ----------
#     # Removes normal spaces, NBSP (U+00A0), BOM (U+FEFF), zero-width no-break (U+FEFF), zero-width space (U+200B)
#     def strip_all(s: str) -> str:
#         if s is None:
#             return ""
#         s = str(s)
#         # Normalize common hidden chars
#         for ch in ("\u00A0", "\uFEFF", "\u200B"):
#             s = s.replace(ch, " ")
#         return s.strip()

#     def norm(s: str) -> str:
#         return strip_all(s).lower()

#     def canon_proc(s: str) -> str:
#         # Unescape twice to handle double-escaped inputs, then normalize
#         clean = unescape(unescape("" if s is None else str(s)))
#         # print("norm : ",norm(clean))
#         return norm(clean)

#     def file_stem_only(name: str) -> str:
#         raw = "" if name is None else str(name)
#         raw = strip_all(raw)
#         base = os.path.basename(raw)
#         stem, _ext = os.path.splitext(base)
#         return norm(stem)

#     # ---------- Early Guard (accept raw or escaped angle brackets) ----------
#     text = "" if method_name is None else str(method_name)
#     if not any(tok in text for tok in ("<", ">", "&lt;", "&gt;", "&amp;lt;", "&amp;gt;", "&amp;amp;lt;", "&amp;amp;gt;")):
#         # print("NONE")
#         return None

#     # ---------- Build canonical inputs ----------
#     canon_method = canon_proc(method_name)
#     stem_candidates = {file_stem_only(file_name)}  # single stem is enough and most robust
#     # print("stem_candidates : ",stem_candidates)
#     # print("canon_method : ",canon_method)

#     # ---------- Read Excel ----------
#     df = pd.read_excel(excel_path, sheet_name=sheet_name, dtype=str, engine="openpyxl")
#     if df.empty:
#         # print("DF EMPTY")
#         return None

#     # ---------- Find columns case-insensitively (with strip) ----------
#     col_map = {strip_all(c).lower(): c for c in df.columns}

#     def get_col(*names):
#         for n in names:
#             key = strip_all(n).lower()
#             if key in col_map:
#                 return col_map[key]
#         return None

#     col_file = get_col("file_name", "filename", "file name")
#     col_proc = get_col("procedure_name", "procedure name", "procedure", "method_name", "method name")
#     col_actual = get_col("actual_value", "actual value", "actual", "value")

#     if not all([col_file, col_proc, col_actual]):
#         # Optional debug for headers
#         # print("DEBUG:: headers:", df.columns.tolist())
#         return None

#     # ---------- Normalize DataFrame ----------
#     df["_file_norm"] = df[col_file].map(file_stem_only)
#     df["_proc_canon"] = df[col_proc].map(canon_proc)
#     # print("df_file_norm : ",df["_file_norm"])
#     # print("DF_proc_canon : ",df["_proc_canon"])

#     # ---------- Match ----------
#     matched = df[
#         (df["_file_norm"].isin(stem_candidates)) &
#         (df["_proc_canon"] == canon_method)
#     ]

#     if matched.empty:
#         # Minimal, targeted debug (safe to keep; prints only on miss)
#         # print("DEBUG:: stem_candidates:", stem_candidates)
#         # print("DEBUG:: canon_method:", canon_method)
#         # print("DEBUG:: file_norm unique:", sorted(set(df["_file_norm"].dropna())))
#         # print("DEBUG:: example proc_canon (top 5):", df["_proc_canon"].dropna().head(5).tolist())
#         return None

#     val = matched[col_actual].dropna()
#     # print("VAL : ",val)
#     return None if val.empty else strip_all(str(val.iloc[0]))
# def extract_method_code(file_name: str,text: str, method_name: str, lang: str,PARAGRAPH_LINEAGE_EXCEL: str,CHUNK_LIMIT:int) -> Optional[str]:

#     if not method_name:
#         return None

#     method_name,LOC = strip_no_of_lines_suffix(method_name)


#     # ---- Java / C-like ----
#     name_re = re.escape(method_name)

#     patterns: List[Tuple[re.Pattern, bool]] = []

#     for brace_same in (True, False):
#         patterns.append((build_java_signature_regex_strict(name_re, brace_same), brace_same))
#     _DEFINITION_LINE_RE = re.compile(
#         r"""(?x)
#         ^\s*
#         (?:f\d+_\d+\s+)?                          # optional line-id token
#         (?:@\s*[\w.$]+(?:\s*\([^)]*\))?\s*)*      # optional annotations
#         (?:
#             (?:public|protected|private|internal|static|final|abstract|
#             synchronized|native|strictfp|default|sealed|virtual|override|
#             extern|unsafe|async|new|partial)
#             \s+
#         )+                                         # at least one modifier OR
#         |
#         ^\s*(?:f\d+_\d+\s+)?
#         [\w.$<>\[\]]+\s+                           # return type
#         """,
#         re.MULTILINE
#     )

#     def _is_definition_match(m: re.Match, src: str) -> bool:
#         """Return True only if the match starts on a method-definition line (not a call site)."""
#         # Find the start of the line containing the match
#         line_start = src.rfind("\n", 0, m.start()) + 1
#         line_end   = src.find("\n", m.start())
#         if line_end == -1:
#             line_end = len(src)
#         line = src[line_start:line_end]

#         # A call site ends with ');' or just ')' — no opening brace on this or next line
#         stripped = line.strip()
#         if stripped.endswith(");") or stripped.endswith(");"):
#             return False

#         # Must have a modifier or return type before the method name
#         return bool(_DEFINITION_LINE_RE.match(line))

#     all_matches: dict = {}  # start_pos -> (match, brace_on_same_line)
#     for pat, brace_same in patterns:
#         for m in pat.finditer(text):
#             if m.start() not in all_matches and _is_definition_match(m, text):
#                 all_matches[m.start()] = (m, brace_same)

#     def _extract_one(match, brace_on_same_line: bool) -> Optional[str]:
#         """Extract a single method body given its signature match."""
#         start_index = _rewind_to_method_start(text, match.start())

#         if brace_on_same_line:
#             if match.end() - 1 >= 0 and text[match.end() - 1] == "{":
#                 brace_index = match.end() - 1
#             else:
#                 brace_index = text.find("{", match.start())
#         else:
#             brace_index = text.find("{", match.end())

#         if brace_index == -1:
#             i = max(match.end(), 0)
#             while i < len(text) and text[i] != "{":
#                 i += 1
#             if i >= len(text) or text[i] != "{":
#                 return None
#             brace_index = i

#         depth = 0
#         i = brace_index
#         end_index = None
#         while i < len(text):
#             ch = text[i]
#             if ch == "{":
#                 depth += 1
#             elif ch == "}":
#                 depth -= 1
#                 if depth == 0:
#                     end_index = i
#                     break
#             i += 1

#         if end_index is None:
#             return None

#         snippet = text[start_index:end_index + 1]
#         first_lines = snippet.splitlines()

#         signature = ""

#         for line in first_lines:
#             s = re.sub(r'^\s*f\d+_\d+\s+', '', line).strip()

#             if not s:
#                 continue

#             if s.startswith("@"):
#                 continue

#             signature = s
#             break

#         if "=" in signature and signature.endswith(";"):
#             return None
#         snippet = _trim_to_signature_with_annotations(snippet, method_name)
#         snippet = _cut_at_next_signature_line_with_line_ids(snippet)
#         return snippet

#     if not all_matches:
#         # last resort: very loose extraction (only first match, as before)
#         fallback = _fallback_method_extract_c_like(text, method_name)
#         if fallback:
#             fallback = _trim_to_signature_with_annotations(fallback, method_name)
#             fallback = _cut_at_next_signature_line_with_line_ids(fallback)
#             return fallback
#         return None

#     # Extract every overload in source order and join with a blank line separator.
#     snippets: List[str] = []
#     for pos in sorted(all_matches):
#         m, brace_same = all_matches[pos]
#         s = _extract_one(m, brace_same)
#         if s:
#             snippets.append(s)

#     if not snippets:
#         return None

#     # Return a single string with all overloads separated by a blank line.
#     return "\n\n".join(snippets)

# def _find_exact_class_files(
#     files: List[Path],
#     method_full_name: str,

# ) -> List[Path]:

#     method_full_name = strip_no_of_lines_name(method_full_name)
#     cls, _ = name_parts(method_full_name)
#     if not cls:
#         return []

#     cls_full = cls.strip()
#     cls_base = cls_full.split(".")[-1]
#     cls_full_lc = cls_full.lower()
#     cls_base_lc = cls_base.lower()

#     matched: List[Path] = []

    
    
#     has_java = any(p.suffix.lower() == ".java" for p in files)
    
#     # -------- Java / others --------
#     for p in files:
#         if p.stem.lower() in (cls_full_lc, cls_base_lc):
#             matched.append(p)

#     if has_java:
#         class_pattern_java = re.compile(
#             rf'\b(class|interface|enum)\s+{re.escape(cls_base)}\b'
#         )
#         for p in files:
#             if p in matched or p.suffix.lower() != ".java":
#                 continue
#             try:
#                 text = p.read_text(encoding="utf-8", errors="ignore")
#                 if class_pattern_java.search(text):
#                     matched.append(p)
#             except Exception:
#                 continue

#     matched.sort()
#     # print("matched : ",matched)
#     return matched

# # ==========================================================
# # YAML summary extraction helpers (kept but unused in code-only mode)
# # ==========================================================
# SECTION_HEADINGS = {
#     "WORKFLOW (TRACE ACROSS METHODS)",
#     "BUSINESS RULES (MANDATORY)",
#     "DATA HANDLING AND TRANSFORMATIONS",
#     "VALIDATIONS AND GUARDS",
#     "RETURN BEHAVIOR",
#     "SAMPLE EXECUTION FLOW",
# }

# _KEY_WITH_OPTIONAL_ID_RE_TMPL = r"""
#     ^(?P<indent>\s*)
#     (?:[A-Za-z][\w.\-]*\s+)?          # optional id prefix like G2.1.5 or GN12
#     (?P<key>{key})\s*:\s*(?P<after>.*)$
# """

# _HEADING_WITH_OPTIONAL_ID_RE = re.compile(
#     r"""^(?P<indent>\s*)(?:[A-Za-z][\w.\-]*\s+)?(?P<label>[A-Z0-9][A-Z0-9 ()/&\-\.,]+)\s*:\s*$""",
#     re.VERBOSE
# )

# def _extract_yaml_key_block_flexible(text: str, key_name: str) -> str:
#     if not text:
#         return ""
#     pattern = re.compile(_KEY_WITH_OPTIONAL_ID_RE_TMPL.format(key=re.escape(key_name)), re.VERBOSE | re.IGNORECASE)
#     lines = text.splitlines(True)

#     for i, line in enumerate(lines):
#         m = pattern.match(line)
#         if not m:
#             continue
#         base_indent = len(m.group("indent"))
#         out = [line.rstrip("\n")]
#         for j in range(i + 1, len(lines)):
#             ln = lines[j]
#             if ln.strip() == "":
#                 out.append(ln.rstrip("\n"))
#                 continue
#             cur_indent = len(ln) - len(ln.lstrip())
#             if cur_indent > base_indent:
#                 out.append(ln.rstrip("\n"))
#             else:
#                 break
#         return "\n".join(out).rstrip()
#     return ""

# def _extract_sections_from_detailed_block_flexible(text: str) -> str:
#     if not text:
#         return ""

#     pat = re.compile(_KEY_WITH_OPTIONAL_ID_RE_TMPL.format(
#         key=re.escape("detailed_technical_explanation")), re.VERBOSE | re.IGNORECASE)
#     lines = text.splitlines(True)

#     start_idx = None
#     base_indent = 0
#     after_token = ""
#     for i, line in enumerate(lines):
#         m = pat.match(line)
#         if m:
#             start_idx = i
#             base_indent = len(m.group("indent"))
#             after_token = (m.group("after") or "").strip()
#             break
#     if start_idx is None:
#         return ""

#     if after_token == "|":
#         block_lines: List[str] = []
#         for j in range(start_idx + 1, len(lines)):
#             ln = lines[j]
#             st = ln.lstrip()
#             if st.strip() == "":
#                 block_lines.append(ln.rstrip("\n"))
#                 continue
#             indent = len(ln) - len(st)
#             if indent > base_indent:
#                 block_lines.append(ln.rstrip("\n"))
#             else:
#                 break

#         if not block_lines:
#             return ""

#         extracted: List[str] = []
#         k = 0
#         while k < len(block_lines):
#             ln = block_lines[k]
#             st = ln.strip()

#             if st.endswith(":"):
#                 m = _HEADING_WITH_OPTIONAL_ID_RE.match(ln)
#                 if m:
#                     label = (m.group("label") or "").strip()
#                 else:
#                     label = st[:-1].strip()

#                 if label in SECTION_HEADINGS:
#                     extracted.append(ln)
#                     k += 1
#                     while k < len(block_lines):
#                         nxt = block_lines[k]
#                         m2 = _HEADING_WITH_OPTIONAL_ID_RE.match(nxt)
#                         if m2:
#                             break
#                         extracted.append(nxt)
#                         k += 1
#                     extracted.append("")
#                     continue
#             k += 1

#         return "\n".join([e.rstrip() for e in extracted]).strip()

#     nested: List[str] = []
#     for j in range(start_idx + 1, len(lines)):
#         ln = lines[j]
#         if ln.strip() == "":
#             nested.append(ln.rstrip("\n"))
#             continue
#         cur_indent = len(ln) - len(ln.lstrip())
#         if cur_indent > base_indent:
#             nested.append(ln.rstrip("\n"))
#         else:
#             break
#     if not nested:
#         return ""

#     extracted2: List[str] = []
#     k = 0
#     while k < len(nested):
#         ln = nested[k]
#         m = _HEADING_WITH_OPTIONAL_ID_RE.match(ln)
#         if m:
#             label = (m.group("label") or "").strip()
#             if label in SECTION_HEADINGS:
#                 extracted2.append(ln)
#                 k += 1
#                 while k < len(nested):
#                     nxt = nested[k]
#                     m2 = _HEADING_WITH_OPTIONAL_ID_RE.match(nxt)
#                     if m2:
#                         break
#                     extracted2.append(nxt)
#                     k += 1
#                 extracted2.append("")
#                 continue
#         k += 1

#     return "\n".join([e.rstrip() for e in extracted2]).strip()

# def extract_essential_sections_from_summary_yaml(yaml_text: str) -> str:
#     if not yaml_text:
#         return ""

#     bf = _extract_yaml_key_block_flexible(yaml_text, "business_function")
#     core_bf = _extract_yaml_key_block_flexible(yaml_text, "core_business_functionality")
#     detailed_sections = _extract_sections_from_detailed_block_flexible(yaml_text)

#     parts: List[str] = []
#     if bf.strip():
#         parts.append(bf.strip())
#     if core_bf.strip():
#         parts.append(core_bf.strip())
#     if detailed_sections.strip():
#         parts.append(detailed_sections.strip())

#     return "\n".join(parts).strip()

# # ==========================================================
# # Group label builder (via GROUP_METHODS_MAP)
# # ==========================================================
# def _format_group_label_with_map(group_id: str) -> str:
#     gid = normalize_group_token(group_id) or group_id
#     if not is_group_id(gid):
#         return gid
#     methods = GROUP_METHODS_MAP.get(gid, [])
#     if not methods:
#         return gid
#     joined = ", ".join(methods)
#     return f"{gid} (methods: {joined})"


# # ==========================================================
# # Combined formatters
# # ==========================================================
# def _format_code_map_as_combined_snippet(
#     ext: Optional[List[str]],
#     unit_id: str,
#     code_map: Dict[str, str],
#     source_map: Dict[str, str],
#     parent_entity: Optional[str] = None,
#     unit_type: str = "chunk",  # "chunk", "group", or "call"
# ) -> str:
#     """
#     Generic combined formatter for CHUNK/GROUP/CALL inputs.
#     - Emits method/file blocks and (in code-only mode) does NOT embed group summaries.
#     """
#     ext = _norm_ext(ext) 
#     lines: List[str] = []
#     header = f"==== COMBINED CODE FOR {unit_type.upper()}: {unit_id}"
#     if unit_type.lower() == "chunk" and parent_entity:
#         header += f" (parent_entity: {parent_entity})"
#     if unit_type.lower() == "group":
#         label = _format_group_label_with_map(unit_id)
#         if label != unit_id:
#             header = f"==== COMBINED CODE FOR GROUP: {label} ===="
#     else:
#         header += " ===="
#     lines.append(header)
#     lines.append("")

#     for token_key, code in code_map.items():
#         clean_name = token_key.strip()
#         value = source_map.get(token_key, "code")

#         no_lines = _count_lines(code)
#         if ".java" in ext:
#             lines.append(f"--- BEGIN METHOD: {clean_name} no_of_lines : {no_lines} ---")
        
#         if code:
#             if isinstance(code, list):
#                 for item in code:
#                     lines.append(item.rstrip())
#             else:
#                 # lines.append(code.rstrip())
#                 code = code if isinstance(code, str) else str(code)
#                 lines.append(code.rstrip())
#         else:
#             lines.append("/* definition not provided */")
#         if ".java" in ext:
#             lines.append(f"--- END METHOD: {clean_name} no_of_lines : {no_lines} ---")
        
#         lines.append("")

#     return "\n".join(lines).rstrip()

# _CANON_DIRTY_SUFFIX_RE = re.compile(
#     r"""
#         \s*
#         (?:
#             -+>?      # -> or ---> (literal)
#           | -+&gt;?     # HTML-escaped arrow (defensive)
#           | &gt;       # just '&gt;' (defensive)
#           | ->        # normal arrow
#         )\s*$
#     """, re.IGNORECASE | re.VERBOSE
# )

# def _canonical_method_token(token: str) -> str:
#     if not token:
#         return ""
#     s = strip_no_of_lines_name(token).strip()
#     s = _CANON_DIRTY_SUFFIX_RE.sub("", s)
#     return s

# def compute_input_line_metrics(input_text: str, ext: Optional[List[str]]) -> Tuple[int, int, int]:
#     ext = _norm_ext(ext)
#     lines = input_text.splitlines()
#     ext_key = [e.strip().lower() for e in (ext or [])]

#     if ".java" in ext_key:
#         llm_fence_patterns = [
#             r"^--- BEGIN METHOD: .* ---$",
#             r"^--- END METHOD: .* ---$",
#             r"^\$\$\$ BEGIN CHILD: .* \$\$\$$",
#             r"^\$\$\$ END CHILD: .* \$\$\$$",
#         ]
   
#     llm_fence_res = [re.compile(p) for p in llm_fence_patterns]

#     header_row_re   = re.compile(r"^\*\* ")
#     placeholder_re  = re.compile(r"^/\* definition not provided \*/$")
#     if ".java" in ext:
#         props_banner = "/* --- Application Properties Bindings (from CSV) --- */"
    
#     code_lines = 0
#     props_lines = 0
#     summary_lines = 0

#     in_method = False
#     in_child = False
#     in_props = False

#     def is_llm_fence(s: str) -> bool:
#         return any(rx.match(s) for rx in llm_fence_res)

#     for raw in lines:
#         s = raw.strip()
#         if s == "":
#             continue

#         if header_row_re.match(s):
#             continue

#         if placeholder_re.match(s):
#             continue

#         if is_llm_fence(s):
#             if ".java" in ext_key:
#                 if s.startswith("--- BEGIN METHOD:"):
#                     in_method = True
#                     in_child = False
#                     in_props = False
#                 elif s.startswith("--- END METHOD:"):
#                     in_method = False
#                     in_props = False
#             elif s.startswith("$$$ BEGIN CHILD:"):
#                 in_child = True
#                 in_method = False
#                 in_props = False
#             elif s.startswith("$$$ END CHILD:"):
#                 in_child = False
#             continue

#         if in_method and s == props_banner:
#             in_props = True
#             props_lines += 1
#             continue

#         if in_child:
#             summary_lines += 1
#             continue

#         if in_method:
#             if in_props:
#                 props_lines += 1
#             else:
#                 code_lines += 1
#             continue

#         continue

#     return code_lines, props_lines, summary_lines

# def compute_document_line_breakdown(input_text: str, extension) -> Dict[str, int]:
   
#     ext = _norm_ext(extension) 

#     print(" ====== extension ====",extension)
#     lines = input_text.splitlines()

#     if ".java" in extension:
#         llm_patterns = [
#             r"^=+ COMBINED CODE FOR CHUNK: .* =+$",
#             r"^\*+\s+Begin Header\s+\*+$",
#             r"^\*+\s+End Header\s+\*+$",
#             r"^--- BEGIN METHOD: .* ---$",
#             r"^--- END METHOD: .* ---$",
#             r"^\$\$\$ BEGIN CHILD: .* \$\$\$$",
#             r"^\$\$\$ END CHILD: .* \$\$\$$",
#             r"^-{5,}$",
#         ]
    
#     llm_res = [re.compile(p) for p in llm_patterns]

#     header_row_re   = re.compile(r"^\*\* ")
#     placeholder_re  = re.compile(r"^/\* definition not provided \*/$")
#     if ".java" in extension:
#         props_banner = "/* --- Application Properties Bindings (from CSV) --- */"
    
#     strict_props_re = re.compile(r"^[A-Za-z0-9_.-]+\s*=\s*.+$")

#     total_lines = len(lines)
#     code_lines = 0
#     props_lines = 0
#     child_summary_lines = 0
#     empty_lines = 0
#     llm_ref_lines = 0

#     in_method = False
#     in_child = False
#     in_props = False

#     def is_llm_line(s: str) -> bool:
#         return any(rx.match(s) for rx in llm_res)

#     for raw in lines:
#         s = raw.strip()

#         if s == "":
#             empty_lines += 1
#             continue

#         if header_row_re.match(s):
#             llm_ref_lines += 1
#             continue

#         if placeholder_re.match(s):
#             llm_ref_lines += 1
#             continue

#         if is_llm_line(s):
#             llm_ref_lines += 1
#             if ".java" in extension:
#                 if s.startswith("--- BEGIN METHOD:"):
#                     in_method = True
#                     in_child = False
#                     in_props = False
#                 elif s.startswith("--- END METHOD:"):
#                     in_method = False
#                     in_props = False
#             elif s.startswith("$$$ BEGIN CHILD:"):
#                 in_child = True
#                 in_method = False
#                 in_props = False
#             elif s.startswith("$$$ END CHILD:"):
#                 in_child = False
#             continue

#         if in_method and s == props_banner:
#             in_props = True
#             props_lines += 1
#             continue

#         if strict_props_re.match(s) and not in_child and not in_method:
#             llm_ref_lines += 1
#             continue

#         if in_child:
#             child_summary_lines += 1
#             continue

#         if in_method:
#             if in_props:
#                 props_lines += 1
#             else:
#                 code_lines += 1
#             continue

#         llm_ref_lines += 1

#     return {
#         "total_lines": total_lines,
#         "code_lines": code_lines,
#         "props_lines": props_lines,
#         "child_summary_lines": child_summary_lines,
#         "empty_lines": empty_lines,
#         "llm_ref_lines": llm_ref_lines,
#     }

# def _format_properties_block(
#     ext: Optional[List[str]],
#     method_full_name: str,
#     props_lookup: Optional[Dict[str, List[Dict[str, str]]]],
#     comment_style: str = "c_like",
#     seen_prop_actual: Optional[Set[Tuple[str, str]]] = None,
# ) -> str:
#     print('+ extension + ',ext)
#     """
#     Per-method properties block (if attach_properties=True). Now robust to fully-qualified
#     class names by checking both full (pkg.Class) and base (Class) variants.
#     """
#     if not props_lookup or not method_full_name:
#         return ""

#     if seen_prop_actual is None:
#         seen_prop_actual = set()

#     # Normalize and split into class + method
#     canon = strip_no_of_lines_name(method_full_name).strip()
#     pre, meth = name_parts(canon)
#     cls_full = (pre or "").strip()
#     cls_base = cls_full.split(".")[-1] if cls_full else ""

#     # Try exact method keys (full and base)
#     candidates: List[Dict[str, str]] = []
#     exact_keys = []
#     if cls_full and meth:
#         exact_keys.append(_normalize_key(f"{cls_full}.{meth}"))
#     if cls_base and meth:
#         exact_keys.append(_normalize_key(f"{cls_base}.{meth}"))

#     for key in exact_keys:
#         if key in props_lookup:
#             candidates.extend(props_lookup[key])

#     # Also allow a direct exact key on the whole original token (if present)
#     direct_exact_key = _normalize_key(canon)
#     if direct_exact_key in props_lookup:
#         candidates.extend(props_lookup[direct_exact_key])

#     # Class-level fallback (full and base)
#     class_keys = []
#     if cls_full:
#         class_keys.append(_normalize_key(f"{cls_full}."))
#     if cls_base:
#         class_keys.append(_normalize_key(f"{cls_base}."))

#     for ckey in class_keys:
#         if ckey in props_lookup:
#             candidates.extend(props_lookup[ckey])

#     if not candidates:
#         return ""

#     if ".java" in ext:
#         header = "/* --- Application Properties Bindings (from CSV) --- */"
    
    
#     lines: List[str] = [header]

#     local_seen: Set[Tuple[str, str]] = set()
#     added_any = False

#     for row in candidates:
#         annotation = (row.get("Annotation") or "").strip()
#         prop      = (row.get("Property") or "").strip()
#         actual    = (row.get("Actual Value") or "").strip()
        

#         if prop and actual:
#             key = (prop.strip().lower(), actual.strip())
#             if key in local_seen:
#                 continue
#             local_seen.add(key)
#             if key in seen_prop_actual:
#                 continue
#             seen_prop_actual.add(key)

#         if annotation or prop:
#             lines.append(f"{annotation}: {prop}".rstrip())
#         if actual:
#             lines.append(f"actual_value: {actual}".rstrip())


#         lines.append("")
#         added_any = True

#     if not added_any:
#         return ""

#     return "\n".join(lines).rstrip()

# def extract_from_files_for_method(
#     method_full_name: str,
#     files: List[Path],
#     PARAGRAPH_LINEAGE_EXCEL:str,
#     CHUNK_LIMIT:int,
#     props_lookup: Optional[Dict[str, List[Dict[str, str]]]] = None
# ) -> Optional[Tuple[Path, str, str]]:
    
#     # maybe_gid = normalize_group_token(method_full_name)
#     # if maybe_gid and is_group_id(maybe_gid):
#     #     return None

#     normalized_full_name = strip_no_of_lines_name(method_full_name)
#     cls, method = name_parts(normalized_full_name)

#     # print("method :",method)
#     if not cls or not method:
#         return None

#     candidates = _find_exact_class_files(files, normalized_full_name)
#     # print("candidates : ",candidates)
#     for p in candidates:
#         # file_name = p[0].name
#         file_name = p.name
#         try:
#             text = p.read_text(encoding="utf-8", errors="replace")
#             lines = text.splitlines()
#         except Exception:
#             continue
#         lang = detect_language_by_ext(p.suffix)
#         # print("lang : ",lang)
        
#         code = extract_method_code(file_name,text, method, lang,PARAGRAPH_LINEAGE_EXCEL,CHUNK_LIMIT)
#         if code:
#             return p, code, "code"

#     return None


# def _format_properties_block_exact(
#     ext: Optional[List[str]],
#     method_name: str,
#     exact_lookup: Dict[str, List[Dict[str, str]]],
# ) -> str:
#     if not method_name:
#         return ""

#     rows = exact_lookup.get(method_name)
#     if not rows:
#         return ""

#     ext = _norm_ext(ext)

#     if ".java" in ext:
#         header = "/* --- Application Properties Bindings (from CSV) --- */"
    

#     out: List[str] = [header, f"// From: {method_name}"]
#     local_seen: Set[Tuple[str, str]] = set()
#     added = False

#     for r in rows:
#         prop   = (r.get("Property") or "").strip()
#         actual = (r.get("Actual Value") or "").strip()
#         annot  = (r.get("Annotation") or "").strip()

#         if not (prop or actual or annot):
#             continue

#         dedup_key = (prop.lower(), actual)
#         if dedup_key in local_seen:
#             continue
#         local_seen.add(dedup_key)

#         if annot:
#             out.append(f'Type: "{annot}"')
#         if prop:
#             out.append(f'Property: "{prop}"')
        
#         if actual:
#             out.append("actual_value:")
#             out.append(f'"{actual}"')


#         out.append("")
#         added = True

#     return "\n".join(out).rstrip() if added else ""

# def _normalize_key(s: str) -> str:
#     return " ".join(s.strip().split()).lower()

# def build_aggregated_app_props_for_unit(
#     ext: Optional[List[str]],
#     method_tokens: List[str],
#     props_lookup: Optional[Dict[str, List[Dict[str, str]]]],
#     exact_lookup: Dict[str, List[Dict[str, str]]],
# ) -> str:
#     """
#     Build ONE consolidated properties block for the unit.

#     GUARANTEE:
#     - If even ONE Applicable Reference exists in exact_lookup
#       for ANY method, it WILL be included.
#     - Result is built ONCE and returned ONCE.
#     """

#     ext = _norm_ext(ext)

#     # ---------------- Banner ----------------
#     if ".java" in ext:
#         banner = "/* --- Application Properties Bindings (from CSV) --- */"
    

#     # ✅ GLOBAL ACCUMULATORS (key change)
#     out: List[str] = [banner]
#     global_seen: Set[Tuple[str, str]] = set()
#     any_added = True

#     # ✅ Decide tokens to process
#     # If method_tokens miss matches but exact_lookup has data,
#     # we STILL process exact_lookup keys.
#     tokens_to_process: List[str]
#     if method_tokens:
#         tokens_to_process = method_tokens
#     else:
#         tokens_to_process = list(exact_lookup.keys())

#     # ==========================================================
#     # Process tokens ONCE
#     # ==========================================================
#     for token in tokens_to_process:
#         canon = _canonical_method_token(strip_no_of_lines_name(token))
#         if not canon:
#             continue

#         exact_rows = exact_lookup.get(canon)
#         if not exact_rows:
#             continue

#         section_lines: List[str] = [f"// From: {canon}"]

#         for r in exact_rows:
#             typ    = (r.get("Annotation") or "").strip()
#             prop   = (r.get("Property") or "").strip()
#             actual = (r.get("Actual Value") or "").strip()

#             if prop and actual:
#                 dedup_key = (prop.lower(), actual)
#                 if dedup_key in global_seen:
#                     continue
#                 global_seen.add(dedup_key)

#             if typ:
#                 section_lines.append(f'Type: "{typ}"')
#             if prop:
#                 section_lines.append(f'Property: "{prop}"')
#             if actual:
#                 out.append("actual_value:")
#                 out.append(f'"{actual}"')

#             section_lines.append("")

#         #  Persist section only once
#         out.extend(section_lines)
#         any_added = True

    
#     joint = "\n".join(out).rstrip() if any_added else ""

#     return joint



# def extract_code_for_methods_with_files(
#     ext: Optional[List[str]],
#     method_names: List[str],
#     files: List[Path],
#     PARAGRAPH_LINEAGE_EXCEL: str,
#     CHUNK_LIMIT:int,
#     props_lookup: Optional[Dict[str, List[Dict[str, str]]]] = None,
#     *,
#     attach_properties: bool = False,
# ) -> Tuple[Dict[str, str], Dict[str, str]]:

#     code_map: Dict[str, str] = {}
#     source_map: Dict[str, str] = {}
#     seen: Set[str] = set()
#     # seen_prop_actual: Set[Tuple[str, str]] = set()

#     ext = _norm_ext(ext)

#     for token in method_names:
#         normalized_key = strip_no_of_lines_name(token).strip()
#         canon_key = _canonical_method_token(normalized_key)

#         print(f"[DEBUG] Processing token: '{canon_key}'")
        
        
#         dup_key = canon_key

#         if not dup_key or dup_key in seen:
#             continue
#         seen.add(dup_key)

#         _, method_only = name_parts(strip_no_of_lines_name(canon_key))
       
        
#         res = extract_from_files_for_method(
#             canon_key, files, PARAGRAPH_LINEAGE_EXCEL,CHUNK_LIMIT, props_lookup
#         )

#         if not res:
#             source_map[canon_key] = "code"
#             code_map[canon_key] = ""
#             continue

#         pth, code, value = res
#         print(f"[DEBUG] code type: {type(code)}")
#         print(f"[DEBUG] code is str: {isinstance(code, str)}")
#         if isinstance(code, str):
#             print(f"[DEBUG] first 100 chars of code: {repr(code[:100])}")

#         source_map[canon_key] = value

#         if value == "group":
#             code_map[canon_key] = code or ""
#             continue

#         # ---------------- PROPERTIES ----------------
#         if attach_properties:

#             # ✅ 1️⃣ EXACT BEGIN TOKEN → method_name
#             props_block = _format_properties_block_exact(
#                 ext,
#                 canon_key,
#                 APP_PROPS_BY_METHOD_EXACT,
#             )

#             # ✅ 2️⃣ FALLBACK (existing behavior)
#             if not props_block:
#                 props_block = _format_properties_block(
#                     ext,
#                     canon_key,
#                     props_lookup,
#                     comment_style="c_like",
#                 )

#             if props_block:
#                 code_map[canon_key] = f"{code.rstrip()}\n\n{props_block}\n"
#             else:
#                 code_map[canon_key] = code
#         else:
#             code_map[canon_key] = code

#     return code_map, source_map


# def get_config(
#     extension,
#     project_path,
#     PARAGRAPH_LINEAGE_EXCEL,
#     OUTPUT_PATH,
#     OUT_XLSX,
#     chunks_sheet,
#     group_sheet,
#     target_sheet,
#     call_sheet,
# ):
#     """
#     Configure paths.
#     Uses ONLY the Excel file for chunks, groups, specs, and call edges.
#     call_sheet and target_sheet are OPTIONAL.
#     """

#     from pathlib import Path
#     import pandas as pd

#     app_props_xlsx = Path(PARAGRAPH_LINEAGE_EXCEL)

#     # Initialize all dataframes as None
#     df = None
#     df_1 = None

#     with pd.ExcelFile(app_props_xlsx, engine="openpyxl") as xf:

#         lower_map = {name.lower(): name for name in xf.sheet_names}

#         # ---------- SPEC SHEET (OPTIONAL) ----------
#         if target_sheet.lower() not in lower_map:
#             print(
#                 f"⚠️ Sheet '{target_sheet}' not found. "
#                 f"Available sheets: {', '.join(xf.sheet_names)}. Skipping spec sheet."
#             )
#             spec_sheet_name = None
#         else:
#             spec_sheet_name = lower_map[target_sheet.lower()]
#             df = pd.read_excel(xf, sheet_name=spec_sheet_name)

#         # ---------- CALL SHEET (OPTIONAL) ----------
#         if call_sheet and call_sheet.lower() in lower_map:
#             call_sheet_name = lower_map[call_sheet.lower()]
#             df_1 = pd.read_excel(xf, sheet_name=call_sheet_name)
#         else:
#             call_sheet_name = None

#     # ---------- CSV OUTPUT HANDLING ----------
#     # SPEC CSV only if df exists
#     if df is not None:
#         app_props_csv = app_props_xlsx.with_name(
#             f"{app_props_xlsx.stem}_{spec_sheet_name}.csv"
#         )
#         df.to_csv(app_props_csv, index=False)
#     else:
#         app_props_csv = None

#     # CALL CSV only if call sheet exists
#     if df_1 is not None:
#         call_csv = app_props_xlsx.with_name(
#             f"{app_props_xlsx.stem}_{call_sheet_name}.csv"
#         )
#         df_1.to_csv(call_csv, index=False)
#     else:
#         call_csv = None

#     # ---------- OTHER CONFIG ----------
#     exts_str = extension
#     PROCESS_MODE = "all"
#     TARGET_CHUNK_ID = None

#     out_dir = Path(OUTPUT_PATH)
#     out_dir.mkdir(parents=True, exist_ok=True)

#     # Normalize extensions
#     exts = {
#         x.strip() if x.strip().startswith(".") else f".{x.strip()}"
#         for x in (exts_str or [])
#         if isinstance(x, str) and x.strip()
#     }

    
#     # ---------- RETURN EVERYTHING ----------
#     return (
#         OUT_XLSX,
#         chunks_sheet,
#         group_sheet,
#         project_path,
#         out_dir,
#         exts,
#         app_props_csv,   # may be None
#         call_csv,        # may be None
#         PROCESS_MODE,
#         TARGET_CHUNK_ID,
#         PARAGRAPH_LINEAGE_EXCEL,
#     )


# def collected_chunks_recursively(start_chunk_id, parent_to_children):
#     visited = set()

#     def dfs(chunk_id):
#         if chunk_id in visited:
#             return
#         visited.add(chunk_id)

#         for child in parent_to_children.get(chunk_id, []):
#             dfs(child)
#     dfs(start_chunk_id)
#     return visited

# def group_summary_path(gid: str) -> Path:
#     return GROUP_SUMMARIES_DIR / f"{gid}.yaml" if GROUP_SUMMARIES_DIR else Path(f"{gid}.yaml")

# def group_combined_path(gid: str) -> Path:
#     return GROUP_COMBINED_DIR / f"{gid}.txt" if GROUP_COMBINED_DIR else Path()

# def chunk_summary_path(cid: str) -> Path:
#     return SUMMARIES_DIR / f"{cid}.yaml" if SUMMARIES_DIR else Path()

# def chunk_combined_path(cid: str) -> Path:
#     return COMBINED_DIR / f"{cid}.txt" if COMBINED_DIR else Path()

# # ==========================================================
# # Group mappings loaders (Excel / CSV)
# # ==========================================================
# def load_group_mappings_csv(csv_path: Optional[Path]) -> Dict[str, List[str]]:
#     mapping: Dict[str, List[str]] = {}
#     if not csv_path:
#         # print("[GroupMap] No CSV path provided.")
#         return mapping
#     if not csv_path.exists():
#         # print(f"[GroupMap] CSV path does not exist: {csv_path}")
#         return mapping

#     # print(f"[GroupMap] Loading group mappings from: {csv_path}")
#     try:
#         with csv_path.open("r", encoding="ISO-8859-1", errors="replace") as f:
#             reader = csv.DictReader(f)
#             # print(f"[GroupMap] CSV headers: {reader.fieldnames}")
#             row_count = 0
#             for r in reader:
#                 row_count += 1
#                 gid = (r.get("chunk_id") or r.get("Group") or "").strip()
#                 methods_raw = (r.get("methods_in_chunk") or r.get("Methods") or "").strip()
#                 subpath_raw = (r.get("Subpath") or "").strip()

#                 if not gid or not is_group_id(gid):
#                     continue

#                 methods: List[str] = []
#                 if methods_raw:
#                     methods.extend([m.strip() for m in re.split(r"[,\n;]+", methods_raw) if m.strip()])

#                 if subpath_raw:
#                     parts = re.split(r"(?:->|,|\n|;)+", subpath_raw)
#                     methods.extend([p.strip() for p in parts if p.strip()])

#                 seen: Set[str] = set()
#                 clean: List[str] = []
#                 for m in methods:
#                     if m and m not in seen:
#                         seen.add(m)
#                         clean.append(m)

#                 mapping[gid] = clean

#             # print(f"[GroupMap] Loaded {len(mapping)} groups.")
#     except Exception as e:
#         print(f"[GroupMap] Failed to load CSV: {e}")
#     return mapping

# def load_group_mappings_df(df: pd.DataFrame) -> Dict[str, List[str]]:
#     cols_lower = {c.lower(): c for c in df.columns}
#     if ("group" not in cols_lower) or ("methods" not in cols_lower):
#         raise ValueError("[GroupMap] Group Mappings Sheet missing required columns: ['Group', 'Methods']")
#     group_col = cols_lower["group"]
#     methods_col = cols_lower["methods"]
#     subpath_col = cols_lower.get("subpath")

#     mapping: Dict[str, List[str]] = {}
#     # print(f"[GroupMap] Loading group mappings from Excel DataFrame with {len(df)} rows.")
#     for _, r in df.iterrows():
#         gid = str(r.get(group_col) or "").strip()
#         methods_raw = str(r.get(methods_col) or "").strip()
#         subpath_raw = str(r.get(subpath_col) or "").strip() if subpath_col else ""

#         if not gid or not is_group_id(gid):
#             continue

#         methods: List[str] = []
#         if methods_raw:
#             methods.extend([m.strip() for m in re.split(r"[,\n;]+", methods_raw) if m.strip()])

#         if subpath_raw:
#             parts = re.split(r"(?:->|,|\n|;)+", subpath_raw)
#             methods.extend([p.strip() for p in parts if p.strip()])

#         seen: Set[str] = set()
#         clean: List[str] = []
#         for m in methods:
#             if m and m not in seen:
#                 seen.add(m)
#                 clean.append(m)

#         mapping[gid] = clean

#     # print(f"[GroupMap] Loaded {len(mapping)} groups from Excel.")
#     return mapping

# # ==========================================================
# # Group / Chunk / Call ensure & builders (CODE-ONLY)
# # ==========================================================
# def ensure_group_combined_only(
#     ext: Optional[List[str]],
#     gid: str,
#     all_source_files: List[Path],
#     PARAGRAPH_LINEAGE_EXCEL:str,
#     CHUNK_LIMIT:int,
#     level: str,
#     app_props_lookup: Optional[Dict[str, List[Dict[str, str]]]] = None,
# ) -> str:
#     combined_text = build_combined_input_for_group(ext, gid, all_source_files,PARAGRAPH_LINEAGE_EXCEL,CHUNK_LIMIT,level,app_props_lookup)
#     group_combined_path(gid).write_text(combined_text, encoding="utf-8")
#     # print(f"[EnsureGroup] Group combined input (code-only) written: {group_combined_path(gid)} (len={len(combined_text)})")
#     return str(group_combined_path(gid))

# def build_combined_input_for_group(
#     ext: Optional[List[str]],
#     gid: str,
#     all_source_files: List[Path],
#     PARAGRAPH_LINEAGE_EXCEL:str,
#     CHUNK_LIMIT:int,
#     level: str,
#     app_props_lookup: Optional[Dict[str, List[Dict[str, str]]]] = None,
# ) -> str:
#     ext = _norm_ext(ext) 
#     tokens = GROUP_METHODS_MAP.get(gid, [])
#     # print(f"[GroupBuild] {gid} mapped tokens: {tokens}")


#     extract_tokens: List[str] = []
#     for t in tokens:
#         if is_chunk_id(t):
#             continue
#         # g = normalize_group_token(t)
#         # if g and is_group_id(g):
#         #     continue
#         if "." in t:
#             extract_tokens.append(strip_no_of_lines_name(t))
#         else:
#             print(f"[GroupBuild] Ignored token (not method): '{t}'")

#     global CURRENT_GROUP_ID
#     CURRENT_GROUP_ID = gid
#     if level == "program":
#         attach_property=False
#     if level == "process":
#         attach_property = True
#         try:
#             # IMPORTANT: no per-method properties; aggregated banner appended at bottom
#             code_map, source_map = extract_code_for_methods_with_files(
#                 ext,
#                 extract_tokens,
#                 all_source_files,
#                 PARAGRAPH_LINEAGE_EXCEL,
#                 CHUNK_LIMIT=CHUNK_LIMIT,
#                 props_lookup=app_props_lookup,
#                 attach_properties=False,
#             )
#         finally:
#             CURRENT_GROUP_ID = None
        
    

#     body = _format_code_map_as_combined_snippet(
#         ext,
#         gid,
#         code_map,
#         source_map,
#         parent_entity=None,
#         unit_type="group",
#     )

#     final_body = body


#     # # --- NEW: Append one aggregated properties block for the group (bottom) ---
    
#     if level == "process":
#         # print("call_1")
#         agg_props = build_aggregated_app_props_for_unit(
#             ext,
#             method_tokens=extract_tokens,
#             props_lookup=app_props_lookup,
#             exact_lookup=APP_PROPS_BY_METHOD_EXACT,
#         )
        
#         pe_no_lines = _count_lines(agg_props)
#         pe_block_title = f"{gid}"

#         if ".java" in ext:
#             fenced_props = (
#                 f"--- BEGIN METHOD: {pe_block_title} no_of_lines : {pe_no_lines} ---\n"
#                 f"{agg_props}\n"
#                 f"--- END METHOD: {pe_block_title} no_of_lines : {pe_no_lines} ---"
#             )
        
#         final_body = f"{final_body}\n\n{fenced_props}"


#     # --- Header + metrics AFTER append ---
#     meta_by_file = EXTRACTION_META_BY_GROUP.get(gid, {})
#     header_without_metrics = build_unit_metadata_block(meta_by_file) if meta_by_file else ""

#     provisional_text = f"{header_without_metrics}\n{final_body}" if header_without_metrics else final_body
#     breakdown = compute_document_line_breakdown(provisional_text, ext)
#     header_with_metrics = build_unit_metadata_block(meta_by_file, breakdown) if meta_by_file else ""

#     # SAFE: preserve entire final_body (including appended props)
#     if header_with_metrics:
#         lines = final_body.splitlines()
#         if lines:
#             final = "\n".join([lines[0], "", header_with_metrics] + lines[1:])
#         else:
#             final = header_with_metrics
#     else:
#         final = final_body

#     # print(f"[GroupBuild] Final combined length for {gid}: {len(final)}")
#     return final

# def ensure_chunk_combined_only(
#     ext: Optional[List[str]],
#     cid: str,
#     level: str,
#     build_combined_input_for_chunk_fn,
# ) -> str:
#     combined_text = build_combined_input_for_chunk_fn(cid, ext,level)
#     chunk_combined_path(cid).write_text(combined_text, encoding="utf-8")
#     # print(f"[EnsureChunk] Combined input (code-only) written: {chunk_combined_path(cid)} (len={len(combined_text)})")
#     return str(chunk_combined_path(cid))

# # ==========================================================
# # Main
# # ==========================================================
# def code_extraction(
#     project_path,
#     extension,
#     PARAGRAPH_LINEAGE_EXCEL,
#     OUTPUT_PATH,
#     OUT_XLSX,
#     CHUNK_LIMIT,
#     level,
#     chunks_sheet,
#     group_sheet,
#     spec_sheet,
#     call_sheet,
#     ui_extension
# ):
#     print(" project_path : ",project_path)
#     start_time = datetime.now()
#     log_time(f"Code Extraction START")
    
#     print("================= code_extraction =========================")
#     extension = _norm_ext(extension)
#     (excel_input, chunks_sheet, groups_sheet,
#      project_root, out_dir, exts, app_props_csv,call_csv, PROCESS_MODE, TARGET_CHUNK_ID, PARAGRAPH_LINEAGE_EXCEL) = get_config(
#         extension, project_path, PARAGRAPH_LINEAGE_EXCEL, OUTPUT_PATH, OUT_XLSX, chunks_sheet, group_sheet, spec_sheet,call_sheet
#     )

#     global SUMMARIES_DIR, COMBINED_DIR, GROUP_SUMMARIES_DIR, GROUP_COMBINED_DIR, REPORTS_DIR
#     SUMMARIES_DIR = (Path(out_dir) / "summaries")
#     COMBINED_DIR = (Path(out_dir) / "combined_inputs")
#     GROUP_SUMMARIES_DIR = (Path(out_dir) / "group_summaries")
#     GROUP_COMBINED_DIR = (Path(out_dir) / "group_combined_inputs")
#     REPORTS_DIR = (Path(out_dir) / "reports")
#     for d in (SUMMARIES_DIR, COMBINED_DIR, GROUP_SUMMARIES_DIR, GROUP_COMBINED_DIR, REPORTS_DIR):
#         d.mkdir(parents=True, exist_ok=True)

#     index_csv = Path(out_dir) / "chunk_summary_index.csv"
#     out_xlsx  = Path(out_dir) / "chunk_flow_reusability.xlsx"
#     llm_report_csv = REPORTS_DIR / "llm_call_report.csv"
#     llm_report_xlsx = REPORTS_DIR / "llm_call_report.xlsx"

#     df_chunks: Optional[pd.DataFrame] = None
#     df_groups: Optional[pd.DataFrame] = None
#     df_calls: Optional[pd.DataFrame] = None

#     excel_path = Path(excel_input) if excel_input else None
#     if not (excel_input and excel_path.exists()):
#         raise FileNotFoundError(f"[ERROR] Excel file not found: {excel_input}")
#     try:
#         xls = pd.ExcelFile(excel_input, engine="openpyxl")

#         if chunks_sheet in xls.sheet_names:
#             df_chunks = pd.read_excel(xls, sheet_name=chunks_sheet)
#         else:
#             raise ValueError(f"[ERROR] Chunks sheet '{chunks_sheet}' not found in Excel.")

#         if groups_sheet in xls.sheet_names:
#             df_groups = pd.read_excel(xls, sheet_name=groups_sheet)
#         else:
#             df_groups = None

#     except Exception as e:
#         raise RuntimeError(f"[ERROR] Failed to read Excel '{excel_input}': {e}")

#     require_columns(
#         df_chunks,
#         ["chunk_id", "parent_entity", "clear_code_sum", "direct_children", "descendants_count", "methods_in_chunk"],
#         "Chunks Input",
#     )

#     global GROUP_METHODS_MAP
#     if df_groups is not None:
#         GROUP_METHODS_MAP = load_group_mappings_df(df_groups)
#     else:
#         GROUP_METHODS_MAP = {}

#     chunk_meta: Dict[str, Dict[str, str]] = {}
#     chunk_to_tokens: Dict[str, List[str]] = {}
#     parent_to_children: Dict[str, List[str]] = {}
#     all_chunk_ids: Set[str] = set()

#     for _, row in df_chunks.iterrows():
#         cid = str(row["chunk_id"]).strip()
#         if not cid or cid.lower() in ("nan", "none"):
#             continue
#         all_chunk_ids.add(cid)

#         chunk_meta[cid] = {
#             "parent_entity": str(row.get("parent_entity") or "").strip(),
#             "clear_code_sum": str(row.get("clear_code_sum") or "").strip(),
#             "descendants_count": str(row.get("descendants_count") or "").strip(),
#         }

#         children = split_names(row.get("direct_children") or "")
#         children = [c for c in children if c and c.lower() not in ("nan", "none")]
#         parent_to_children[cid] = children

#         tokens = split_names(row.get("methods_in_chunk") or "")
#         chunk_to_tokens[cid] = tokens

#     PROCESS_MODE = "all" if PROCESS_MODE is None else PROCESS_MODE

#     if PROCESS_MODE == "single":
#         valid_chunks = collected_chunks_recursively(TARGET_CHUNK_ID, parent_to_children)
#         all_chunk_ids = set(valid_chunks)

#         chunk_meta = {cid: meta for cid, meta in chunk_meta.items() if cid in all_chunk_ids}
#         chunk_to_tokens = {cid: toks for cid, toks in chunk_to_tokens.items() if cid in all_chunk_ids}
#         parent_to_children = {cid: kids for cid, kids in parent_to_children.items() if cid in all_chunk_ids}

#     parent_ids = {cid for cid, ch in parent_to_children.items() if len(ch) > 0}
#     child_ids = {c for ch in parent_to_children.values() for c in ch}

#     # ---- App props loader (existing) ----
#     app_props_lookup = None
#     if app_props_csv:
#         app_props_path = Path(app_props_csv)
#         if app_props_path.exists():
#             app_props_lookup = load_app_properties_csv(app_props_path, extension)

#     # ---- NEW: Edges loader (optional path) ----
#     global EDGES_LOOKUP
#     EDGES_LOOKUP = {}
#     if call_csv:
#         try:
#             edges_path = Path(call_csv)
#             if edges_path.exists():
#                 EDGES_LOOKUP = load_edges_csv(edges_path)
#         except Exception as e:
#             print(f"[Main][WARN] Failed to load edges CSV '{call_csv}': {e}")

#     global PROJECT_ROOT, FILE_ID_MAP
#     src_root = Path(project_root)
#     PROJECT_ROOT = src_root
#     all_source_files = list_source_files(src_root, exts)
#     FILE_ID_MAP = {}
#     for idx, p in enumerate(all_source_files, start=1):
#         FILE_ID_MAP[p] = f"f{idx}"
#     global summary_path_by_chunk
#     processing_state: Dict[str, str] = {}
#     summary_path_by_chunk = {}
#     llm_rows: List[Dict[str, str]] = []
#     index_rows: List[Dict[str, str]] = []

#     def process_chunk_recursive(cid: str, ext: Optional[List[str]], level: str):
#         state = processing_state.get(cid)
#         if state == "done":
#             return
#         if state == "pending":
#             return
#         processing_state[cid] = "pending"
#         ensure_chunk_combined_only(extension, cid, level, build_combined_input_for_chunk)
#         combined_text = chunk_combined_path(cid).read_text(encoding="utf-8", errors="replace")
#         code_lines, props_lines, summary_lines = compute_input_line_metrics(combined_text, extension)
#         llm_rows.append({
#             "unit_type": "chunk",
#             "chunk_id": cid,
#             "code_lines": str(code_lines),
#             "application_properties_lines": str(props_lines),
#             "summary_lines": str(summary_lines),
#             "combined_input_path": str(chunk_combined_path(cid)),
#             "yaml_summary_path": "",
#             "timestamp": datetime.now().isoformat(timespec="seconds"),
#         })
#         processing_state[cid] = "done"
#         # print(f"[Process] Completed chunk: {cid}")


#     def build_combined_input_for_chunk(cid: str, ext: Optional[List[str]],level: str) -> str:
#         ext = _norm_ext(extension)          # <--- ADD
#         ui_ext_norm = _norm_ext(ui_extension) 

#         attach_property = (level == "process")
#         tokens = chunk_to_tokens.get(cid, [])
#         children = parent_to_children.get(cid, [])

#         extract_tokens: List[str] = []
#         for t in tokens:
#             if is_chunk_id(t):
#                 continue
#             if "." in t:
#                 normalized_method = strip_no_of_lines_name(t)
#                 extract_tokens.append(normalized_method)
#             else:
#                 print(f"[Build] Ignored token (neither method nor group): '{t}'")

#         normalized_primary_tokens = [
#             _normalize_key(strip_no_of_lines_name(t.lower()))
#             for t in extract_tokens
#         ]

#         dependency_tokens = set()

#         for t in normalized_primary_tokens:
#             deps = EDGES_LOOKUP.get(t, [])
#             for e in deps:
#                 imported_norm = e["imported_name"].lower().strip()
#                 if imported_norm:
#                     dependency_tokens.add(imported_norm)
#         extract_tokens_extended = list(extract_tokens)

#         def _unique_preserve_order(items):
#             seen = set()
#             out = []
#             for x in items:
#                 if x not in seen:
#                     seen.add(x)
#                     out.append(x)
#             return out
#         for dep in dependency_tokens:
#             extract_tokens_extended.append(dep)
#             extract_tokens_extended.append(f"{dep}.{dep}")  # fallback form
#         extract_tokens_extended = _unique_preserve_order(extract_tokens_extended)
#         global CURRENT_CHUNK_ID
#         CURRENT_CHUNK_ID = cid
#         try:
#             code_map, source_map = extract_code_for_methods_with_files(
#                 extension,
#                 extract_tokens_extended,
#                 all_source_files,
#                 PARAGRAPH_LINEAGE_EXCEL,
#                 CHUNK_LIMIT =CHUNK_LIMIT,
#                 props_lookup=app_props_lookup,
#                 attach_properties=False,
#             )
#         finally:
#             CURRENT_CHUNK_ID = None

#         ordered_code_map = {}
#         ordered_source_map = {}

#         for token in extract_tokens_extended:
#             method_only = token.split()[0]   # get ONLY "ACCTAPPR.INIT"
#             key_norm = strip_no_of_lines_name(method_only)

#             if key_norm in code_map:
#                 ordered_code_map[key_norm] = code_map[key_norm]
#                 ordered_source_map[key_norm] = source_map.get(key_norm)
#         for k in code_map:
#             if k not in ordered_code_map:
#                 ordered_code_map[k] = code_map[k]
#                 ordered_source_map[k] = source_map.get(k)
#         combined_parts: List[str] = []
#         parent_entity = (chunk_meta.get(cid) or {}).get("parent_entity") or ""
#         body = _format_code_map_as_combined_snippet(
#             extension,
#             cid,
#             ordered_code_map,
#             ordered_source_map,
#             parent_entity=parent_entity,
#             unit_type="chunk",
#         )
#         combined_parts.append(body)

#         final_body = "\n".join(combined_parts).rstrip()

#         call_graph_lines = []
#         normalized_primary_tokens = [
#             _normalize_key(strip_no_of_lines_name(t.lower()))
#             for t in extract_tokens
#         ]

#         for t in normalized_primary_tokens:
#             deps = []
#             for key, rows in EDGES_LOOKUP.items():
#                 if key.startswith(t + "."):
#                     deps.extend(rows)

#             for e in deps:
#                 src = e["name"]
#                 tgt = e["imported_name"]
#                 etype = e.get("edge_type", "")
#                 call_graph_lines.append(f"{src}  →  {tgt}   ({etype})")

#         if call_graph_lines:
#             call_graph_block = (
#                 "\n--- CALL GRAPH ---\n" +
#                 "\n".join(call_graph_lines) +
#                 "\n--- END CALL GRAPH ---\n"
#             )
#             final_body = f"{final_body}\n\n{call_graph_block}"
        
#         if level == "process":
#             agg_props = build_aggregated_app_props_for_unit(
#                 extension,
#                 method_tokens=extract_tokens_extended,
#                 props_lookup=app_props_lookup,
#                 exact_lookup=APP_PROPS_BY_METHOD_EXACT,
#             )

#             pe_no_lines = _count_lines(agg_props)
#             pe_block_title = f"{cid}"

#             if ".java" in extension:
#                 fenced_props = (
#                     f"--- BEGIN METHOD: {pe_block_title} no_of_lines : {pe_no_lines} ---\n"
#                     f"{agg_props}\n"
#                     f"--- END METHOD: {pe_block_title} no_of_lines : {pe_no_lines} ---"
#                 )
            

#             final_body = f"{final_body}\n\n{fenced_props}"

#             meta_by_file = EXTRACTION_META_BY_CHUNK.get(cid, {})
#             header_without_metrics = build_unit_metadata_block(meta_by_file) if meta_by_file else ""
#             provisional_text = f"{header_without_metrics}\n{final_body}" if header_without_metrics else final_body
#             breakdown = compute_document_line_breakdown(provisional_text, extension)
#             header_with_metrics = build_unit_metadata_block(meta_by_file, breakdown) if meta_by_file else ""

#             if header_with_metrics:
#                 lines = final_body.splitlines()
#                 if lines:
#                     final_body = "\n".join([lines[0], "", header_with_metrics] + lines[1:])
#                 else:
#                     final_body = header_with_metrics

#         # ✅ ALWAYS RETURN
#         return final_body

#     if PROCESS_MODE == "single":
#         target_methods = set()
#         for cid in all_chunk_ids:
#             target_methods.update(chunk_to_tokens.get(cid, []))
#         filtered_group_ids = []
#         for gid, tokens in GROUP_METHODS_MAP.items():
#             for t in tokens:
#                 if t in target_methods:
#                     filtered_group_ids.append(gid)
#                     break
#         for gid in sorted(filtered_group_ids):
#             ensure_group_combined_only(extension, gid, all_source_files,PARAGRAPH_LINEAGE_EXCEL,CHUNK_LIMIT,level, app_props_lookup)

#     else:
#         all_group_ids = sorted(GROUP_METHODS_MAP.keys())
#         for gid in all_group_ids:
#             ensure_group_combined_only(extension, gid, all_source_files,PARAGRAPH_LINEAGE_EXCEL,level,CHUNK_LIMIT, app_props_lookup)

#     for cid in sorted(all_chunk_ids):
#         process_chunk_recursive(cid, extension,level)

#     for cid in all_chunk_ids:
#         role = "parent" if cid in parent_ids else "child"
#         meta = chunk_meta.get(cid, {})
#         index_rows.append(
#             {
#                 "unit_type": "chunk",
#                 "chunk_id": cid,
#                 "type": role,
#                 "parent_entity": meta.get("parent_entity", ""),
#                 "clear_code_sum": meta.get("clear_code_sum", ""),
#                 "descendants_count": meta.get("descendants_count", ""),
#                 "combined_input_path": str(chunk_combined_path(cid)) if chunk_combined_path(cid).exists() else "",
#                 "yaml_summary_path": "",
#             }
#         )

#     for gid in GROUP_METHODS_MAP.keys():
#         index_rows.append(
#             {
#                 "unit_type": "group",
#                 "chunk_id": gid,
#                 "type": "group",
#                 "parent_entity": "",
#                 "clear_code_sum": "",
#                 "descendants_count": "",
#                 "combined_input_path": str(group_combined_path(gid)) if group_combined_path(gid).exists() else "",
#                 "yaml_summary_path": "",
#             }
#         )

#     index_df = pd.DataFrame(index_rows)
#     Path(index_csv).write_text(index_df.to_csv(index=False), encoding="utf-8")
#     # print(f"[Main] Index CSV written: {index_csv}")
#     try:
#         with pd.ExcelWriter(out_xlsx, engine="openpyxl") as xw:
#             index_df.to_excel(xw, sheet_name="unit_index", index=False)
#     except Exception as e:
#         print(f"[WARN] Could not write Excel index: {e}")

#     llm_df = pd.DataFrame(llm_rows)
#     llm_report_csv.write_text(llm_df.to_csv(index=False), encoding="utf-8")
#     try:
#         with pd.ExcelWriter(llm_report_xlsx, engine="openpyxl") as xw:
#             llm_df.to_excel(xw, sheet_name="llm_calls", index=False)
#         # print(f"[Main] LLM Report Excel written: {llm_report_xlsx}")
#     except Exception as e:
#         print(f"[WARN] Could not write LLM report Excel: {e}")

#     end_time = datetime.now()

#     elapsed = (end_time - start_time).total_seconds()
#     log_time(
#         f"Code extraction END | "
#         f"Duration={elapsed:.3f} sec"
#     )
#     return out_dir,COMBINED_DIR

# def load_app_properties_csv(csv_path: Optional[Path], ext: Optional[List[str]]) -> Dict[str, List[Dict[str, str]]]:
#     global APP_PROPS_BY_METHOD_EXACT

#     lookup: Dict[str, List[Dict[str, str]]] = {}
#     APP_PROPS_BY_METHOD_EXACT = {}

#     if not csv_path or not csv_path.exists():
#         # print(f"[AppProps] CSV missing or invalid: {csv_path}")
#         return lookup

#     # print(f"[AppProps] Loading from: {csv_path}")

#     with csv_path.open("r", encoding="utf-8", errors="replace") as f:
#         reader = csv.DictReader(f)

#         def get_val(r: dict, *names: str) -> str:
#             for n in names:
#                 for k in r:
#                     if k and k.strip().lower() == n.lower():
#                         return (r.get(k) or "").strip()
#             return ""

#         for r in reader:
#             annotation = get_val(r, "Annotation", "Type", "spec_type")
#             prop       = get_val(r, "Property", "variable", "PropertyName")
#             actual     = get_val(r, "Actual Value", "actual_value", "Value")
#             method_raw = get_val(r, "method_name", "MethodName", "ProcedureName", "ParagraphName")

#             # -------------------------
#             # ✅ EXACT BEGIN TOKEN MAP
#             # -------------------------
#             if method_raw:
#                 method_key = strip_no_of_lines_name(method_raw).strip()
#                 print("method_key : ",method_key)
#                 APP_PROPS_BY_METHOD_EXACT.setdefault(method_key, []).append({
#                     "Annotation": annotation,
#                     "Property": prop,
#                     "Actual Value": actual,
#                 })

#             # -------------------------
#             # ✅ EXISTING NORMALIZED MAP (UNCHANGED)
#             # -------------------------
#             file_name_raw = get_val(r, "FileName", "Program", "ProgramName")
#             file_stem = Path(file_name_raw).stem.lower() if file_name_raw else ""

#             if file_stem and method_raw:
#                 norm_key = _normalize_key(f"{file_stem}.{method_raw}")
#                 lookup.setdefault(norm_key, []).append({
#                     "Annotation": annotation,
#                     "Property": prop,
#                     "Actual Value": actual,
#                 })

#             elif file_stem:
#                 norm_key = _normalize_key(f"{file_stem}.")
#                 lookup.setdefault(norm_key, []).append({
#                     "Annotation": annotation,
#                     "Property": prop,
#                     "Actual Value": actual,
#                 })

#     print(
#         f"[AppProps] Loaded exact method keys: {len(APP_PROPS_BY_METHOD_EXACT)}, "
#         f"normalized keys: {len(lookup)}"
#     )

#     return lookup

# def load_edges_csv(csv_path_1: Optional[Path]) -> Dict[str, List[Dict[str, str]]]:

#     global EDGES_BY_IMPORTED_EXACT
#     lookup: Dict[str, List[Dict[str, str]]] = {}
#     EDGES_BY_IMPORTED_EXACT = {}

#     if not csv_path_1 or not csv_path_1.exists():
#         # print(f"[Edges] CSV missing or invalid: {csv_path_1}")
#         return lookup

#     # print(f"[Edges] Loading from: {csv_path_1}")
#     with csv_path_1.open("r", encoding="utf-8", errors="replace") as f:
#         reader = csv.DictReader(f)
#         raw_headers = reader.fieldnames or []
#         # print(f"[Edges] Headers: {raw_headers}")

#         def get_val(r: Dict[str, str], *candidates: str) -> str:
#             # Header-tolerant extraction (same strategy as load_app_properties_csv)
#             norm_map = {(k or "").strip(): v for k, v in r.items()}
#             for cand in candidates:
#                 for key_variant in (cand, cand.strip(), cand.replace("\r", "")):
#                     if key_variant in r and r[key_variant] is not None:
#                         return r[key_variant]
#                     if key_variant in norm_map and norm_map[key_variant] is not None:
#                         return norm_map[key_variant]
#                 # case-insensitive fallback
#                 for k in r.keys():
#                     if k and k.strip().lower() == cand.strip().lower():
#                         val = r.get(k)
#                         if val is not None:
#                             return val
#             return ""

#         count = 0
#         normalized_key_insertions = 0
#         exact_imported_insertions = 0

#         for r in reader:
#             count += 1

#             # Core identifiers (normalized)
#             name_raw = (get_val(r, "name") or "").strip()
#             imported_name_raw = (get_val(r, "imported_name") or "").strip()
#             name_norm = name_raw.lower()
#             imported_name_norm = imported_name_raw.lower()

#             # Other fields for context (kept similar to your std dict style)
#             row_id = (get_val(r, "id") or "").strip()
#             typ = (get_val(r, "Type", "type") or "").strip()
#             total_lines = (get_val(r, "Total_lines", "total_lines") or "").strip()
#             imported_id = (get_val(r, "imported_id") or "").strip()
#             imported_lines = (get_val(r, "imported_lines") or "").strip()
#             edge_type = (get_val(r, "edge_type") or "").strip()

#             std = {
#                 "id": row_id,
#                 "Type": typ,
#                 "name": name_norm,
#                 "Total_lines": total_lines,
#                 "imported_id": imported_id,
#                 "imported_name": imported_name_norm,
#                 "imported_lines": imported_lines,
#                 "edge_type": edge_type,
#             }

#             # ---- Normalized lookup keys (mirrors props loader style) ----
#             if name_norm and imported_name_norm:
#                 key_norm = _normalize_key(strip_no_of_lines_name(f"{name_norm}.{imported_name_norm}"))
#                 lookup.setdefault(key_norm, []).append(std)
#                 normalized_key_insertions += 1
#             elif name_norm:
#                 key_norm = _normalize_key(f"{name_norm}.")
#                 lookup.setdefault(key_norm, []).append(std)
#                 normalized_key_insertions += 1
#             elif imported_name_norm:
#                 key_norm = _normalize_key(f".{imported_name_norm}")
#                 lookup.setdefault(key_norm, []).append(std)
#                 normalized_key_insertions += 1

#             # ---- Exact map keyed ONLY by imported_name (like props' exact by method) ----
#             if imported_name_norm:
#                 EDGES_BY_IMPORTED_EXACT.setdefault(imported_name_norm, []).append(
#                     {
#                         "id": row_id,
#                         "Type": typ,
#                         "name": name_norm,
#                         "imported_name": imported_name_norm,
#                         "edge_type": edge_type,
#                     }
#                 )
#                 exact_imported_insertions += 1


#     return lookup

import os
import re
import csv
import json
import logging
import traceback
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Set, Sequence, Any
from datetime import datetime
import pandas as pd
# import requests  # not needed anymore since we removed LLM calls

def log_time(message):
    with open("execution_log_service.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} - {message}\n")

# ==========================================================
# Configuration
# ==========================================================

LOGGER_NAME = "copilotclient"
LOG_FILE = "copilot_errors.log"

logger = logging.getLogger(LOGGER_NAME)
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    logger.addHandler(ch)

# ------------------ Global paths/state (initialized in main) ---------------
SUMMARIES_DIR: Optional[Path] = None              # chunks YAML (not used in code-only mode)
COMBINED_DIR: Optional[Path] = None               # chunks combined txt
GROUP_SUMMARIES_DIR: Optional[Path] = None        # groups YAML (not used in code-only mode)
GROUP_COMBINED_DIR: Optional[Path] = None         # groups combined txt
REPORTS_DIR: Optional[Path] = None

# ------------------ Memoized paths -----------------------------------------
summary_path_by_chunk: Dict[str, str] = {}  # not used in code-only mode
group_summary_path_by_gid: Dict[str, str] = {}  # not used in code-only mode

# ------------------ NEW: Project/file metadata globals ----------------------
PROJECT_ROOT: Optional[Path] = None
FILE_ID_MAP: Dict[Path, str] = {}  # e.g., {Path(.../Foo.java): 'f33'}

# Accumulate per-chunk/group file metadata (file ids, ranges, totals)
EXTRACTION_META_BY_CHUNK: Dict[str, Dict[Path, Dict[str, object]]] = {}
EXTRACTION_META_BY_GROUP: Dict[str, Dict[Path, Dict[str, object]]] = {}

# Track the unit currently being built so we can accumulate metadata
CURRENT_CHUNK_ID: Optional[str] = None
CURRENT_GROUP_ID: Optional[str] = None

# ------------------ NEW: Group mappings ------------------------------------
GROUP_METHODS_MAP: Dict[str, List[str]] = {}  # gid -> [methods and/or nested groups]

# ------------------ NEW: Called-files mappings -----------------------------

# ------------------ App props (normalized & exact for COBOL) ---------------
APP_PROPS_BY_METHOD_EXACT: Dict[str, List[Dict[str, str]]] = {}

# ------------------ Edges (normalized & exact by imported_name) -------------
EDGES_BY_IMPORTED_EXACT: Dict[str, List[Dict[str, str]]] = {}

# ==========================================================
# Token detection
# ==========================================================
CHUNK_ID_RE = re.compile(r"^C\d+", re.IGNORECASE)

# Support both G<number> and GN<number>
GROUP_ID_RE = re.compile(r"^G(?:N)?\d+$", re.IGNORECASE)

# Extract a group id embedded in longer tokens (e.g., "GN1 no_of_lines")
GROUP_ID_EXTRACT_RE = re.compile(r"\bG(?:N)?\d+\b", re.IGNORECASE)

# Strip "no_of_lines : X" suffix from method tokens
NO_OF_LINES_TOKEN_RE = re.compile(
    r"^(?P<name>.+?)\s+no_of_lines\s*:\s*(?P<loc>\d+|Nil|None)\s*$",
    re.IGNORECASE
)


def is_chunk_id(token: str) -> bool:
    return bool(token) and bool(CHUNK_ID_RE.match(token.strip()))

def is_group_id(token: str) -> bool:
    """
    Strict check: entire token is a canonical group id like 'G1' or 'GN1'.
    """
    return bool(token) and bool(GROUP_ID_RE.match(token.strip()))

def normalize_group_token(token: Optional[str]) -> Optional[str]:
    """
    Return canonical group id if the token contains a group id anywhere.
    """
    if not token:
        return None
    t = token.strip()
    if GROUP_ID_RE.match(t):
        return t
    m = GROUP_ID_EXTRACT_RE.search(t)
    if m:
        gid = m.group(0).strip()
        return gid
    return None

def is_groupish(token: Optional[str]) -> bool:
    return normalize_group_token(token) is not None

def strip_no_of_lines_name(token: str) -> str:
    if not token:
        return ""
    s = token.strip()
    m = NO_OF_LINES_TOKEN_RE.match(s)
    if m:
        return m.group("name").strip()
    return s

def strip_no_of_lines_suffix(token: str):
    if not token:
        return "", None

    s = token.strip()
    m = NO_OF_LINES_TOKEN_RE.match(s)

    if m:
        name = m.group("name").strip()
        loc = m.group("loc")

        # Normalize loc
        if loc.isdigit():
            loc = int(loc)
        else:
            loc = None

        return name, loc

    # No suffix found
    return s, None


# ==========================================================
# Utilities
# ==========================================================
def require_columns(df: pd.DataFrame, cols: List[str], name: str):
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"[ERROR] {name} missing required columns: {missing}")

def split_names(raw) -> List[str]:
    if raw is None:
        return []
    # Treat NaN-like values as empty
    try:
        if pd.isna(raw):
            return []
    except Exception:
        pass

    s = str(raw).strip().strip('"').strip("'")
    parts = re.split(r"[,\n;]+", s)
    return [p.strip() for p in parts if p.strip() and p.strip().lower() != "nan"]

def name_parts(full_name: str) -> Tuple[Optional[str], str]:
    if "." in full_name:
        pre, post = full_name.rsplit(".", 1)
        return pre, post
    return None, full_name

def list_source_files(src_root: Path, exts: Set[str]) -> List[Path]:
    # print("src_root : ",src_root)
    # print("exts : ",exts)
    exts = {item.lower() for item in exts}
    out: List[Path] = []
    for root, _, files in os.walk(src_root):
        for fn in files:
            p = Path(root) / fn
            # print(" P_1: ",p.suffix)
            # print()
            if p.suffix.lower() in exts:
                # print(" P : ",p)
                out.append(p)
                # print("outt : ",out)
    return out

def detect_language_by_ext(ext: str) -> str:
    ext = ext.lower()
    
    if ext in {".java", ".js", ".ts"}:
        return "c_like"
    
    return "text"


# def _norm_ext(ext: Optional[str]) -> str:
#     """Normalize extension for case-insensitive, whitespace-safe comparison."""
#     return (ext or "").strip().lower()


def _norm_ext(ext: Optional[List[str]]) -> List[str]:
    """Normalize extensions for case-insensitive, whitespace-safe comparison."""
    return [e.strip().lower() for e in ext or []]

def _count_lines(s) -> int:
    if not s:
        return 0

    # If s is a list of strings, count lines in each
    if isinstance(s, list):
        return sum(len(item.splitlines()) for item in s if isinstance(item, str))

    # NEW: If s is a dict, count lines in string values
    if isinstance(s, dict):
        return sum(len(v.splitlines()) for v in s.values() if isinstance(v, str))

    # If s is a string, count normally
    return len(s.splitlines())   

# ------------------ NEW: helpers for metadata header & ranges ----------------
def _compute_snippet_line_range(full_text: str, snippet_text: str) -> Tuple[int, int]:
    if not full_text or not snippet_text:
        return (0, 0)

    start_idx = full_text.find(snippet_text)
    if start_idx == -1:
        normalized_file = re.sub(r"\s+", " ", full_text)
        normalized_snip = re.sub(r"\s+", " ", snippet_text)
        start_idx = normalized_file.find(normalized_snip)
        if start_idx == -1:
            return (0, 0)
        start_line = normalized_file[:start_idx].count("\n") + 1
        end_line = start_line + len(snippet_text.splitlines()) - 1
        return (start_line, end_line)

    start_line = full_text[:start_idx].count("\n") + 1
    end_line = start_line + len(snippet_text.splitlines()) - 1
    return (start_line, end_line)

def _relative_path_for_header(file_path: Path) -> str:
    global PROJECT_ROOT
    try:
        if PROJECT_ROOT and PROJECT_ROOT.exists():
            return str(file_path.relative_to(PROJECT_ROOT)).replace("/", "\\")
    except Exception:
        pass
    return str(file_path).replace("/", "\\")

def _display_file_id_from_snippet(snippet_text: str, fallback_file_id: str = "") -> str:
    if not snippet_text:
        return (fallback_file_id or "").lower()

    for line in snippet_text.splitlines():
        m = re.match(r"\s*(f\d+)_\d+\b", line.strip(), re.IGNORECASE)
        if m:
            return m.group(1).lower()   # 'f336'
    return (fallback_file_id or "").lower()

def _display_file_id_from_path(pth: Path) -> str:
    try:
        return (pth.stem or "").lower()
    except Exception:
        return ""

def build_unit_metadata_block(meta_by_file: Dict[Path, Dict[str, object]],
                              breakdown: Optional[Dict[str, int]] = None) -> str:
    if not meta_by_file:
        return ""

    file_sections: List[str] = []

    for pth, meta in meta_by_file.items():
        snippet_text = str(meta.get("snippet_text") or "")

        provided = str(
            meta.get("extracted_file_id")
            or meta.get("file_id")
            or meta.get("display_file_id")
            or ""
        ).lower()

        fallback_from_path = _display_file_id_from_path(pth)

        file_id_display = _display_file_id_from_snippet(
            snippet_text,
            provided or fallback_from_path
        )

        file_name = pth.name
        rel_path = _relative_path_for_header(pth)
        total_lines = int(meta.get("total_lines") or 0)

        ranges = meta.get("ranges") or []
        ranges_display: List[str] = []

        for rng in ranges:
            if not isinstance(rng, (tuple, list)) or len(rng) < 2:
                continue
            try:
                start = int(rng[0])
                end = int(rng[1])
            except (TypeError, ValueError):
                continue

            if end >= start:
                if file_id_display:
                    ranges_display.append(f"[{file_id_display}_{start}:{file_id_display}_{end}]")
                else:
                    ranges_display.append(f"[{start}:{end}]")

        ranges_str = ", ".join(ranges_display) if ranges_display else "[]"

        section_lines = [
            f"** File Id : {file_id_display}",
            f"** File name : {file_name}",
            f"** File_Path : {rel_path}",
            f"** Total number of lines in File : {total_lines}",
            f"** Lines Used : {ranges_str}",
        ]

        if breakdown:
            section_lines.extend([
                f"** Total lines in combined input : {int(breakdown.get('total_lines', 0))}",
                f"** Code lines : {int(breakdown.get('code_lines', 0))}",
                f"** Application properties lines : {int(breakdown.get('props_lines', 0))}",
                f"** Child summary lines : {int(breakdown.get('child_summary_lines', 0))}",
                f"** Empty lines : {int(breakdown.get('empty_lines', 0))}",
                f"** LLM reference lines : {int(breakdown.get('llm_ref_lines', 0))}",
            ])

        file_sections.append("\n".join(section_lines))

    final_lines = [
        "*****************************  Begin Header ***********************************",
        ("\n-----------------------------------------------------------------------------\n").join(file_sections),
        "*****************************  End Header ***************************************",
        ""
    ]

    return "\n".join(final_lines).rstrip()

_GROUP_HEADER_RE = re.compile(
    r"^(=+\s*COMBINED CODE FOR GROUP:\s*[^\n]+?=+)\s*$",
    re.IGNORECASE | re.MULTILINE
)

def inject_metadata_after_group_header(combined_text: str, header_text: str) -> str:
    """
    Insert metadata immediately after the first group header line.
    """
    if not header_text.strip():
        return combined_text

    m = _GROUP_HEADER_RE.search(combined_text or "")
    if not m:
        # Fallback: prepend if header line not found
        return f"{header_text}\n{combined_text}"

    insert_at = m.end()
    return combined_text[:insert_at] + "\n\n" + header_text + "\n" + combined_text[insert_at:]

def attach_file_metadata_to_combined_text(
    extension,
    combined_text: str,
    meta_by_file: Dict[Path, Dict[str, object]]
) -> str:
    breakdown = compute_document_line_breakdown(combined_text, extension)
    header = build_unit_metadata_block(meta_by_file, breakdown=breakdown)
    return inject_metadata_after_group_header(combined_text, header)

# ---------------------------------------------------------------------------
# STRICT and LOOSE signature regex builders (FIXED)
# ---------------------------------------------------------------------------
def build_java_signature_regex_strict(name_re: str, brace_on_same_line: bool) -> re.Pattern:
    # Correct generic matcher using actual '<' and '>'
    generic = r"(?:<[^<>\n]*?(?:<[^<>\n]*?>[^<>\n]*?)*?>)"

    line_id_prefix = r"^\s*(?:f\d+_\d+\s+)?"

    annotations = (
        r"(?P<annotations>("
        r"(?:@\s*[\w.$]+(?:\s*\([^)]*\))?\s*)|"
        r"(?:\[\s*[\w.$,\s]+(?:\s*\([^)]*\))?\s*\]\s*)"
        r")*)"
    )

    modifiers = (
        r"(?P<modifiers>("
        r"(?:public|protected|private|internal|static|final|abstract|"
        r"synchronized|native|strictfp|default|sealed|virtual|override|"
        r"extern|unsafe|async|new|partial)"
        r"\s+"
        r")*)"
    )

    type_params = rf"(?:{generic}\s+)?"

    ret = rf"""
        (?P<ret>
            (?:
                [\w.$]+
                (?:\s*{generic})?
            )
            (?:\s*\[\s*\])*
            (?:\s*\.\.\.)?
        )
        \s+
    """

    core = rf"""
        {line_id_prefix}
        {annotations}
        {modifiers}
        {type_params}
        (?:
            {ret}
        )?
        (?P<name>{name_re})
        \s*
        \(
            (?P<params>[\s\S]*?)
        \)
        (?:\s*throws\s+(?P<throws>[^\){{;]+))?
    """

    if brace_on_same_line:
        pattern = rf"""{core}\s*\{{"""
    else:
        pattern = rf"""{core}\s*$"""

    return re.compile(pattern, re.MULTILINE | re.VERBOSE | re.DOTALL)


def build_java_signature_regex_loose(name_re: str, brace_on_same_line: bool) -> re.Pattern:
    line_id_prefix = r"^\s*(?:f\d+_\d+\s+)?"

    annotations = (
        r"(?P<annotations>("
        r"(?:@\s*[\w.$]+(?:\s*\([^)]*\))?\s*)|"
        r"(?:\[\s*[\w.$,\s]+(?:\s*\([^)]*\))?\s*\]\s*)"
        r")*)"
    )

    modifiers = (
        r"(?P<modifiers>("
        r"(?:public|protected|private|internal|static|final|abstract|"
        r"synchronized|native|strictfp|default|sealed|virtual|override|"
        r"extern|unsafe|async|new|partial)"
        r"\s+"
        r")*)"
    )

    core = rf"""
        {line_id_prefix}
        {annotations}
        {modifiers}

        (?:
            [\w.$]+
            (?:\s*<[^>\n]+>)?
            (?:\s*\[\])*
        )
        \s+

        (?P<name>{name_re})
        \s*
        \(
            (?P<params>[\s\S]*?)
        \)
        (?:\s*throws\s+(?P<throws>[^\){{;]+))?
    """

    if brace_on_same_line:
        pattern = rf"""{core}\s*\{{"""
    else:
        pattern = rf"""{core}\s*$"""

    return re.compile(pattern, re.MULTILINE | re.VERBOSE | re.DOTALL)


def _rewind_to_method_start(text: str, sig_start: int) -> int:
    pos = sig_start
    keep_from = sig_start

    while True:
        prev_nl = text.rfind("\n", 0, pos)
        if prev_nl == -1:
            return 0

        line_start = prev_nl + 1
        line = text[line_start:pos]

        if line.strip() == "":
            keep_from = line_start
            pos = prev_nl
            continue

        if re.match(r"^\s*}\s*$", line):
            return keep_from

        if re.match(r"^\s*(?:f\d+_\d+\s+)?@\w", line):
            keep_from = line_start
            pos = prev_nl
            continue

        return keep_from

def _trim_to_signature_with_annotations(snippet: str, method_name: str) -> str:
    """
    Trim the snippet so it starts at the actual method *definition* line
    (not a call site or a return statement that happens to contain the method name).

    A definition line must have at least one access/return-type token before
    the method name.  Call sites (return foo(...), foo(...);, etc.) are skipped.
    """
    if not snippet:
        return snippet

    lines = snippet.splitlines(True)

    # Strict definition pattern: requires a modifier OR a return-type word
    # immediately before the method name.  This prevents matching
    #   return methodName(...)
    #   someVar = methodName(...)
    # while still catching:
    #   public void methodName(...)
    #   private static String methodName(...)
    #   List<Foo> methodName(...)
    sig_pattern = re.compile(
        rf"""(?mx)
        ^\s*(?:f\d+_\d+\s+)?                      # optional line-id token
        (?:@\s*[\w.$]+(?:\s*\([^)]*\))?\s*)*       # optional annotations (same line)
        (?:
            # At least one modifier keyword before the method name
            (?:public|protected|private|internal|static|final|abstract|
               synchronized|native|strictfp|default|sealed|virtual|override|
               extern|unsafe|async|new|partial)
            \s+
        )+
        (?:[\w.$<>\[\],\s]+?\s+)?                  # optional return type
        \b{re.escape(method_name)}\s*\(
        |
        ^\s*(?:f\d+_\d+\s+)?                      # OR: no modifier but explicit return type
        (?:@\s*[\w.$]+(?:\s*\([^)]*\))?\s*)*
        [\w.$][\w.$<>\[\],]*(?:\s*\[\s*\])*\s+    # return type (must start with word char)
        \b{re.escape(method_name)}\s*\(
        """
    )

    sig_text = "".join(lines)

    # Find the FIRST line that looks like a real definition (not a call site)
    best_match = None
    for m in sig_pattern.finditer(sig_text):
        # Double-check: the matched line must NOT end with ');' or just ')'
        # (those are call/return sites, not definitions)
        line_start = sig_text.rfind("\n", 0, m.start()) + 1
        line_end = sig_text.find("\n", m.start())
        if line_end == -1:
            line_end = len(sig_text)
        line_text = sig_text[line_start:line_end].strip()
        # Skip call-site patterns
        if line_text.endswith(");") or line_text.endswith(")"):
            # Allow if the line also has '{' after ')' — that's a one-liner body
            if "{" not in line_text:
                continue
        best_match = m
        break  # First valid definition match wins

    if not best_match:
        # Fallback: return as-is (strip leading blank lines only)
        return snippet.lstrip()

    upto = sig_text[:best_match.start()]
    start_line_idx = upto.count("\n")

    # Walk back to include any annotation lines that precede the signature
    keep_from = start_line_idx
    while keep_from - 1 >= 0:
        prev_line = lines[keep_from - 1]
        if re.match(r"^\s*(?:f\d+_\d+\s+)?@\w", prev_line) or prev_line.strip() == "":
            keep_from -= 1
            continue
        break

    trimmed = "".join(lines[keep_from:])
    # Strip any stray closing-brace lines that leaked in before the definition
    trimmed = re.sub(r"^(?:\s*}\s*\n)+", "", trimmed)
    return trimmed

COBOL_LINES_SUFFIX_RE = re.compile(r"\s+no_of_lines\s*:\s*\d+\s*$", re.IGNORECASE)

# Support either numeric column ids and/or line-id tokens; allow both in sequence.
LINE_ID_PREFIX_TOKEN   = r"[A-Za-z]{1,5}\d{0,4}_\d{1,7}"
PREFIX_TOKEN_RE        = rf"(?:\d{{3,}}\s+|{LINE_ID_PREFIX_TOKEN}\s+)"
LINE_OR_COL_PREFIX_RE  = rf"(?:{PREFIX_TOKEN_RE})*"


def _cut_at_next_signature_line_with_line_ids(snippet: str) -> str:
    if not snippet:
        return snippet

    new_sig_re = re.compile(
        r'^\s*f\d+_\d+\s+(public|protected|private)\b.*\(',
        re.MULTILINE
    )

    matches = list(new_sig_re.finditer(snippet))
    if len(matches) <= 1:
        return snippet

    cut_pos = matches[1].start()
    return snippet[:cut_pos].rstrip("\n")


def _fallback_method_extract_c_like(text: str, method_name: str) -> Optional[str]:

    sig = re.search(
        rf"(?ms)^[^\n]*\b{re.escape(method_name)}\s*\([\s\S]*?\)\s*[^\n]*\{{",
        text
    )
    if not sig:
        return None

    start = sig.start()
    brace_start = text.find("{", sig.start())
    if brace_start == -1:
        return None

    depth = 0
    i = brace_start
    if i < 0:
        return None
    end_index = None
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                keep_from = _rewind_to_method_start(text, sig.start())
                return text[keep_from:i+1]
        i += 1
    return None

def lookup_actual_value(
    excel_path: str,
    file_name: str,
    method_name: str,
    sheet_name: str = "standalone",
):

    import os
    import pandas as pd
    from html import unescape

    # ---------- Helpers ----------
    # Removes normal spaces, NBSP (U+00A0), BOM (U+FEFF), zero-width no-break (U+FEFF), zero-width space (U+200B)
    def strip_all(s: str) -> str:
        if s is None:
            return ""
        s = str(s)
        # Normalize common hidden chars
        for ch in ("\u00A0", "\uFEFF", "\u200B"):
            s = s.replace(ch, " ")
        return s.strip()

    def norm(s: str) -> str:
        return strip_all(s).lower()

    def canon_proc(s: str) -> str:
        # Unescape twice to handle double-escaped inputs, then normalize
        clean = unescape(unescape("" if s is None else str(s)))
        # print("norm : ",norm(clean))
        return norm(clean)

    def file_stem_only(name: str) -> str:
        raw = "" if name is None else str(name)
        raw = strip_all(raw)
        base = os.path.basename(raw)
        stem, _ext = os.path.splitext(base)
        return norm(stem)

    # ---------- Early Guard (accept raw or escaped angle brackets) ----------
    text = "" if method_name is None else str(method_name)
    if not any(tok in text for tok in ("<", ">", "&lt;", "&gt;", "&amp;lt;", "&amp;gt;", "&amp;amp;lt;", "&amp;amp;gt;")):
        # print("NONE")
        return None

    # ---------- Build canonical inputs ----------
    canon_method = canon_proc(method_name)
    stem_candidates = {file_stem_only(file_name)}  # single stem is enough and most robust
    # print("stem_candidates : ",stem_candidates)
    # print("canon_method : ",canon_method)

    # ---------- Read Excel ----------
    df = pd.read_excel(excel_path, sheet_name=sheet_name, dtype=str, engine="openpyxl")
    if df.empty:
        # print("DF EMPTY")
        return None

    # ---------- Find columns case-insensitively (with strip) ----------
    col_map = {strip_all(c).lower(): c for c in df.columns}

    def get_col(*names):
        for n in names:
            key = strip_all(n).lower()
            if key in col_map:
                return col_map[key]
        return None

    col_file = get_col("file_name", "filename", "file name")
    col_proc = get_col("procedure_name", "procedure name", "procedure", "method_name", "method name")
    col_actual = get_col("actual_value", "actual value", "actual", "value")

    if not all([col_file, col_proc, col_actual]):
        # Optional debug for headers
        # print("DEBUG:: headers:", df.columns.tolist())
        return None

    # ---------- Normalize DataFrame ----------
    df["_file_norm"] = df[col_file].map(file_stem_only)
    df["_proc_canon"] = df[col_proc].map(canon_proc)
    # print("df_file_norm : ",df["_file_norm"])
    # print("DF_proc_canon : ",df["_proc_canon"])

    # ---------- Match ----------
    matched = df[
        (df["_file_norm"].isin(stem_candidates)) &
        (df["_proc_canon"] == canon_method)
    ]

    if matched.empty:
        # Minimal, targeted debug (safe to keep; prints only on miss)
        # print("DEBUG:: stem_candidates:", stem_candidates)
        # print("DEBUG:: canon_method:", canon_method)
        # print("DEBUG:: file_norm unique:", sorted(set(df["_file_norm"].dropna())))
        # print("DEBUG:: example proc_canon (top 5):", df["_proc_canon"].dropna().head(5).tolist())
        return None

    val = matched[col_actual].dropna()
    # print("VAL : ",val)
    return None if val.empty else strip_all(str(val.iloc[0]))
def extract_method_code(file_name: str,text: str, method_name: str, lang: str,PARAGRAPH_LINEAGE_EXCEL: str,CHUNK_LIMIT:int) -> Optional[str]:

    if not method_name:
        return None

    method_name,LOC = strip_no_of_lines_suffix(method_name)


    # ---- Java / C-like ----
    name_re = re.escape(method_name)

    patterns: List[Tuple[re.Pattern, bool]] = []

    for brace_same in (True, False):
        patterns.append((build_java_signature_regex_strict(name_re, brace_same), brace_same))
    _DEFINITION_LINE_RE = re.compile(
        r"""(?x)
        ^\s*
        (?:f\d+_\d+\s+)?                          # optional line-id token
        (?:@\s*[\w.$]+(?:\s*\([^)]*\))?\s*)*      # optional annotations
        (?:
            (?:public|protected|private|internal|static|final|abstract|
            synchronized|native|strictfp|default|sealed|virtual|override|
            extern|unsafe|async|new|partial)
            \s+
        )+                                         # at least one modifier OR
        |
        ^\s*(?:f\d+_\d+\s+)?
        [\w.$<>\[\]]+\s+                           # return type
        """,
        re.MULTILINE
    )

    def _is_definition_match(m: re.Match, src: str) -> bool:
        """Return True only if the match starts on a method-definition line (not a call site)."""
        # Find the start of the line containing the match
        line_start = src.rfind("\n", 0, m.start()) + 1
        line_end   = src.find("\n", m.start())
        if line_end == -1:
            line_end = len(src)
        line = src[line_start:line_end]

        # A call site ends with ');' or just ')' — no opening brace on this or next line
        stripped = line.strip()
        if stripped.endswith(");") or stripped.endswith(");"):
            return False

        # Must have a modifier or return type before the method name
        return bool(_DEFINITION_LINE_RE.match(line))

    all_matches: dict = {}  # start_pos -> (match, brace_on_same_line)
    for pat, brace_same in patterns:
        for m in pat.finditer(text):
            if m.start() not in all_matches and _is_definition_match(m, text):
                all_matches[m.start()] = (m, brace_same)

    def _extract_one(match, brace_on_same_line: bool) -> Optional[str]:
        """Extract a single method body given its signature match.

        The extraction window is:
          [definition_line_start .. closing_brace]

        _rewind_to_method_start is called starting from the *beginning of the
        line* that contains the signature match.  This prevents it from
        walking backwards into code that belongs to a previous method or into
        a return/call expression that happens to contain the method name.
        """
        # Anchor to the start of the line where the signature begins.
        # Do NOT use match.start() directly — the regex can match in the middle
        # of a line (e.g. inside "return methodName(...)").
        line_begin = text.rfind("\n", 0, match.start())
        line_begin = 0 if line_begin == -1 else line_begin + 1

        # Walk back to pick up any preceding annotation lines (@Override etc.)
        start_index = _rewind_to_method_start(text, line_begin)

        if brace_on_same_line:
            if match.end() - 1 >= 0 and text[match.end() - 1] == "{":
                brace_index = match.end() - 1
            else:
                brace_index = text.find("{", match.start())
        else:
            brace_index = text.find("{", match.end())

        if brace_index == -1:
            i = max(match.end(), 0)
            while i < len(text) and text[i] != "{":
                i += 1
            if i >= len(text) or text[i] != "{":
                return None
            brace_index = i

        depth = 0
        i = brace_index
        end_index = None
        while i < len(text):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end_index = i
                    break
            i += 1

        if end_index is None:
            return None

        snippet = text[start_index:end_index + 1]
        first_lines = snippet.splitlines()

        signature = ""

        for line in first_lines:
            s = re.sub(r'^\s*f\d+_\d+\s+', '', line).strip()

            if not s:
                continue

            if s.startswith("@"):
                continue

            signature = s
            break

        if "=" in signature and signature.endswith(";"):
            return None
        snippet = _trim_to_signature_with_annotations(snippet, method_name)
        snippet = _cut_at_next_signature_line_with_line_ids(snippet)
        return snippet

    if not all_matches:
        # last resort: very loose extraction (only first match, as before)
        fallback = _fallback_method_extract_c_like(text, method_name)
        if fallback:
            fallback = _trim_to_signature_with_annotations(fallback, method_name)
            fallback = _cut_at_next_signature_line_with_line_ids(fallback)
            return fallback
        return None

    # Extract every overload in source order and join with a blank line separator.
    snippets: List[str] = []
    for pos in sorted(all_matches):
        m, brace_same = all_matches[pos]
        s = _extract_one(m, brace_same)
        if s:
            snippets.append(s)

    if not snippets:
        return None

    # Return a single string with all overloads separated by a blank line.
    return "\n\n".join(snippets)

def _find_exact_class_files(
    files: List[Path],
    method_full_name: str,

) -> List[Path]:

    method_full_name = strip_no_of_lines_name(method_full_name)
    cls, _ = name_parts(method_full_name)
    if not cls:
        return []

    cls_full = cls.strip()
    cls_base = cls_full.split(".")[-1]
    cls_full_lc = cls_full.lower()
    cls_base_lc = cls_base.lower()

    matched: List[Path] = []

    
    
    has_java = any(p.suffix.lower() == ".java" for p in files)
    
    # -------- Java / others --------
    for p in files:
        if p.stem.lower() in (cls_full_lc, cls_base_lc):
            matched.append(p)

    if has_java:
        class_pattern_java = re.compile(
            rf'\b(class|interface|enum)\s+{re.escape(cls_base)}\b'
        )
        for p in files:
            if p in matched or p.suffix.lower() != ".java":
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
                if class_pattern_java.search(text):
                    matched.append(p)
            except Exception:
                continue

    matched.sort()
    # print("matched : ",matched)
    return matched


def _resolve_file_by_path(
    path_without_ext: str,
    src_root: Path,
    known_exts: Optional[List[str]] = None,
    files: Optional[List[Path]]=None,
) -> Optional[Path]:
    """
    Given a path-without-extension (e.g. 'com/example/service/Foo' or
    'com\\example\\service\\Foo'), resolve the actual file in the codebase.

    Strategy (in order):
    1. Try PROJECT_ROOT / path + each known extension.
    2. Walk all_source_files and match by suffix-of-relative-path (handles
       nested source roots like src/main/java/...).
    """
    global PROJECT_ROOT

    if not path_without_ext:
        return None

    # Normalise separators so both / and \ work
    norm_path = path_without_ext.replace("\\", "/").strip("/")

    # Extensions to probe – prefer caller-supplied list, else common defaults
    if known_exts:
        exts_to_try = [e if e.startswith(".") else f".{e}" for e in known_exts]
    else:
        exts_to_try = [".java", ".js", ".ts"]

    root = PROJECT_ROOT if (PROJECT_ROOT and PROJECT_ROOT.exists()) else src_root

    # --- Strategy 1: direct path under project root ---
    for ext in exts_to_try:
        candidate = root / (norm_path + ext)
        if candidate.exists():
            return candidate

    target_stem = norm_path.split("/")[-1].lower()
    for src_file in files:
        if src_file.stem.lower() == target_stem:
            return src_file

    return None


# ==========================================================
# YAML summary extraction helpers (kept but unused in code-only mode)
# ==========================================================
SECTION_HEADINGS = {
    "WORKFLOW (TRACE ACROSS METHODS)",
    "BUSINESS RULES (MANDATORY)",
    "DATA HANDLING AND TRANSFORMATIONS",
    "VALIDATIONS AND GUARDS",
    "RETURN BEHAVIOR",
    "SAMPLE EXECUTION FLOW",
}

_KEY_WITH_OPTIONAL_ID_RE_TMPL = r"""
    ^(?P<indent>\s*)
    (?:[A-Za-z][\w.\-]*\s+)?          # optional id prefix like G2.1.5 or GN12
    (?P<key>{key})\s*:\s*(?P<after>.*)$
"""

_HEADING_WITH_OPTIONAL_ID_RE = re.compile(
    r"""^(?P<indent>\s*)(?:[A-Za-z][\w.\-]*\s+)?(?P<label>[A-Z0-9][A-Z0-9 ()/&\-\.,]+)\s*:\s*$""",
    re.VERBOSE
)

def _extract_yaml_key_block_flexible(text: str, key_name: str) -> str:
    if not text:
        return ""
    pattern = re.compile(_KEY_WITH_OPTIONAL_ID_RE_TMPL.format(key=re.escape(key_name)), re.VERBOSE | re.IGNORECASE)
    lines = text.splitlines(True)

    for i, line in enumerate(lines):
        m = pattern.match(line)
        if not m:
            continue
        base_indent = len(m.group("indent"))
        out = [line.rstrip("\n")]
        for j in range(i + 1, len(lines)):
            ln = lines[j]
            if ln.strip() == "":
                out.append(ln.rstrip("\n"))
                continue
            cur_indent = len(ln) - len(ln.lstrip())
            if cur_indent > base_indent:
                out.append(ln.rstrip("\n"))
            else:
                break
        return "\n".join(out).rstrip()
    return ""

def _extract_sections_from_detailed_block_flexible(text: str) -> str:
    if not text:
        return ""

    pat = re.compile(_KEY_WITH_OPTIONAL_ID_RE_TMPL.format(
        key=re.escape("detailed_technical_explanation")), re.VERBOSE | re.IGNORECASE)
    lines = text.splitlines(True)

    start_idx = None
    base_indent = 0
    after_token = ""
    for i, line in enumerate(lines):
        m = pat.match(line)
        if m:
            start_idx = i
            base_indent = len(m.group("indent"))
            after_token = (m.group("after") or "").strip()
            break
    if start_idx is None:
        return ""

    if after_token == "|":
        block_lines: List[str] = []
        for j in range(start_idx + 1, len(lines)):
            ln = lines[j]
            st = ln.lstrip()
            if st.strip() == "":
                block_lines.append(ln.rstrip("\n"))
                continue
            indent = len(ln) - len(st)
            if indent > base_indent:
                block_lines.append(ln.rstrip("\n"))
            else:
                break

        if not block_lines:
            return ""

        extracted: List[str] = []
        k = 0
        while k < len(block_lines):
            ln = block_lines[k]
            st = ln.strip()

            if st.endswith(":"):
                m = _HEADING_WITH_OPTIONAL_ID_RE.match(ln)
                if m:
                    label = (m.group("label") or "").strip()
                else:
                    label = st[:-1].strip()

                if label in SECTION_HEADINGS:
                    extracted.append(ln)
                    k += 1
                    while k < len(block_lines):
                        nxt = block_lines[k]
                        m2 = _HEADING_WITH_OPTIONAL_ID_RE.match(nxt)
                        if m2:
                            break
                        extracted.append(nxt)
                        k += 1
                    extracted.append("")
                    continue
            k += 1

        return "\n".join([e.rstrip() for e in extracted]).strip()

    nested: List[str] = []
    for j in range(start_idx + 1, len(lines)):
        ln = lines[j]
        if ln.strip() == "":
            nested.append(ln.rstrip("\n"))
            continue
        cur_indent = len(ln) - len(ln.lstrip())
        if cur_indent > base_indent:
            nested.append(ln.rstrip("\n"))
        else:
            break
    if not nested:
        return ""

    extracted2: List[str] = []
    k = 0
    while k < len(nested):
        ln = nested[k]
        m = _HEADING_WITH_OPTIONAL_ID_RE.match(ln)
        if m:
            label = (m.group("label") or "").strip()
            if label in SECTION_HEADINGS:
                extracted2.append(ln)
                k += 1
                while k < len(nested):
                    nxt = nested[k]
                    m2 = _HEADING_WITH_OPTIONAL_ID_RE.match(nxt)
                    if m2:
                        break
                    extracted2.append(nxt)
                    k += 1
                extracted2.append("")
                continue
        k += 1

    return "\n".join([e.rstrip() for e in extracted2]).strip()

def extract_essential_sections_from_summary_yaml(yaml_text: str) -> str:
    if not yaml_text:
        return ""

    bf = _extract_yaml_key_block_flexible(yaml_text, "business_function")
    core_bf = _extract_yaml_key_block_flexible(yaml_text, "core_business_functionality")
    detailed_sections = _extract_sections_from_detailed_block_flexible(yaml_text)

    parts: List[str] = []
    if bf.strip():
        parts.append(bf.strip())
    if core_bf.strip():
        parts.append(core_bf.strip())
    if detailed_sections.strip():
        parts.append(detailed_sections.strip())

    return "\n".join(parts).strip()

# ==========================================================
# Group label builder (via GROUP_METHODS_MAP)
# ==========================================================
def _format_group_label_with_map(group_id: str) -> str:
    gid = normalize_group_token(group_id) or group_id
    if not is_group_id(gid):
        return gid
    methods = GROUP_METHODS_MAP.get(gid, [])
    if not methods:
        return gid
    joined = ", ".join(methods)
    return f"{gid} (methods: {joined})"


# ==========================================================
# Combined formatters
# ==========================================================
def _format_code_map_as_combined_snippet(
    ext: Optional[List[str]],
    unit_id: str,
    code_map: Dict[str, str],
    source_map: Dict[str, str],
    parent_entity: Optional[str] = None,
    unit_type: str = "chunk",  # "chunk", "group", or "call"
) -> str:
    """
    Generic combined formatter for CHUNK/GROUP/CALL inputs.
    - Emits method/file blocks and (in code-only mode) does NOT embed group summaries.
    """
    ext = _norm_ext(ext) 
    lines: List[str] = []
    header = f"==== COMBINED CODE FOR {unit_type.upper()}: {unit_id}"
    if unit_type.lower() == "chunk" and parent_entity:
        header += f" (parent_entity: {parent_entity})"
    if unit_type.lower() == "group":
        label = _format_group_label_with_map(unit_id)
        if label != unit_id:
            header = f"==== COMBINED CODE FOR GROUP: {label} ===="
    else:
        header += " ===="
    lines.append(header)
    lines.append("")

    for token_key, code in code_map.items():
        clean_name = token_key.strip()
        value = source_map.get(token_key, "code")

        no_lines = _count_lines(code)
        if ".java" in ext:
            lines.append(f"--- BEGIN METHOD: {clean_name} no_of_lines : {no_lines} ---")
        
        if code:
            if isinstance(code, list):
                for item in code:
                    lines.append(item.rstrip())
            else:
                # lines.append(code.rstrip())
                code = code if isinstance(code, str) else str(code)
                lines.append(code.rstrip())
        else:
            lines.append("/* definition not provided */")
        if ".java" in ext:
            lines.append(f"--- END METHOD: {clean_name} no_of_lines : {no_lines} ---")
        
        lines.append("")

    return "\n".join(lines).rstrip()

_CANON_DIRTY_SUFFIX_RE = re.compile(
    r"""
        \s*
        (?:
            -+>?      # -> or ---> (literal)
          | -+&gt;?     # HTML-escaped arrow (defensive)
          | &gt;       # just '&gt;' (defensive)
          | ->        # normal arrow
        )\s*$
    """, re.IGNORECASE | re.VERBOSE
)

def _canonical_method_token(token: str) -> str:
    if not token:
        return ""
    s = strip_no_of_lines_name(token).strip()
    s = _CANON_DIRTY_SUFFIX_RE.sub("", s)
    return s

def compute_input_line_metrics(input_text: str, ext: Optional[List[str]]) -> Tuple[int, int, int]:
    ext = _norm_ext(ext)
    lines = input_text.splitlines()
    ext_key = [e.strip().lower() for e in (ext or [])]

    if ".java" in ext_key:
        llm_fence_patterns = [
            r"^--- BEGIN METHOD: .* ---$",
            r"^--- END METHOD: .* ---$",
            r"^\$\$\$ BEGIN CHILD: .* \$\$\$$",
            r"^\$\$\$ END CHILD: .* \$\$\$$",
        ]
   
    llm_fence_res = [re.compile(p) for p in llm_fence_patterns]

    header_row_re   = re.compile(r"^\*\* ")
    placeholder_re  = re.compile(r"^/\* definition not provided \*/$")
    if ".java" in ext:
        props_banner = "/* --- Application Properties Bindings (from CSV) --- */"
    
    code_lines = 0
    props_lines = 0
    summary_lines = 0

    in_method = False
    in_child = False
    in_props = False

    def is_llm_fence(s: str) -> bool:
        return any(rx.match(s) for rx in llm_fence_res)

    for raw in lines:
        s = raw.strip()
        if s == "":
            continue

        if header_row_re.match(s):
            continue

        if placeholder_re.match(s):
            continue

        if is_llm_fence(s):
            if ".java" in ext_key:
                if s.startswith("--- BEGIN METHOD:"):
                    in_method = True
                    in_child = False
                    in_props = False
                elif s.startswith("--- END METHOD:"):
                    in_method = False
                    in_props = False
            elif s.startswith("$$$ BEGIN CHILD:"):
                in_child = True
                in_method = False
                in_props = False
            elif s.startswith("$$$ END CHILD:"):
                in_child = False
            continue

        if in_method and s == props_banner:
            in_props = True
            props_lines += 1
            continue

        if in_child:
            summary_lines += 1
            continue

        if in_method:
            if in_props:
                props_lines += 1
            else:
                code_lines += 1
            continue

        continue

    return code_lines, props_lines, summary_lines

def compute_document_line_breakdown(input_text: str, extension) -> Dict[str, int]:
   
    ext = _norm_ext(extension) 

    print(" ====== extension ====",extension)
    lines = input_text.splitlines()

    if ".java" in extension:
        llm_patterns = [
            r"^=+ COMBINED CODE FOR CHUNK: .* =+$",
            r"^\*+\s+Begin Header\s+\*+$",
            r"^\*+\s+End Header\s+\*+$",
            r"^--- BEGIN METHOD: .* ---$",
            r"^--- END METHOD: .* ---$",
            r"^\$\$\$ BEGIN CHILD: .* \$\$\$$",
            r"^\$\$\$ END CHILD: .* \$\$\$$",
            r"^-{5,}$",
        ]
    
    llm_res = [re.compile(p) for p in llm_patterns]

    header_row_re   = re.compile(r"^\*\* ")
    placeholder_re  = re.compile(r"^/\* definition not provided \*/$")
    if ".java" in extension:
        props_banner = "/* --- Application Properties Bindings (from CSV) --- */"
    
    strict_props_re = re.compile(r"^[A-Za-z0-9_.-]+\s*=\s*.+$")

    total_lines = len(lines)
    code_lines = 0
    props_lines = 0
    child_summary_lines = 0
    empty_lines = 0
    llm_ref_lines = 0

    in_method = False
    in_child = False
    in_props = False

    def is_llm_line(s: str) -> bool:
        return any(rx.match(s) for rx in llm_res)

    for raw in lines:
        s = raw.strip()

        if s == "":
            empty_lines += 1
            continue

        if header_row_re.match(s):
            llm_ref_lines += 1
            continue

        if placeholder_re.match(s):
            llm_ref_lines += 1
            continue

        if is_llm_line(s):
            llm_ref_lines += 1
            if ".java" in extension:
                if s.startswith("--- BEGIN METHOD:"):
                    in_method = True
                    in_child = False
                    in_props = False
                elif s.startswith("--- END METHOD:"):
                    in_method = False
                    in_props = False
            elif s.startswith("$$$ BEGIN CHILD:"):
                in_child = True
                in_method = False
                in_props = False
            elif s.startswith("$$$ END CHILD:"):
                in_child = False
            continue

        if in_method and s == props_banner:
            in_props = True
            props_lines += 1
            continue

        if strict_props_re.match(s) and not in_child and not in_method:
            llm_ref_lines += 1
            continue

        if in_child:
            child_summary_lines += 1
            continue

        if in_method:
            if in_props:
                props_lines += 1
            else:
                code_lines += 1
            continue

        llm_ref_lines += 1

    return {
        "total_lines": total_lines,
        "code_lines": code_lines,
        "props_lines": props_lines,
        "child_summary_lines": child_summary_lines,
        "empty_lines": empty_lines,
        "llm_ref_lines": llm_ref_lines,
    }

def _format_properties_block(
    ext: Optional[List[str]],
    method_full_name: str,
    props_lookup: Optional[Dict[str, List[Dict[str, str]]]],
    comment_style: str = "c_like",
    seen_prop_actual: Optional[Set[Tuple[str, str]]] = None,
) -> str:
    print('+ extension + ',ext)
    """
    Per-method properties block (if attach_properties=True). Now robust to fully-qualified
    class names by checking both full (pkg.Class) and base (Class) variants.
    """
    if not props_lookup or not method_full_name:
        return ""

    if seen_prop_actual is None:
        seen_prop_actual = set()

    # Normalize and split into class + method
    canon = strip_no_of_lines_name(method_full_name).strip()
    pre, meth = name_parts(canon)
    cls_full = (pre or "").strip()
    cls_base = cls_full.split(".")[-1] if cls_full else ""

    # Try exact method keys (full and base)
    candidates: List[Dict[str, str]] = []
    exact_keys = []
    if cls_full and meth:
        exact_keys.append(_normalize_key(f"{cls_full}.{meth}"))
    if cls_base and meth:
        exact_keys.append(_normalize_key(f"{cls_base}.{meth}"))

    for key in exact_keys:
        if key in props_lookup:
            candidates.extend(props_lookup[key])

    # Also allow a direct exact key on the whole original token (if present)
    direct_exact_key = _normalize_key(canon)
    if direct_exact_key in props_lookup:
        candidates.extend(props_lookup[direct_exact_key])

    # Class-level fallback (full and base)
    class_keys = []
    if cls_full:
        class_keys.append(_normalize_key(f"{cls_full}."))
    if cls_base:
        class_keys.append(_normalize_key(f"{cls_base}."))

    for ckey in class_keys:
        if ckey in props_lookup:
            candidates.extend(props_lookup[ckey])

    if not candidates:
        return ""

    if ".java" in ext:
        header = "/* --- Application Properties Bindings (from CSV) --- */"
    
    
    lines: List[str] = [header]

    local_seen: Set[Tuple[str, str]] = set()
    added_any = False

    for row in candidates:
        annotation = (row.get("Annotation") or "").strip()
        prop      = (row.get("Property") or "").strip()
        actual    = (row.get("Actual Value") or "").strip()
        

        if prop and actual:
            key = (prop.strip().lower(), actual.strip())
            if key in local_seen:
                continue
            local_seen.add(key)
            if key in seen_prop_actual:
                continue
            seen_prop_actual.add(key)

        if annotation or prop:
            lines.append(f"{annotation}: {prop}".rstrip())
        if actual:
            lines.append(f"actual_value: {actual}".rstrip())


        lines.append("")
        added_any = True

    if not added_any:
        return ""

    return "\n".join(lines).rstrip()

def extract_from_files_for_method(
    method_full_name: str,
    files: List[Path],
    PARAGRAPH_LINEAGE_EXCEL:str,
    CHUNK_LIMIT:int,
    props_lookup: Optional[Dict[str, List[Dict[str, str]]]] = None
) -> Optional[Tuple[Path, str, str]]:
    
    # maybe_gid = normalize_group_token(method_full_name)
    # if maybe_gid and is_group_id(maybe_gid):
    #     return None

    normalized_full_name = strip_no_of_lines_name(method_full_name)
    cls, method = name_parts(normalized_full_name)

    # print("method :",method)
    if not cls or not method:
        return None

    # ------------------------------------------------------------------
    # PATH-BASED LOOKUP
    # When methods_in_chunk contains "path_of_java.methodName" the cls
    # part may look like a file path (contains '/' or '\', or multiple
    # dot-separated segments that map to a directory structure).
    # In that case we resolve the file directly from the codebase instead
    # of scanning all files by class name.
    # path_of_java has NO extension, so we probe known extensions below.
    # ------------------------------------------------------------------
    def _looks_like_path(s: str) -> bool:
        """True if s contains a path separator, suggesting it is a file path."""
        return "/" in s or "\\" in s

    src_root = PROJECT_ROOT if (PROJECT_ROOT and PROJECT_ROOT.exists()) else (files[0].parent if files else Path("."))

    if _looks_like_path(cls):
        # Derive extension list from the files we already have
        known_exts = list({p.suffix for p in files if p.suffix})
        resolved = _resolve_file_by_path(cls, src_root, known_exts,files)
        if resolved:
            candidates_path = [resolved]
        else:
            print(f"[PathLookup] Could not resolve path '{cls}' in codebase; falling back to name search.")
            candidates_path = _find_exact_class_files(files, normalized_full_name)
    else:
        candidates_path = _find_exact_class_files(files, normalized_full_name)

    candidates = candidates_path
    # print("candidates : ",candidates)
    for p in candidates:
        file_name = p.name
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        lang = detect_language_by_ext(p.suffix)
        # print("lang : ",lang)
        
        code = extract_method_code(file_name, text, method, lang, PARAGRAPH_LINEAGE_EXCEL, CHUNK_LIMIT)
        if code:
            return p, code, "code"

    return None


def _format_properties_block_exact(
    ext: Optional[List[str]],
    method_name: str,
    exact_lookup: Dict[str, List[Dict[str, str]]],
) -> str:
    if not method_name:
        return ""

    rows = exact_lookup.get(method_name)
    if not rows:
        return ""

    ext = _norm_ext(ext)

    if ".java" in ext:
        header = "/* --- Application Properties Bindings (from CSV) --- */"
    

    out: List[str] = [header, f"// From: {method_name}"]
    local_seen: Set[Tuple[str, str]] = set()
    added = False

    for r in rows:
        prop   = (r.get("Property") or "").strip()
        actual = (r.get("Actual Value") or "").strip()
        annot  = (r.get("Annotation") or "").strip()

        if not (prop or actual or annot):
            continue

        dedup_key = (prop.lower(), actual)
        if dedup_key in local_seen:
            continue
        local_seen.add(dedup_key)

        if annot:
            out.append(f'Type: "{annot}"')
        if prop:
            out.append(f'Property: "{prop}"')
        
        if actual:
            out.append("actual_value:")
            out.append(f'"{actual}"')


        out.append("")
        added = True

    return "\n".join(out).rstrip() if added else ""

def _normalize_key(s: str) -> str:
    return " ".join(s.strip().split()).lower()

def build_aggregated_app_props_for_unit(
    ext: Optional[List[str]],
    method_tokens: List[str],
    props_lookup: Optional[Dict[str, List[Dict[str, str]]]],
    exact_lookup: Dict[str, List[Dict[str, str]]],
) -> str:
    """
    Build ONE consolidated properties block for the unit.

    GUARANTEE:
    - If even ONE Applicable Reference exists in exact_lookup
      for ANY method, it WILL be included.
    - Result is built ONCE and returned ONCE.
    """

    ext = _norm_ext(ext)

    # ---------------- Banner ----------------
    if ".java" in ext:
        banner = "/* --- Application Properties Bindings (from CSV) --- */"
    

    # ✅ GLOBAL ACCUMULATORS (key change)
    out: List[str] = [banner]
    global_seen: Set[Tuple[str, str]] = set()
    any_added = True

    # ✅ Decide tokens to process
    # If method_tokens miss matches but exact_lookup has data,
    # we STILL process exact_lookup keys.
    tokens_to_process: List[str]
    if method_tokens:
        tokens_to_process = method_tokens
    else:
        tokens_to_process = list(exact_lookup.keys())

    # ==========================================================
    # Process tokens ONCE
    # ==========================================================
    for token in tokens_to_process:
        canon = _canonical_method_token(strip_no_of_lines_name(token))
        if not canon:
            continue

        exact_rows = exact_lookup.get(canon)
        if not exact_rows:
            continue

        section_lines: List[str] = [f"// From: {canon}"]

        for r in exact_rows:
            typ    = (r.get("Annotation") or "").strip()
            prop   = (r.get("Property") or "").strip()
            actual = (r.get("Actual Value") or "").strip()

            if prop and actual:
                dedup_key = (prop.lower(), actual)
                if dedup_key in global_seen:
                    continue
                global_seen.add(dedup_key)

            if typ:
                section_lines.append(f'Type: "{typ}"')
            if prop:
                section_lines.append(f'Property: "{prop}"')
            if actual:
                out.append("actual_value:")
                out.append(f'"{actual}"')

            section_lines.append("")

        #  Persist section only once
        out.extend(section_lines)
        any_added = True

    
    joint = "\n".join(out).rstrip() if any_added else ""

    return joint



def extract_code_for_methods_with_files(
    ext: Optional[List[str]],
    method_names: List[str],
    files: List[Path],
    PARAGRAPH_LINEAGE_EXCEL: str,
    CHUNK_LIMIT:int,
    props_lookup: Optional[Dict[str, List[Dict[str, str]]]] = None,
    *,
    attach_properties: bool = False,
) -> Tuple[Dict[str, str], Dict[str, str]]:

    code_map: Dict[str, str] = {}
    source_map: Dict[str, str] = {}
    seen: Set[str] = set()
    # seen_prop_actual: Set[Tuple[str, str]] = set()

    ext = _norm_ext(ext)

    for token in method_names:
        normalized_key = strip_no_of_lines_name(token).strip()
        canon_key = _canonical_method_token(normalized_key)

        print(f"[DEBUG] Processing token: '{canon_key}'")
        
        
        dup_key = canon_key

        if not dup_key or dup_key in seen:
            continue
        seen.add(dup_key)

        _, method_only = name_parts(strip_no_of_lines_name(canon_key))
       
        
        res = extract_from_files_for_method(
            canon_key, files, PARAGRAPH_LINEAGE_EXCEL,CHUNK_LIMIT, props_lookup
        )

        if not res:
            source_map[canon_key] = "code"
            code_map[canon_key] = ""
            continue

        pth, code, value = res
        print(f"[DEBUG] code type: {type(code)}")
        print(f"[DEBUG] code is str: {isinstance(code, str)}")
        if isinstance(code, str):
            print(f"[DEBUG] first 100 chars of code: {repr(code[:100])}")

        source_map[canon_key] = value

        if value == "group":
            code_map[canon_key] = code or ""
            continue

        # ---------------- PROPERTIES ----------------
        if attach_properties:

            # ✅ 1️⃣ EXACT BEGIN TOKEN → method_name
            props_block = _format_properties_block_exact(
                ext,
                canon_key,
                APP_PROPS_BY_METHOD_EXACT,
            )

            # ✅ 2️⃣ FALLBACK (existing behavior)
            if not props_block:
                props_block = _format_properties_block(
                    ext,
                    canon_key,
                    props_lookup,
                    comment_style="c_like",
                )

            if props_block:
                code_map[canon_key] = f"{code.rstrip()}\n\n{props_block}\n"
            else:
                code_map[canon_key] = code
        else:
            code_map[canon_key] = code

    return code_map, source_map


def get_config(
    extension,
    project_path,
    PARAGRAPH_LINEAGE_EXCEL,
    OUTPUT_PATH,
    OUT_XLSX,
    chunks_sheet,
    group_sheet,
    target_sheet,
    call_sheet,
):
    """
    Configure paths.
    Uses ONLY the Excel file for chunks, groups, specs, and call edges.
    call_sheet and target_sheet are OPTIONAL.
    """

    from pathlib import Path
    import pandas as pd

    app_props_xlsx = Path(PARAGRAPH_LINEAGE_EXCEL)

    # Initialize all dataframes as None
    df = None
    df_1 = None

    with pd.ExcelFile(app_props_xlsx, engine="openpyxl") as xf:

        lower_map = {name.lower(): name for name in xf.sheet_names}

        # ---------- SPEC SHEET (OPTIONAL) ----------
        if target_sheet.lower() not in lower_map:
            print(
                f"⚠️ Sheet '{target_sheet}' not found. "
                f"Available sheets: {', '.join(xf.sheet_names)}. Skipping spec sheet."
            )
            spec_sheet_name = None
        else:
            spec_sheet_name = lower_map[target_sheet.lower()]
            df = pd.read_excel(xf, sheet_name=spec_sheet_name)

        # ---------- CALL SHEET (OPTIONAL) ----------
        if call_sheet and call_sheet.lower() in lower_map:
            call_sheet_name = lower_map[call_sheet.lower()]
            df_1 = pd.read_excel(xf, sheet_name=call_sheet_name)
        else:
            call_sheet_name = None

    # ---------- CSV OUTPUT HANDLING ----------
    # SPEC CSV only if df exists
    if df is not None:
        app_props_csv = app_props_xlsx.with_name(
            f"{app_props_xlsx.stem}_{spec_sheet_name}.csv"
        )
        df.to_csv(app_props_csv, index=False)
    else:
        app_props_csv = None

    # CALL CSV only if call sheet exists
    if df_1 is not None:
        call_csv = app_props_xlsx.with_name(
            f"{app_props_xlsx.stem}_{call_sheet_name}.csv"
        )
        df_1.to_csv(call_csv, index=False)
    else:
        call_csv = None

    # ---------- OTHER CONFIG ----------
    exts_str = extension
    PROCESS_MODE = "all"
    TARGET_CHUNK_ID = None

    out_dir = Path(OUTPUT_PATH)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Normalize extensions
    exts = {
        x.strip() if x.strip().startswith(".") else f".{x.strip()}"
        for x in (exts_str or [])
        if isinstance(x, str) and x.strip()
    }

    
    # ---------- RETURN EVERYTHING ----------
    return (
        OUT_XLSX,
        chunks_sheet,
        group_sheet,
        project_path,
        out_dir,
        exts,
        app_props_csv,   # may be None
        call_csv,        # may be None
        PROCESS_MODE,
        TARGET_CHUNK_ID,
        PARAGRAPH_LINEAGE_EXCEL,
    )


def collected_chunks_recursively(start_chunk_id, parent_to_children):
    visited = set()

    def dfs(chunk_id):
        if chunk_id in visited:
            return
        visited.add(chunk_id)

        for child in parent_to_children.get(chunk_id, []):
            dfs(child)
    dfs(start_chunk_id)
    return visited

def group_summary_path(gid: str) -> Path:
    return GROUP_SUMMARIES_DIR / f"{gid}.yaml" if GROUP_SUMMARIES_DIR else Path(f"{gid}.yaml")

def group_combined_path(gid: str) -> Path:
    return GROUP_COMBINED_DIR / f"{gid}.txt" if GROUP_COMBINED_DIR else Path()

def chunk_summary_path(cid: str) -> Path:
    return SUMMARIES_DIR / f"{cid}.yaml" if SUMMARIES_DIR else Path()

def chunk_combined_path(cid: str) -> Path:
    return COMBINED_DIR / f"{cid}.txt" if COMBINED_DIR else Path()

# ==========================================================
# Group mappings loaders (Excel / CSV)
# ==========================================================
def load_group_mappings_csv(csv_path: Optional[Path]) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = {}
    if not csv_path:
        # print("[GroupMap] No CSV path provided.")
        return mapping
    if not csv_path.exists():
        # print(f"[GroupMap] CSV path does not exist: {csv_path}")
        return mapping

    # print(f"[GroupMap] Loading group mappings from: {csv_path}")
    try:
        with csv_path.open("r", encoding="ISO-8859-1", errors="replace") as f:
            reader = csv.DictReader(f)
            # print(f"[GroupMap] CSV headers: {reader.fieldnames}")
            row_count = 0
            for r in reader:
                row_count += 1
                gid = (r.get("chunk_id") or r.get("Group") or "").strip()
                methods_raw = (r.get("methods_in_chunk") or r.get("Methods") or "").strip()
                subpath_raw = (r.get("Subpath") or "").strip()

                if not gid or not is_group_id(gid):
                    continue

                methods: List[str] = []
                if methods_raw:
                    methods.extend([m.strip() for m in re.split(r"[,\n;]+", methods_raw) if m.strip()])

                if subpath_raw:
                    parts = re.split(r"(?:->|,|\n|;)+", subpath_raw)
                    methods.extend([p.strip() for p in parts if p.strip()])

                seen: Set[str] = set()
                clean: List[str] = []
                for m in methods:
                    if m and m not in seen:
                        seen.add(m)
                        clean.append(m)

                mapping[gid] = clean

            # print(f"[GroupMap] Loaded {len(mapping)} groups.")
    except Exception as e:
        print(f"[GroupMap] Failed to load CSV: {e}")
    return mapping

def load_group_mappings_df(df: pd.DataFrame) -> Dict[str, List[str]]:
    cols_lower = {c.lower(): c for c in df.columns}
    if ("group" not in cols_lower) or ("methods" not in cols_lower):
        raise ValueError("[GroupMap] Group Mappings Sheet missing required columns: ['Group', 'Methods']")
    group_col = cols_lower["group"]
    methods_col = cols_lower["methods"]
    subpath_col = cols_lower.get("subpath")

    mapping: Dict[str, List[str]] = {}
    # print(f"[GroupMap] Loading group mappings from Excel DataFrame with {len(df)} rows.")
    for _, r in df.iterrows():
        gid = str(r.get(group_col) or "").strip()
        methods_raw = str(r.get(methods_col) or "").strip()
        subpath_raw = str(r.get(subpath_col) or "").strip() if subpath_col else ""

        if not gid or not is_group_id(gid):
            continue

        methods: List[str] = []
        if methods_raw:
            methods.extend([m.strip() for m in re.split(r"[,\n;]+", methods_raw) if m.strip()])

        if subpath_raw:
            parts = re.split(r"(?:->|,|\n|;)+", subpath_raw)
            methods.extend([p.strip() for p in parts if p.strip()])

        seen: Set[str] = set()
        clean: List[str] = []
        for m in methods:
            if m and m not in seen:
                seen.add(m)
                clean.append(m)

        mapping[gid] = clean

    # print(f"[GroupMap] Loaded {len(mapping)} groups from Excel.")
    return mapping

# ==========================================================
# Group / Chunk / Call ensure & builders (CODE-ONLY)
# ==========================================================
def ensure_group_combined_only(
    ext: Optional[List[str]],
    gid: str,
    all_source_files: List[Path],
    PARAGRAPH_LINEAGE_EXCEL:str,
    CHUNK_LIMIT:int,
    level: str,
    app_props_lookup: Optional[Dict[str, List[Dict[str, str]]]] = None,
) -> str:
    combined_text = build_combined_input_for_group(ext, gid, all_source_files,PARAGRAPH_LINEAGE_EXCEL,CHUNK_LIMIT,level,app_props_lookup)
    group_combined_path(gid).write_text(combined_text, encoding="utf-8")
    # print(f"[EnsureGroup] Group combined input (code-only) written: {group_combined_path(gid)} (len={len(combined_text)})")
    return str(group_combined_path(gid))

def build_combined_input_for_group(
    ext: Optional[List[str]],
    gid: str,
    all_source_files: List[Path],
    PARAGRAPH_LINEAGE_EXCEL:str,
    CHUNK_LIMIT:int,
    level: str,
    app_props_lookup: Optional[Dict[str, List[Dict[str, str]]]] = None,
) -> str:
    ext = _norm_ext(ext) 
    tokens = GROUP_METHODS_MAP.get(gid, [])
    # print(f"[GroupBuild] {gid} mapped tokens: {tokens}")


    extract_tokens: List[str] = []
    for t in tokens:
        if is_chunk_id(t):
            continue
        # g = normalize_group_token(t)
        # if g and is_group_id(g):
        #     continue
        if "." in t:
            extract_tokens.append(strip_no_of_lines_name(t))
        else:
            print(f"[GroupBuild] Ignored token (not method): '{t}'")

    global CURRENT_GROUP_ID
    CURRENT_GROUP_ID = gid
    if level == "program":
        attach_property=False
    if level == "process":
        attach_property = True
        try:
            # IMPORTANT: no per-method properties; aggregated banner appended at bottom
            code_map, source_map = extract_code_for_methods_with_files(
                ext,
                extract_tokens,
                all_source_files,
                PARAGRAPH_LINEAGE_EXCEL,
                CHUNK_LIMIT=CHUNK_LIMIT,
                props_lookup=app_props_lookup,
                attach_properties=False,
            )
        finally:
            CURRENT_GROUP_ID = None
        
    

    body = _format_code_map_as_combined_snippet(
        ext,
        gid,
        code_map,
        source_map,
        parent_entity=None,
        unit_type="group",
    )

    final_body = body


    # # --- NEW: Append one aggregated properties block for the group (bottom) ---
    
    if level == "process":
        # print("call_1")
        agg_props = build_aggregated_app_props_for_unit(
            ext,
            method_tokens=extract_tokens,
            props_lookup=app_props_lookup,
            exact_lookup=APP_PROPS_BY_METHOD_EXACT,
        )
        
        pe_no_lines = _count_lines(agg_props)
        pe_block_title = f"{gid}"

        if ".java" in ext:
            fenced_props = (
                f"--- BEGIN METHOD: {pe_block_title} no_of_lines : {pe_no_lines} ---\n"
                f"{agg_props}\n"
                f"--- END METHOD: {pe_block_title} no_of_lines : {pe_no_lines} ---"
            )
        
        final_body = f"{final_body}\n\n{fenced_props}"


    # --- Header + metrics AFTER append ---
    meta_by_file = EXTRACTION_META_BY_GROUP.get(gid, {})
    header_without_metrics = build_unit_metadata_block(meta_by_file) if meta_by_file else ""

    provisional_text = f"{header_without_metrics}\n{final_body}" if header_without_metrics else final_body
    breakdown = compute_document_line_breakdown(provisional_text, ext)
    header_with_metrics = build_unit_metadata_block(meta_by_file, breakdown) if meta_by_file else ""

    # SAFE: preserve entire final_body (including appended props)
    if header_with_metrics:
        lines = final_body.splitlines()
        if lines:
            final = "\n".join([lines[0], "", header_with_metrics] + lines[1:])
        else:
            final = header_with_metrics
    else:
        final = final_body

    # print(f"[GroupBuild] Final combined length for {gid}: {len(final)}")
    return final

def ensure_chunk_combined_only(
    ext: Optional[List[str]],
    cid: str,
    level: str,
    build_combined_input_for_chunk_fn,
) -> str:
    combined_text = build_combined_input_for_chunk_fn(cid, ext,level)
    chunk_combined_path(cid).write_text(combined_text, encoding="utf-8")
    # print(f"[EnsureChunk] Combined input (code-only) written: {chunk_combined_path(cid)} (len={len(combined_text)})")
    return str(chunk_combined_path(cid))

# ==========================================================
# Main
# ==========================================================
def code_extraction(
    project_path,
    extension,
    PARAGRAPH_LINEAGE_EXCEL,
    OUTPUT_PATH,
    OUT_XLSX,
    CHUNK_LIMIT,
    level,
    chunks_sheet,
    group_sheet,
    spec_sheet,
    call_sheet,
    ui_extension
):
    print(" project_path : ",project_path)
    start_time = datetime.now()
    log_time(f"Code Extraction START")
    
    print("================= code_extraction =========================")
    extension = _norm_ext(extension)
    (excel_input, chunks_sheet, groups_sheet,
     project_root, out_dir, exts, app_props_csv,call_csv, PROCESS_MODE, TARGET_CHUNK_ID, PARAGRAPH_LINEAGE_EXCEL) = get_config(
        extension, project_path, PARAGRAPH_LINEAGE_EXCEL, OUTPUT_PATH, OUT_XLSX, chunks_sheet, group_sheet, spec_sheet,call_sheet
    )

    global SUMMARIES_DIR, COMBINED_DIR, GROUP_SUMMARIES_DIR, GROUP_COMBINED_DIR, REPORTS_DIR
    SUMMARIES_DIR = (Path(out_dir) / "summaries")
    COMBINED_DIR = (Path(out_dir) / "combined_inputs")
    GROUP_SUMMARIES_DIR = (Path(out_dir) / "group_summaries")
    GROUP_COMBINED_DIR = (Path(out_dir) / "group_combined_inputs")
    REPORTS_DIR = (Path(out_dir) / "reports")
    for d in (SUMMARIES_DIR, COMBINED_DIR, GROUP_SUMMARIES_DIR, GROUP_COMBINED_DIR, REPORTS_DIR):
        d.mkdir(parents=True, exist_ok=True)

    index_csv = Path(out_dir) / "chunk_summary_index.csv"
    out_xlsx  = Path(out_dir) / "chunk_flow_reusability.xlsx"
    llm_report_csv = REPORTS_DIR / "llm_call_report.csv"
    llm_report_xlsx = REPORTS_DIR / "llm_call_report.xlsx"

    df_chunks: Optional[pd.DataFrame] = None
    df_groups: Optional[pd.DataFrame] = None
    df_calls: Optional[pd.DataFrame] = None

    excel_path = Path(excel_input) if excel_input else None
    if not (excel_input and excel_path.exists()):
        raise FileNotFoundError(f"[ERROR] Excel file not found: {excel_input}")
    try:
        xls = pd.ExcelFile(excel_input, engine="openpyxl")

        if chunks_sheet in xls.sheet_names:
            df_chunks = pd.read_excel(xls, sheet_name=chunks_sheet)
        else:
            raise ValueError(f"[ERROR] Chunks sheet '{chunks_sheet}' not found in Excel.")

        if groups_sheet in xls.sheet_names:
            df_groups = pd.read_excel(xls, sheet_name=groups_sheet)
        else:
            df_groups = None

    except Exception as e:
        raise RuntimeError(f"[ERROR] Failed to read Excel '{excel_input}': {e}")

    require_columns(
        df_chunks,
        ["chunk_id", "parent_entity", "clear_code_sum", "direct_children", "descendants_count", "methods_in_chunk"],
        "Chunks Input",
    )

    global GROUP_METHODS_MAP
    if df_groups is not None:
        GROUP_METHODS_MAP = load_group_mappings_df(df_groups)
    else:
        GROUP_METHODS_MAP = {}

    chunk_meta: Dict[str, Dict[str, str]] = {}
    chunk_to_tokens: Dict[str, List[str]] = {}
    parent_to_children: Dict[str, List[str]] = {}
    all_chunk_ids: Set[str] = set()

    for _, row in df_chunks.iterrows():
        cid = str(row["chunk_id"]).strip()
        if not cid or cid.lower() in ("nan", "none"):
            continue
        all_chunk_ids.add(cid)

        chunk_meta[cid] = {
            "parent_entity": str(row.get("parent_entity") or "").strip(),
            "clear_code_sum": str(row.get("clear_code_sum") or "").strip(),
            "descendants_count": str(row.get("descendants_count") or "").strip(),
        }

        children = split_names(row.get("direct_children") or "")
        children = [c for c in children if c and c.lower() not in ("nan", "none")]
        parent_to_children[cid] = children

        tokens = split_names(row.get("methods_in_chunk") or "")
        chunk_to_tokens[cid] = tokens

    PROCESS_MODE = "all" if PROCESS_MODE is None else PROCESS_MODE

    if PROCESS_MODE == "single":
        valid_chunks = collected_chunks_recursively(TARGET_CHUNK_ID, parent_to_children)
        all_chunk_ids = set(valid_chunks)

        chunk_meta = {cid: meta for cid, meta in chunk_meta.items() if cid in all_chunk_ids}
        chunk_to_tokens = {cid: toks for cid, toks in chunk_to_tokens.items() if cid in all_chunk_ids}
        parent_to_children = {cid: kids for cid, kids in parent_to_children.items() if cid in all_chunk_ids}

    parent_ids = {cid for cid, ch in parent_to_children.items() if len(ch) > 0}
    child_ids = {c for ch in parent_to_children.values() for c in ch}

    # ---- App props loader (existing) ----
    app_props_lookup = None
    if app_props_csv:
        app_props_path = Path(app_props_csv)
        if app_props_path.exists():
            app_props_lookup = load_app_properties_csv(app_props_path, extension)

    # ---- NEW: Edges loader (optional path) ----
    global EDGES_LOOKUP
    EDGES_LOOKUP = {}
    if call_csv:
        try:
            edges_path = Path(call_csv)
            if edges_path.exists():
                EDGES_LOOKUP = load_edges_csv(edges_path)
        except Exception as e:
            print(f"[Main][WARN] Failed to load edges CSV '{call_csv}': {e}")

    global PROJECT_ROOT, FILE_ID_MAP
    src_root = Path(project_root)
    PROJECT_ROOT = src_root
    all_source_files = list_source_files(src_root, exts)
    FILE_ID_MAP = {}
    for idx, p in enumerate(all_source_files, start=1):
        FILE_ID_MAP[p] = f"f{idx}"
    global summary_path_by_chunk
    processing_state: Dict[str, str] = {}
    summary_path_by_chunk = {}
    llm_rows: List[Dict[str, str]] = []
    index_rows: List[Dict[str, str]] = []

    def process_chunk_recursive(cid: str, ext: Optional[List[str]], level: str):
        state = processing_state.get(cid)
        if state == "done":
            return
        if state == "pending":
            return
        processing_state[cid] = "pending"
        ensure_chunk_combined_only(extension, cid, level, build_combined_input_for_chunk)
        combined_text = chunk_combined_path(cid).read_text(encoding="utf-8", errors="replace")
        code_lines, props_lines, summary_lines = compute_input_line_metrics(combined_text, extension)
        llm_rows.append({
            "unit_type": "chunk",
            "chunk_id": cid,
            "code_lines": str(code_lines),
            "application_properties_lines": str(props_lines),
            "summary_lines": str(summary_lines),
            "combined_input_path": str(chunk_combined_path(cid)),
            "yaml_summary_path": "",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        })
        processing_state[cid] = "done"
        # print(f"[Process] Completed chunk: {cid}")


    def build_combined_input_for_chunk(cid: str, ext: Optional[List[str]],level: str) -> str:
        ext = _norm_ext(extension)          # <--- ADD
        ui_ext_norm = _norm_ext(ui_extension) 

        attach_property = (level == "process")
        tokens = chunk_to_tokens.get(cid, [])
        children = parent_to_children.get(cid, [])

        extract_tokens: List[str] = []
        for t in tokens:
            if is_chunk_id(t):
                continue
            if "." in t:
                normalized_method = strip_no_of_lines_name(t)
                extract_tokens.append(normalized_method)
            else:
                print(f"[Build] Ignored token (neither method nor group): '{t}'")

        normalized_primary_tokens = [
            _normalize_key(strip_no_of_lines_name(t.lower()))
            for t in extract_tokens
        ]

        dependency_tokens = set()

        for t in normalized_primary_tokens:
            deps = EDGES_LOOKUP.get(t, [])
            for e in deps:
                imported_norm = e["imported_name"].lower().strip()
                if imported_norm:
                    dependency_tokens.add(imported_norm)
        extract_tokens_extended = list(extract_tokens)

        def _unique_preserve_order(items):
            seen = set()
            out = []
            for x in items:
                if x not in seen:
                    seen.add(x)
                    out.append(x)
            return out
        for dep in dependency_tokens:
            extract_tokens_extended.append(dep)
            extract_tokens_extended.append(f"{dep}.{dep}")  # fallback form
        extract_tokens_extended = _unique_preserve_order(extract_tokens_extended)
        global CURRENT_CHUNK_ID
        CURRENT_CHUNK_ID = cid
        try:
            code_map, source_map = extract_code_for_methods_with_files(
                extension,
                extract_tokens_extended,
                all_source_files,
                PARAGRAPH_LINEAGE_EXCEL,
                CHUNK_LIMIT =CHUNK_LIMIT,
                props_lookup=app_props_lookup,
                attach_properties=False,
            )
        finally:
            CURRENT_CHUNK_ID = None

        ordered_code_map = {}
        ordered_source_map = {}

        for token in extract_tokens_extended:
            method_only = token.split()[0]   # get ONLY "ACCTAPPR.INIT"
            key_norm = strip_no_of_lines_name(method_only)

            if key_norm in code_map:
                ordered_code_map[key_norm] = code_map[key_norm]
                ordered_source_map[key_norm] = source_map.get(key_norm)
        for k in code_map:
            if k not in ordered_code_map:
                ordered_code_map[k] = code_map[k]
                ordered_source_map[k] = source_map.get(k)
        combined_parts: List[str] = []
        parent_entity = (chunk_meta.get(cid) or {}).get("parent_entity") or ""
        body = _format_code_map_as_combined_snippet(
            extension,
            cid,
            ordered_code_map,
            ordered_source_map,
            parent_entity=parent_entity,
            unit_type="chunk",
        )
        combined_parts.append(body)

        final_body = "\n".join(combined_parts).rstrip()

        call_graph_lines = []
        normalized_primary_tokens = [
            _normalize_key(strip_no_of_lines_name(t.lower()))
            for t in extract_tokens
        ]

        for t in normalized_primary_tokens:
            deps = []
            for key, rows in EDGES_LOOKUP.items():
                if key.startswith(t + "."):
                    deps.extend(rows)

            for e in deps:
                src = e["name"]
                tgt = e["imported_name"]
                etype = e.get("edge_type", "")
                call_graph_lines.append(f"{src}  →  {tgt}   ({etype})")

        if call_graph_lines:
            call_graph_block = (
                "\n--- CALL GRAPH ---\n" +
                "\n".join(call_graph_lines) +
                "\n--- END CALL GRAPH ---\n"
            )
            final_body = f"{final_body}\n\n{call_graph_block}"
        
        if level == "process":
            agg_props = build_aggregated_app_props_for_unit(
                extension,
                method_tokens=extract_tokens_extended,
                props_lookup=app_props_lookup,
                exact_lookup=APP_PROPS_BY_METHOD_EXACT,
            )

            pe_no_lines = _count_lines(agg_props)
            pe_block_title = f"{cid}"

            if ".java" in extension:
                fenced_props = (
                    f"--- BEGIN METHOD: {pe_block_title} no_of_lines : {pe_no_lines} ---\n"
                    f"{agg_props}\n"
                    f"--- END METHOD: {pe_block_title} no_of_lines : {pe_no_lines} ---"
                )
            

            final_body = f"{final_body}\n\n{fenced_props}"

            meta_by_file = EXTRACTION_META_BY_CHUNK.get(cid, {})
            header_without_metrics = build_unit_metadata_block(meta_by_file) if meta_by_file else ""
            provisional_text = f"{header_without_metrics}\n{final_body}" if header_without_metrics else final_body
            breakdown = compute_document_line_breakdown(provisional_text, extension)
            header_with_metrics = build_unit_metadata_block(meta_by_file, breakdown) if meta_by_file else ""

            if header_with_metrics:
                lines = final_body.splitlines()
                if lines:
                    final_body = "\n".join([lines[0], "", header_with_metrics] + lines[1:])
                else:
                    final_body = header_with_metrics

        # ✅ ALWAYS RETURN
        return final_body

    if PROCESS_MODE == "single":
        target_methods = set()
        for cid in all_chunk_ids:
            target_methods.update(chunk_to_tokens.get(cid, []))
        filtered_group_ids = []
        for gid, tokens in GROUP_METHODS_MAP.items():
            for t in tokens:
                if t in target_methods:
                    filtered_group_ids.append(gid)
                    break
        for gid in sorted(filtered_group_ids):
            ensure_group_combined_only(extension, gid, all_source_files,PARAGRAPH_LINEAGE_EXCEL,CHUNK_LIMIT,level, app_props_lookup)

    else:
        all_group_ids = sorted(GROUP_METHODS_MAP.keys())
        for gid in all_group_ids:
            ensure_group_combined_only(extension, gid, all_source_files,PARAGRAPH_LINEAGE_EXCEL,level,CHUNK_LIMIT, app_props_lookup)

    for cid in sorted(all_chunk_ids):
        process_chunk_recursive(cid, extension,level)

    for cid in all_chunk_ids:
        role = "parent" if cid in parent_ids else "child"
        meta = chunk_meta.get(cid, {})
        index_rows.append(
            {
                "unit_type": "chunk",
                "chunk_id": cid,
                "type": role,
                "parent_entity": meta.get("parent_entity", ""),
                "clear_code_sum": meta.get("clear_code_sum", ""),
                "descendants_count": meta.get("descendants_count", ""),
                "combined_input_path": str(chunk_combined_path(cid)) if chunk_combined_path(cid).exists() else "",
                "yaml_summary_path": "",
            }
        )

    for gid in GROUP_METHODS_MAP.keys():
        index_rows.append(
            {
                "unit_type": "group",
                "chunk_id": gid,
                "type": "group",
                "parent_entity": "",
                "clear_code_sum": "",
                "descendants_count": "",
                "combined_input_path": str(group_combined_path(gid)) if group_combined_path(gid).exists() else "",
                "yaml_summary_path": "",
            }
        )

    index_df = pd.DataFrame(index_rows)
    Path(index_csv).write_text(index_df.to_csv(index=False), encoding="utf-8")
    # print(f"[Main] Index CSV written: {index_csv}")
    try:
        with pd.ExcelWriter(out_xlsx, engine="openpyxl") as xw:
            index_df.to_excel(xw, sheet_name="unit_index", index=False)
    except Exception as e:
        print(f"[WARN] Could not write Excel index: {e}")

    llm_df = pd.DataFrame(llm_rows)
    llm_report_csv.write_text(llm_df.to_csv(index=False), encoding="utf-8")
    try:
        with pd.ExcelWriter(llm_report_xlsx, engine="openpyxl") as xw:
            llm_df.to_excel(xw, sheet_name="llm_calls", index=False)
        # print(f"[Main] LLM Report Excel written: {llm_report_xlsx}")
    except Exception as e:
        print(f"[WARN] Could not write LLM report Excel: {e}")

    end_time = datetime.now()

    elapsed = (end_time - start_time).total_seconds()
    log_time(
        f"Code extraction END | "
        f"Duration={elapsed:.3f} sec"
    )
    return out_dir,COMBINED_DIR

def load_app_properties_csv(csv_path: Optional[Path], ext: Optional[List[str]]) -> Dict[str, List[Dict[str, str]]]:
    global APP_PROPS_BY_METHOD_EXACT

    lookup: Dict[str, List[Dict[str, str]]] = {}
    APP_PROPS_BY_METHOD_EXACT = {}

    if not csv_path or not csv_path.exists():
        # print(f"[AppProps] CSV missing or invalid: {csv_path}")
        return lookup

    # print(f"[AppProps] Loading from: {csv_path}")

    with csv_path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)

        def get_val(r: dict, *names: str) -> str:
            for n in names:
                for k in r:
                    if k and k.strip().lower() == n.lower():
                        return (r.get(k) or "").strip()
            return ""

        for r in reader:
            annotation = get_val(r, "Annotation", "Type", "spec_type")
            prop       = get_val(r, "Property", "variable", "PropertyName")
            actual     = get_val(r, "Actual Value", "actual_value", "Value")
            method_raw = get_val(r, "method_name", "MethodName", "ProcedureName", "ParagraphName")

            # -------------------------
            # ✅ EXACT BEGIN TOKEN MAP
            # -------------------------
            if method_raw:
                method_key = strip_no_of_lines_name(method_raw).strip()
                print("method_key : ",method_key)
                APP_PROPS_BY_METHOD_EXACT.setdefault(method_key, []).append({
                    "Annotation": annotation,
                    "Property": prop,
                    "Actual Value": actual,
                })

            # -------------------------
            # ✅ EXISTING NORMALIZED MAP (UNCHANGED)
            # -------------------------
            file_name_raw = get_val(r, "FileName", "Program", "ProgramName")
            file_stem = Path(file_name_raw).stem.lower() if file_name_raw else ""

            if file_stem and method_raw:
                norm_key = _normalize_key(f"{file_stem}.{method_raw}")
                lookup.setdefault(norm_key, []).append({
                    "Annotation": annotation,
                    "Property": prop,
                    "Actual Value": actual,
                })

            elif file_stem:
                norm_key = _normalize_key(f"{file_stem}.")
                lookup.setdefault(norm_key, []).append({
                    "Annotation": annotation,
                    "Property": prop,
                    "Actual Value": actual,
                })

    print(
        f"[AppProps] Loaded exact method keys: {len(APP_PROPS_BY_METHOD_EXACT)}, "
        f"normalized keys: {len(lookup)}"
    )

    return lookup

def load_edges_csv(csv_path_1: Optional[Path]) -> Dict[str, List[Dict[str, str]]]:

    global EDGES_BY_IMPORTED_EXACT
    lookup: Dict[str, List[Dict[str, str]]] = {}
    EDGES_BY_IMPORTED_EXACT = {}

    if not csv_path_1 or not csv_path_1.exists():
        # print(f"[Edges] CSV missing or invalid: {csv_path_1}")
        return lookup

    # print(f"[Edges] Loading from: {csv_path_1}")
    with csv_path_1.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        raw_headers = reader.fieldnames or []
        # print(f"[Edges] Headers: {raw_headers}")

        def get_val(r: Dict[str, str], *candidates: str) -> str:
            # Header-tolerant extraction (same strategy as load_app_properties_csv)
            norm_map = {(k or "").strip(): v for k, v in r.items()}
            for cand in candidates:
                for key_variant in (cand, cand.strip(), cand.replace("\r", "")):
                    if key_variant in r and r[key_variant] is not None:
                        return r[key_variant]
                    if key_variant in norm_map and norm_map[key_variant] is not None:
                        return norm_map[key_variant]
                # case-insensitive fallback
                for k in r.keys():
                    if k and k.strip().lower() == cand.strip().lower():
                        val = r.get(k)
                        if val is not None:
                            return val
            return ""

        count = 0
        normalized_key_insertions = 0
        exact_imported_insertions = 0

        for r in reader:
            count += 1

            # Core identifiers (normalized)
            name_raw = (get_val(r, "name") or "").strip()
            imported_name_raw = (get_val(r, "imported_name") or "").strip()
            name_norm = name_raw.lower()
            imported_name_norm = imported_name_raw.lower()

            # Other fields for context (kept similar to your std dict style)
            row_id = (get_val(r, "id") or "").strip()
            typ = (get_val(r, "Type", "type") or "").strip()
            total_lines = (get_val(r, "Total_lines", "total_lines") or "").strip()
            imported_id = (get_val(r, "imported_id") or "").strip()
            imported_lines = (get_val(r, "imported_lines") or "").strip()
            edge_type = (get_val(r, "edge_type") or "").strip()

            std = {
                "id": row_id,
                "Type": typ,
                "name": name_norm,
                "Total_lines": total_lines,
                "imported_id": imported_id,
                "imported_name": imported_name_norm,
                "imported_lines": imported_lines,
                "edge_type": edge_type,
            }

            # ---- Normalized lookup keys (mirrors props loader style) ----
            if name_norm and imported_name_norm:
                key_norm = _normalize_key(strip_no_of_lines_name(f"{name_norm}.{imported_name_norm}"))
                lookup.setdefault(key_norm, []).append(std)
                normalized_key_insertions += 1
            elif name_norm:
                key_norm = _normalize_key(f"{name_norm}.")
                lookup.setdefault(key_norm, []).append(std)
                normalized_key_insertions += 1
            elif imported_name_norm:
                key_norm = _normalize_key(f".{imported_name_norm}")
                lookup.setdefault(key_norm, []).append(std)
                normalized_key_insertions += 1

            # ---- Exact map keyed ONLY by imported_name (like props' exact by method) ----
            if imported_name_norm:
                EDGES_BY_IMPORTED_EXACT.setdefault(imported_name_norm, []).append(
                    {
                        "id": row_id,
                        "Type": typ,
                        "name": name_norm,
                        "imported_name": imported_name_norm,
                        "edge_type": edge_type,
                    }
                )
                exact_imported_insertions += 1


    return lookup
