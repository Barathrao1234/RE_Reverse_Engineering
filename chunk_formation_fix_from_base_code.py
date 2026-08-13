# # trigger node included

# # import pandas as pd
# # import html
# # import itertools
# # from pathlib import Path
# # import re
# # import os
# # import sys
# # import unicodedata
# # from collections import defaultdict,deque,Counter,OrderedDict
# # # -----------------------------
# # # Interactive config (preserved)
# # # -----------------------------
# def ask(prompt: str, default: str = "") -> str:
#     val = input(f"{prompt} [{default}]: ").strip()
#     return val if val else default

# # def get_config(unique_group):
# #     default_chunk_limit = "3000"
# #     chunk_limit_str = ask("Enter CHUNK_LIMIT (integer)", default_chunk_limit)
# #     try:
# #         chunk_limit = int(chunk_limit_str)
# #     except ValueError:
# #         print(f"[WARN] Invalid CHUNK_LIMIT '{chunk_limit_str}', falling back to {default_chunk_limit}")
# #         chunk_limit = int(default_chunk_limit)

# #     sheet_name = 0
# #     ext = Path(unique_group).suffix.lower()
# #     if ext in (".xlsx", ".xls"):
# #         sheet_name_in = ask("Excel sheet name (blank for first sheet / number for index)", "")
# #         if sheet_name_in.isdigit():
# #             sheet_name = int(sheet_name_in)
# #         elif sheet_name_in:
# #             sheet_name = sheet_name_in
# #         else:
# #             sheet_name = 0

# #     default_out_xlsx = os.path.splitext(unique_group)[0] + "_chunks_reusability.xlsx"
# #     out_xlsx = ask("Enter output Excel file", default_out_xlsx)

# #     return unique_group, sheet_name, chunk_limit, out_xlsx

# # # -----------------------------
# # # Branch loaders (stepped + any)
# # # -----------------------------
# # def load_branch_df_any(path: str, sheet_name=0) -> pd.DataFrame:
# #     p = Path(path)
# #     if not p.exists():
# #         raise FileNotFoundError(f"Input file not found: {path}")

# #     ext = p.suffix.lower()

# #     if ext == ".xlsx":
# #         df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
# #     elif ext == ".xls":
# #         df = pd.read_excel(path, sheet_name=sheet_name, engine="xlrd")
# #     else:
# #         lines = []
# #         with open(path, "r", encoding="ISO-8859-1", errors="replace") as f:
# #             for line in f:
# #                 s = line.strip().strip('"').strip("'").lstrip("\ufeff")
# #                 if s:
# #                     lines.append(s)
# #         if lines and lines[0].strip().lower() == "branch":
# #             lines = lines[1:]
# #         if not lines:
# #             raise ValueError("The input file appears to be empty or unreadable.")
# #         return pd.DataFrame({"Branch": lines})

# #     df.columns = [str(c).strip() for c in df.columns]

# #     if "Branch" in df.columns:
# #         out = df[["Branch"]].copy()
# #         out["Branch"] = out["Branch"].astype(str)
# #         out = out[out["Branch"].str.strip().astype(bool)]
# #         return out

# #     if df.shape[1] == 1:
# #         only_col = df.columns[0]
# #         out = df[[only_col]].copy()
# #         out.rename(columns={only_col: "Branch"}, inplace=True)
# #         out["Branch"] = out["Branch"].astype(str)
# #         out = out[out["Branch"].str.strip().astype(bool)]
# #         return out

# #     def row_to_branch(row):
# #         vals = []
# #         for val in row:
# #             if pd.isnull(val):
# #                 continue
# #             s = str(val).strip()
# #             if s:
# #                 vals.append(s)
# #         return " -> ".join(vals)

# #     branch_series = df.apply(row_to_branch, axis=1)
# #     branch_series = branch_series.astype(str)
# #     branch_series = branch_series[branch_series.str.strip().astype(bool)]
# #     return pd.DataFrame({"Branch": branch_series})

# # def load_branch_df_stepped(path: str, sheet_name=0) -> pd.DataFrame:
# #     """
# #     Parses 'stepped' Excel data where empty cells imply carry-over from above row.
# #     Preserves insertion order row-wise.
# #     """
# #     p = Path(path)
# #     if p.suffix.lower() not in (".xlsx", ".xls"):
# #         return load_branch_df_any(path, sheet_name)

# #     df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")

# #     level_cols = [c for c in df.columns if str(c).lower().startswith("level")]
# #     if not level_cols:
# #         level_cols = df.columns.tolist()

# #     rows_as_branches = []
# #     current_context = [None] * len(level_cols)

# #     for _, row in df.iterrows():
# #         path_segments = []
# #         for i, col in enumerate(level_cols):
# #             val = row[col]
# #             if pd.notnull(val) and str(val).strip() != "":
# #                 current_context[i] = str(val).strip()
# #                 for j in range(i + 1, len(level_cols)):
# #                     current_context[j] = None
# #             if current_context[i]:
# #                 path_segments.append(current_context[i])
# #             else:
# #                 break
# #         if path_segments:
# #             rows_as_branches.append(" -> ".join(path_segments))

# #     return pd.DataFrame({"Branch": rows_as_branches})

# # # -----------------------------
# # # Name/lines extractors and helpers
# # # -----------------------------
# # NO_LINES_RE = re.compile(
# #     r'^(?P<method>.*?)\s+no_of_lines\s*:\s*(?P<lines>\d+|Nil|None)\s*$',
# #     re.IGNORECASE
# # )

# # # -----------------------------
# # # Method detection
# # # -----------------------------
# # METHOD_PATTERN = re.compile(r'^[^.]+\.[^.]+')
# # def is_method(name: str) -> bool:
# #     if not name:
# #         return False
# #     s = str(name).strip()
# #     return bool(METHOD_PATTERN.match(s))

# # def extract_name_lines(segment: str):
# #     if segment is None or (isinstance(segment, float) and pd.isnull(segment)):
# #         return None, 0, 0

# #     s = str(segment).strip()

# #     level = 0
# #     m_level = re.search(r'\[\s*level\s*:\s*(\d+)\s*\]', s, flags=re.IGNORECASE)
# #     if m_level:
# #         level = int(m_level.group(1))
# #         s = re.sub(r'\[\s*level\s*:\s*\d+\s*\]', '', s, flags=re.IGNORECASE).strip()

# #     m_loc = re.search(r'^(.*?)\s*\[\s*LOC\s*:\s*(\d+)\s*\]$', s, flags=re.IGNORECASE)
# #     if m_loc:
# #         return m_loc.group(1).strip(), int(m_loc.group(2)), level

# #     m_nol_br = re.search(r'^(.*?)\s*\[\s*no_of_lines\s*:\s*(\d+|None|Nil)\s*\]$', s, flags=re.IGNORECASE)
# #     if m_nol_br:
# #         token = m_nol_br.group(2).lower()
# #         return m_nol_br.group(1).strip(), 0 if token in ('none', 'nil') else int(token), level

# #     m_nol_inline = re.search(r'^(.*?)\s+no_of_lines\s*:\s*(\d+|None|Nil)\s*$', s, flags=re.IGNORECASE)
# #     if m_nol_inline:
# #         token = m_nol_inline.group(2).lower()
# #         return m_nol_inline.group(1).strip(), 0 if token in ('none', 'nil') else int(token), level

# #     m_simple = re.search(r'^(.*?)(?:\s+(\d+|None|Nil))$', s, flags=re.IGNORECASE)
# #     if m_simple:
# #         token = (m_simple.group(2) or '').lower()
# #         return m_simple.group(1).strip(), 0 if token in ('none', 'nil') else (int(token) if token.isdigit() else 0), level

# #     return s.strip(), 0, level

# # def slugify_filename(name: str) -> str:
# #     if not isinstance(name, str):
# #         name = str(name)
# #     name = unicodedata.normalize("NFKD", name)
# #     name = name.replace(" ", "_")
# #     keep = []
# #     for ch in name:
# #         if ch.isalnum() or ch in ["_", "+", "-"]:
# #             keep.append(ch)
# #     return "".join(keep)

# # # -----------------------------
# # # Group detection
# # # -----------------------------
# # GROUP_PATTERN = re.compile(r'^(?:G|GRP|GROUP)[-_]?\d+(?:\.\d+)?$', re.I)

# # def is_group(name: str) -> bool:
# #     if not name:
# #         return False
# #     s = str(name).strip()
# #     return bool(GROUP_PATTERN.match(s))

# # # -----------------------------
# # # Global state for chunking
# # # -----------------------------
# # chunk_registry = {}
# # chunk_id_counter = itertools.count(1)
# # chunks_unique = []
# # chunk_usages = []
# # global_lines_by_name = {}
# # chunked_subtrees = set()
# # assigned_files_by_parent = {}

# # def gather_filenames_subtree(node):
# #     files = set(node.get("filenames", set()))
# #     for child in node.get("children", {}).values():
# #         files.update(gather_filenames_subtree(child))
# #     return files

# # def format_method_with_lines(name: str) -> str:
# #     if name is None:
# #         return ""
# #     s = str(name).strip()
# #     if not s:
# #         return ""
# #     lines = global_lines_by_name.get(s, 0)
# #     try:
# #         lines_int = int(lines)
# #         lines_txt = "Nil" if lines_int == 0 else str(lines_int)
# #     except Exception:
# #         lines_txt = "Nil"
# #     return f"{s} no_of_lines : {lines_txt}"

# # def format_refs_with_lines(refs_csv: str) -> str:
# #     parts = [p.strip() for p in str(refs_csv).split(",") if p.strip()]
# #     return ", ".join(format_method_with_lines(p) for p in parts)

# # # -----------------------------
# # # ORDER-PRESERVING register
# # # -----------------------------
# # def register_or_get_chunk_id(
# #     filenames_ordered,
# #     code_sum,
# #     row_indices_ordered,
# #     trigger_node,
# #     level,
# #     trigger_node_name,
# #     child_chunk_refs="",
# #     chunk_type="leaf",
# #     groups_ordered=None,
# # ):
# #     if groups_ordered is None:
# #         groups_ordered = []

# #     # PATCH: avoid cross-parent collisions; preserve order
# #     key = (trigger_node, tuple(filenames_ordered))
# #     if key in chunk_registry:
# #         return chunk_registry[key]

# #     num = next(chunk_id_counter)
# #     base_id = f"C{num:03d}"
# #     trigger_slug = slugify_filename(trigger_node) if trigger_node else "unknown_trigger"
# #     chunk_id = f"{base_id}_{trigger_slug}"
# #     chunk_registry[key] = chunk_id

# #     if chunk_type == "leaf":
# #         display_filenames = ", ".join(format_method_with_lines(fn) for fn in filenames_ordered)
# #     else:
# #         display_filenames = format_refs_with_lines(child_chunk_refs) if child_chunk_refs \
# #                             else ", ".join(format_method_with_lines(fn) for fn in filenames_ordered)

# #     # Groups always formatted with Nil LOC (their lines must not count toward chunk size)
# #     groups_csv = ", ".join(
# #         f"{g} no_of_lines : Nil" for g in groups_ordered
# #     ) if groups_ordered else ""

# #     display = display_filenames
# #     if groups_csv:
# #         display = ", ".join([s for s in [display_filenames, groups_csv] if s])

# #     row_indices_display = ", ".join(map(str, row_indices_ordered))

# #     record_common = {
# #         "chunk_id": chunk_id,
# #         "trigger_node": trigger_node,
# #         "level": level,
# #         "trigger_node_name": trigger_node_name,
# #         "methods_in_chunk": display,
# #         "groups": groups_csv,
# #         "row_indices": row_indices_display,
# #         "code_sum": code_sum,
# #         "child_chunk_refs": child_chunk_refs,
# #         "chunk_type": chunk_type,
# #         "triggered_by": f"{chunk_type.capitalize()} chunk for '{trigger_node}'"
# #     }

# #     chunks_unique.append({
# #         **record_common,
# #         "parent_node": f"Chunk under {trigger_node}",
# #     })

# #     chunk_usages.append({
# #         **record_common,
# #         "used_under_parent_node": f"Chunk under {trigger_node}",
# #     })

# #     return chunk_id

# # # -----------------------------
# # # Row-wise DFS iterator
# # # -----------------------------
# # def iter_entities_dfs(node):
# #     """
# #     Yield entity names in row-wise DFS order:
# #       - visit node itself (its own name is stored in 'filenames')
# #       - then visit children in insertion order
# #     """
# #     for nm in node.get("filenames", []):
# #         yield nm
# #     for _, child in node.get("children", {}).items():
# #         yield from iter_entities_dfs(child)

# # # -----------------------------
# # # Chunk creation (ORDERED) — PATCHED for UNIQUE-aware sizing
# # # -----------------------------
# # def create_chunks_for_children(children, level, trigger_node, CHUNK_LIMIT):
# #     """
# #     Greedy bin-packing of direct children into chunks bounded by CHUNK_LIMIT.

# #     SMALL child  (unique_total_lines <= CHUNK_LIMIT):
# #         Inline ALL its descendant methods into the current bin.
# #         The true incremental cost (sum of NEW unique method LOC) is charged
# #         to current_unique_sum.  Flush before adding if it would overflow.

# #     LARGE child  (unique_total_lines >= CHUNK_LIMIT):
# #         Do NOT inline its descendants here — doing so would silently produce
# #         a chunk whose content exceeds the limit while the cost-counter only
# #         shows the child root's own LOC.  Instead:
# #           1. Flush any accumulated small-child content first.
# #           2. Emit a minimal pointer-chunk: trigger_node + this child's own
# #              root method only, with child_name as child_chunk_ref.
# #           3. The child's full subtree will be split by its own
# #              assign_chunks_top_down call in PASS 2.

# #     This guarantees every flushed chunk's methods_in_chunk is actually ≤
# #     CHUNK_LIMIT in LOC, not just the accounting variable.
# #     """
# #     global chunked_subtrees, assigned_files_by_parent

# #     ordered_children = list(children.items())
# #     parent_seen = assigned_files_by_parent.setdefault(trigger_node, set())

# #     current_methods_list = []
# #     current_methods_set  = set()
# #     current_groups_list  = []
# #     current_groups_set   = set()
# #     current_unique_sum   = 0
# #     have_items           = False
# #     child_chunks_in_current = []

# #     def _flush_chunk():
# #         nonlocal current_methods_list, current_methods_set, current_groups_list, current_groups_set
# #         nonlocal current_unique_sum, have_items, child_chunks_in_current, parent_seen

# #         if not have_items:
# #             return

# #         methods_out = list(current_methods_list)
# #         groups_out  = list(current_groups_list)

# #         # Prepend trigger node itself if not already present
# #         if trigger_node:
# #             if is_method(trigger_node) and trigger_node not in methods_out:
# #                 methods_out.insert(0, trigger_node)
# #             elif is_group(trigger_node) and trigger_node not in groups_out:
# #                 groups_out.insert(0, trigger_node)

# #         unique_sum = sum(global_lines_by_name.get(fn, 0) for fn in methods_out if is_method(fn))

# #         register_or_get_chunk_id(
# #             filenames_ordered=methods_out,
# #             code_sum=unique_sum,
# #             row_indices_ordered=methods_out,
# #             trigger_node=trigger_node,
# #             level=level,
# #             trigger_node_name=trigger_node,
# #             child_chunk_refs=", ".join(child_chunks_in_current),
# #             chunk_type="leaf",
# #             groups_ordered=groups_out,
# #         )

# #         for child_name in child_chunks_in_current:
# #             chunked_subtrees.add((trigger_node, child_name))

# #         parent_seen.update(x for x in methods_out if is_method(x))

# #         # reset accumulators
# #         current_methods_list = []
# #         current_methods_set  = set()
# #         current_groups_list  = []
# #         current_groups_set   = set()
# #         current_unique_sum   = 0
# #         have_items           = False
# #         child_chunks_in_current = []

# #     for child_name, child_node in ordered_children:
# #         if (trigger_node, child_name) in chunked_subtrees:
# #             continue

# #         child_unique_total = child_node.get(
# #             "unique_total_lines", child_node.get("total_lines", child_node["lines"])
# #         )

# #         # ── LARGE child: pointer-only, do not inline descendants ────────────
# #         if child_unique_total >= CHUNK_LIMIT:
# #             # Flush whatever small-child content has accumulated so far.
# #             _flush_chunk()

# #             # Build a minimal pointer chunk: trigger + child root only.
# #             ptr_methods = []
# #             ptr_groups  = []
# #             if is_method(child_name) and child_name not in parent_seen:
# #                 ptr_methods = [child_name]
# #             elif is_group(child_name) and child_name not in parent_seen:
# #                 ptr_groups = [child_name]

# #             methods_out = list(ptr_methods)
# #             groups_out  = list(ptr_groups)
# #             if trigger_node:
# #                 if is_method(trigger_node) and trigger_node not in methods_out:
# #                     methods_out.insert(0, trigger_node)
# #                 elif is_group(trigger_node) and trigger_node not in groups_out:
# #                     groups_out.insert(0, trigger_node)

# #             if methods_out or groups_out:
# #                 unique_sum = sum(global_lines_by_name.get(fn, 0) for fn in methods_out if is_method(fn))
# #                 register_or_get_chunk_id(
# #                     filenames_ordered=methods_out,
# #                     code_sum=unique_sum,
# #                     row_indices_ordered=methods_out,
# #                     trigger_node=trigger_node,
# #                     level=level,
# #                     trigger_node_name=trigger_node,
# #                     child_chunk_refs=child_name,
# #                     chunk_type="leaf",
# #                     groups_ordered=groups_out,
# #                 )
# #                 parent_seen.update(m for m in methods_out if is_method(m))

# #             chunked_subtrees.add((trigger_node, child_name))
# #             continue

# #         # ── SMALL child: inline methods into current bin ─────────────────────
# #         child_entities        = list(iter_entities_dfs(child_node))
# #         child_methods_ordered = [e for e in child_entities if is_method(e)]
# #         child_groups_ordered  = [e for e in child_entities if is_group(e)]

# #         child_methods_new = [
# #             m for m in child_methods_ordered
# #             if m not in parent_seen and m not in current_methods_set
# #         ]
# #         incremental_unique = sum(global_lines_by_name.get(m, 0) for m in child_methods_new)

# #         # Flush if adding this child would overflow the current bin
# #         if have_items and child_methods_new and (current_unique_sum + incremental_unique) > CHUNK_LIMIT:
# #             _flush_chunk()
# #             # Recompute after flush — parent_seen may have grown
# #             child_methods_new = [
# #                 m for m in child_methods_ordered
# #                 if m not in parent_seen and m not in current_methods_set
# #             ]
# #             incremental_unique = sum(global_lines_by_name.get(m, 0) for m in child_methods_new)

