from pathlib import Path
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

# Named roots; add more entries as needed
ROOTS = {
    "champsim/src": (Path(__file__).resolve().parents[1] / "ChampSim" / "src").resolve(),
    "champsim/inc": (Path(__file__).resolve().parents[1] / "ChampSim" / "inc").resolve(),
    # Add more roots here if needed, e.g. "prefetchers": Path(...).resolve(),
}


def _resolve_root(alias: str | None = None) -> Path:
    """Return the configured root path for an alias."""
    if alias is None:
        raise ValueError("No root alias provided.")
    try:
        return ROOTS[alias]
    except KeyError:
        raise ValueError(f"Unknown root alias '{alias}'. Available: {', '.join(ROOTS)}") from None


def _find_roots_for_path(rel_path: str, must_be_dir: bool) -> list[tuple[str, Path]]:
    """Return list of (alias, resolved_path) that contain the given relative path."""
    matches: list[tuple[str, Path]] = []
    for alias, root in ROOTS.items():
        candidate = (root / rel_path).resolve()
        if root not in candidate.parents and candidate != root:
            continue
        if must_be_dir and candidate.is_dir():
            matches.append((alias, candidate))
        elif not must_be_dir and candidate.is_file():
            matches.append((alias, candidate))
    return matches

@tool
def list_files(rel_path: str = ".", root_alias: str | None = None) -> str:
    """List files. If no root_alias, will search all roots for the path."""
    if rel_path in ("", ".") and root_alias is None:
        # List top-level of all roots
        sections = []
        for alias, root in ROOTS.items():
            try:
                items = [p.relative_to(root).as_posix() for p in root.iterdir()]
                sections.append(f"[{alias}]\n" + "\n".join(sorted(items)))
            except FileNotFoundError:
                sections.append(f"[{alias}]\n(root missing)")
        return "\n\n".join(sections)

    if root_alias:
        root = _resolve_root(root_alias)
        base = (root / rel_path).resolve()
        if root not in base.parents and base != root:
            return "Path outside root."
        if not base.exists():
            return "Path not found."
        if not base.is_dir():
            return "Not a directory."
        items = [p.relative_to(root).as_posix() for p in base.iterdir()]
        return "\n".join(sorted(items))

    matches = _find_roots_for_path(rel_path, must_be_dir=True)
    if not matches:
        return "Path not found in any root."
    if len(matches) > 1:
        choices = ", ".join(a for a, _ in matches)
        return f"Ambiguous path. Found in: {choices}. Specify root_alias."
    alias, base = matches[0]
    items = [p.relative_to(ROOTS[alias]).as_posix() for p in base.iterdir()]
    return "\n".join(sorted(items))

@tool
def read_file(rel_path: str, root_alias: str | None = None) -> str:
    """Read a file. If no root_alias, will search all roots for the path."""
    if root_alias:
        root = _resolve_root(root_alias)
        target = (root / rel_path).resolve()
        if root not in target.parents and target != root:
            return "Path outside root."
        if not target.is_file():
            return "Not a file."
        return target.read_text()

    matches = _find_roots_for_path(rel_path, must_be_dir=False)
    if not matches:
        return "File not found in any root."
    if len(matches) > 1:
        choices = ", ".join(a for a, _ in matches)
        return f"Ambiguous file. Found in: {choices}. Specify root_alias."
    alias, target = matches[0]
    return target.read_text()

tools = [list_files, read_file]

SYSTEM_PROMPT = (
    "You are a helpful coding assistant. "
    "Use the list_files and read_file tools as needed to help the user understand the codebase. "
    "Available roots: "
    + ", ".join(ROOTS.keys())
    + ". If a path exists in multiple roots, disambiguate with root_alias."
)

# Create an agent graph that loops model/tool calls until the model stops
llm = ChatOpenAI(model="gpt-4o-mini")
agent = create_agent(model=llm, tools=tools, system_prompt=SYSTEM_PROMPT)

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run the coding assistant agent.")
    parser.add_argument(
        "query",
        nargs="*",
        help="What you want the agent to do. If omitted, you'll be prompted.",
    )
    args = parser.parse_args()

    user_input = " ".join(args.query).strip()
    if not user_input:
        user_input = input("Enter your request: ").strip()
    if not user_input:
        raise SystemExit("No input provided.")

    result = agent.invoke({"messages": [{"role": "user", "content": user_input}]})

    messages = result.get("messages", [])
    if messages:
        print(messages[-1].content)
    else:
        print("No response messages returned.")
