#!/usr/bin/env python3
"""Fail when an LLM prompt embeds document text that did not pass through quarantine().

    python3 quarantine_check.py [--format text|github] PATH [PATH ...]

Exit code 1 when anything is flagged. Silence a reviewed line with `quarantine: ignore <reason>`.
"""

from __future__ import annotations

import argparse
import ast
import json
import pathlib
import re
import sys
from dataclasses import dataclass

LLM_CALL_SUFFIXES = (
    "messages.create",
    "messages.stream",
    "messages.batches.create",
    "chat.completions.create",
    "chat.completions.parse",
    "responses.create",
    "completions.create",
    "generate_content",
    "ChatCompletion.create",
    "litellm.completion",
    "invoke",
    "ainvoke",
)
JS_LLM_CALL = re.compile(
    r"messages\.(create|stream)\s*\(|chat\.completions\.(create|parse)\s*\(|responses\.create\s*\(|"
    r"\b(generateText|streamText|generateObject|streamObject|generateContent)\s*\(|api\.anthropic\.com|api\.openai\.com"
)
LLM_IMPORT = re.compile(r"^\s*(import|from)\s+(anthropic|openai|langchain\w*|litellm|google\.generativeai|claude_agent_sdk)\b", re.M)

# A value counts as outside content when its identifiers contain one of these words.
UNTRUSTED_WORDS = {
    "doc", "docs", "document", "documents", "text", "texts", "content", "contents", "policy", "policies",
    "evidence", "report", "reports", "file", "files", "pdf", "docx", "upload", "uploaded", "chunk", "chunks",
    "page", "pages", "body", "questionnaire", "transcript", "email", "emails", "mail", "readme", "excerpt",
    "excerpts", "context", "source", "sources", "finding", "findings", "answer", "answers", "response",
    "responses", "output", "outputs", "markdown", "html", "csv", "sheet", "row", "rows", "record", "records",
    "data", "code", "diff", "repo", "raw", "extracted", "parsed", "article", "articles", "feed", "attachment",
    "attachments", "contract", "invoice", "transcription", "ticket", "tickets", "issue", "issues", "notes",
    "bundle", "snippet", "snippets", "material", "materials",
}
# Serialised or file-read data dropped straight into a prompt, whatever the variable is called.
DATA_CALLS = ("json.dumps", "yaml.dump", "yaml.safe_dump", "to_json", "to_csv", "to_markdown", "to_string",
              "read_text", "read", "getvalue", "extract_text")
SAFE_CALLS = {"len", "int", "float", "round", "bool", "sum", "min", "max", "enumerate", "range", "type"}
PROMPT_NAME = re.compile(r"prompt|message|instruction|system|user|query|question|content|context|input|template|task", re.I)
PROMPT_KEYS = {"content", "text", "prompt", "system", "input", "instructions", "contents"}
IGNORE_MARK = "quarantine: ignore"
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "env", "dist", "build", "__pycache__", ".next", "site-packages",
    "tests", "test", "__tests__", "fixtures", "sample_data", "samples", ".saaf-quarantine",
}
TEST_FILE = re.compile(r"^test_.*\.py$|_test\.py$|\.(test|spec)\.[jt]sx?$")
# Long literals count as prompt templates only when they read like instructions to a model, not HTML or SQL.
PROMPT_WORDS = re.compile(
    r"\b(you are|you will|your task|respond|instructions?|analy[sz]e|assess|evaluate|summari[sz]e|extract|return (only )?(valid )?json)\b",
    re.I,
)
NOT_PROMPT = re.compile(r"<(div|html|table|tr|td|span|style|head|body|svg)\b|\b(SELECT|INSERT|UPDATE)\s|className=", re.I)
WRAPPER_NAME = re.compile(r"quarantine|untrusted|spotlight", re.I)
SKIP_FILES = {"saaf_quarantine.py", "quarantine_check.py"}


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    expression: str