# #         if child_methods_new or child_groups_ordered:
# #             # Pre-charge trigger node's own LOC every time a new bin starts.
# #             # The trigger is prepended at flush regardless of parent_seen, so its
# #             # LOC must always be counted against the budget.
# #             if not have_items and trigger_node and is_method(trigger_node):
# #                 current_unique_sum += global_lines_by_name.get(trigger_node, 0)
# #             for m in child_methods_ordered:
# #                 if m not in parent_seen and m not in current_methods_set:
# #                     current_methods_list.append(m)
# #                     current_methods_set.add(m)
# #             for g in child_groups_ordered:
# #                 if g not in current_groups_set:
# #                     current_groups_list.append(g)
# #                     current_groups_set.add(g)
# #             current_unique_sum += incremental_unique
# #             have_items = True
# #             child_chunks_in_current.append(child_name)
# #         else:
# #             chunked_subtrees.add((trigger_node, child_name))

# #     _flush_chunk()


# # def create_parent_reference_chunk(node_name, node, level):
# #     """
# #     Create a parent_ref chunk with ORDERED content.
# #     """
# #     global chunked_subtrees

# #     parent_methods_list = []
# #     parent_groups_list  = []
# #     seen_m = set()
# #     seen_g = set()

# #     if is_method(node_name):
# #         parent_methods_list.append(node_name); seen_m.add(node_name)
# #     elif is_group(node_name):
# #         parent_groups_list.append(node_name); seen_g.add(node_name)

# #     child_refs = []

# #     for child_name, child_node in node["children"].items():
# #         if (node_name, child_name) in chunked_subtrees:
# #             child_refs.append(child_name)
# #         else:
# #             for ent in iter_entities_dfs(child_node):
# #                 if is_method(ent) and ent not in seen_m:
# #                     parent_methods_list.append(ent); seen_m.add(ent)
# #                 elif is_group(ent) and ent not in seen_g:
# #                     parent_groups_list.append(ent); seen_g.add(ent)

# #     if parent_methods_list or parent_groups_list:
# #         unique_sum = sum(global_lines_by_name.get(fn, 0) for fn in parent_methods_list if is_method(fn))
# #         register_or_get_chunk_id(
# #             filenames_ordered=parent_methods_list,
# #             code_sum=unique_sum,
# #             row_indices_ordered=parent_methods_list,
# #             trigger_node=node_name,
# #             level=level,
# #             trigger_node_name=f"{node_name}_PARENT_ONLY",
# #             child_chunk_refs=", ".join(child_refs),
# #             chunk_type="parent_ref",
# #             groups_ordered=parent_groups_list,
# #         )


# # # =====================================================================
# # # Two-pass chunking strategy
# # # =====================================================================


# # def compute_totals(node_name, node):
# #     """
# #     PASS 1: Recursively compute:
# #       - total_lines        (structural sum; may double-count shared nodes)
# #       - unique_methods     (set of unique method names in this subtree)
# #       - unique_total_lines (sum of LOC for unique methods; groups always 0)
# #     No chunks are created here.
# #     """
# #     total_structural = node["lines"]

# #     unique_methods = set()
# #     if is_method(node_name):
# #         unique_methods.add(node_name)

# #     for child_name, child_node in node["children"].items():
# #         child_structural = compute_totals(child_name, child_node)
# #         total_structural += child_structural
# #         unique_methods |= child_node.get("unique_methods", set())

# #     node["total_lines"]        = total_structural
# #     node["unique_methods"]     = unique_methods
# #     # Groups are excluded from the LOC sum — they carry 0 lines by design
# #     node["unique_total_lines"] = sum(
# #         global_lines_by_name.get(m, 0) for m in unique_methods if is_method(m)
# #     )
# #     return total_structural


# # def assign_chunks_top_down(node_name, node, level, CHUNK_LIMIT, ancestor_chunked=False):
# #     """
# #     PASS 2: Walk top-down using UNIQUE totals to decide whether to split.

# #     A node is split when its unique_total_lines >= CHUNK_LIMIT AND it has
# #     children.  The split is performed by create_chunks_for_children, which
# #     now correctly handles large vs small children (see that function).
# #     Recursion always continues into children so every level gets evaluated.
# #     """
# #     total_unique = node.get("unique_total_lines", node.get("total_lines", node["lines"]))

# #     if total_unique >= CHUNK_LIMIT and node["children"]:
# #         create_chunks_for_children(node["children"], level + 1, node_name, CHUNK_LIMIT)

# #     for child_name, child_node in node["children"].items():
# #         assign_chunks_top_down(
# #             child_name, child_node, level + 1, CHUNK_LIMIT,
# #             ancestor_chunked=False,
# #         )


# # def aggregate_and_chunk(node_name, node, level, CHUNK_LIMIT):
# #     """
# #     LEGACY entry point kept for compatibility.
# #     Now delegates to the two-pass approach; no-op here.
# #     """
# #     pass


# # def _finalize_chunks(chunks_unique_list, chunk_usages_list):
# #     nodes_with_parent_ref = {
# #         c.get("trigger_node")
# #         for c in chunks_unique_list
# #         if c.get("chunk_type") == "parent_ref"
# #     }

# #     def keep_record(rec):
# #         return not (
# #             rec.get("chunk_type") == "leaf" and
# #             rec.get("trigger_node") in nodes_with_parent_ref
# #         )

# #     final_unique = [c for c in chunks_unique_list if keep_record(c)]
# #     final_usages = [u for u in chunk_usages_list if keep_record(u)]
# #     return final_unique, final_usages

# # # -----------------------------
# # # Build tree from Branch column (ORDERED children)
# # # -----------------------------
# # def build_tree(df: pd.DataFrame):
# #     tree = OrderedDict()
# #     for idx, row in df.iterrows():
# #         current = tree
# #         s = html.unescape(str(row["Branch"]))
# #         # normalize arrows from various escaped forms
# #         for arrow in ["->", "=> ", "→", " - > "]:
# #             s = s.replace(arrow, " -> ")
# #         segments = [seg.strip() for seg in re.split(r'\s*->\s*', s) if seg.strip()]

# #         for seg in segments:
# #             name, lines, _ = extract_name_lines(seg)
# #             if name:
# #                 if name not in current:
# #                     current[name] = {
# #                         "lines": lines,
# #                         "children": OrderedDict(),
# #                         "rows": set(),
# #                         "filenames": [name],   # LIST to preserve insertion order
# #                     }
# #                 # keep max lines
# #                 current[name]["lines"] = max(current[name]["lines"], lines)
# #                 current[name]["rows"].add(idx)
# #                 if name not in current[name]["filenames"]:
# #                     current[name]["filenames"].append(name)
# #                 global_lines_by_name[name] = max(global_lines_by_name.get(name, 0), lines if not is_group(name) else 0)
# #                 current = current[name]["children"]
# #     return tree

# # # -----------------------------
# # # Reusability functions (integrated)
# # # -----------------------------
# # _NO_OF_LINES_RE = re.compile(r"\s*no_of_lines\s*:\s*(?:\d+|nil|none)\s*$", re.IGNORECASE)

# # def normalize_entity_token(token: str) -> str:
# #     if token is None:
# #         return ""
# #     s = str(token).strip()
# #     if not s:
# #         return ""
# #     s = _NO_OF_LINES_RE.sub("", s).strip()
# #     return s

# # def parse_list(cell):
# #     if pd.isnull(cell):
# #         return []
# #     raw = str(cell)
# #     parts = [x.strip() for x in raw.split(",") if x.strip()]
# #     norm = [normalize_entity_token(x) for x in parts]
# #     return [x for x in norm if x]

# # def build_entity_to_chunk_id(df):
# #     """
# #     Choose a canonical chunk per entity (max code_sum, then min level).
# #     """
# #     canonical_map = {}
# #     if df is None or df.empty:
# #         return canonical_map
# #     grouped = df.groupby("parent_entity")
# #     for entity, group in grouped:
# #         sorted_group = group.sort_values(by=["code_sum", "level"], ascending=[False, True])
# #         canonical_map[str(entity)] = str(sorted_group.iloc[0]["chunk_id"])
# #     return canonical_map

# # def build_child_graph_raw(df):
# #     rows = df.to_dict("records")
# #     entity_to_rows = defaultdict(list)
# #     for r in rows:
# #         entity_to_rows[r["parent_entity"]].append(r)

# #     edges = []
# #     for r in rows:
# #         parent_chunk_id = r["chunk_id"]
# #         parent_entity   = r["parent_entity"]
# #         parent_level    = r.get("level", None)
# #         parent_code_sum = r.get("code_sum", None)

# #         filenames = parse_list(r.get("methods_in_chunk"))

# #         for fname in filenames:
# #             for child in entity_to_rows.get(fname, []):
# #                 if child["chunk_id"] == parent_chunk_id:
# #                     continue
# #                 edges.append({
# #                     "parent_chunk_id": parent_chunk_id,
# #                     "parent_entity": parent_entity,
# #                     "parent_level": parent_level,
# #                     "parent_code_sum": parent_code_sum,
# #                     "child_chunk_id": child["chunk_id"],
# #                     "child_entity": child["parent_entity"],
# #                     "child_level": child.get("level", None),
# #                     "child_code_sum": child.get("code_sum", None),
# #                 })

# #     edges_df = pd.DataFrame(edges)
# #     if edges_df.empty:
# #         return edges_df

# #     edges_df = edges_df.drop_duplicates()
# #     edges_df = edges_df.sort_values(
# #         by=["parent_entity", "parent_level", "parent_chunk_id",
# #             "child_entity", "child_level", "child_chunk_id"],
# #         ascending=[True, True, True, True, True, True]
# #     )
# #     return edges_df

# # def prune_edges(edges_df):
# #     if edges_df.empty:
# #         return edges_df.copy()

# #     df = edges_df[edges_df["parent_chunk_id"] != edges_df["child_chunk_id"]].copy()
# #     if df.empty:
# #         return df

# #     pairs = set(zip(df["parent_chunk_id"], df["child_chunk_id"]))
# #     mutual_pairs = {(u, v) for (u, v) in pairs if (v, u) in pairs}

# #     if not mutual_pairs:
# #         return df

# #     mask = ~df.apply(lambda r: (r["parent_chunk_id"], r["child_chunk_id"]) in mutual_pairs, axis=1)
# #     pruned = df[mask].copy()
# #     pruned = pruned.drop_duplicates()
# #     pruned = pruned.sort_values(
# #         by=["parent_entity", "parent_level", "parent_chunk_id",
# #             "child_entity", "child_level", "child_chunk_id"],
# #         ascending=[True, True, True, True, True, True]
# #     )
# #     return pruned

# # def _build_adj(edges_df):
# #     adj = defaultdict(set)
# #     for _, r in edges_df.iterrows():
# #         adj[r["parent_chunk_id"]].add(r["child_chunk_id"])
# #         _ = adj[r["child_chunk_id"]]
# #     return adj

# # def _tarjan_scc(adj):
# #     index = 0
# #     stack = []
# #     onstack = set()
# #     indices = {}
# #     lowlink = {}
# #     sccs = []

# #     def strongconnect(v):
# #         nonlocal index
# #         indices[v] = index
# #         lowlink[v] = index
# #         index += 1
# #         stack.append(v)
# #         onstack.add(v)

# #         for w in adj.get(v, set()):
# #             if w not in indices:
# #                 strongconnect(w)
# #                 lowlink[v] = min(lowlink[v], lowlink[w])
# #             elif w in onstack:
# #                 lowlink[v] = min(lowlink[v], indices[w])

# #         if lowlink[v] == indices[v]:
# #             comp = set()
# #             while True:
# #                 w = stack.pop()
# #                 onstack.discard(w)
# #                 comp.add(w)
# #                 if w == v:
# #                     break
# #             sccs.append(comp)

# #     for v in list(adj.keys()):
# #         if v not in indices:
# #             strongconnect(v)
# #     return sccs

# # def _dfs_reach(adj, start, cache):
# #     if start in cache:
# #         return cache[start]
# #     visited = set()
# #     stack = [start]
# #     while stack:
# #         u = stack.pop()
# #         for v in adj.get(u, set()):
# #             if v not in visited:
# #                 visited.add(v)
# #                 stack.append(v)
# #     cache[start] = visited
# #     return visited

# # def _transitive_reduction_dag(adj):
# #     reach_cache = {}
# #     to_remove = set()
# #     for u, children in adj.items():
# #         children_list = list(children)
# #         reach_by_v = {v: (_dfs_reach(adj, v, reach_cache) | {v}) for v in children_list}
# #         for w in children_list:
# #             for v in children_list:
# #                 if v != w and w in reach_by_v[v]:
# #                     to_remove.add((u, w))
# #                     break
# #     reduced = set()
# #     for u, children in adj.items():
# #         for v in children:
# #             if (u, v) not in to_remove:
# #                 reduced.add((u, v))
# #     return reduced

# # def transitive_reduction_safe(edges_df):
# #     if edges_df.empty:
# #         return edges_df.copy()

# #     adj = _build_adj(edges_df)
# #     sccs = _tarjan_scc(adj)
# #     comp_id = {node: i for i, comp in enumerate(sccs) for node in comp}

# #     comp_adj = defaultdict(set)
# #     for u, children in adj.items():
# #         cu = comp_id[u]
# #         for v in children:
# #             cv = comp_id[v]
# #             if cu != cv:
# #                 comp_adj[cu].add(cv)

# #     reduced_comp_edges = _transitive_reduction_dag(comp_adj)
# #     keep_edges = set()

# #     for u, children in adj.items():
# #         for v in children:
# #             if comp_id[u] == comp_id[v]:
# #                 keep_edges.add((u, v))

# #     needed_comp_pairs = set(reduced_comp_edges)
# #     for u, children in adj.items():
# #         cu = comp_id[u]
# #         for v in children:
# #             cv = comp_id[v]
# #             if cu != cv and (cu, cv) in needed_comp_pairs:
# #                 keep_edges.add((u, v))

# #     mask = edges_df.apply(lambda r: (r["parent_chunk_id"], r["child_chunk_id"]) in keep_edges, axis=1)
# #     reduced_df = edges_df[mask].copy()
# #     reduced_df = reduced_df.drop_duplicates(subset=["parent_chunk_id", "child_chunk_id"])
# #     reduced_df = reduced_df.sort_values(
# #         by=["parent_entity", "parent_level", "parent_chunk_id",
# #             "child_entity", "child_level", "child_chunk_id"],
# #         ascending=[True, True, True, True, True, True]
# #     )
# #     return reduced_df
# # def _append_isolated_nodes(edges_df, chunks_df):
# #     cols = [
# #         "parent_chunk_id", "parent_entity", "parent_level", "parent_code_sum",
# #         "child_chunk_id", "child_entity", "child_level", "child_code_sum"
# #     ]

# #     if edges_df.empty:
# #         edges_df = pd.DataFrame(columns=cols)

# #     parents = set(edges_df["parent_chunk_id"].dropna().astype(str))
# #     children = set(edges_df["child_chunk_id"].dropna().astype(str))
# #     nodes_in_graph = parents.union(children)

# #     all_chunks = chunks_df[["chunk_id", "parent_entity", "level", "code_sum"]].drop_duplicates()
# #     isolated = all_chunks[~all_chunks["chunk_id"].astype(str).isin(nodes_in_graph)]

# #     if isolated.empty:
# #         return edges_df

# #     iso_rows = []
# #     for _, r in isolated.iterrows():
# #         iso_rows.append({
# #             "parent_chunk_id": r["chunk_id"],
# #             "parent_entity": r["parent_entity"],
# #             "parent_level": r["level"],
# #             "parent_code_sum": r["code_sum"],
# #             "child_chunk_id": "",
# #             "child_entity": "",
# #             "child_level": None,
# #             "child_code_sum": None,
# #         })
# #     iso_df = pd.DataFrame(iso_rows, columns=cols)

# #     out = pd.concat([edges_df, iso_df], ignore_index=True)
# #     out = out.drop_duplicates()
# #     out = out.sort_values(
# #         by=["parent_entity", "parent_level", "parent_chunk_id",
# #             "child_entity", "child_level", "child_chunk_id"],
# #         ascending=[True, True, True, True, True, True]
# #     )
# #     return out

# # def compute_reusability_edges(reduced_edges_df):
# #     if reduced_edges_df.empty:
# #         return reduced_edges_df.copy()
# #     dedup_df = reduced_edges_df.drop_duplicates(subset=["parent_chunk_id", "child_chunk_id"]).copy()
# #     dedup_df = dedup_df.sort_values(
# #         by=["parent_entity", "parent_level", "parent_chunk_id",
# #             "child_entity", "child_level", "child_chunk_id"],
# #         ascending=[True, True, True, True, True, True]
# #     )
# #     return dedup_df

# # def compute_reusability_summary(reduced_edges_df: pd.DataFrame) -> pd.DataFrame:
# #     expected = [
# #         "parent_chunk_id", "parent_entity", "parent_level", "parent_code_sum",
# #         "child_chunk_id", "child_entity", "child_level", "child_code_sum"
# #     ]
# #     for col in expected:
# #         if col not in reduced_edges_df.columns:
# #             reduced_edges_df[col] = pd.Series(dtype=object)

# #     if reduced_edges_df.empty:
# #         return pd.DataFrame(columns=[
# #             "child_chunk_id", "child_entity", "child_level", "child_code_sum",
# #             "used_in_parents_count", "parents_list"
# #         ])

# #     agg = (reduced_edges_df
# #            .groupby(["child_chunk_id", "child_entity", "child_level", "child_code_sum"], dropna=False, as_index=False)
# #            .agg(parents=("parent_chunk_id", lambda xs: sorted({str(x) for x in xs if pd.notnull(x) and str(x)})))
# #     )
# #     agg["used_in_parents_count"] = agg["parents"].apply(len)
# #     agg["parents_list"] = agg["parents"].apply(lambda xs: ", ".join(xs))

# #     out = agg.drop(columns=["parents"]).sort_values(
# #         by=["used_in_parents_count", "child_entity"], ascending=[False, True]
# #     )
# #     return out

# # # ─────────────────────────────────────────────────────────────────────────────
# # # Hierarchy helpers
# # # ─────────────────────────────────────────────────────────────────────────────
# # def _build_flat_tree(tree: dict) -> dict:
# #     flat = {}
# #     stack = list(tree.items())
# #     while stack:
# #         name, node = stack.pop()
# #         bare = _NO_OF_LINES_RE.sub("", name).strip()
# #         flat[bare] = node
# #         for cname, cnode in node["children"].items():
# #             stack.append((cname, cnode))
# #     return flat

