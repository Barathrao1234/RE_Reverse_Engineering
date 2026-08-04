
import re,os
import pandas as pd
from collections import defaultdict

# -----------------------------
# Config C:\Users\2860112\Downloads\macs_2.0.0\macs_2.0.0\adc_analysis\chunks_macs.xlsx
OUT_XLSX = r"plsql_10_4_calls_2500_latest.xlsx"

# NEW: groups mapping input


# -----------------------------
# Parsers for your token format: "X.Y no_of_lines : 12" or "Nil"
NO_OF_LINES_RE = re.compile(r"^(?P<name>.*?)\s+no_of_lines\s*:\s*(?P<loc>\d+|Nil|None)\s*$", re.IGNORECASE)

CHUNK_ID_RE = re.compile(r"^C\d{3}_.+")  # example: C105_AsServiceupdateTxLog
GROUP_ID_RE = re.compile(r"^(?:G|GRP|GROUP)[-_]?\d+(?:\.\d+)?$", re.IGNORECASE)

def is_chunk_id(token: str) -> bool:
    return bool(token) and bool(CHUNK_ID_RE.match(str(token).strip()))

def is_group_id(token: str) -> bool:
    return bool(token) and bool(GROUP_ID_RE.match(str(token).strip()))

def split_tokens(cell):
    """Split comma-separated filenames string into list of tokens (strings)."""
    if pd.isna(cell):
        return []
    return [t.strip() for t in str(cell).split(",") if t.strip()]

def parse_method_token(token):
    """
    Returns tuple: (kind, name, loc, raw)
      kind: "method" | "chunk" | "other"
      name: normalized method name or chunk id
      loc: int (0 for Nil/None) for methods; None for chunks
      raw: original token
    """
    if not token:
        return ("other", "", None, token)

    if CHUNK_ID_RE.match(token):
        return ("chunk", token.strip(), None, token)

    m = NO_OF_LINES_RE.match(token)
    if m:
        name = m.group("name").strip()
        loc_raw = m.group("loc").strip().lower()
        if loc_raw in ("nil", "none"):
            loc = 0
        else:
            loc = int(loc_raw)
        return ("method", name, loc, token)

    # fallback: treat as method name but loc unknown => 0
    return ("method", token.strip(), 0, token)

def format_method(name, loc):
    """Format back to 'name no_of_lines : X|Nil'."""
    if loc is None or int(loc) == 0:
        return f"{name} no_of_lines : Nil"
    return f"{name} no_of_lines : {int(loc)}"

# -----------------------------
def build_methods_index(parent_df):
    """
    Build:
      chunk_id -> set(method_names)  (for removal)
      chunk_id -> dict(method_name -> loc) (for loc lookup if needed)
    """
    methods_set = {}
    methods_loc = {}

    for _, r in parent_df.iterrows():
        cid = str(r["chunk_id"]).strip()
        tokens = split_tokens(r.get("methods_in_chunk", ""))
        mset = set()
        mloc = {}

        for tok in tokens:
            kind, name, loc, _ = parse_method_token(tok)
            if kind == "method" and name:
                mset.add(name)
                prev = mloc.get(name, 0)
                mloc[name] = max(prev, loc or 0)

        methods_set[cid] = mset
        methods_loc[cid] = mloc

    return methods_set, methods_loc

def build_children_map(graph_df):
    """
    Build:
      parent_chunk_id -> list of (child_chunk_id, child_entity)
    Skip blank child ids (isolated rows).
    """
    from collections import defaultdict
    children = defaultdict(list)

    for _, r in graph_df.iterrows():
        p = str(r.get("parent_chunk_id", "")).strip()
        c = str(r.get("child_chunk_id", "")).strip()
        ce = str(r.get("child_entity", "")).strip()

        if not p:
            continue
        if not c:
            continue  # isolated placeholder
        children[p].append((c, ce))

    return children

def update_parent_row(cid, filenames_cell, children_list, child_methods_by_chunk):
    """
    Apply the transformation for a single parent chunk:

    - remove methods that appear inside any child chunk's methods_in_chunk  
    - compute remaining loc from remaining methods  
    - keep order stable; DO NOT put chunk ids in methods_in_chunk  
    - KEEP group ids in methods_in_chunk but EXCLUDE their LOC from clear_code_sum  

    NOTE: child_entity is metadata (the trigger/parent name stored on the child  
    chunk record). It is NOT a method inside the child's methods_in_chunk, so it  
    must NOT be used to suppress tokens from the parent. Only methods actually  
    present in a child chunk's methods_in_chunk are removed here.  
    """  
    tokens = split_tokens(filenames_cell)  

    # Union of method names that live inside child chunks — these are genuinely  
    # moved down and should be removed from the parent's methods_in_chunk.  
    remove_methods = set()  
    for child_cid, _ in children_list:  
        remove_methods |= child_methods_by_chunk.get(child_cid, set())  

    out_method_tokens = []  
    remaining_loc = 0  

    for tok in tokens:  
        # Drop any stale chunk-id tokens that may have crept into methods_in_chunk  
        if is_chunk_id(tok):  
            continue  

        kind, name, loc, raw = parse_method_token(tok)  

        if kind == "chunk":  
            continue  # defensive — already caught above  

        if kind == "method":  
            # Remove only methods that are genuinely present inside a child chunk  
            if name in remove_methods:  
                continue  

            out_method_tokens.append(format_method(name, loc))  

            # Exclude groups from the LOC sum  
            if not is_group_id(name):  
                remaining_loc += (loc or 0)  
            continue  

        # fallback: include as-is  
        out_method_tokens.append(raw)  

    return ", ".join(out_method_tokens), remaining_loc, sorted(remove_methods)