def split_words(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+", text)}


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return dotted(node.func)
    return ""


def is_llm_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = dotted(node.func)
    return any(name == s or name.endswith("." + s) for s in LLM_CALL_SUFFIXES)


def is_quarantine_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and dotted(node.func).split(".")[-1] == "quarantine"


def identifiers(node: ast.AST) -> list[str]:
    found = []
    for part in ast.walk(node):
        if isinstance(part, ast.Name):
            found.append(part.id)
        elif isinstance(part, ast.Attribute):
            found.append(part.attr)
        elif isinstance(part, ast.Constant) and isinstance(part.value, str) and len(part.value) < 40:
            found.append(part.value)
    return found


def node_untrusted(node: ast.AST) -> bool:
    if isinstance(node, ast.Call) and dotted(node.func) in SAFE_CALLS:
        return False
    if isinstance(node, ast.Call) and dotted(node.func).split(".")[-1] in {d.split(".")[-1] for d in DATA_CALLS}:
        return not any(WRAPPER_NAME.search(n) for n in identifiers(node))
    names = identifiers(node)
    if any(WRAPPER_NAME.search(n) for n in names):
        return False
    return bool(split_words(" ".join(names)) & UNTRUSTED_WORDS)


def node_text(node: ast.AST) -> str:
    if hasattr(ast, "unparse"):
        return " ".join(ast.unparse(node).split())[:80]
    return ".".join(identifiers(node))[:80]


class PythonScanner:
    def __init__(self, source: str, path: str):
        self.path = path
        self.lines = source.splitlines()
        self.tree = ast.parse(source)
        self.parents: dict = {}
        for parent in ast.walk(self.tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent
        self.safe_names = {
            target.id
            for node in ast.walk(self.tree)
            if isinstance(node, ast.Assign) and is_quarantine_call(node.value)
            for target in node.targets
            if isinstance(target, ast.Name)
        }

    def calls_llm(self) -> bool:
        return any(is_llm_call(node) for node in ast.walk(self.tree))

    def hits(self) -> list[Hit]:
        found = []
        for container in ast.walk(self.tree):
            for value in self.__interpolated(container):
                if self.__flag(value, container):
                    # Positions of nodes inside f-strings are unreliable before Python 3.12: report the f-string's line.
                    line = container.lineno if isinstance(container, ast.JoinedStr) else value.lineno
                    found.append(Hit(self.path, line, node_text(value)))
        return found

    def __interpolated(self, node: ast.AST) -> list:
        if isinstance(node, ast.JoinedStr):
            if isinstance(self.parents.get(node), ast.JoinedStr) or not self.__prompt_like(node):
                return []
            return [v.value for v in node.values if isinstance(v, ast.FormattedValue)]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add) and self.__is_string(node):
            if isinstance(self.parents.get(node), ast.BinOp) or not self.__prompt_like(node):
                return []
            return [part for part in self.__flatten_add(node) if not self.__is_string(part)]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod) and self.__is_string(node.left):
            if not self.__prompt_like(node):
                return []
            return list(node.right.elts) if isinstance(node.right, ast.Tuple) else [node.right]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
            receiver = node.func.value
            if (self.__is_string(receiver) and self.__prompt_like(node)) or PROMPT_NAME.search(dotted(receiver)):
                return list(node.args) + [k.value for k in node.keywords]
        if isinstance(node, ast.Dict) and self.__is_message(node):
            return [
                v
                for k, v in zip(node.keys, node.values)
                if isinstance(k, ast.Constant) and k.value in PROMPT_KEYS and isinstance(v, (ast.Name, ast.Attribute, ast.Subscript, ast.Call))
            ]
        if is_llm_call(node):
            direct = list(node.args) + [k.value for k in node.keywords if k.arg in PROMPT_KEYS | {"messages"}]
            flat = []
            for arg in direct:
                if isinstance(arg, (ast.List, ast.Tuple)):
                    flat.extend(arg.elts)
                elif isinstance(arg, ast.Dict):
                    flat.extend(arg.values)
                elif isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Add):
                    flat.extend(self.__flatten_add(arg))
                else:
                    flat.append(arg)
            return [a for a in flat if isinstance(a, (ast.Name, ast.Attribute, ast.Subscript, ast.Call))]
        return []

    def __flag(self, value: ast.AST, container: ast.AST) -> bool:
        if is_quarantine_call(value) or (isinstance(value, ast.Name) and value.id in self.safe_names):
            return False
        if self.__inside_quarantine(container):
            return False
        line = self.lines[container.lineno - 1] if 0 < container.lineno <= len(self.lines) else ""
        return IGNORE_MARK not in line and node_untrusted(value)

    def __prompt_like(self, node: ast.AST) -> bool:
        literal = "".join(
            part.value for part in ast.walk(node) if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
        if len(literal) >= 200 and "\n" in literal and PROMPT_WORDS.search(literal) and not NOT_PROMPT.search(literal):
            return True
        current = node
        while current in self.parents:
            parent = self.parents[current]
            if is_llm_call(parent):
                return True
            if isinstance(parent, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = parent.targets if isinstance(parent, ast.Assign) else [parent.target]
                return any(PROMPT_NAME.search(dotted(t) or "") for t in targets)
            if isinstance(parent, ast.Dict):
                return any(
                    isinstance(k, ast.Constant) and k.value in PROMPT_KEYS and v is current
                    for k, v in zip(parent.keys, parent.values)
                )
            if isinstance(parent, ast.keyword):
                return bool(PROMPT_NAME.search(parent.arg or ""))
            if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute):
                if parent.func.attr == "join" or (
                    parent.func.attr in {"append", "extend"} and PROMPT_NAME.search(dotted(parent.func.value))
                ):
                    if parent.func.attr != "join":
                        return True
                    current = parent
                    continue
            if isinstance(parent, ast.Return):
                return bool(PROMPT_NAME.search(self.__enclosing_function(parent)))
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module, ast.Expr)):
                return False
            current = parent
        return False

    def __enclosing_function(self, node: ast.AST) -> str:
        while node in self.parents:
            node = self.parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return ""

    def __inside_quarantine(self, node: ast.AST) -> bool:
        while node in self.parents:
            node = self.parents[node]
            if is_quarantine_call(node):
                return True
        return False

    def __is_message(self, node: ast.Dict) -> bool:
        keys = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
        role = keys.get("role")
        if isinstance(role, ast.Constant) and role.value == "assistant":
            return False
        return "role" in keys or "type" in keys

    def __is_string(self, node: ast.AST) -> bool:
        if isinstance(node, ast.JoinedStr) or (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            return True
        return isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add) and (
            self.__is_string(node.left) or self.__is_string(node.right)
        )

    def __flatten_add(self, node: ast.AST) -> list:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self.__flatten_add(node.left) + self.__flatten_add(node.right)
        return [node]