# # def build_call_flow(trigger_name: str, chunk_methods: set, flat_tree: dict) -> dict:
# #     bare_trigger = _NO_OF_LINES_RE.sub("", str(trigger_name or "")).strip()

# #     if bare_trigger not in flat_tree:
# #         return {
# #             "method": bare_trigger,
# #             "lines" : global_lines_by_name.get(bare_trigger, 0),
# #             "calls" : [
# #                 {"method": m, "lines": global_lines_by_name.get(m, 0), "calls": []}
# #                 for m in sorted(chunk_methods - {bare_trigger})
# #             ],
# #         }

# #     visited = set()

# #     def _dfs(bare_name):
# #         if bare_name in visited:
# #             return None
# #         visited.add(bare_name)
# #         node  = flat_tree.get(bare_name, {"children": {}})
# #         lines = global_lines_by_name.get(bare_name, 0)
# #         calls = []

# #         for child_raw in node.get("children", {}):
# #             child_bare = _NO_OF_LINES_RE.sub("", child_raw).strip()
# #             if child_bare in chunk_methods:
# #                 cf = _dfs(child_bare)
# #                 if cf:
# #                     calls.append(cf)
# #             else:
# #                 def _passthrough(n_bare, seen_local):
# #                     pnode = flat_tree.get(n_bare, {"children": {}})
# #                     for gcraw in pnode.get("children", {}):
# #                         gc_bare = _NO_OF_LINES_RE.sub("", gcraw).strip()
# #                         if gc_bare in chunk_methods and gc_bare not in seen_local:
# #                             gf = _dfs(gc_bare)
# #                             if gf:
# #                                 calls.append(gf)
# #                         elif gc_bare not in seen_local:
# #                             _passthrough(gc_bare, seen_local | {gc_bare})
# #                 _passthrough(child_bare, visited.copy())

# #         return {"method": bare_name, "lines": lines, "calls": calls}

# #     root_flow = _dfs(bare_trigger)

# #     if root_flow is not None:
# #         for m in chunk_methods:
# #             if m not in visited and m != bare_trigger:
# #                 visited.add(m)
# #                 root_flow["calls"].append(
# #                     {"method": m, "lines": global_lines_by_name.get(m, 0), "calls": []}
# #                 )

# #     return root_flow

# # def compact_call_flow(node: dict) -> str:
# #     def _render(n, is_root=False):
# #         name  = n["method"]
# #         calls = n.get("calls", [])
# #         if not calls:
# #             return name
# #         children_str = ", ".join(_render(c) for c in calls)
# #         return f"{name} -> {{{children_str}}}" if is_root else f"{name}: {{{children_str}}}"

# #     return _render(node, is_root=True)

# # # -----------------------------
# # # Execution order
# # # -----------------------------
# # def build_execution_order(chunks_df: pd.DataFrame) -> pd.DataFrame:
# #     if chunks_df is None or chunks_df.empty:
# #         return pd.DataFrame()

# #     cid_map = {str(r["chunk_id"]): r for _, r in chunks_df.iterrows()}
# #     entity_to_chunk = build_entity_to_chunk_id(chunks_df)

# #     adj     = defaultdict(list)
# #     radj    = defaultdict(list)
# #     all_cids = list(cid_map.keys())

# #     for _, r in chunks_df.iterrows():
# #         parent_cid = str(r["chunk_id"])
# #         refs = str(r.get("child_chunk_refs", "") or "")
# #         tokens = [x.strip() for x in refs.split(",") if x.strip() and x.strip() != "nan"]
# #         mapped_ids = []
# #         for token in tokens:
# #             mapped = entity_to_chunk.get(token)
# #             if mapped and mapped in cid_map and mapped != parent_cid:
# #                 mapped_ids.append(mapped)
# #         mapped_ids = list(dict.fromkeys(mapped_ids))
# #         for mcid in mapped_ids:
# #             adj[parent_cid].append(mcid)
# #             radj[mcid].append(parent_cid)

# #     dep_count = {cid: len(adj.get(cid, [])) for cid in all_cids}
# #     queue = deque(sorted(
# #         [c for c in all_cids if dep_count.get(c, 0) == 0],
# #         key=lambda c: cid_map[c].get("level", 99),
# #         reverse=True,
# #     ))
# #     order   = []
# #     visited = set()
# #     while queue:
# #         cid = queue.popleft()
# #         if cid in visited:
# #             continue
# #         visited.add(cid)
# #         order.append(cid)
# #         for parent in radj.get(cid, []):
# #             dep_count[parent] = dep_count.get(parent, 0) - 1
# #             if dep_count[parent] == 0:
# #                 queue.append(parent)

# #     for cid in all_cids:
# #         if cid not in visited:
# #             order.append(cid)

# #     rows = []
# #     for i, cid in enumerate(order, 1):
# #         r        = cid_map.get(cid, {})
# #         children = adj.get(cid, [])
# #         parents  = radj.get(cid, [])
# #         rows.append({
# #             "exec_order"        : i,
# #             "chunk_id"          : cid,
# #             "parent_entity"     : r.get("parent_entity", ""),
# #             "level"             : r.get("level", ""),
# #             "code_sum"          : r.get("code_sum", ""),
# #             "chunk_type"        : r.get("chunk_type", ""),
# #             "depends_on_chunks" : ", ".join(children) if children else "(none)",
# #             "needed_by_chunks"  : ", ".join(parents)  if parents  else "(root — spec independently)",
# #             "methods_in_chunk"  : r.get("methods_in_chunk", ""),
# #         })
# #     return pd.DataFrame(rows)

# # # -----------------------------
# # # Hierarchy JSON per chunk
# # # -----------------------------
# # def build_hierarchy_json(chunks_df: pd.DataFrame, tree: dict) -> pd.DataFrame:
# #     import json as _json

# #     if chunks_df is None or chunks_df.empty:
# #         return pd.DataFrame(columns=[
# #             "chunk_id", "parent_entity", "level", "chunk_type", "code_sum",
# #             "call_flow", "child_chunk_refs", "used_by_chunks",
# #             "spec_action", "hierarchy_json",
# #         ])

# #     flat_tree = _build_flat_tree(tree)
# #     _GROUP_RE = re.compile(r'^(?:G|GRP|GROUP)[-_]?\d+', re.I)

# #     cid_map = {}
# #     for _, r in chunks_df.iterrows():
# #         cid = str(r["chunk_id"]).strip()
# #         raw_methods  = parse_list(r.get("methods_in_chunk", ""))
# #         methods_list = [t for t in raw_methods if t and not _GROUP_RE.match(t)]
# #         groups_list  = [t for t in raw_methods if t and _GROUP_RE.match(t)]
# #         refs_raw     = str(r.get("child_chunk_refs", "") or "")
# #         child_refs_tokens = [
# #             x.strip() for x in refs_raw.split(",")
# #             if x.strip() and x.strip() not in ("nan", "(none)", "")
# #         ]
# #         cid_map[cid] = {
# #             "chunk_id"     : cid,
# #             "parent_entity": str(r.get("parent_entity", "")),
# #             "level"        : int(r.get("level", 1)),
# #             "chunk_type"   : str(r.get("chunk_type", "")),
# #             "code_sum"     : int(r.get("code_sum", 0) or 0),
# #             "methods"      : methods_list,
# #             "groups"       : list(dict.fromkeys(groups_list)),
# #             "child_refs_tokens": child_refs_tokens,
# #         }

# #     entity_to_chunk = build_entity_to_chunk_id(chunks_df)
# #     for cid, d in cid_map.items():
# #         tokens = d.get("child_refs_tokens", [])
# #         mapped_ids = []
# #         for t in tokens:
# #             mapped = entity_to_chunk.get(t)
# #             if mapped and mapped in cid_map and mapped != cid:
# #                 mapped_ids.append(mapped)
# #         d["child_refs"] = list(dict.fromkeys(mapped_ids))

# #     referenced_by = defaultdict(list)
# #     for cid, d in cid_map.items():
# #         for child_cid in d["child_refs"]:
# #             referenced_by[child_cid].append(cid)

# #     def _flow_for_chunk(cid):
# #         n = cid_map.get(cid, {})
# #         entity   = n.get("parent_entity", "")
# #         methods  = n.get("methods", [])
# #         flow_dict = build_call_flow(entity, set(methods), flat_tree)
# #         return compact_call_flow(flow_dict) if flow_dict else entity

# #     rows = []
# #     for cid, n in cid_map.items():
# #         entity   = n["parent_entity"]
# #         children = n.get("child_refs", [])
# #         parents  = referenced_by.get(cid, [])

# #         flow_str = _flow_for_chunk(cid)

# #         hierarchy = {
# #             "chunk_id"            : cid,
# #             "parent_entity"       : entity,
# #             "level"               : n["level"],
# #             "chunk_type"          : n["chunk_type"],
# #             "code_sum"            : n["code_sum"],
# #             "groups"              : n["groups"],
# #             "call_flow"           : flow_str,
# #             "child_chunk_refs"    : children,
# #             "used_by_chunks"      : parents,
# #             "spec_action"         : (
# #                 "GENERATE: build spec from raw YAML files only (leaf — no child specs)"
# #                 if not children else
# #                 f"CONSOLIDATE: inject {len(children)} child spec(s) + own files → produce merged spec"
# #             ),
# #             "call_flow_note": (
# #                 "call_flow format: 'root -> {caller: {callee1, callee2}, leaf}'. "
# #                 "Siblings are comma-separated in DFS execution order."
# #             ),
# #         }

# #         rows.append({
# #             "chunk_id"        : cid,
# #             "parent_entity"   : entity,
# #             "level"           : n["level"],
# #             "chunk_type"      : n["chunk_type"],
# #             "code_sum"        : n["code_sum"],
# #             "call_flow"       : flow_str,
# #             "child_chunk_refs": ", ".join(children) if children else "",
# #             "used_by_chunks"  : ", ".join(parents)  if parents  else "",
# #             "spec_action"     : hierarchy["spec_action"],
# #             "hierarchy_json"  : _json.dumps(hierarchy, indent=2),
# #         })

# #     result_df = pd.DataFrame(
# #         rows,
# #         columns=[
# #             "chunk_id", "parent_entity", "level", "chunk_type", "code_sum",
# #             "call_flow", "child_chunk_refs", "used_by_chunks",
# #             "spec_action", "hierarchy_json",
# #         ],
# #     )
# #     return result_df.sort_values(
# #         by=["level", "parent_entity", "chunk_id"],
# #         ascending=[False, True, True],
# #     ).reset_index(drop=True)

# # # -----------------------------
# # # Main pipeline
# # # -----------------------------
# # def chunks_formation(unique_group):
# #     input_path, sheet_name, CHUNK_LIMIT, out_xlsx = get_config(unique_group)

# #     # Load Branches (stepped preferred)
# #     df = load_branch_df_stepped(input_path, sheet_name)
# #     total_lines_ingested = len(df)
# #     print(f"[INFO] Paths constructed: {total_lines_ingested}")

# #     # Reset global state
# #     chunk_registry.clear()
# #     chunks_unique.clear()
# #     chunk_usages.clear()
# #     global_lines_by_name.clear()
# #     chunked_subtrees.clear()
# #     assigned_files_by_parent.clear()
# #     # Reset the chunk ID counter
# #     global chunk_id_counter
# #     chunk_id_counter = itertools.count(1)

# #     # Build tree
# #     tree = build_tree(df)

# #     # ================================================================
# #     # TWO-PASS CHUNKING (UNIQUE-aware)
# #     # ================================================================
# #     # PASS 1: compute all subtree totals without creating any chunks
# #     print("[INFO] Pass 1: Computing subtree totals...")
# #     for root_name, root_node in tree.items():
# #         compute_totals(root_name, root_node)

# #     # (Optional debug)
# #     # for root_name, root_node in tree.items():
# #     #     print(f"[DEBUG] {root_name}: total_lines={root_node['total_lines']} | unique_total_lines={root_node['unique_total_lines']}")

# #     # PASS 2: assign chunks top-down
# #     print("[INFO] Pass 2: Assigning chunks top-down...")
# #     for root_name, root_node in tree.items():
# #         assign_chunks_top_down(root_name, root_node, 1, CHUNK_LIMIT, ancestor_chunked=False)

# #     # Parent reference chunks
# #     for root_name, root_node in tree.items():
# #         create_parent_reference_chunk(root_name, root_node, 1)

# #     # Flatten hierarchy for diagnostics
# #     aggregated_rows = []
# #     def flatten(name, node, level):
# #         aggregated_rows.append({
# #             "Node": name,
# #             "Level": level,
# #             "Own_Lines": node["lines"],
# #             "Total_Lines": node.get("total_lines", node["lines"]),
# #             "Unique_Total_Lines": node.get("unique_total_lines", node.get("total_lines", node["lines"]))
# #         })
# #         for c_n, c_node in node["children"].items():
# #             flatten(c_n, c_node, level + 1)
# #     for root_name, root_node in tree.items():
# #         flatten(root_name, root_node, 1)
# #     aggregated_df = pd.DataFrame(aggregated_rows)

# #     # Finalize chunks lists
# #     final_unique, final_usages = _finalize_chunks(chunks_unique, chunk_usages)
# #     chunks_df = pd.DataFrame(final_unique)
# #     chunks_usage_df = pd.DataFrame(final_usages)

# #     # Ensure required columns exist for reusability pipeline
# #     if not chunks_df.empty:
# #         if "parent_node" not in chunks_df.columns:
# #             chunks_df["parent_node"] = "Chunk under " + chunks_df.get("trigger_node", "")
# #         chunks_df["parent_entity"] = chunks_df["parent_node"].str.replace("Chunk under ", "", regex=False)

# #     # Base mapping view (include groups)
# #     map_df = chunks_df[["chunk_id", "parent_entity", "level", "code_sum", "methods_in_chunk", "groups"]].copy()
# #     map_df = map_df.sort_values(by=["parent_entity", "level", "code_sum"], ascending=[True, True, False])

# #     # Summary per entity
# #     summary_df = (map_df.groupby(["parent_entity"], as_index=False)
# #                   .agg(chunks_count=("chunk_id", "count"),
# #                        total_code_sum=("code_sum", "sum")))

# #     # Canonical chunk mapping per entity
# #     entity_to_chunk = build_entity_to_chunk_id(chunks_df)

# #     # Replacements view
# #     rep_rows = []
# #     for _, r in chunks_df.iterrows():
# #         filenames_list = parse_list(r.get("methods_in_chunk"))
# #         mapped = [entity_to_chunk.get(fname, fname) for fname in filenames_list]
# #         rep_rows.append({
# #             "chunk_id": r["chunk_id"],
# #             "parent_entity": r["parent_entity"],
# #             "level": r["level"],
# #             "code_sum": r["code_sum"],
# #             "methods_in_chunk_normalized": ", ".join(filenames_list),
# #             "filenames_mapped_to_chunk_ids": ", ".join(mapped),
# #         })
# #     replacements_df = pd.DataFrame(rep_rows).sort_values(
# #         by=["parent_entity", "level", "code_sum"], ascending=[True, True, False]
# #     )

# #     # Graphs
# #     child_graph_raw_df     = build_child_graph_raw(chunks_df)
# #     child_graph_pruned_df  = prune_edges(child_graph_raw_df)
# #     child_graph_reduced_df = transitive_reduction_safe(child_graph_pruned_df)

# #     child_graph_pruned_df  = _append_isolated_nodes(child_graph_pruned_df, chunks_df)
# #     child_graph_reduced_df = _append_isolated_nodes(child_graph_reduced_df, chunks_df)

# #     reusability_edges_df   = compute_reusability_edges(child_graph_reduced_df)
# #     reusability_summary_df = compute_reusability_summary(child_graph_reduced_df)

# #     execution_order_df = build_execution_order(chunks_df)
# #     hierarchy_df       = build_hierarchy_json(chunks_df, tree)

# #     # Export
# #     with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
# #         # aggregated_df.to_excel(writer,     sheet_name="Aggregated_Hierarchy",  index=False)
# #         # chunks_df.to_excel(writer,         sheet_name="Chunks_Summary",        index=False)
# #         # chunks_usage_df.to_excel(writer,   sheet_name="Chunks_Usage",          index=False)
# #         map_df.to_excel(writer,            sheet_name="Parent_to_Chunks",      index=False)
# #         # summary_df.to_excel(writer,        sheet_name="Parent_Summary",        index=False)
# #         # replacements_df.to_excel(writer,   sheet_name="Replacements",          index=False)
# #         # child_graph_raw_df.to_excel(writer,     sheet_name="Child_Graph_Raw",     index=False)
# #         # child_graph_pruned_df.to_excel(writer,  sheet_name="Child_Graph_Pruned",  index=False)
# #         child_graph_reduced_df.to_excel(writer, sheet_name="Child_Graph_Reduced", index=False)
# #         reusability_edges_df.to_excel(writer,   sheet_name="Reusability_Edges",   index=False)
# #         reusability_summary_df.to_excel(writer, sheet_name="Reusability_Summary", index=False)
# #         execution_order_df.to_excel(writer, sheet_name="Execution_Order",  index=False)
# #         hierarchy_df.to_excel(writer,       sheet_name="Hierarchy_JSON",   index=False)

# #     print(f"[DONE] Exported all results to: {out_xlsx}")
# #     return out_xlsx

# #  ============================================================================================================

# # trigger node not included chunk limit exceeding (wrong code)

# import pandas as pd
# import html
# import itertools
# from pathlib import Path
# import re
# import os
# import unicodedata
# from collections import defaultdict, deque, OrderedDict
# import numpy as np
# SHEET_NAME  = 0

# # -----------------------------
# # Branch loaders
# # -----------------------------
# def load_branch_df_stepped(path: str, sheet_name=0) -> pd.DataFrame:
#     p = Path(path)
#     df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
#     level_cols = [c for c in df.columns if str(c).lower().startswith("level")]
#     if not level_cols:
#         level_cols = df.columns.tolist()

#     rows_as_branches = []
#     current_context = [None] * len(level_cols)

#     for _, row in df.iterrows():
#         path_segments = []
#         for i, col in enumerate(level_cols):
#             val = row[col]
#             if pd.notnull(val) and str(val).strip() != "":
#                 current_context[i] = str(val).strip()
#                 for j in range(i + 1, len(level_cols)):
#                     current_context[j] = None
#             if current_context[i]:
#                 path_segments.append(current_context[i])
#             else:
#                 break
#         if path_segments:
#             rows_as_branches.append(" -> ".join(path_segments))