def find_top_parent_chunks(parent_df, graph_df):
    """
    Top parent chunks = chunks that never appear as child_chunk_id
    """
    all_chunks = set(
        parent_df["chunk_id"]
        .dropna()
        .astype(str)
        .str.strip()
    )

    child_chunks = set(
        graph_df["child_chunk_id"]
        .dropna()
        .astype(str)
        .str.strip()
    )

    top_parents = sorted(all_chunks - child_chunks)
    return top_parents

# -----------------------------
def llm_input_sheet(CHUNK_EXCEL,PROCEDURE_LINEAGE_EXCEL,OUTPUT_PATH,EXCEL_REPORT,DEMERGED_FLOW,PARENT_SHEET,GRAPH_SHEET,GROUPS_SHEET):
    # Read main inputs
    parent_df = pd.read_excel(CHUNK_EXCEL, sheet_name=PARENT_SHEET, engine="openpyxl")
    graph_df  = pd.read_excel(CHUNK_EXCEL, sheet_name=GRAPH_SHEET, engine="openpyxl")
    top_parent_chunks = find_top_parent_chunks(parent_df, graph_df)

    print(f"[OK] Top parent chunks found: {len(top_parent_chunks)}")
    print(top_parent_chunks[:10])  # sample
    
    # Validate required columns
    for col in ["chunk_id", "methods_in_chunk"]:
        if col not in parent_df.columns:
            raise ValueError(f"Parent sheet missing required column: {col}")
    for col in ["parent_chunk_id", "child_chunk_id", "child_entity"]:
        if col not in graph_df.columns:
            raise ValueError(f"Graph sheet missing required column: {col}")

    # Build indices for removal and child mapping
    child_methods_by_chunk, _ = build_methods_index(parent_df)
    children_map = build_children_map(graph_df)

    updated_rows = []
    debug_rows = []

    for _, r in parent_df.iterrows():
        cid = str(r["chunk_id"]).strip()
        orig = r.get("methods_in_chunk", "")
        children_list = children_map.get(cid, [])

        if children_list:
            new_filenames, remaining_loc, removed = update_parent_row(
                cid=cid,
                filenames_cell=orig,
                children_list=children_list,
                child_methods_by_chunk=child_methods_by_chunk
            )
        else:
            # Keep group IDs in methods_in_chunk but exclude their LOC from sum
            tokens = split_tokens(orig)
            out_method_tokens = []
            remaining_loc = 0
            for tok in tokens:
                if is_chunk_id(tok):
                    continue
                kind, name, loc, _ = parse_method_token(tok)
                if kind == "method":
                    out_method_tokens.append(format_method(name, loc))
                    if not is_group_id(name):
                        remaining_loc += (loc or 0)
                # ignore non-method tokens in methods_in_chunk
            new_filenames = ", ".join(out_method_tokens)
            removed = []

        out = dict(r)
        out["methods_in_chunk"] = new_filenames
        out["clear_code_sum"] = remaining_loc
        out["direct_children"] = ", ".join(name for name, _ in children_list)
        out["descendants_count"] = len(children_list)

        updated_rows.append(out)

        if children_list:
            debug_rows.append({
                "chunk_id": cid,
                "child_chunks": ", ".join([c for c, _ in children_list]),
                "removed_methods_count": len(removed),
                "removed_methods_sample": ", ".join(removed[:30])
            })

    updated_df = pd.DataFrame(updated_rows)
    debug_df = pd.DataFrame(debug_rows)

    # NEW: Read groups mapping (sheet1_group_mapping) from separate file
    group_map_df = None
    try:
        group_map_df = pd.read_excel(DEMERGED_FLOW, sheet_name=GROUPS_SHEET, engine="openpyxl")
        # Optional: trim columns, strip whitespace
        group_map_df.columns = [str(c).strip() for c in group_map_df.columns]
        print(f"[OK] Loaded groups mapping: {DEMERGED_FLOW} [{GROUPS_SHEET}] rows={len(group_map_df)}")
    except Exception as e:
        print(f"[WARN] Could not load groups mapping from '{DEMERGED_FLOW}' sheet '{GROUPS_SHEET}': {e}")
        group_map_df = None

    
    top_parent_df = pd.DataFrame({
        "top_parent_chunk_id": top_parent_chunks
    })

    # Write output with all sheets

    OUT_XLSX = os.path.join(OUTPUT_PATH,EXCEL_REPORT)
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as writer:
        updated_df.to_excel(writer, sheet_name="Parent_to_Chunks_Updated", index=False)
        if not debug_df.empty:
            debug_df.to_excel(writer, sheet_name="Debug_Removals", index=False)
        graph_df.to_excel(writer, sheet_name="Child_Graph_Reduced", index=False)

        # NEW: add group_mappings sheet if available
        if group_map_df is not None:
            group_map_df.to_excel(writer, sheet_name="group_mappings", index=False)

        top_parent_df.to_excel(writer, sheet_name="Top_Parent_Chunks", index=False)

    print(f"[OK] Written: {OUT_XLSX}")
    if group_map_df is not None:
        print("     Sheets: Parent_to_Chunks_Updated, Debug_Removals, Child_Graph_Reduced, group_mappings")
    else:
        print("     Sheets: Parent_to_Chunks_Updated, Debug_Removals, Child_Graph_Reduced")

    return OUT_XLSX