def python_scanners(path: pathlib.Path, label: str) -> list[PythonScanner]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix != ".ipynb":
        sources = [(label, text)]
    else:
        try:
            cells = json.loads(text).get("cells", [])
        except (json.JSONDecodeError, AttributeError):
            return []
        sources = [
            (f"{label}#cell{n}", "\n".join("" if l.lstrip().startswith(("%", "!")) else l for l in "".join(c.get("source", [])).splitlines()))
            for n, c in enumerate(cells, start=1)
            if c.get("cell_type") == "code"
        ]
    scanners = []
    for name, source in sources:
        try:
            scanners.append(PythonScanner(source, name))
        except (SyntaxError, ValueError):
            print(f"{name}: skipped, does not parse", file=sys.stderr)
    return scanners


TEMPLATE_LITERAL = re.compile(r"`((?:[^`\\]|\\.)*)`", re.DOTALL)
PLACEHOLDER = re.compile(r"\$\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}")
JS_PROMPT_ASSIGN = re.compile(r"(prompt|system|content|message|instruction|input|text|query)\w*\s*[:=]\s*$", re.I)
JS_CONTENT_PROPERTY = re.compile(r"\b(content|prompt|system|input)\s*:\s*([A-Za-z_$][\w$.]*(?:\[[^\]]+\])?)\s*[,}\n]")
JS_SAFE = re.compile(r"\.(length|size)\s*$|^\s*(String|Number)\(")


def script_hits(path: pathlib.Path, label: str) -> list[Hit]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    found = []

    def add(position: int, expression: str) -> None:
        line = text.count("\n", 0, position) + 1
        if IGNORE_MARK in lines[line - 1] or WRAPPER_NAME.search(expression) or JS_SAFE.search(expression):
            return
        if split_words(expression) & UNTRUSTED_WORDS:
            found.append(Hit(label, line, " ".join(expression.split())[:80]))

    for literal in TEMPLATE_LITERAL.finditer(text):
        before = text[max(0, literal.start() - 80) : literal.start()]
        body = literal.group(1)
        long_prompt = len(body) >= 200 and "\n" in body and PROMPT_WORDS.search(body) and not NOT_PROMPT.search(body)
        if not (JS_PROMPT_ASSIGN.search(before) or long_prompt):
            continue
        for placeholder in PLACEHOLDER.finditer(body):
            add(literal.start(1) + placeholder.start(1), placeholder.group(1))
    for prop in JS_CONTENT_PROPERTY.finditer(text):
        add(prop.start(2), prop.group(2))
    return found


PYTHON_SUFFIXES = {".py", ".ipynb"}
SCRIPT_SUFFIXES = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"}


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def scan(root: pathlib.Path) -> list[Hit]:
    files = []
    for path in [root] if root.is_file() else sorted(root.rglob("*")):
        relative = path.relative_to(root) if root.is_dir() else pathlib.Path(path.name)
        skipped = path.name in SKIP_FILES or TEST_FILE.search(path.name) or SKIP_DIRS & set(relative.parts)
        if path.is_file() and not skipped:
            if path.suffix in PYTHON_SUFFIXES | SCRIPT_SUFFIXES:
                files.append((path, str(relative) if root.is_dir() else str(path)))
    python = [s for path, label in files if path.suffix in PYTHON_SUFFIXES for s in python_scanners(path, label)]
    scripts = [(path, label) for path, label in files if path.suffix in SCRIPT_SUFFIXES]
    # Agents often build prompts in one module and call the model through a shared client in another,
    # so a repository that calls an LLM anywhere has all of its prompt-building code checked.
    calls_llm = (
        any(s.calls_llm() for s in python)
        or any(JS_LLM_CALL.search(read(p)) for p, _ in scripts)
        or any(LLM_IMPORT.search(read(p)) for p, _ in files if p.suffix == ".py")
    )
    if not calls_llm:
        return []
    found = [hit for s in python for hit in s.hits()]
    found += [hit for path, label in scripts for hit in script_hits(path, label)]
    return sorted(set(found), key=lambda h: (h.path, h.line, h.expression))


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("paths", nargs="+", type=pathlib.Path)
    parser.add_argument("--format", choices=["text", "github"], default="text")
    args = parser.parse_args()
    hits = [hit for root in args.paths for hit in scan(root)]
    for hit in hits:
        message = f"'{hit.expression}' reaches an LLM prompt without quarantine()"
        if args.format == "github":
            print(f"::error file={hit.path},line={hit.line},title=saaf-quarantine::{message}")
        else:
            print(f"{hit.path}:{hit.line}: {message}")
    print(f"saaf-quarantine: {len(hits)} unquarantined prompt input(s)", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