#     return pd.DataFrame({"Branch": rows_as_branches})


# # -----------------------------
# # Name/lines extractors
# # -----------------------------
# NO_LINES_RE = re.compile(
#     r'^(?P<method>.*?)\s+no_of_lines\s*:\s*(?P<lines>\d+|Nil|None)\s*$',
#     re.IGNORECASE
# )
# METHOD_PATTERN = re.compile(r'^[^.]+\.[^.]+')


# def is_method(name: str) -> bool:
#     if not name:
#         return False
#     return bool(METHOD_PATTERN.match(str(name).strip()))


# def extract_name_lines(segment: str):
#     if segment is None or (isinstance(segment, float) and pd.isnull(segment)):
#         return None, 0, 0
#     s = str(segment).strip()
#     level = 0
#     m_level = re.search(r'\[\s*level\s*:\s*(\d+)\s*\]', s, flags=re.IGNORECASE)
#     if m_level:
#         level = int(m_level.group(1))
#         s = re.sub(r'\[\s*level\s*:\s*\d+\s*\]', '', s, flags=re.IGNORECASE).strip()
#     m_loc = re.search(r'^(.*?)\s*\[\s*LOC\s*:\s*(\d+)\s*\]$', s, flags=re.IGNORECASE)
#     if m_loc:
#         return m_loc.group(1).strip(), int(m_loc.group(2)), level
#     m_nol_br = re.search(r'^(.*?)\s*\[\s*no_of_lines\s*:\s*(\d+|None|Nil)\s*\]$', s, flags=re.IGNORECASE)
#     if m_nol_br:
#         token = m_nol_br.group(2).lower()
#         return m_nol_br.group(1).strip(), 0 if token in ('none', 'nil') else int(token), level
#     m_nol_inline = re.search(r'^(.*?)\s+no_of_lines\s*:\s*(\d+|None|Nil)\s*$', s, flags=re.IGNORECASE)
#     if m_nol_inline:
#         token = m_nol_inline.group(2).lower()
#         return m_nol_inline.group(1).strip(), 0 if token in ('none', 'nil') else int(token), level
#     m_simple = re.search(r'^(.*?)(?:\s+(\d+|None|Nil))$', s, flags=re.IGNORECASE)
#     if m_simple:
#         token = (m_simple.group(2) or '').lower()
#         return m_simple.group(1).strip(), 0 if token in ('none', 'nil') else (int(token) if token.isdigit() else 0), level
#     return s.strip(), 0, level


# def slugify_filename(name: str) -> str:
#     if not isinstance(name, str):
#         name = str(name)
#     name = unicodedata.normalize("NFKD", name)
#     name = name.replace(" ", "_")
#     return "".join(ch for ch in name if ch.isalnum() or ch in ["_", "+", "-"])


# GROUP_PATTERN = re.compile(r'^(?:G|GRP|GROUP)[-_]?\d+(?:\.\d+)?$', re.I)


# def is_group(name: str) -> bool:
#     if not name:
#         return False
#     return bool(GROUP_PATTERN.match(str(name).strip()))


# # -----------------------------
# # Global state
# # -----------------------------
# chunk_registry = {}
# chunk_id_counter = itertools.count(1)
# chunks_unique = []
# chunk_usages = []
# global_lines_by_name = {}
# chunked_subtrees = set()
# assigned_files_by_parent = {}

# # FIX 1 — cross-trigger deduplication
# # Maps root_file_name -> set of method names already assigned to any chunk
# # under that root. Prevents the same method appearing in two sibling chunks
# # when it is reachable via multiple call paths in the same file.
# global_assigned_methods: dict[str, set] = {}


# def format_method_with_lines(name: str) -> str:
#     if name is None:
#         return ""
#     s = str(name).strip()
#     if not s:
#         return ""
#     lines = global_lines_by_name.get(s, 0)
#     try:
#         lines_int = int(lines)
#         lines_txt = "Nil" if lines_int == 0 else str(lines_int)
#     except Exception:
#         lines_txt = "Nil"
#     return f"{s} no_of_lines : {lines_txt}"


# def format_refs_with_lines(refs_csv: str) -> str:
#     parts = [p.strip() for p in str(refs_csv).split(",") if p.strip()]
#     return ", ".join(format_method_with_lines(p) for p in parts)


# def register_or_get_chunk_id(filenames_ordered, code_sum, row_indices_ordered,
#                               trigger_node, level, trigger_node_name,
#                               child_chunk_refs="", chunk_type="leaf", groups_ordered=None):
#     if groups_ordered is None:
#         groups_ordered = []
#     key = (trigger_node, tuple(filenames_ordered))
#     if key in chunk_registry:
#         return chunk_registry[key]
#     num = next(chunk_id_counter)
#     base_id = f"C{num:03d}"
#     trigger_slug = slugify_filename(trigger_node) if trigger_node else "unknown_trigger"
#     chunk_id = f"{base_id}_{trigger_slug}"
#     chunk_registry[key] = chunk_id

#     if chunk_type == "leaf":
#         display_filenames = ", ".join(format_method_with_lines(fn) for fn in filenames_ordered)
#     else:
#         display_filenames = format_refs_with_lines(child_chunk_refs) if child_chunk_refs \
#             else ", ".join(format_method_with_lines(fn) for fn in filenames_ordered)

#     groups_csv = ", ".join(f"{g} no_of_lines : Nil" for g in groups_ordered) if groups_ordered else ""
#     display = display_filenames
#     if groups_csv:
#         display = ", ".join([s for s in [display_filenames, groups_csv] if s])

#     record_common = {
#         "chunk_id": chunk_id,
#         "trigger_node": trigger_node,
#         "level": level,
#         "trigger_node_name": trigger_node_name,
#         "methods_in_chunk": display,
#         "groups": groups_csv,
#         "row_indices": ", ".join(map(str, row_indices_ordered)),
#         "code_sum": code_sum,
#         "child_chunk_refs": child_chunk_refs,
#         "chunk_type": chunk_type,
#         "triggered_by": f"{chunk_type.capitalize()} chunk for '{trigger_node}'"
#     }
#     chunks_unique.append({**record_common, "parent_node": f"Chunk under {trigger_node}"})
#     chunk_usages.append({**record_common, "used_under_parent_node": f"Chunk under {trigger_node}"})
#     return chunk_id


# def iter_entities_dfs(node):
#     for nm in node.get("filenames", []):
#         yield nm
#     for _, child in node.get("children", {}).items():
#         yield from iter_entities_dfs(child)


# def create_chunks_for_children(children, level, trigger_node, CHUNK_LIMIT,
#                                root_file: str = ""):
#     # FIX 1: root_file identifies which file-level dedup set to use.
#     # Every method assigned anywhere under the same root file is recorded in
#     # global_assigned_methods[root_file], so a method that appears under two
#     # different call paths (two different trigger_nodes) is only chunked once.
#     global chunked_subtrees, assigned_files_by_parent, global_assigned_methods

#     ordered_children = list(children.items())
#     parent_seen      = assigned_files_by_parent.setdefault(trigger_node, set())
#     # Global set for this root file — shared across all trigger nodes
#     file_seen        = global_assigned_methods.setdefault(root_file or trigger_node, set())

#     current_methods_list = []
#     current_methods_set  = set()
#     current_groups_list  = []
#     current_groups_set   = set()
#     current_unique_sum   = 0
#     have_items           = False
#     child_chunks_in_current = []

#     def _flush_chunk():
#         nonlocal current_methods_list, current_methods_set, current_groups_list, current_groups_set
#         nonlocal current_unique_sum, have_items, child_chunks_in_current, parent_seen
#         if not have_items:
#             return
#         methods_out = list(current_methods_list)
#         groups_out  = list(current_groups_list)
#         unique_sum = sum(global_lines_by_name.get(fn, 0) for fn in methods_out if is_method(fn))
#         register_or_get_chunk_id(
#             filenames_ordered=methods_out, code_sum=unique_sum,
#             row_indices_ordered=methods_out, trigger_node=trigger_node,
#             level=level, trigger_node_name=trigger_node,
#             child_chunk_refs=", ".join(child_chunks_in_current),
#             chunk_type="leaf", groups_ordered=groups_out,
#         )
#         for child_name in child_chunks_in_current:
#             chunked_subtrees.add((trigger_node, child_name))
#         parent_seen.update(x for x in methods_out if is_method(x))
#         # FIX 1: record globally so sibling triggers don't re-chunk these
#         file_seen.update(x for x in methods_out if is_method(x))
#         current_methods_list[:]    = []
#         current_methods_set.clear()
#         current_groups_list[:]     = []
#         current_groups_set.clear()
#         child_chunks_in_current[:] = []
#         current_unique_sum = 0
#         have_items         = False

#     for child_name, child_node in ordered_children:
#         if (trigger_node, child_name) in chunked_subtrees:
#             continue
#         # Classify LARGE/small using the child's OWN LOC only, not its
#         # subtree total. The subtree is split recursively by
#         # assign_chunks_top_down regardless, so using subtree total here
#         # only isolates methods whose own body is small into unnecessary
#         # singleton pointer chunks.
#         # e.g. ps_pr_val_cam_form_type_docs (own=328) had subtree=2839
#         # and was wrongly classified LARGE, preventing bin-packing with siblings.
#         child_own_loc = child_node.get("lines", 0)

#         if child_own_loc >= CHUNK_LIMIT:
#             _flush_chunk()
#             ptr_methods = []
#             ptr_groups  = []
#             # FIX 1: also exclude from pointer chunks if already file-globally assigned
#             if is_method(child_name) and child_name not in parent_seen \
#                     and child_name not in file_seen:
#                 ptr_methods = [child_name]
#             elif is_group(child_name) and child_name not in parent_seen:
#                 ptr_groups = [child_name]
#             methods_out = list(ptr_methods)
#             groups_out  = list(ptr_groups)
#             if methods_out or groups_out:
#                 unique_sum = sum(global_lines_by_name.get(fn, 0) for fn in methods_out if is_method(fn))
#                 register_or_get_chunk_id(
#                     filenames_ordered=methods_out, code_sum=unique_sum,
#                     row_indices_ordered=methods_out, trigger_node=trigger_node,
#                     level=level, trigger_node_name=trigger_node,
#                     child_chunk_refs=child_name, chunk_type="leaf", groups_ordered=groups_out,
#                 )
#                 parent_seen.update(m for m in methods_out if is_method(m))
#                 file_seen.update(m for m in methods_out if is_method(m))
#             chunked_subtrees.add((trigger_node, child_name))
#             continue

#         child_entities        = list(iter_entities_dfs(child_node))
#         child_methods_ordered = [e for e in child_entities if is_method(e)]
#         child_groups_ordered  = [e for e in child_entities if is_group(e)]
#         # FIX 1: exclude methods already assigned anywhere in this root file
#         child_methods_new = [m for m in child_methods_ordered
#                              if m not in parent_seen
#                              and m not in current_methods_set
#                              and m not in file_seen]
#         incremental_unique = sum(global_lines_by_name.get(m, 0) for m in child_methods_new)

#         if have_items and child_methods_new and (current_unique_sum + incremental_unique) > CHUNK_LIMIT:
#             _flush_chunk()
#             child_methods_new = [m for m in child_methods_ordered
#                                  if m not in parent_seen
#                                  and m not in current_methods_set
#                                  and m not in file_seen]
#             incremental_unique = sum(global_lines_by_name.get(m, 0) for m in child_methods_new)

#         if child_methods_new or child_groups_ordered:
#             for m in child_methods_ordered:
#                 if m not in parent_seen and m not in current_methods_set and m not in file_seen:
#                     current_methods_list.append(m)
#                     current_methods_set.add(m)
#             for g in child_groups_ordered:
#                 if g not in current_groups_set:
#                     current_groups_list.append(g)
#                     current_groups_set.add(g)
#             current_unique_sum += incremental_unique
#             have_items = True
#             child_chunks_in_current.append(child_name)
#         else:
#             chunked_subtrees.add((trigger_node, child_name))

#     _flush_chunk()


# def create_parent_reference_chunk(node_name, node, level):
#     global chunked_subtrees
#     parent_methods_list = []
#     parent_groups_list  = []
#     seen_m = set()
#     seen_g = set()
#     child_refs = []

#     for child_name, child_node in node["children"].items():
#         if (node_name, child_name) in chunked_subtrees:
#             child_refs.append(child_name)
#         else:
#             for ent in iter_entities_dfs(child_node):
#                 if is_method(ent) and ent not in seen_m:
#                     parent_methods_list.append(ent)
#                     seen_m.add(ent)
#                 elif is_group(ent) and ent not in seen_g:
#                     parent_groups_list.append(ent)
#                     seen_g.add(ent)

#     if parent_methods_list or parent_groups_list:
#         unique_sum = sum(global_lines_by_name.get(fn, 0) for fn in parent_methods_list if is_method(fn))
#         register_or_get_chunk_id(
#             filenames_ordered=parent_methods_list, code_sum=unique_sum,
#             row_indices_ordered=parent_methods_list, trigger_node=node_name,
#             level=level, trigger_node_name=f"{node_name}_PARENT_ONLY",
#             child_chunk_refs=", ".join(child_refs),
#             chunk_type="parent_ref", groups_ordered=parent_groups_list,
#         )


# def compute_totals(node_name, node):
#     total_structural = node["lines"]
#     unique_methods = set()
#     if is_method(node_name):
#         unique_methods.add(node_name)
#     for child_name, child_node in node["children"].items():
#         child_structural = compute_totals(child_name, child_node)
#         total_structural += child_structural
#         unique_methods |= child_node.get("unique_methods", set())
#     node["total_lines"]        = total_structural
#     node["unique_methods"]     = unique_methods
#     node["unique_total_lines"] = sum(
#         global_lines_by_name.get(m, 0) for m in unique_methods if is_method(m))
#     return total_structural


# def assign_chunks_top_down(node_name, node, level, CHUNK_LIMIT,
#                            ancestor_chunked=False, root_file: str = ""):
#     # root_file is set once at the top-level call and passed unchanged all the
#     # way down so create_chunks_for_children can look up the shared file_seen set.
#     if not root_file:
#         root_file = node_name   # first call: this node IS the root file
#         # Reset this root file's dedup set so it never inherits methods
#         # from a previous root file processed in the same run.
#         global_assigned_methods[root_file] = set()
#     total_unique = node.get("unique_total_lines", node.get("total_lines", node["lines"]))
#     if total_unique >= CHUNK_LIMIT and node["children"]:
#         create_chunks_for_children(node["children"], level + 1, node_name,
#                                    CHUNK_LIMIT, root_file=root_file)
#     for child_name, child_node in node["children"].items():
#         assign_chunks_top_down(child_name, child_node, level + 1, CHUNK_LIMIT,
#                                ancestor_chunked=False, root_file=root_file)


# def _finalize_chunks(chunks_unique_list, chunk_usages_list):
#     nodes_with_parent_ref = {
#         c.get("trigger_node") for c in chunks_unique_list if c.get("chunk_type") == "parent_ref"
#     }
#     def keep_record(rec):
#         return not (rec.get("chunk_type") == "leaf" and
#                     rec.get("trigger_node") in nodes_with_parent_ref)
#     return [c for c in chunks_unique_list if keep_record(c)], \
#            [u for u in chunk_usages_list if keep_record(u)]


# def build_tree(df: pd.DataFrame):
#     tree = OrderedDict()
#     for idx, row in df.iterrows():
#         current = tree
#         s = html.unescape(str(row["Branch"]))
#         for arrow in ["->", "=> ", "→", " - > "]:
#             s = s.replace(arrow, " -> ")
#         segments = [seg.strip() for seg in re.split(r'\s*->\s*', s) if seg.strip()]
#         for seg in segments:
#             name, lines, _ = extract_name_lines(seg)
#             if name:
#                 if name not in current:
#                     current[name] = {"lines": lines, "children": OrderedDict(),
#                                      "rows": set(), "filenames": [name]}
#                 current[name]["lines"] = max(current[name]["lines"], lines)
#                 current[name]["rows"].add(idx)
#                 if name not in current[name]["filenames"]:
#                     current[name]["filenames"].append(name)
#                 global_lines_by_name[name] = max(
#                     global_lines_by_name.get(name, 0),
#                     lines if not is_group(name) else 0)
#                 current = current[name]["children"]
#     return tree


# # ── Reusability helpers ───────────────────────────────────────────────────────
# _NO_OF_LINES_RE = re.compile(r"\s*no_of_lines\s*:\s*(?:\d+|nil|none)\s*$", re.IGNORECASE)


# def normalize_entity_token(token: str) -> str:
#     if token is None:
#         return ""
#     s = str(token).strip()
#     return _NO_OF_LINES_RE.sub("", s).strip()


# def parse_list(cell):
#     if pd.isnull(cell):
#         return []
#     parts = [x.strip() for x in str(cell).split(",") if x.strip()]
#     norm = [normalize_entity_token(x) for x in parts]
#     return [x for x in norm if x]


# def build_entity_to_chunk_id(df):
#     canonical_map = {}
#     if df is None or df.empty:
#         return canonical_map
#     for entity, group in df.groupby("parent_entity"):
#         sorted_group = group.sort_values(by=["code_sum", "level"], ascending=[False, True])
#         canonical_map[str(entity)] = str(sorted_group.iloc[0]["chunk_id"])
#     return canonical_map


# def build_child_graph_raw(df):
#     rows = df.to_dict("records")
#     entity_to_rows  = defaultdict(list)
#     trigger_to_rows = defaultdict(list)
#     for r in rows:
#         entity_to_rows[r["parent_entity"]].append(r)
#         trig = str(r.get("trigger_node", r.get("parent_entity", "")))
#         trigger_to_rows[trig].append(r)

#     edges = []
#     seen_pairs = set()

#     def _add_edge(pc, pe, pl, ps, cc, ce, cl, cs):
#         pair = (str(pc), str(cc))
#         if pair in seen_pairs or str(pc) == str(cc):
#             return
#         seen_pairs.add(pair)
#         edges.append({"parent_chunk_id": pc, "parent_entity": pe, "parent_level": pl,
#                       "parent_code_sum": ps, "child_chunk_id": cc, "child_entity": ce,
#                       "child_level": cl, "child_code_sum": cs})

