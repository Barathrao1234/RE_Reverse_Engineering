# ADD THESE:
print(f"[DEBUG] tree type={type(tree)}")
print(f"[DEBUG] tree.types={getattr(tree, 'types', 'NO_ATTR')}")
all_nodes = []
for path, node in tree:
    all_nodes.append(type(node).__name__)
print(f"[DEBUG] all node types in AST: {list(set(all_nodes))}")
print(f"[DEBUG] declaration_types_early={declaration_types_early}")
# print first 300 chars of raw_text to confirm it's actual Java source
print(f"[DEBUG] raw_text preview: {repr(raw_text[:300])}")
