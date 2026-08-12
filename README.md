if node["children"]:
    trigger = node["filenames"][0] if node.get("filenames") else node_name
    node_unique = node.get("unique_total_lines", node.get("total_lines", 0))
    
    # Only split into chunks if this subtree's total LOC exceeds the limit.
    # If the whole subtree fits in one chunk, let the parent absorb it — 
    # create_chunks_for_children at the parent level will inline everything.
    if node_unique >= CHUNK_LIMIT:
        create_chunks_for_children(
            node["children"], level + 1, trigger, CHUNK_LIMIT,
            root_file=root_file
        )