#     for r in rows:
#         pc  = r["chunk_id"]
#         pe  = r["parent_entity"]
#         pl  = r.get("level")
#         ps  = r.get("code_sum")
#         for fname in parse_list(r.get("methods_in_chunk")):
#             for child in entity_to_rows.get(fname, []):
#                 if child["chunk_id"] != pc:
#                     _add_edge(pc, pe, pl, ps, child["chunk_id"], child["parent_entity"],
#                               child.get("level"), child.get("code_sum"))
#         refs_raw = str(r.get("child_chunk_refs", "") or "")
#         for token in [x.strip() for x in refs_raw.split(",")
#                       if x.strip() and x.strip() not in ("nan", "(none)", "")]:
#             norm_token = _NO_OF_LINES_RE.sub("", token).strip()
#             for child in trigger_to_rows.get(norm_token, []):
#                 if child["chunk_id"] != pc:
#                     _add_edge(pc, pe, pl, ps, child["chunk_id"], child["parent_entity"],
#                               child.get("level"), child.get("code_sum"))

#     cols = ["parent_chunk_id","parent_entity","parent_level","parent_code_sum",
#             "child_chunk_id","child_entity","child_level","child_code_sum"]
#     edges_df = pd.DataFrame(edges) if edges else pd.DataFrame(columns=cols)
#     if not edges_df.empty:
#         edges_df = edges_df.drop_duplicates().sort_values(
#             by=["parent_entity","parent_level","parent_chunk_id",
#                 "child_entity","child_level","child_chunk_id"])
#     return edges_df


# def prune_edges(edges_df):
#     if edges_df.empty:
#         return edges_df.copy()
#     df = edges_df[edges_df["parent_chunk_id"] != edges_df["child_chunk_id"]].copy()
#     if df.empty:
#         return df
#     pairs = set(zip(df["parent_chunk_id"], df["child_chunk_id"]))
#     mutual_pairs = {(u, v) for (u, v) in pairs if (v, u) in pairs}
#     if not mutual_pairs:
#         return df
#     mask = ~df.apply(lambda r: (r["parent_chunk_id"], r["child_chunk_id"]) in mutual_pairs, axis=1)
#     return df[mask].drop_duplicates().sort_values(
#         by=["parent_entity","parent_level","parent_chunk_id",
#             "child_entity","child_level","child_chunk_id"])


# def _build_adj(edges_df):
#     adj = defaultdict(set)
#     for _, r in edges_df.iterrows():
#         adj[r["parent_chunk_id"]].add(r["child_chunk_id"])
#         _ = adj[r["child_chunk_id"]]
#     return adj


# def _tarjan_scc(adj):
#     index = 0; stack = []; onstack = set(); indices = {}; lowlink = {}; sccs = []
#     def strongconnect(v):
#         nonlocal index
#         indices[v] = lowlink[v] = index; index += 1
#         stack.append(v); onstack.add(v)
#         for w in adj.get(v, set()):
#             if w not in indices:
#                 strongconnect(w); lowlink[v] = min(lowlink[v], lowlink[w])
#             elif w in onstack:
#                 lowlink[v] = min(lowlink[v], indices[w])
#         if lowlink[v] == indices[v]:
#             comp = set()
#             while True:
#                 w = stack.pop(); onstack.discard(w); comp.add(w)
#                 if w == v: break
#             sccs.append(comp)
#     for v in list(adj.keys()):
#         if v not in indices:
#             strongconnect(v)
#     return sccs


# def _dfs_reach(adj, start, cache):
#     if start in cache:
#         return cache[start]
#     visited = set(); stack = [start]
#     while stack:
#         u = stack.pop()
#         for v in adj.get(u, set()):
#             if v not in visited:
#                 visited.add(v); stack.append(v)
#     cache[start] = visited
#     return visited


# def _transitive_reduction_dag(adj):
#     reach_cache = {}; to_remove = set()
#     for u, children in adj.items():
#         cl = list(children)
#         reach_by_v = {v: (_dfs_reach(adj, v, reach_cache) | {v}) for v in cl}
#         for w in cl:
#             for v in cl:
#                 if v != w and w in reach_by_v[v]:
#                     to_remove.add((u, w)); break
#     return {(u, v) for u, children in adj.items() for v in children if (u, v) not in to_remove}


# def transitive_reduction_safe(edges_df):
#     if edges_df.empty:
#         return edges_df.copy()
#     adj = _build_adj(edges_df)
#     sccs = _tarjan_scc(adj)
#     comp_id = {node: i for i, comp in enumerate(sccs) for node in comp}
#     comp_adj = defaultdict(set)
#     for u, children in adj.items():
#         cu = comp_id[u]
#         for v in children:
#             cv = comp_id[v]
#             if cu != cv:
#                 comp_adj[cu].add(cv)
#     reduced_comp_edges = _transitive_reduction_dag(comp_adj)
#     keep_edges = set()
#     for u, children in adj.items():
#         for v in children:
#             if comp_id[u] == comp_id[v]:
#                 keep_edges.add((u, v))
#     needed_comp_pairs = set(reduced_comp_edges)
#     for u, children in adj.items():
#         cu = comp_id[u]
#         for v in children:
#             cv = comp_id[v]
#             if cu != cv and (cu, cv) in needed_comp_pairs:
#                 keep_edges.add((u, v))
#     mask = edges_df.apply(lambda r: (r["parent_chunk_id"], r["child_chunk_id"]) in keep_edges, axis=1)
#     return edges_df[mask].drop_duplicates(subset=["parent_chunk_id","child_chunk_id"]).sort_values(
#         by=["parent_entity","parent_level","parent_chunk_id",
#             "child_entity","child_level","child_chunk_id"])


# def _anchor_orphans_to_root(edges_df, chunks_df, tree):
#     cols = ["parent_chunk_id","parent_entity","parent_level","parent_code_sum",
#             "child_chunk_id","child_entity","child_level","child_code_sum"]
#     if edges_df.empty:
#         edges_df = pd.DataFrame(columns=cols)

#     trigger_to_chunks = defaultdict(list)
#     cid_to_row = {}
#     for _, r in chunks_df.iterrows():
#         cid  = str(r["chunk_id"])
#         trig = str(r.get("trigger_node", r.get("parent_entity", "")))
#         trigger_to_chunks[trig].append(r)
#         cid_to_row[cid] = r

#     node_parent = {}
#     def _walk(node_dict, parent_name):
#         for name, node in node_dict.items():
#             node_parent[name] = parent_name
#             _walk(node["children"], name)
#     for root_name, root_node in tree.items():
#         node_parent[root_name] = None
#         _walk(root_node["children"], root_name)

#     existing_children = set(edges_df["child_chunk_id"].dropna().astype(str))
#     all_cids    = set(cid_to_row.keys())
#     orphan_cids = all_cids - existing_children

#     new_edges = []
#     virtual_root_chunks = {}

#     def _get_or_create_virtual_root(root_name):
#         if root_name not in virtual_root_chunks:
#             virtual_root_chunks[root_name] = f"VROOT_{slugify_filename(root_name)}"
#         return virtual_root_chunks[root_name]

#     # FIX 2: collect ALL orphans per level-1 root first, then find/create ONE
#     # shared parent for the whole sibling group.  This prevents the first chunk
#     # processed from being promoted to parent of the remaining siblings.
#     # Group orphans: root_name -> list of (cid, row)
#     orphan_groups: dict[str, list] = defaultdict(list)
#     for cid in orphan_cids:
#         r    = cid_to_row[cid]
#         trig = str(r.get("trigger_node", r.get("parent_entity", "")))
#         root = trig
#         while node_parent.get(root) is not None:
#             root = node_parent[root]
#         orphan_groups[root].append((cid, r))

#     for root_name, orphan_list in orphan_groups.items():
#         for cid, r in orphan_list:
#             trig = str(r.get("trigger_node", r.get("parent_entity", "")))

#             # Step 1: nearest real ancestor chunk (walk up from trigger's parent)
#             ancestor = node_parent.get(trig)
#             ancestor_chunk = None
#             while ancestor is not None:
#                 candidates = trigger_to_chunks.get(ancestor, [])
#                 if candidates:
#                     ancestor_chunk = min(candidates, key=lambda x: int(x.get("level", 99)))
#                     break
#                 ancestor = node_parent.get(ancestor)

#             # Step 2: no ancestor found — check the level-1 root itself for a real chunk
#             if ancestor_chunk is None:
#                 root_candidates = trigger_to_chunks.get(root_name, [])
#                 if root_candidates:
#                     ancestor_chunk = min(root_candidates,
#                                         key=lambda x: int(x.get("level", 99)))

#             # Step 3: still nothing — use/create a virtual root shared by all
#             # siblings in this group (NOT the first sibling found)
#             if ancestor_chunk is None:
#                 virt_id = _get_or_create_virtual_root(root_name)
#                 new_edges.append({
#                     "parent_chunk_id": virt_id, "parent_entity": root_name,
#                     "parent_level": 1,           "parent_code_sum": None,
#                     "child_chunk_id":  cid,
#                     "child_entity":    str(r.get("parent_entity", "")),
#                     "child_level":     r.get("level"),
#                     "child_code_sum":  r.get("code_sum"),
#                 })
#                 continue

#             a_cid = str(ancestor_chunk["chunk_id"])
#             if a_cid == cid:
#                 continue
#             new_edges.append({
#                 "parent_chunk_id": a_cid,
#                 "parent_entity":   str(ancestor_chunk.get("parent_entity", "")),
#                 "parent_level":    ancestor_chunk.get("level"),
#                 "parent_code_sum": ancestor_chunk.get("code_sum"),
#                 "child_chunk_id":  cid,
#                 "child_entity":    str(r.get("parent_entity", "")),
#                 "child_level":     r.get("level"),
#                 "child_code_sum":  r.get("code_sum"),
#             })

#     if new_edges:
#         edges_df = pd.concat([edges_df, pd.DataFrame(new_edges)], ignore_index=True)
#         edges_df = edges_df.drop_duplicates(subset=["parent_chunk_id","child_chunk_id"])

#     # Truly isolated — self-row so they appear in the sheet
#     existing_children2 = set(edges_df["child_chunk_id"].dropna().astype(str))
#     existing_parents2  = set(edges_df["parent_chunk_id"].dropna().astype(str))
#     truly_isolated = all_cids - (existing_children2 | existing_parents2)
#     iso_rows = []
#     for cid in truly_isolated:
#         r = cid_to_row[cid]
#         iso_rows.append({"parent_chunk_id": cid, "parent_entity": str(r.get("parent_entity","")),
#                          "parent_level": r.get("level"), "parent_code_sum": r.get("code_sum"),
#                          "child_chunk_id": "", "child_entity": "",
#                          "child_level": None, "child_code_sum": None})
#     if iso_rows:
#         edges_df = pd.concat([edges_df, pd.DataFrame(iso_rows)], ignore_index=True)

#     return edges_df.drop_duplicates().sort_values(
#         by=["parent_entity","parent_level","parent_chunk_id",
#             "child_entity","child_level","child_chunk_id"])


# def compute_reusability_edges(reduced_edges_df):
#     if reduced_edges_df.empty:
#         return reduced_edges_df.copy()
#     return reduced_edges_df.drop_duplicates(subset=["parent_chunk_id","child_chunk_id"]).sort_values(
#         by=["parent_entity","parent_level","parent_chunk_id",
#             "child_entity","child_level","child_chunk_id"])


# def compute_reusability_summary(reduced_edges_df: pd.DataFrame) -> pd.DataFrame:
#     expected = ["parent_chunk_id","parent_entity","parent_level","parent_code_sum",
#                 "child_chunk_id","child_entity","child_level","child_code_sum"]
#     for col in expected:
#         if col not in reduced_edges_df.columns:
#             reduced_edges_df[col] = pd.Series(dtype=object)
#     if reduced_edges_df.empty:
#         return pd.DataFrame(columns=["child_chunk_id","child_entity","child_level",
#                                      "child_code_sum","used_in_parents_count","parents_list"])
#     agg = (reduced_edges_df
#            .groupby(["child_chunk_id","child_entity","child_level","child_code_sum"],
#                     dropna=False, as_index=False)
#            .agg(parents=("parent_chunk_id",
#                          lambda xs: sorted({str(x) for x in xs if pd.notnull(x) and str(x)}))))
#     agg["used_in_parents_count"] = agg["parents"].apply(len)
#     agg["parents_list"]          = agg["parents"].apply(lambda xs: ", ".join(xs))
#     return agg.drop(columns=["parents"]).sort_values(
#         by=["used_in_parents_count","child_entity"], ascending=[False, True])


# def _build_flat_tree(tree: dict) -> dict:
#     flat  = {}
#     stack = list(tree.items())
#     while stack:
#         name, node = stack.pop()
#         bare = _NO_OF_LINES_RE.sub("", name).strip()
#         flat[bare] = node
#         for cname, cnode in node["children"].items():
#             stack.append((cname, cnode))
#     return flat


# def build_call_flow(trigger_name: str, chunk_methods: set, flat_tree: dict) -> dict:
#     bare_trigger = _NO_OF_LINES_RE.sub("", str(trigger_name or "")).strip()
#     if bare_trigger not in flat_tree:
#         return {"method": bare_trigger, "lines": global_lines_by_name.get(bare_trigger, 0),
#                 "calls": [{"method": m, "lines": global_lines_by_name.get(m, 0), "calls": []}
#                            for m in sorted(chunk_methods - {bare_trigger})]}
#     visited = set()
#     def _dfs(bare_name):
#         if bare_name in visited:
#             return None
#         visited.add(bare_name)
#         node  = flat_tree.get(bare_name, {"children": {}})
#         calls = []
#         for child_raw in node.get("children", {}):
#             child_bare = _NO_OF_LINES_RE.sub("", child_raw).strip()
#             if child_bare in chunk_methods:
#                 cf = _dfs(child_bare)
#                 if cf:
#                     calls.append(cf)
#             else:
#                 def _passthrough(n_bare, seen_local):
#                     pnode = flat_tree.get(n_bare, {"children": {}})
#                     for gcraw in pnode.get("children", {}):
#                         gc_bare = _NO_OF_LINES_RE.sub("", gcraw).strip()
#                         if gc_bare in chunk_methods and gc_bare not in seen_local:
#                             gf = _dfs(gc_bare)
#                             if gf:
#                                 calls.append(gf)
#                         elif gc_bare not in seen_local:
#                             _passthrough(gc_bare, seen_local | {gc_bare})
#                 _passthrough(child_bare, visited.copy())
#         return {"method": bare_name, "lines": global_lines_by_name.get(bare_name, 0), "calls": calls}
#     root_flow = _dfs(bare_trigger)
#     if root_flow is not None:
#         for m in chunk_methods:
#             if m not in visited and m != bare_trigger:
#                 visited.add(m)
#                 root_flow["calls"].append({"method": m, "lines": global_lines_by_name.get(m, 0), "calls": []})
#     return root_flow


# def compact_call_flow(node: dict) -> str:
#     def _render(n, is_root=False):
#         name  = n["method"]
#         calls = n.get("calls", [])
#         if not calls:
#             return name
#         children_str = ", ".join(_render(c) for c in calls)
#         return f"{name} -> {{{children_str}}}" if is_root else f"{name}: {{{children_str}}}"
#     return _render(node, is_root=True)


# def build_execution_order(chunks_df: pd.DataFrame,
#                           child_graph_df: pd.DataFrame = None) -> pd.DataFrame:
#     # FIX 3: accept the already-correct Child_Graph_Reduced edges so we don't
#     # have to re-derive parent→child relationships from child_chunk_refs strings.
#     # The old approach mapped child_chunk_refs tokens through entity_to_chunk,
#     # which resolved a trigger name back to its own chunk (self-reference) and
#     # therefore missed the real child chunks, leaving them as disconnected roots.
#     if chunks_df is None or chunks_df.empty:
#         return pd.DataFrame()

#     cid_map  = {str(r["chunk_id"]): r for _, r in chunks_df.iterrows()}
#     all_cids = list(cid_map.keys())

#     adj  = defaultdict(list)   # parent -> [children]
#     radj = defaultdict(list)   # child  -> [parents]

#     if child_graph_df is not None and not child_graph_df.empty:
#         # Use the pre-built graph — ground truth for parent/child relationships
#         for _, r in child_graph_df.iterrows():
#             pc = str(r["parent_chunk_id"])
#             cc = str(r.get("child_chunk_id", "") or "")
#             if pc and cc and pc in cid_map and cc in cid_map and pc != cc:
#                 if cc not in adj[pc]:
#                     adj[pc].append(cc)
#                 if pc not in radj[cc]:
#                     radj[cc].append(pc)
#     else:
#         # Fallback: old entity_to_chunk resolution (kept for compatibility)
#         entity_to_chunk = build_entity_to_chunk_id(chunks_df)
#         for _, r in chunks_df.iterrows():
#             parent_cid = str(r["chunk_id"])
#             refs = str(r.get("child_chunk_refs", "") or "")
#             tokens = [x.strip() for x in refs.split(",") if x.strip() and x.strip() != "nan"]
#             mapped_ids = list(dict.fromkeys(
#                 [entity_to_chunk[t] for t in tokens
#                  if entity_to_chunk.get(t) and entity_to_chunk[t] in cid_map
#                  and entity_to_chunk[t] != parent_cid]))
#             for mcid in mapped_ids:
#                 adj[parent_cid].append(mcid)
#                 radj[mcid].append(parent_cid)

#     # Kahn's topological sort (leaves first → roots last = correct spec order)
#     dep_count = {cid: len(adj.get(cid, [])) for cid in all_cids}
#     queue = deque(sorted([c for c in all_cids if dep_count.get(c, 0) == 0],
#                          key=lambda c: cid_map[c].get("level", 99), reverse=True))
#     order = []; visited = set()
#     while queue:
#         cid = queue.popleft()
#         if cid in visited: continue
#         visited.add(cid); order.append(cid)
#         for parent in radj.get(cid, []):
#             dep_count[parent] = dep_count.get(parent, 0) - 1
#             if dep_count[parent] == 0:
#                 queue.append(parent)
#     for cid in all_cids:
#         if cid not in visited:
#             order.append(cid)

#     rows = []
#     for i, cid in enumerate(order, 1):
#         r = cid_map.get(cid, {})
#         rows.append({
#             "exec_order":        i,
#             "chunk_id":          cid,
#             "parent_entity":     r.get("parent_entity", ""),
#             "level":             r.get("level", ""),
#             "code_sum":          r.get("code_sum", ""),
#             "chunk_type":        r.get("chunk_type", ""),
#             "depends_on_chunks": ", ".join(adj.get(cid, [])) or "(none)",
#             "needed_by_chunks":  ", ".join(radj.get(cid, [])) or "(root — spec independently)",
#             "methods_in_chunk":  r.get("methods_in_chunk", ""),
#         })
#     return pd.DataFrame(rows)


