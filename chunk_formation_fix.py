import pandas as pd
import html
import itertools
from pathlib import Path
import re
import os
import unicodedata
from collections import defaultdict, deque, OrderedDict
import numpy as np
from datetime import datetime

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
METHOD_PATTERN = re.compile(r'.*[^.]+\.[^.\s]+')


def is_method(name: str) -> bool:
    if not name:
        return False
    return bool(METHOD_PATTERN.match(str(name).strip()))


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
    if not isinstance(name, str):
        name = str(name)
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
        # FIXED: use child's full_entity as the chunked_subtrees key so the guard
        # is consistent with create_parent_reference_chunk which also uses full_entity.
        child_full = child_node["filenames"][0] if child_node.get("filenames") else child_name
        if (trigger_node, child_full) in chunked_subtrees or (trigger_node, child_name) in chunked_subtrees:
            continue

        child_unique_total = child_node.get(
            "unique_total_lines", child_node.get("total_lines", child_node["lines"])
        )

        # ── LARGE child ──────────────────────────────────────────────────────
        if child_unique_total >= CHUNK_LIMIT:
            child_refs_in_current.append(child_full)
            have_items = True
            chunked_subtrees.add((trigger_node, child_full))
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
            child_refs_in_current.append(child_full)
        # FIXED: no else -> chunked_subtrees; empty children are not marked as
        # done so the parent reference pass can still recover their methods.

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
    # FIXED 1: no longer gated on total_unique >= CHUNK_LIMIT — always chunk.
    # FIXED 2: pass full_entity (filenames[0]) as trigger_node, not raw node_name.
    #   node_name is the bare segment key (e.g. "MyClass" in multi-column input)
    #   which has no dot, so is_method() returns False and the trigger is never
    #   inserted into methods_out inside _flush_chunk.
    #   filenames[0] holds the fully-qualified token so is_method() passes correctly.
    if node["children"]:
        trigger = node["filenames"][0] if node.get("filenames") else node_name
        create_chunks_for_children(
            node["children"], level + 1, trigger, CHUNK_LIMIT,
            root_file=root_file
        )


def create_parent_reference_chunk(node_name, node, level, CHUNK_LIMIT):
    """
    Bin-packed version: unresolved child subtrees are packed into one or more
    parent_ref chunks bounded by CHUNK_LIMIT, instead of a single unbounded
    chunk. A child whose subtree alone exceeds CHUNK_LIMIT gets flushed into
    its own bin right after being added, so it never drags siblings over.
    """
    global chunked_subtrees

    full_trigger = node["filenames"][0] if node.get("filenames") else node_name

    seed_methods = [full_trigger] if is_method(full_trigger) else []
    seed_groups  = [full_trigger] if is_group(full_trigger) else []

    bins = []

    def _new_bin():
        return {"methods": [], "methods_set": set(),
                "groups": [], "groups_set": set(),
                "sum": 0, "child_refs": []}

    current = _new_bin()
    for m in seed_methods:
        if m not in current["methods_set"]:
            current["methods"].append(m)
            current["methods_set"].add(m)
            current["sum"] += global_lines_by_name.get(m, 0)
    for g in seed_groups:
        if g not in current["groups_set"]:
            current["groups"].append(g)
            current["groups_set"].add(g)

    def _commit(bin_):
        if bin_["methods"] or bin_["groups"]:
            bins.append(bin_)

    for child_name, child_node in node["children"].items():
        child_trigger = child_node["filenames"][0] if child_node.get("filenames") else child_name
        if (full_trigger, child_trigger) in chunked_subtrees or (node_name, child_name) in chunked_subtrees:
            current["child_refs"].append(child_trigger)
            continue

        child_entities = list(iter_entities_dfs(child_node))
        child_methods  = [e for e in child_entities if is_method(e)]
        child_groups   = [e for e in child_entities if is_group(e)]

        new_methods = [m for m in child_methods if m not in current["methods_set"]]
        incremental = sum(global_lines_by_name.get(m, 0) for m in new_methods)

        # Flush current bin first if adding this child would overflow it.
        if (current["methods"] or current["groups"]) and (current["sum"] + incremental) > CHUNK_LIMIT:
            _commit(current)
            current = _new_bin()
            new_methods = child_methods
            incremental = sum(global_lines_by_name.get(m, 0) for m in new_methods)

        for m in child_methods:
            if m not in current["methods_set"]:
                current["methods"].append(m)
                current["methods_set"].add(m)
        for g in child_groups:
            if g not in current["groups_set"]:
                current["groups"].append(g)
                current["groups_set"].add(g)
        current["sum"] += incremental
        current["child_refs"].append(child_trigger)

        # A single child alone can exceed CHUNK_LIMIT; flush immediately so
        # it never absorbs further siblings into the same bin.
        if current["sum"] > CHUNK_LIMIT:
            _commit(current)
            current = _new_bin()

    _commit(current)

    for bin_ in bins:
        register_or_get_chunk_id(
            filenames_ordered=bin_["methods"],
            code_sum=bin_["sum"],
            row_indices_ordered=bin_["methods"],
            trigger_node=full_trigger,
            level=level,
            trigger_node_name=f"{full_trigger}_PARENT_ONLY",
            child_chunk_refs=", ".join(bin_["child_refs"]),
            chunk_type="parent_ref",
            groups_ordered=bin_["groups"],
        )


