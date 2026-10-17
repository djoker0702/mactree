"""Serialize a scanned :class:`~mactree.scanner.Node` tree into compact JSON.

Scans can produce millions of nodes. Sending all of them to the browser would be
slow to transfer and slow for D3 to lay out. We prune the tree for the UI by:

- collapsing nodes smaller than a fraction of their parent into a synthetic
  "<small files>" bucket, and
- limiting recursion depth.

The frontend can request a subtree starting at any path to "zoom in" with full
detail, so pruning only affects the initial payload.
"""

from __future__ import annotations

from typing import Optional

from .scanner import Node

# A node contributing less than this fraction of its parent's size is bucketed.
MIN_FRACTION = 0.005
# Maximum children kept per directory (largest N); the rest get bucketed.
MAX_CHILDREN = 60
# Maximum depth of the serialized tree.
MAX_DEPTH = 8


def serialize(
    node: Node,
    depth: int = 0,
    max_depth: int = MAX_DEPTH,
    min_fraction: float = MIN_FRACTION,
    max_children: int = MAX_CHILDREN,
) -> dict:
    """Convert ``node`` into a nested dict suitable for D3 hierarchies."""
    out: dict = {
        "name": node.name,
        "path": node.path,
        "size": node.size,
        "isDir": node.is_dir,
    }

    if not node.is_dir or not node.children:
        return out

    if depth >= max_depth:
        # Collapse everything below into a leaf representing the subtree size.
        out["hasChildren"] = True
        return out

    # Sort children largest-first.
    children = sorted(node.children, key=lambda c: c.size, reverse=True)

    threshold = node.size * min_fraction if node.size else 0
    kept: list[Node] = []
    small: list[Node] = []
    for i, child in enumerate(children):
        if i < max_children and child.size >= threshold:
            kept.append(child)
        else:
            small.append(child)

    serialized_children = [
        serialize(c, depth + 1, max_depth, min_fraction, max_children) for c in kept
    ]

    if small:
        small_total = sum(c.size for c in small)
        if small_total > 0:
            serialized_children.append(
                {
                    "name": f"<{len(small)} small items>",
                    "path": node.path,
                    "size": small_total,
                    "isDir": False,
                    "bucket": True,
                }
            )

    if serialized_children:
        out["children"] = serialized_children
    return out


def find_node(root: Node, target_path: str) -> Optional[Node]:
    """Locate the node with ``path == target_path`` within ``root`` (DFS)."""
    if root.path == target_path:
        return root
    stack = list(root.children)
    while stack:
        n = stack.pop()
        if n.path == target_path:
            return n
        if n.is_dir:
            stack.extend(n.children)
    return None