# def build_hierarchy_json(chunks_df: pd.DataFrame, tree: dict) -> pd.DataFrame:
#     import json as _json
#     if chunks_df is None or chunks_df.empty:
#         return pd.DataFrame(columns=["chunk_id","parent_entity","level","chunk_type","code_sum",
#                                      "call_flow","child_chunk_refs","used_by_chunks","spec_action","hierarchy_json"])
#     flat_tree = _build_flat_tree(tree)
#     _GROUP_RE = re.compile(r'^(?:G|GRP|GROUP)[-_]?\d+', re.I)
#     cid_map = {}
#     for _, r in chunks_df.iterrows():
#         cid = str(r["chunk_id"]).strip()
#         raw_methods  = parse_list(r.get("methods_in_chunk", ""))
#         methods_list = [t for t in raw_methods if t and not _GROUP_RE.match(t)]
#         groups_list  = [t for t in raw_methods if t and _GROUP_RE.match(t)]
#         refs_raw     = str(r.get("child_chunk_refs", "") or "")
#         child_refs_tokens = [x.strip() for x in refs_raw.split(",")
#                              if x.strip() and x.strip() not in ("nan","(none)","")]
#         cid_map[cid] = {
#             "chunk_id": cid, "parent_entity": str(r.get("parent_entity","")),
#             "level": int(r.get("level", 1)), "chunk_type": str(r.get("chunk_type","")),
#             "code_sum": int(r.get("code_sum", 0) or 0),
#             "methods": methods_list, "groups": list(dict.fromkeys(groups_list)),
#             "child_refs_tokens": child_refs_tokens,
#         }
#     entity_to_chunk = build_entity_to_chunk_id(chunks_df)
#     for cid, d in cid_map.items():
#         d["child_refs"] = list(dict.fromkeys(
#             [entity_to_chunk[t] for t in d["child_refs_tokens"]
#              if entity_to_chunk.get(t) and entity_to_chunk[t] in cid_map
#              and entity_to_chunk[t] != cid]))
#     referenced_by = defaultdict(list)
#     for cid, d in cid_map.items():
#         for child_cid in d["child_refs"]:
#             referenced_by[child_cid].append(cid)
#     def _flow_for_chunk(cid):
#         n = cid_map.get(cid, {})
#         fd = build_call_flow(n.get("parent_entity",""), set(n.get("methods",[])), flat_tree)
#         return compact_call_flow(fd) if fd else n.get("parent_entity","")
#     rows = []
#     for cid, n in cid_map.items():
#         children = n.get("child_refs", [])
#         parents  = referenced_by.get(cid, [])
#         flow_str = _flow_for_chunk(cid)
#         hierarchy = {
#             "chunk_id": cid, "parent_entity": n["parent_entity"], "level": n["level"],
#             "chunk_type": n["chunk_type"], "code_sum": n["code_sum"], "groups": n["groups"],
#             "call_flow": flow_str, "child_chunk_refs": children, "used_by_chunks": parents,
#             "spec_action": (
#                 "GENERATE: build spec from raw YAML files only (leaf — no child specs)"
#                 if not children else
#                 f"CONSOLIDATE: inject {len(children)} child spec(s) + own files → produce merged spec"),
#             "call_flow_note": "call_flow format: 'root -> {caller: {callee1, callee2}, leaf}'. Siblings in DFS order.",
#         }
#         rows.append({
#             "chunk_id": cid, "parent_entity": n["parent_entity"], "level": n["level"],
#             "chunk_type": n["chunk_type"], "code_sum": n["code_sum"], "call_flow": flow_str,
#             "child_chunk_refs": ", ".join(children) if children else "",
#             "used_by_chunks":   ", ".join(parents)  if parents  else "",
#             "spec_action": hierarchy["spec_action"],
#             "hierarchy_json": _json.dumps(hierarchy, indent=2),
#         })
#     return pd.DataFrame(rows, columns=["chunk_id","parent_entity","level","chunk_type","code_sum",
#                                        "call_flow","child_chunk_refs","used_by_chunks",
#                                        "spec_action","hierarchy_json"]).sort_values(
#         by=["level","parent_entity","chunk_id"], ascending=[False,True,True]).reset_index(drop=True)


# # ── Main ─────────────────────────────────────────────────────────────────────
# def chunks_formation(INPUT_PATH):
#     global chunk_registry, chunk_id_counter, chunks_unique, chunk_usages
#     global global_lines_by_name, chunked_subtrees, assigned_files_by_parent
#     global global_assigned_methods

#     def ask(prompt: str, default: str = "") -> str:
#         val = input(f"{prompt} [{default}]: ").strip()
#         return val if val else default

    # default_chunk_limit = "6000"
    # chunk_limit_str = ask("Enter CHUNK_LIMIT (integer)", default_chunk_limit)
#     try:
#         CHUNK_LIMIT = int(chunk_limit_str)
#     except ValueError:
#         print(f"[WARN] Invalid CHUNK_LIMIT '{chunk_limit_str}', falling back to {default_chunk_limit}")
#         CHUNK_LIMIT = int(default_chunk_limit)

#     df = load_branch_df_stepped(INPUT_PATH, SHEET_NAME)
#     print(f"[INFO] Paths constructed: {len(df)}")
#     print(df.to_string())

#     chunk_registry.clear(); chunks_unique.clear(); chunk_usages.clear()
#     global_lines_by_name.clear(); chunked_subtrees.clear()
#     assigned_files_by_parent.clear()
#     # global_assigned_methods is NOT cleared here — each root file resets
#     # its own slot at the top of assign_chunks_top_down, so dedup is
#     # scoped per-root and never bleeds across root files.
#     global_assigned_methods.clear()  # still wipe between full pipeline runs
#     chunk_id_counter = itertools.count(1)

#     tree = build_tree(df)

#     print("\n[INFO] Pass 1: Computing subtree totals...")
#     for root_name, root_node in tree.items():
#         compute_totals(root_name, root_node)

#     print("[INFO] Pass 2: Assigning chunks top-down...")
#     for root_name, root_node in tree.items():
#         # FIX 1: pass root_file so every recursive call shares the same dedup set
#         assign_chunks_top_down(root_name, root_node, 1, CHUNK_LIMIT,
#                                ancestor_chunked=False, root_file=root_name)

#     for root_name, root_node in tree.items():
#         create_parent_reference_chunk(root_name, root_node, 1)

#     final_unique, final_usages = _finalize_chunks(chunks_unique, chunk_usages)
#     chunks_df = pd.DataFrame(final_unique)

#     if not chunks_df.empty:
#         if "parent_node" not in chunks_df.columns:
#             chunks_df["parent_node"] = "Chunk under " + chunks_df.get("trigger_node", "")
#         chunks_df["parent_entity"] = chunks_df["parent_node"].str.replace(
#             "Chunk under ", "", regex=False)

#     map_df = chunks_df[["chunk_id","parent_entity","level","code_sum",
#                          "methods_in_chunk","groups"]].copy()
#     map_df = map_df.sort_values(by=["parent_entity","level","code_sum"],
#                                 ascending=[True,True,False])

#     child_graph_raw_df     = build_child_graph_raw(chunks_df)
#     child_graph_pruned_df  = prune_edges(child_graph_raw_df)
#     child_graph_reduced_df = transitive_reduction_safe(child_graph_pruned_df)

#     # FIX 2: anchoring now groups orphan siblings correctly
#     child_graph_pruned_df  = _anchor_orphans_to_root(child_graph_pruned_df,  chunks_df, tree)
#     child_graph_reduced_df = _anchor_orphans_to_root(child_graph_reduced_df, chunks_df, tree)

#     reusability_edges_df   = compute_reusability_edges(child_graph_reduced_df)
#     reusability_summary_df = compute_reusability_summary(child_graph_reduced_df)

#     # FIX 3: pass the correct graph so execution order uses real chunk edges
#     execution_order_df = build_execution_order(chunks_df,
#                                                child_graph_df=child_graph_reduced_df)
#     hierarchy_df       = build_hierarchy_json(chunks_df, tree)

#     print("\n=== Parent_to_Chunks ===")
#     print(map_df.to_string(index=False))
#     print("\n=== Child_Graph_Reduced ===")
#     print(child_graph_reduced_df.to_string(index=False))
#     print("\n=== Execution_Order ===")
#     print(execution_order_df[["exec_order","chunk_id","depends_on_chunks",
#                                "needed_by_chunks"]].to_string(index=False))

    # OUT_XLSX = os.path.splitext(INPUT_PATH)[0] + "_chunks_reusability.xlsx"
#     with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
#         map_df.to_excel(writer,                sheet_name="Parent_to_Chunks",     index=False)
#         child_graph_reduced_df.to_excel(writer, sheet_name="Child_Graph_Reduced", index=False)
#         reusability_edges_df.to_excel(writer,   sheet_name="Reusability_Edges",   index=False)
#         reusability_summary_df.to_excel(writer, sheet_name="Reusability_Summary", index=False)
#         execution_order_df.to_excel(writer,     sheet_name="Execution_Order",     index=False)
#         hierarchy_df.to_excel(writer,           sheet_name="Hierarchy_JSON",      index=False)

#     print(f"\n[DONE] Exported to: {OUT_XLSX}")

#     return OUT_XLSX



import pandas as pd
import html
import itertools
from pathlib import Path
import re
import os
import unicodedata
from collections import defaultdict, deque, OrderedDict
import numpy as np

# ── Non-interactive config ────────────────────────────────────────────────────
SHEET_NAME  = 0
# -----------------------------
# Branch loaders
# -----------------------------
def load_branch_df_stepped(path: str, sheet_name=0, start_level: int = 0) -> pd.DataFrame:
    p = Path(path)
    df = pd.read_excel(path, sheet_name=sheet_name, engine="openpyxl")
    level_cols = [c for c in df.columns if str(c).lower().startswith("level")]

    if not level_cols:
        level_cols = df.columns.tolist()

    # ── PROCESS MODE CUT: ignore columns before selected level ──
    # start_level = 1 means no change
    if start_level > 0:
        if start_level <= len(level_cols):
            level_cols = level_cols[start_level - 1 :]
        else:
            raise ValueError(
                f"Invalid level {start_level}. File has only {len(level_cols)} columns."
            )

    rows_as_branches = []
    current_context = [None] * len(level_cols)

    for _, row in df.iterrows():
        path_segments = []
        for i, col in enumerate(level_cols):
            val = row[col]
            if pd.notnull(val) and str(val).strip() != "":
                current_context[i] = str(val).strip()
                for j in range(i + 1, len(level_cols)):
                    current_context[j] = None
            if current_context[i]:
                path_segments.append(current_context[i])
            else:
                break
        if path_segments:
            rows_as_branches.append(" -> ".join(path_segments))

    return pd.DataFrame({"Branch": rows_as_branches})


# -----------------------------
# Name/lines extractors
# -----------------------------
NO_LINES_RE = re.compile(
    r'^(?P<method>.*?)\s+no_of_lines\s*:\s*(?P<lines>\d+|Nil|None)\s*$',
    re.IGNORECASE
)
_METHOD_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')

# Known file extensions that must NOT be treated as method names
_FILE_EXTENSIONS = {
    'java', 'class', 'jar', 'war', 'ear', 'xml', 'json', 'yaml', 'yml',
    'properties', 'txt', 'log', 'sql', 'html', 'htm', 'js', 'ts', 'css',
    'py', 'sh', 'bat', 'cmd', 'bak', 'tmp', 'zip', 'tar', 'gz', 'pdf',
    'doc', 'docx', 'xls', 'xlsx', 'csv', 'png', 'jpg', 'jpeg', 'gif',
    'bmp', 'ico', 'svg', 'wsdl', 'xsd', 'jsp', 'jspx', 'groovy', 'kt',
    'scala', 'gradle', 'pom', 'config', 'ini', 'cfg', 'env',
}


def is_method(name: str) -> bool:
    """
    Returns True if `name` ends with a valid Java method identifier after the
    last dot — regardless of what precedes the dot (e.g. full Windows paths
    like C:\\Download\\Path.method are handled correctly).

    Explicitly rejects known file extensions (e.g. .java, .class, .xml)
    so that file paths without a method suffix are never misclassified.
    """
    if not name:
        return False
    s = str(name).strip()
    if '.' not in s:
        return False
    last_dot = s.rfind('.')
    after_dot = s[last_dot + 1:]
    if after_dot.lower() in _FILE_EXTENSIONS:
        return False
    return bool(_METHOD_NAME_RE.match(after_dot))


def extract_name_lines(segment: str):
    if segment is None or (isinstance(segment, float) and pd.isnull(segment)):
        return None, 0, 0
    s = str(segment).strip()
    level = 0
    m_level = re.search(r'\[\s*level\s*:\s*(\d+)\s*\]', s, flags=re.IGNORECASE)
    if m_level:
        level = int(m_level.group(1))
        s = re.sub(r'\[\s*level\s*:\s*\d+\s*\]', '', s, flags=re.IGNORECASE).strip()
    m_loc = re.search(r'^(.*?)\s*\[\s*LOC\s*:\s*(\d+)\s*\]$', s, flags=re.IGNORECASE)
    if m_loc:
        return m_loc.group(1).strip(), int(m_loc.group(2)), level
    m_nol_br = re.search(r'^(.*?)\s*\[\s*no_of_lines\s*:\s*(\d+|None|Nil)\s*\]$', s, flags=re.IGNORECASE)
    if m_nol_br:
        token = m_nol_br.group(2).lower()
        return m_nol_br.group(1).strip(), 0 if token in ('none', 'nil') else int(token), level
    m_nol_inline = re.search(r'^(.*?)\s+no_of_lines\s*:\s*(\d+|None|Nil)\s*$', s, flags=re.IGNORECASE)
    if m_nol_inline:
        token = m_nol_inline.group(2).lower()
        return m_nol_inline.group(1).strip(), 0 if token in ('none', 'nil') else int(token), level
    m_simple = re.search(r'^(.*?)(?:\s+(\d+|None|Nil))$', s, flags=re.IGNORECASE)
    if m_simple:
        token = (m_simple.group(2) or '').lower()
        return m_simple.group(1).strip(), 0 if token in ('none', 'nil') else (int(token) if token.isdigit() else 0), level
    return s.strip(), 0, level


def slugify_filename(name: str) -> str:
    """
    Produces a safe, readable chunk-ID suffix from a method name.
    For full Windows/Unix paths (e.g. C:\\Download\\Path.method) only the
    last path component is used so chunk IDs stay short and non-ambiguous.
    """
    if not isinstance(name, str):
        name = str(name)
    # Use only the tail after the last path separator (handles both / and \)
    name = re.split(r'[/\\]', name)[-1]
    name = unicodedata.normalize("NFKD", name)
    name = name.replace(" ", "_")
    return "".join(ch for ch in name if ch.isalnum() or ch in ["_", "+", "-"])


GROUP_PATTERN = re.compile(r'^(?:G|GRP|GROUP)[-_]?\d+(?:\.\d+)?$', re.I)


def is_group(name: str) -> bool:
    if not name:
        return False
    return bool(GROUP_PATTERN.match(str(name).strip()))


# -----------------------------
# Global state
# -----------------------------
chunk_registry   = {}
chunk_id_counter = itertools.count(1)
chunks_unique    = []
chunk_usages     = []
global_lines_by_name = {}
chunked_subtrees = set()


def format_method_with_lines(name: str) -> str:
    if name is None:
        return ""
    s = str(name).strip()
    if not s:
        return ""
    lines = global_lines_by_name.get(s, 0)
    try:
        lines_int = int(lines)
        lines_txt = "Nil" if lines_int == 0 else str(lines_int)
    except Exception:
        lines_txt = "Nil"
    return f"{s} no_of_lines : {lines_txt}"


def format_refs_with_lines(refs_csv: str) -> str:
    parts = [p.strip() for p in str(refs_csv).split(",") if p.strip()]
    return ", ".join(format_method_with_lines(p) for p in parts)


def register_or_get_chunk_id(filenames_ordered, code_sum, row_indices_ordered,
                              trigger_node, level, trigger_node_name,
                              child_chunk_refs="", chunk_type="leaf", groups_ordered=None):
    if groups_ordered is None:
        groups_ordered = []
    key = (trigger_node, tuple(filenames_ordered))
    if key in chunk_registry:
        return chunk_registry[key]
    num = next(chunk_id_counter)
    base_id = f"C{num:03d}"
    trigger_slug = slugify_filename(trigger_node) if trigger_node else "unknown_trigger"
    chunk_id = f"{base_id}_{trigger_slug}"
    chunk_registry[key] = chunk_id

    if chunk_type == "leaf":
        display_filenames = ", ".join(format_method_with_lines(fn) for fn in filenames_ordered)
    else:
        display_filenames = format_refs_with_lines(child_chunk_refs) if child_chunk_refs \
            else ", ".join(format_method_with_lines(fn) for fn in filenames_ordered)

    groups_csv = ", ".join(f"{g} no_of_lines : Nil" for g in groups_ordered) if groups_ordered else ""
    display = display_filenames
    if groups_csv:
        display = ", ".join([s for s in [display_filenames, groups_csv] if s])

    record_common = {
        "chunk_id":          chunk_id,
        "trigger_node":      trigger_node,
        "level":             level,
        "trigger_node_name": trigger_node_name,
        "methods_in_chunk":  display,
        "groups":            groups_csv,
        "row_indices":       ", ".join(map(str, row_indices_ordered)),
        "code_sum":          code_sum,
        "child_chunk_refs":  child_chunk_refs,
        "chunk_type":        chunk_type,
        "triggered_by":      f"{chunk_type.capitalize()} chunk for '{trigger_node}'"
    }
    chunks_unique.append({**record_common, "parent_node": f"Chunk under {trigger_node}"})
    chunk_usages.append({**record_common,  "used_under_parent_node": f"Chunk under {trigger_node}"})
    return chunk_id


def iter_entities_dfs(node):
    for nm in node.get("filenames", []):
        yield nm
    for _, child in node.get("children", {}).items():
        yield from iter_entities_dfs(child)