def compute_totals(node_name, node):
    total_structural = node["lines"]
    unique_methods   = set()
    # FIXED: use the full_entity stored in filenames (not the raw node_name key)
    # so that global_lines_by_name lookups resolve correctly for full-path tokens.
    for fname in node.get("filenames", []):
        if is_method(fname):
            unique_methods.add(fname)
    for child_name, child_node in node["children"].items():
        child_structural = compute_totals(child_name, child_node)
        total_structural += child_structural
        unique_methods |= child_node.get("unique_methods", set())
    node["total_lines"]        = total_structural
    node["unique_methods"]     = unique_methods
    node["unique_total_lines"] = sum(
        global_lines_by_name.get(m, 0) for m in unique_methods if is_method(m)
    )
    return total_structural


def _finalize_chunks(chunks_unique_list, chunk_usages_list):
    nodes_with_parent_ref = {
        c.get("trigger_node") for c in chunks_unique_list if c.get("chunk_type") == "parent_ref"
    }
    def keep_record(rec):
        return not (
            rec.get("chunk_type") == "leaf" and
            rec.get("trigger_node") in nodes_with_parent_ref
        )
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
        # Build accumulated full path so leaf key == "full_path.method"
        accumulated_segments = []
        for seg in segments:
            name, lines, _ = extract_name_lines(seg)
            if name:
                accumulated_segments.append(name)
                # Use the last two parts joined by "." as the node key when the
                # final segment already contains a dot (i.e. is a method token).
                # For intermediate path segments (no dot), use the name itself.
                # The full identity stored in filenames is the raw name as parsed.
                node_key = name  # tree key stays as the parsed segment name
                if node_key not in current:
                    current[node_key] = {"lines": lines, "children": OrderedDict(),
                                         "rows": set(), "filenames": [name]}
                current[node_key]["lines"] = max(current[node_key]["lines"], lines)
                current[node_key]["rows"].add(idx)
                # Store full accumulated path for leaf method nodes so that
                # iter_entities_dfs returns the complete "full_path.method" token.
                # If the segment itself already contains a dot it IS the full token
                # (e.g. "MyClass.myMethod" or "C:\path\MyClass.method") — use as-is.
                # Otherwise join the accumulated path with dots.
                if "." in name:
                    full_entity = name          # already the full qualified token
                elif len(accumulated_segments) > 1:
                    full_entity = ".".join(accumulated_segments)
                else:
                    full_entity = name
                if full_entity not in current[node_key]["filenames"]:
                    current[node_key]["filenames"] = [full_entity]
                global_lines_by_name[full_entity] = max(
                    global_lines_by_name.get(full_entity, 0),
                    lines if not is_group(name) else 0
                )
                # Also keep bare name mapped for fallback lookups
                if name != full_entity:
                    global_lines_by_name[name] = max(
                        global_lines_by_name.get(name, 0),
                        lines if not is_group(name) else 0
                    )
                current = current[node_key]["children"]
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

def log_time(message):
    with open("execution_log_service.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} - {message}\n")


# ── Main ─────────────────────────────────────────────────────────────────────
def chunks_formation(INPUT_PATH, program_or_process="program"):
    start_time = datetime.now()
    log_time(f"Chunk Formation START")
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
        create_parent_reference_chunk(root_name, root_node, 1, CHUNK_LIMIT)

    final_unique, final_usages = _finalize_chunks(chunks_unique, chunk_usages)
    chunks_df = pd.DataFrame(final_unique)

    print(f"\n[INFO] Total chunks formed: {len(chunks_df)}")
    if not chunks_df.empty:
        print(f"[INFO] code_sum per chunk:")
        for _, r in chunks_df[["chunk_id", "code_sum"]].iterrows():
            print(f"    {r['chunk_id']}: {r['code_sum']}")
        print(f"[INFO] Total code_sum across all chunks: {chunks_df['code_sum'].sum()}")
        print(f"[INFO] Max code_sum among chunks: {chunks_df['code_sum'].max()} "
              f"(CHUNK_LIMIT = {CHUNK_LIMIT})")


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

    end_time = datetime.now()

    elapsed = (end_time - start_time).total_seconds()
    log_time(
        f"Chunk Formation END | "
        f"Duration={elapsed:.3f} sec"
    )
    return OUT_XLSX,program_or_process,CHUNK_LIMIT