# -----------------------------
# Core chunking
# -----------------------------
def create_chunks_for_children(children, level, trigger_node, CHUNK_LIMIT,
                               root_file: str = ""):
    """
    Greedy bin-packing of direct children into chunks bounded by CHUNK_LIMIT.

    LARGE child (unique_total_lines >= CHUNK_LIMIT):
        Do NOT flush or create an isolated pointer chunk.  Simply append the
        child name to child_refs_in_current — its subtree LOC costs nothing
        in this bin (recursion handles it).  This lets small siblings on both
        sides of a large child pack into the same bin together.

    SMALL child (unique_total_lines < CHUNK_LIMIT):
        Inline all its descendant methods into the current bin.
        Flush before adding if the incremental LOC would exceed CHUNK_LIMIT.

    Within-bin dedup (current_methods_set) prevents the same method appearing
    twice inside one chunk.  No cross-chunk global filtering is applied.
    """
    global chunked_subtrees

    ordered_children = list(children.items())

    current_methods_list = []
    current_methods_set  = set()
    current_groups_list  = []
    current_groups_set   = set()
    current_unique_sum   = 0
    have_items           = False

    # Include parent only once across all child chunks
    parent_used_in_chunk = False

    # Tracks ALL children for the current bin (small inlined + large refs).
    child_refs_in_current = []

    def _flush_chunk():
        nonlocal current_methods_list, current_methods_set
        nonlocal current_groups_list,  current_groups_set
        nonlocal current_unique_sum, have_items, child_refs_in_current
        nonlocal parent_used_in_chunk

        if not have_items:
            return

        methods_out = list(current_methods_list)
        groups_out  = list(current_groups_list)

        # Include parent ONLY in the first child chunk
        if (
            not parent_used_in_chunk
            and trigger_node
            and is_method(trigger_node)
            and trigger_node not in methods_out
        ):
            methods_out.insert(0, trigger_node)

        unique_sum = sum(
            global_lines_by_name.get(fn, 0) for fn in methods_out if is_method(fn)
        )

        # Mark parent as consumed so it is NOT added in future chunks
        if trigger_node and trigger_node in methods_out:
            parent_used_in_chunk = True
        register_or_get_chunk_id(
            filenames_ordered=methods_out,
            code_sum=unique_sum,
            row_indices_ordered=methods_out,
            trigger_node=trigger_node,
            level=level,
            trigger_node_name=trigger_node,
            child_chunk_refs=", ".join(child_refs_in_current),
            chunk_type="leaf",
            groups_ordered=groups_out,
        )

        for ref in child_refs_in_current:
            chunked_subtrees.add((trigger_node, ref))

        current_methods_list[:]   = []
        current_methods_set.clear()
        current_groups_list[:]    = []
        current_groups_set.clear()
        current_unique_sum        = 0
        have_items                = False
        child_refs_in_current[:]  = []

    for child_name, child_node in ordered_children:
        if (trigger_node, child_name) in chunked_subtrees:
            continue

        child_unique_total = child_node.get(
            "unique_total_lines", child_node.get("total_lines", child_node["lines"])
        )

        # ── LARGE child ──────────────────────────────────────────────────────
        if child_unique_total >= CHUNK_LIMIT:
            child_refs_in_current.append(child_name)
            have_items = True
            chunked_subtrees.add((trigger_node, child_name))
            continue

        # ── SMALL child: inline all its methods into the current bin ─────────
        child_entities        = list(iter_entities_dfs(child_node))
        child_methods_ordered = [e for e in child_entities if is_method(e)]
        child_groups_ordered  = [e for e in child_entities if is_group(e)]

        child_methods_new = [m for m in child_methods_ordered if m not in current_methods_set]
        incremental_unique = sum(global_lines_by_name.get(m, 0) for m in child_methods_new)

        # Flush if adding this child would overflow the current bin
        if have_items and child_methods_new and (current_unique_sum + incremental_unique) > CHUNK_LIMIT:
            _flush_chunk()
            # Recompute after flush — current_methods_set is now empty
            child_methods_new  = list(child_methods_ordered)
            incremental_unique = sum(global_lines_by_name.get(m, 0) for m in child_methods_new)

        if child_methods_new or child_groups_ordered:
            for m in child_methods_ordered:
                if m not in current_methods_set:
                    current_methods_list.append(m)
                    current_methods_set.add(m)
            for g in child_groups_ordered:
                if g not in current_groups_set:
                    current_groups_list.append(g)
                    current_groups_set.add(g)
            current_unique_sum += incremental_unique
            have_items = True
            child_refs_in_current.append(child_name)
        else:
            chunked_subtrees.add((trigger_node, child_name))

    _flush_chunk()


def assign_chunks_top_down(node_name, node, level, CHUNK_LIMIT,
                           ancestor_chunked=False, root_file: str = ""):
    """
    POST-ORDER chunking: recurse into all children first, then chunk the
    current node's children.

    This guarantees deepest leaf chunks receive the lowest C-numbers and
    every parent pointer chunk has a higher ID than the sub-chunks it
    references — matching natural bottom-up execution order.
    """
    if not root_file:
        root_file = node_name

    # ── Recurse into children FIRST (post-order) ─────────────────────────
    for child_name, child_node in node["children"].items():
        assign_chunks_top_down(
            child_name, child_node, level + 1, CHUNK_LIMIT,
            ancestor_chunked=False, root_file=root_file
        )

    # ── Then chunk THIS node's children ──────────────────────────────────
    total_unique = node.get(
        "unique_total_lines", node.get("total_lines", node.get("lines", 0))
    )
    if total_unique >= CHUNK_LIMIT and node["children"]:
        create_chunks_for_children(
            node["children"], level + 1, node_name, CHUNK_LIMIT,
            root_file=root_file
        )


def create_parent_reference_chunk(node_name, node, level):
    global chunked_subtrees
    parent_methods_list = []
    parent_groups_list  = []
    seen_m = set()
    seen_g = set()
    child_refs = []

    # FIX: include the level_1 method (node_name) itself in the parent_ref chunk
    # so that when this chunk is used to extract code, the root method is also
    # part of the input spec.
    if is_method(node_name) and node_name not in seen_m:
        parent_methods_list.append(node_name)
        seen_m.add(node_name)
    elif is_group(node_name) and node_name not in seen_g:
        parent_groups_list.append(node_name)
        seen_g.add(node_name)

    for child_name, child_node in node["children"].items():
        if (node_name, child_name) in chunked_subtrees:
            child_refs.append(child_name)
        else:
            for ent in iter_entities_dfs(child_node):
                if is_method(ent) and ent not in seen_m:
                    parent_methods_list.append(ent)
                    seen_m.add(ent)
                elif is_group(ent) and ent not in seen_g:
                    parent_groups_list.append(ent)
                    seen_g.add(ent)

    if parent_methods_list:
        unique_sum = sum(global_lines_by_name.get(fn, 0) for fn in parent_methods_list if is_method(fn))
        register_or_get_chunk_id(
            filenames_ordered=parent_methods_list,
            code_sum=unique_sum,
            row_indices_ordered=parent_methods_list,
            trigger_node=node_name,
            level=level,
            trigger_node_name=f"{node_name}_PARENT_ONLY",
            child_chunk_refs=", ".join(child_refs),
            chunk_type="parent_ref",
            groups_ordered=parent_groups_list,
        )


def compute_totals(node_name, node):
    total_structural = node["lines"]
    unique_methods   = set()
    if is_method(node_name):
        unique_methods.add(node_name)
    for child_name, child_node in node["children"].items():
        child_structural = compute_totals(child_name, child_node)
        total_structural += child_structural
        # FIX Bug 1: add child_name itself into unique_methods before unioning
        # grandchildren. Previously only grandchildren were captured, causing
        # direct children to be missing from unique_total_lines, which led to
        # subtree LOC being under-counted and splitting not triggering correctly.
        if is_method(child_name):
            unique_methods.add(child_name)
        unique_methods |= child_node.get("unique_methods", set())
    node["total_lines"]        = total_structural
    node["unique_methods"]     = unique_methods
    node["unique_total_lines"] = sum(
        global_lines_by_name.get(m, 0) for m in unique_methods if is_method(m)
    )
    return total_structural


def _finalize_chunks(chunks_unique_list, chunk_usages_list):
    # Map trigger_node -> level of its parent_ref chunk
    parent_ref_level = {
        c.get("trigger_node"): c.get("level", 1)
        for c in chunks_unique_list if c.get("chunk_type") == "parent_ref"
    }
    def keep_record(rec):
        if rec.get("chunk_type") != "leaf":
            return True
        trigger = rec.get("trigger_node")
        if trigger not in parent_ref_level:
            return True
        # FIX Bug 2: only drop a leaf if it is at the SAME level as its
        # parent_ref. Child-level leaf chunks (level > parent_ref level)
        # are produced by splitting and must be kept — previously they were
        # all dropped because the trigger_node match alone was used, which
        # wiped out every child chunk and left only parent_ref chunks visible.
        return rec.get("level", 1) != parent_ref_level[trigger]
    return (
        [c for c in chunks_unique_list if keep_record(c)],
        [u for u in chunk_usages_list  if keep_record(u)],
    )


def build_tree(df: pd.DataFrame):
    tree = OrderedDict()
    for idx, row in df.iterrows():
        current = tree
        s = html.unescape(str(row["Branch"]))
        for arrow in ["->", "=> ", "→", " - > "]:
            s = s.replace(arrow, " -> ")
        segments = [seg.strip() for seg in re.split(r'\s*->\s*', s) if seg.strip()]
        for seg in segments:
            name, lines, _ = extract_name_lines(seg)
            if name:
                if name not in current:
                    current[name] = {"lines": lines, "children": OrderedDict(),
                                     "rows": set(), "filenames": [name]}
                current[name]["lines"] = max(current[name]["lines"], lines)
                current[name]["rows"].add(idx)
                if name not in current[name]["filenames"]:
                    current[name]["filenames"].append(name)
                global_lines_by_name[name] = max(
                    global_lines_by_name.get(name, 0),
                    lines if not is_group(name) else 0
                )
                current = current[name]["children"]
    return tree


# ── Reusability helpers ───────────────────────────────────────────────────────
_NO_OF_LINES_RE = re.compile(r"\s*no_of_lines\s*:\s*(?:\d+|nil|none)\s*$", re.IGNORECASE)


def normalize_entity_token(token: str) -> str:
    if token is None:
        return ""
    s = str(token).strip()
    return _NO_OF_LINES_RE.sub("", s).strip()


def parse_list(cell):
    if pd.isnull(cell):
        return []
    parts = [x.strip() for x in str(cell).split(",") if x.strip()]
    norm  = [normalize_entity_token(x) for x in parts]
    return [x for x in norm if x]


def build_entity_to_chunk_id(df):
    canonical_map = {}
    if df is None or df.empty:
        return canonical_map
    for entity, group in df.groupby("parent_entity"):
        sorted_group = group.sort_values(by=["code_sum", "level"], ascending=[False, True])
        canonical_map[str(entity)] = str(sorted_group.iloc[0]["chunk_id"])
    return canonical_map


def build_child_graph_raw(df):
    rows = df.to_dict("records")
    entity_to_rows  = defaultdict(list)
    trigger_to_rows = defaultdict(list)
    for r in rows:
        entity_to_rows[r["parent_entity"]].append(r)
        trig = str(r.get("trigger_node", r.get("parent_entity", "")))
        trigger_to_rows[trig].append(r)

    edges      = []
    seen_pairs = set()

    def _add_edge(pc, pe, pl, ps, cc, ce, cl, cs):
        pair = (str(pc), str(cc))
        if pair in seen_pairs or str(pc) == str(cc):
            return
        seen_pairs.add(pair)
        edges.append({
            "parent_chunk_id": pc, "parent_entity": pe,
            "parent_level": pl,   "parent_code_sum": ps,
            "child_chunk_id": cc, "child_entity": ce,
            "child_level": cl,    "child_code_sum": cs,
        })

    for r in rows:
        pc = r["chunk_id"]
        pe = r["parent_entity"]
        pl = r.get("level")
        ps = r.get("code_sum")
        for fname in parse_list(r.get("methods_in_chunk")):
            for child in entity_to_rows.get(fname, []):
                if child["chunk_id"] != pc:
                    _add_edge(pc, pe, pl, ps, child["chunk_id"], child["parent_entity"],
                              child.get("level"), child.get("code_sum"))
        refs_raw = str(r.get("child_chunk_refs", "") or "")
        for token in [x.strip() for x in refs_raw.split(",")
                      if x.strip() and x.strip() not in ("nan", "(none)", "")]:
            norm_token = _NO_OF_LINES_RE.sub("", token).strip()
            for child in trigger_to_rows.get(norm_token, []):
                if child["chunk_id"] != pc:
                    _add_edge(pc, pe, pl, ps, child["chunk_id"], child["parent_entity"],
                              child.get("level"), child.get("code_sum"))

    cols = ["parent_chunk_id", "parent_entity", "parent_level", "parent_code_sum",
            "child_chunk_id",  "child_entity",  "child_level",  "child_code_sum"]
    edges_df = pd.DataFrame(edges) if edges else pd.DataFrame(columns=cols)
    if not edges_df.empty:
        edges_df = edges_df.drop_duplicates().sort_values(
            by=["parent_entity", "parent_level", "parent_chunk_id",
                "child_entity",  "child_level",  "child_chunk_id"])
    return edges_df


def prune_edges(edges_df):
    if edges_df.empty:
        return edges_df.copy()
    df = edges_df[edges_df["parent_chunk_id"] != edges_df["child_chunk_id"]].copy()
    if df.empty:
        return df
    pairs        = set(zip(df["parent_chunk_id"], df["child_chunk_id"]))
    mutual_pairs = {(u, v) for (u, v) in pairs if (v, u) in pairs}
    if not mutual_pairs:
        return df
    mask = ~df.apply(
        lambda r: (r["parent_chunk_id"], r["child_chunk_id"]) in mutual_pairs, axis=1
    )
    return df[mask].drop_duplicates().sort_values(
        by=["parent_entity", "parent_level", "parent_chunk_id",
            "child_entity",  "child_level",  "child_chunk_id"])


def _build_adj(edges_df):
    adj = defaultdict(set)
    for _, r in edges_df.iterrows():
        adj[r["parent_chunk_id"]].add(r["child_chunk_id"])
        _ = adj[r["child_chunk_id"]]
    return adj


def _tarjan_scc(adj):
    index = 0; stack = []; onstack = set(); indices = {}; lowlink = {}; sccs = []

    def strongconnect(v):
        nonlocal index
        indices[v] = lowlink[v] = index; index += 1
        stack.append(v); onstack.add(v)
        for w in adj.get(v, set()):
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in onstack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            comp = set()
            while True:
                w = stack.pop(); onstack.discard(w); comp.add(w)
                if w == v:
                    break
            sccs.append(comp)

    for v in list(adj.keys()):
        if v not in indices:
            strongconnect(v)
    return sccs


def _dfs_reach(adj, start, cache):
    if start in cache:
        return cache[start]
    visited = set(); stack = [start]
    while stack:
        u = stack.pop()
        for v in adj.get(u, set()):
            if v not in visited:
                visited.add(v); stack.append(v)
    cache[start] = visited
    return visited


def _transitive_reduction_dag(adj):
    reach_cache = {}; to_remove = set()
    for u, children in adj.items():
        cl = list(children)
        reach_by_v = {v: (_dfs_reach(adj, v, reach_cache) | {v}) for v in cl}
        for w in cl:
            for v in cl:
                if v != w and w in reach_by_v[v]:
                    to_remove.add((u, w)); break
    return {(u, v) for u, children in adj.items() for v in children if (u, v) not in to_remove}


def transitive_reduction_safe(edges_df):
    if edges_df.empty:
        return edges_df.copy()
    adj     = _build_adj(edges_df)
    sccs    = _tarjan_scc(adj)
    comp_id = {node: i for i, comp in enumerate(sccs) for node in comp}
    comp_adj = defaultdict(set)
    for u, children in adj.items():
        cu = comp_id[u]
        for v in children:
            cv = comp_id[v]
            if cu != cv:
                comp_adj[cu].add(cv)
    reduced_comp_edges = _transitive_reduction_dag(comp_adj)
    keep_edges = set()
    for u, children in adj.items():
        for v in children:
            if comp_id[u] == comp_id[v]:
                keep_edges.add((u, v))
    needed_comp_pairs = set(reduced_comp_edges)
    for u, children in adj.items():
        cu = comp_id[u]
        for v in children:
            cv = comp_id[v]
            if cu != cv and (cu, cv) in needed_comp_pairs:
                keep_edges.add((u, v))
    mask = edges_df.apply(
        lambda r: (r["parent_chunk_id"], r["child_chunk_id"]) in keep_edges, axis=1
    )
    return edges_df[mask].drop_duplicates(
        subset=["parent_chunk_id", "child_chunk_id"]
    ).sort_values(
        by=["parent_entity", "parent_level", "parent_chunk_id",
            "child_entity",  "child_level",  "child_chunk_id"])


def _anchor_orphans_to_root(edges_df, chunks_df, tree):
    cols = ["parent_chunk_id", "parent_entity", "parent_level", "parent_code_sum",
            "child_chunk_id",  "child_entity",  "child_level",  "child_code_sum"]
    if edges_df.empty:
        edges_df = pd.DataFrame(columns=cols)

    trigger_to_chunks = defaultdict(list)
    cid_to_row = {}
    for _, r in chunks_df.iterrows():
        cid  = str(r["chunk_id"])
        trig = str(r.get("trigger_node", r.get("parent_entity", "")))
        trigger_to_chunks[trig].append(r)
        cid_to_row[cid] = r

    node_parent = {}
    def _walk(node_dict, parent_name):
        for name, node in node_dict.items():
            node_parent[name] = parent_name
            _walk(node["children"], name)
    for root_name, root_node in tree.items():
        node_parent[root_name] = None
        _walk(root_node["children"], root_name)

    existing_children = set(edges_df["child_chunk_id"].dropna().astype(str))
    all_cids          = set(cid_to_row.keys())
    orphan_cids       = all_cids - existing_children

    new_edges            = []
    virtual_root_chunks  = {}

    def _get_or_create_virtual_root(root_name):
        if root_name not in virtual_root_chunks:
            virtual_root_chunks[root_name] = f"VROOT_{slugify_filename(root_name)}"
        return virtual_root_chunks[root_name]

    orphan_groups: dict[str, list] = defaultdict(list)
    for cid in orphan_cids:
        r    = cid_to_row[cid]
        trig = str(r.get("trigger_node", r.get("parent_entity", "")))
        root = trig
        while node_parent.get(root) is not None:
            root = node_parent[root]
        orphan_groups[root].append((cid, r))

    for root_name, orphan_list in orphan_groups.items():
        for cid, r in orphan_list:
            trig = str(r.get("trigger_node", r.get("parent_entity", "")))

            ancestor = node_parent.get(trig)
            ancestor_chunk = None
            while ancestor is not None:
                candidates = trigger_to_chunks.get(ancestor, [])
                if candidates:
                    ancestor_chunk = min(candidates, key=lambda x: int(x.get("level", 99)))
                    break
                ancestor = node_parent.get(ancestor)

            if ancestor_chunk is None:
                root_candidates = trigger_to_chunks.get(root_name, [])
                if root_candidates:
                    ancestor_chunk = min(root_candidates, key=lambda x: int(x.get("level", 99)))

            if ancestor_chunk is None:
                virt_id = _get_or_create_virtual_root(root_name)
                new_edges.append({
                    "parent_chunk_id": virt_id, "parent_entity": root_name,
                    "parent_level":    1,        "parent_code_sum": None,
                    "child_chunk_id":  cid,
                    "child_entity":    str(r.get("parent_entity", "")),
                    "child_level":     r.get("level"),
                    "child_code_sum":  r.get("code_sum"),
                })
                continue

            a_cid = str(ancestor_chunk["chunk_id"])
            if a_cid == cid:
                continue
            new_edges.append({
                "parent_chunk_id": a_cid,
                "parent_entity":   str(ancestor_chunk.get("parent_entity", "")),
                "parent_level":    ancestor_chunk.get("level"),
                "parent_code_sum": ancestor_chunk.get("code_sum"),
                "child_chunk_id":  cid,
                "child_entity":    str(r.get("parent_entity", "")),
                "child_level":     r.get("level"),
                "child_code_sum":  r.get("code_sum"),
            })

    if new_edges:
        edges_df = pd.concat([edges_df, pd.DataFrame(new_edges)], ignore_index=True)
        edges_df = edges_df.drop_duplicates(subset=["parent_chunk_id", "child_chunk_id"])

    existing_children2 = set(edges_df["child_chunk_id"].dropna().astype(str))
    existing_parents2  = set(edges_df["parent_chunk_id"].dropna().astype(str))
    truly_isolated     = all_cids - (existing_children2 | existing_parents2)
    iso_rows = []
    for cid in truly_isolated:
        r = cid_to_row[cid]
        iso_rows.append({
            "parent_chunk_id": cid,
            "parent_entity":   str(r.get("parent_entity", "")),
            "parent_level":    r.get("level"),
            "parent_code_sum": r.get("code_sum"),
            "child_chunk_id":  "", "child_entity": "",
            "child_level":     None, "child_code_sum": None,
        })
    if iso_rows:
        edges_df = pd.concat([edges_df, pd.DataFrame(iso_rows)], ignore_index=True)

    return edges_df.drop_duplicates().sort_values(
        by=["parent_entity", "parent_level", "parent_chunk_id",
            "child_entity",  "child_level",  "child_chunk_id"])


def compute_reusability_edges(reduced_edges_df):
    if reduced_edges_df.empty:
        return reduced_edges_df.copy()
    return reduced_edges_df.drop_duplicates(
        subset=["parent_chunk_id", "child_chunk_id"]
    ).sort_values(
        by=["parent_entity", "parent_level", "parent_chunk_id",
            "child_entity",  "child_level",  "child_chunk_id"])


def compute_reusability_summary(reduced_edges_df: pd.DataFrame) -> pd.DataFrame:
    expected = ["parent_chunk_id", "parent_entity", "parent_level", "parent_code_sum",
                "child_chunk_id",  "child_entity",  "child_level",  "child_code_sum"]
    for col in expected:
        if col not in reduced_edges_df.columns:
            reduced_edges_df[col] = pd.Series(dtype=object)
    if reduced_edges_df.empty:
        return pd.DataFrame(columns=["child_chunk_id", "child_entity", "child_level",
                                     "child_code_sum", "used_in_parents_count", "parents_list"])
    agg = (
        reduced_edges_df
        .groupby(["child_chunk_id", "child_entity", "child_level", "child_code_sum"],
                 dropna=False, as_index=False)
        .agg(parents=("parent_chunk_id",
                      lambda xs: sorted({str(x) for x in xs if pd.notnull(x) and str(x)})))
    )
    agg["used_in_parents_count"] = agg["parents"].apply(len)
    agg["parents_list"]          = agg["parents"].apply(lambda xs: ", ".join(xs))
    return agg.drop(columns=["parents"]).sort_values(
        by=["used_in_parents_count", "child_entity"], ascending=[False, True])


def _build_flat_tree(tree: dict) -> dict:
    flat  = {}
    stack = list(tree.items())
    while stack:
        name, node = stack.pop()
        bare = _NO_OF_LINES_RE.sub("", name).strip()
        flat[bare] = node
        for cname, cnode in node["children"].items():
            stack.append((cname, cnode))
    return flat


def build_call_flow(trigger_name: str, chunk_methods: set, flat_tree: dict) -> dict:
    bare_trigger = _NO_OF_LINES_RE.sub("", str(trigger_name or "")).strip()
    if bare_trigger not in flat_tree:
        return {
            "method": bare_trigger,
            "lines":  global_lines_by_name.get(bare_trigger, 0),
            "calls":  [{"method": m, "lines": global_lines_by_name.get(m, 0), "calls": []}
                       for m in sorted(chunk_methods - {bare_trigger})],
        }
    visited = set()

    def _dfs(bare_name):
        if bare_name in visited:
            return None
        visited.add(bare_name)
        node  = flat_tree.get(bare_name, {"children": {}})
        calls = []
        for child_raw in node.get("children", {}):
            child_bare = _NO_OF_LINES_RE.sub("", child_raw).strip()
            if child_bare in chunk_methods:
                cf = _dfs(child_bare)
                if cf:
                    calls.append(cf)
            else:
                def _passthrough(n_bare, seen_local):
                    pnode = flat_tree.get(n_bare, {"children": {}})
                    for gcraw in pnode.get("children", {}):
                        gc_bare = _NO_OF_LINES_RE.sub("", gcraw).strip()
                        if gc_bare in chunk_methods and gc_bare not in seen_local:
                            gf = _dfs(gc_bare)
                            if gf:
                                calls.append(gf)
                        elif gc_bare not in seen_local:
                            _passthrough(gc_bare, seen_local | {gc_bare})
                _passthrough(child_bare, visited.copy())
        return {"method": bare_name, "lines": global_lines_by_name.get(bare_name, 0), "calls": calls}

    root_flow = _dfs(bare_trigger)
    if root_flow is not None:
        for m in chunk_methods:
            if m not in visited and m != bare_trigger:
                visited.add(m)
                root_flow["calls"].append(
                    {"method": m, "lines": global_lines_by_name.get(m, 0), "calls": []}
                )
    return root_flow


def compact_call_flow(node: dict) -> str:
    def _render(n, is_root=False):
        name  = n["method"]
        calls = n.get("calls", [])
        if not calls:
            return name
        children_str = ", ".join(_render(c) for c in calls)
        return f"{name} -> {{{children_str}}}" if is_root else f"{name}: {{{children_str}}}"
    return _render(node, is_root=True)


def build_execution_order(chunks_df: pd.DataFrame,
                          child_graph_df: pd.DataFrame = None) -> pd.DataFrame:
    if chunks_df is None or chunks_df.empty:
        return pd.DataFrame()

    cid_map  = {str(r["chunk_id"]): r for _, r in chunks_df.iterrows()}
    all_cids = list(cid_map.keys())
    adj      = defaultdict(list)
    radj     = defaultdict(list)

    if child_graph_df is not None and not child_graph_df.empty:
        for _, r in child_graph_df.iterrows():
            pc = str(r["parent_chunk_id"])
            cc = str(r.get("child_chunk_id", "") or "")
            if pc and cc and pc in cid_map and cc in cid_map and pc != cc:
                if cc not in adj[pc]:
                    adj[pc].append(cc)
                if pc not in radj[cc]:
                    radj[cc].append(pc)
    else:
        entity_to_chunk = build_entity_to_chunk_id(chunks_df)
        for _, r in chunks_df.iterrows():
            parent_cid = str(r["chunk_id"])
            refs       = str(r.get("child_chunk_refs", "") or "")
            tokens     = [x.strip() for x in refs.split(",")
                          if x.strip() and x.strip() != "nan"]
            mapped_ids = list(dict.fromkeys(
                [entity_to_chunk[t] for t in tokens
                 if entity_to_chunk.get(t) and entity_to_chunk[t] in cid_map
                 and entity_to_chunk[t] != parent_cid]
            ))
            for mcid in mapped_ids:
                adj[parent_cid].append(mcid)
                radj[mcid].append(parent_cid)

    dep_count = {cid: len(adj.get(cid, [])) for cid in all_cids}
    queue = deque(sorted(
        [c for c in all_cids if dep_count.get(c, 0) == 0],
        key=lambda c: cid_map[c].get("level", 99), reverse=True
    ))
    order   = []
    visited = set()
    while queue:
        cid = queue.popleft()
        if cid in visited:
            continue
        visited.add(cid); order.append(cid)
        for parent in radj.get(cid, []):
            dep_count[parent] = dep_count.get(parent, 0) - 1
            if dep_count[parent] == 0:
                queue.append(parent)
    for cid in all_cids:
        if cid not in visited:
            order.append(cid)

    rows = []
    for i, cid in enumerate(order, 1):
        r = cid_map.get(cid, {})
        rows.append({
            "exec_order":        i,
            "chunk_id":          cid,
            "parent_entity":     r.get("parent_entity", ""),
            "level":             r.get("level", ""),
            "code_sum":          r.get("code_sum", ""),
            "chunk_type":        r.get("chunk_type", ""),
            "depends_on_chunks": ", ".join(adj.get(cid, []))  or "(none)",
            "needed_by_chunks":  ", ".join(radj.get(cid, [])) or "(root — spec independently)",
            "methods_in_chunk":  r.get("methods_in_chunk", ""),
        })
    return pd.DataFrame(rows)


def build_hierarchy_json(chunks_df: pd.DataFrame, tree: dict) -> pd.DataFrame:
    import json as _json
    if chunks_df is None or chunks_df.empty:
        return pd.DataFrame(columns=[
            "chunk_id", "parent_entity", "level", "chunk_type", "code_sum",
            "call_flow", "child_chunk_refs", "used_by_chunks", "spec_action", "hierarchy_json",
        ])
    flat_tree = _build_flat_tree(tree)
    _GROUP_RE = re.compile(r'^(?:G|GRP|GROUP)[-_]?\d+', re.I)
    cid_map = {}
    for _, r in chunks_df.iterrows():
        cid              = str(r["chunk_id"]).strip()
        raw_methods      = parse_list(r.get("methods_in_chunk", ""))
        methods_list     = [t for t in raw_methods if t and not _GROUP_RE.match(t)]
        groups_list      = [t for t in raw_methods if t and     _GROUP_RE.match(t)]
        refs_raw         = str(r.get("child_chunk_refs", "") or "")
        child_refs_tokens = [x.strip() for x in refs_raw.split(",")
                             if x.strip() and x.strip() not in ("nan", "(none)", "")]
        cid_map[cid] = {
            "chunk_id":          cid,
            "parent_entity":     str(r.get("parent_entity", "")),
            "level":             int(r.get("level", 1)),
            "chunk_type":        str(r.get("chunk_type", "")),
            "code_sum":          int(r.get("code_sum", 0) or 0),
            "methods":           methods_list,
            "groups":            list(dict.fromkeys(groups_list)),
            "child_refs_tokens": child_refs_tokens,
        }
    entity_to_chunk = build_entity_to_chunk_id(chunks_df)
    for cid, d in cid_map.items():
        d["child_refs"] = list(dict.fromkeys(
            [entity_to_chunk[t] for t in d["child_refs_tokens"]
             if entity_to_chunk.get(t) and entity_to_chunk[t] in cid_map
             and entity_to_chunk[t] != cid]
        ))
    referenced_by = defaultdict(list)
    for cid, d in cid_map.items():
        for child_cid in d["child_refs"]:
            referenced_by[child_cid].append(cid)

    def _flow_for_chunk(cid):
        n  = cid_map.get(cid, {})
        fd = build_call_flow(n.get("parent_entity", ""), set(n.get("methods", [])), flat_tree)
        return compact_call_flow(fd) if fd else n.get("parent_entity", "")

    rows = []
    for cid, n in cid_map.items():
        children = n.get("child_refs", [])
        parents  = referenced_by.get(cid, [])
        flow_str = _flow_for_chunk(cid)
        hierarchy = {
            "chunk_id":         cid,
            "parent_entity":    n["parent_entity"],
            "level":            n["level"],
            "chunk_type":       n["chunk_type"],
            "code_sum":         n["code_sum"],
            "groups":           n["groups"],
            "call_flow":        flow_str,
            "child_chunk_refs": children,
            "used_by_chunks":   parents,
            "spec_action": (
                "GENERATE: build spec from raw YAML files only (leaf — no child specs)"
                if not children else
                f"CONSOLIDATE: inject {len(children)} child spec(s) + own files → produce merged spec"
            ),
            "call_flow_note": (
                "call_flow format: 'root -> {caller: {callee1, callee2}, leaf}'. "
                "Siblings in DFS order."
            ),
        }
        rows.append({
            "chunk_id":         cid,
            "parent_entity":    n["parent_entity"],
            "level":            n["level"],
            "chunk_type":       n["chunk_type"],
            "code_sum":         n["code_sum"],
            "call_flow":        flow_str,
            "child_chunk_refs": ", ".join(children) if children else "",
            "used_by_chunks":   ", ".join(parents)  if parents  else "",
            "spec_action":      hierarchy["spec_action"],
            "hierarchy_json":   _json.dumps(hierarchy, indent=2),
        })

    return pd.DataFrame(
        rows,
        columns=["chunk_id", "parent_entity", "level", "chunk_type", "code_sum",
                 "call_flow", "child_chunk_refs", "used_by_chunks",
                 "spec_action", "hierarchy_json"],
    ).sort_values(
        by=["level", "parent_entity", "chunk_id"], ascending=[False, True, True]
    ).reset_index(drop=True)


def ask(prompt: str, default: str = "") -> str:
    val = input(f"{prompt} [{default}]: ").strip()
    return val if val else default

# ── Main ─────────────────────────────────────────────────────────────────────
def chunks_formation(INPUT_PATH, program_or_process="program"):
    
    start_level = 0  # default: column 1

    if program_or_process.lower() == "process":
        level_inp = ask("Enter level (column number to act as parent)", "1")
        start_level = max(1, int(level_inp))

    default_chunk_limit = "6000"
    CHUNK_LIMIT = ask("Enter CHUNK_LIMIT (integer)", default_chunk_limit)
    CHUNK_LIMIT = int(CHUNK_LIMIT)
    global chunk_registry, chunk_id_counter, chunks_unique, chunk_usages
    global global_lines_by_name, chunked_subtrees

    df = load_branch_df_stepped(
        INPUT_PATH,
        SHEET_NAME,
        start_level=start_level
    )
    print(f"[INFO] Paths constructed: {len(df)}")
    print(df.to_string())

    chunk_registry.clear()
    chunks_unique.clear()
    chunk_usages.clear()
    global_lines_by_name.clear()
    chunked_subtrees.clear()
    chunk_id_counter = itertools.count(1)

    tree = build_tree(df)

    print("\n[INFO] Pass 1: Computing subtree totals...")
    for root_name, root_node in tree.items():
        compute_totals(root_name, root_node)

    print("[INFO] Pass 2: Assigning chunks (post-order)...")
    for root_name, root_node in tree.items():
        assign_chunks_top_down(root_name, root_node, 1, CHUNK_LIMIT,
                               ancestor_chunked=False, root_file=root_name)

    for root_name, root_node in tree.items():
        create_parent_reference_chunk(root_name, root_node, 1)

    final_unique, final_usages = _finalize_chunks(chunks_unique, chunk_usages)
    chunks_df = pd.DataFrame(final_unique)

    if not chunks_df.empty:
        if "parent_node" not in chunks_df.columns:
            chunks_df["parent_node"] = "Chunk under " + chunks_df.get("trigger_node", "")
        chunks_df["parent_entity"] = chunks_df["parent_node"].str.replace(
            "Chunk under ", "", regex=False)

    map_df = chunks_df[["chunk_id", "parent_entity", "level", "code_sum",
                         "methods_in_chunk", "groups"]].copy()
    map_df = map_df.sort_values(by=["parent_entity", "level", "code_sum"],
                                ascending=[True, True, False])

    child_graph_raw_df     = build_child_graph_raw(chunks_df)
    child_graph_pruned_df  = prune_edges(child_graph_raw_df)
    child_graph_reduced_df = transitive_reduction_safe(child_graph_pruned_df)

    child_graph_pruned_df  = _anchor_orphans_to_root(child_graph_pruned_df,  chunks_df, tree)
    child_graph_reduced_df = _anchor_orphans_to_root(child_graph_reduced_df, chunks_df, tree)

    reusability_edges_df   = compute_reusability_edges(child_graph_reduced_df)
    reusability_summary_df = compute_reusability_summary(child_graph_reduced_df)

    execution_order_df = build_execution_order(chunks_df,
                                               child_graph_df=child_graph_reduced_df)
    hierarchy_df       = build_hierarchy_json(chunks_df, tree)

    print("\n=== Parent_to_Chunks ===")
    print(map_df.to_string(index=False))
    print("\n=== Child_Graph_Reduced ===")
    print(child_graph_reduced_df.to_string(index=False))
    print("\n=== Execution_Order ===")
    print(execution_order_df[["exec_order", "chunk_id", "depends_on_chunks",
                               "needed_by_chunks"]].to_string(index=False))

    OUT_XLSX = os.path.splitext(INPUT_PATH)[0] + "_chunks_reusability.xlsx"
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        map_df.to_excel(writer,                 sheet_name="Parent_to_Chunks",     index=False)
        child_graph_reduced_df.to_excel(writer, sheet_name="Child_Graph_Reduced",  index=False)
        reusability_edges_df.to_excel(writer,   sheet_name="Reusability_Edges",    index=False)
        reusability_summary_df.to_excel(writer, sheet_name="Reusability_Summary",  index=False)
        execution_order_df.to_excel(writer,     sheet_name="Execution_Order",      index=False)
        hierarchy_df.to_excel(writer,           sheet_name="Hierarchy_JSON",       index=False)

    print(f"\n[DONE] Exported to: {OUT_XLSX}")
    return OUT_XLSX,program_or_process,CHUNK_LIMIT
