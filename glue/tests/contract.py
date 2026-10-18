"""The console's JSON contract, read from glue/console-ui/src/lib/api/types.ts.

The React pages are written against types.ts; this module reads its interfaces and type aliases and
checks that what the back end sends matches them: every field present, no field the pages do not know,
and values of the right kind. Used by the unit tests and by the lab checks against the real tools.

    from contract import Contract
    problems = Contract.load().check(answer, "Overview")    # [] when it matches
"""

from __future__ import annotations

import re
from pathlib import Path

TYPES = Path(__file__).resolve().parent.parent / "console-ui" / "src" / "lib" / "api" / "types.ts"
PRIMITIVES = {"string", "number", "boolean", "null", "unknown", "any", "undefined"}


def _strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _split_top(text: str, sep: str) -> list[str]:
    """Split at sep where no bracket or quote is open."""
    parts, depth, quote, current = [], 0, "", []
    for ch in text:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "([{<":
            depth += 1
        elif ch in ")]}>":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts if p.strip()]


def _matching(text: str, start: int) -> int:
    """Index of the bracket closing the one at text[start]."""
    pairs = {"(": ")", "[": "]", "{": "}", "<": ">"}
    opening, closing, depth = text[start], pairs[text[start]], 0
    for i in range(start, len(text)):
        if text[i] == opening:
            depth += 1
        elif text[i] == closing:
            depth -= 1
            if depth == 0:
                return i
    raise ValueError(f"unbalanced {opening} in {text!r}")


def parse_type(text: str):
    text = text.strip()
    members = _split_top(text, "|")
    if len(members) > 1:
        return ("union", [parse_type(m) for m in members])
    text = members[0] if members else text
    if text.endswith("[]"):
        return ("array", parse_type(text[:-2]))
    if text.startswith("(") and _matching(text, 0) == len(text) - 1:
        return parse_type(text[1:-1])
    if text.startswith("{") and _matching(text, 0) == len(text) - 1:
        return ("object", _fields(text[1:-1]))
    if text.startswith("[") and _matching(text, 0) == len(text) - 1:
        return ("tuple", [parse_type(t) for t in _split_top(text[1:-1], ",")])
    if text[:1] in "\"'":
        return ("lit", text[1:-1])
    if re.fullmatch(r"-?\d+(\.\d+)?", text):
        return ("lit", float(text) if "." in text else int(text))
    if text in ("true", "false"):
        return ("lit", text == "true")
    generic = re.fullmatch(r"(\w+)<(.*)>", text, re.S)
    if generic:
        name, args = generic.group(1), _split_top(generic.group(2), ",")
        if name == "Partial":
            inner = parse_type(args[0])
            if inner[0] == "record":
                return ("record", inner[1], inner[2], True)
            return ("partial", inner)
        if name == "Record":
            return ("record", parse_type(args[0]), parse_type(args[1]), False)
        return ("prim", "unknown")
    indexed = re.fullmatch(r"(\w+)\[\"(\w+)\"\]", text)
    if indexed:
        return ("index", indexed.group(1), indexed.group(2))
    if text in PRIMITIVES:
        return ("prim", text)
    return ("ref", text)


def _fields(body: str) -> dict[str, tuple[bool, object]]:
    out = {}
    for part in _split_top(body.replace("\n", " "), ";"):
        for piece in _split_top(part, ","):   # "{ a: string, b: number }" is written with ; but allow ,
            match = re.fullmatch(r"(?:readonly\s+)?(\w+)(\?)?\s*:\s*(.+)", piece.strip(), re.S)
            if match:
                out[match.group(1)] = (bool(match.group(2)), parse_type(match.group(3)))
    return out


class Contract:
    def __init__(self, text: str):
        self.interfaces: dict[str, dict] = {}
        self.parents: dict[str, list[str]] = {}
        self.aliases: dict[str, object] = {}
        source = _strip_comments(text)
        for match in re.finditer(r"export\s+interface\s+(\w+)(?:\s+extends\s+([\w\s,]+))?\s*\{", source):
            start = match.end() - 1
            body = source[start + 1:_matching(source, start)]
            fields = {}
            for line in self._top_level(body):
                if "(" in line.split(":")[0]:
                    continue   # methods of ConsoleApi
                fields.update(_fields(line))
            self.interfaces[match.group(1)] = fields
            self.parents[match.group(1)] = [p.strip() for p in (match.group(2) or "").split(",") if p.strip()]
        for match in re.finditer(r"export\s+type\s+(\w+)\s*=\s*(.+?);", source, re.S):
            self.aliases[match.group(1)] = parse_type(" ".join(match.group(2).split()))

    @staticmethod
    def _top_level(body: str) -> list[str]:
        """The members of an interface body, nested object types kept whole."""
        return _split_top(body, ";")

    @classmethod
    def load(cls, path: Path = TYPES) -> "Contract":
        return cls(path.read_text())

    def fields(self, name: str) -> dict:
        out = {}
        for parent in self.parents.get(name, []):
            out.update(self.fields(parent))
        out.update(self.interfaces[name])
        return out

    # ------------------------------------------------------------------ checking
    def check(self, value, type_name: str, path: str | None = None) -> list[str]:
        errors: list[str] = []
        self._check(value, ("ref", type_name), path or type_name, errors)
        return errors

    def _describe(self, node) -> str:
        kind = node[0]
        if kind == "union":
            return " | ".join(self._describe(m) for m in node[1])
        if kind == "array":
            return self._describe(node[1]) + "[]"
        if kind in ("prim", "ref"):
            return node[1]
        if kind == "lit":
            return repr(node[1])
        return kind

    def _check(self, value, node, path: str, errors: list[str]) -> None:
        kind = node[0]
        if kind == "prim":
            name = node[1]
            ok = {"string": isinstance(value, str), "boolean": isinstance(value, bool),
                  "number": isinstance(value, (int, float)) and not isinstance(value, bool),
                  "null": value is None, "undefined": value is None}.get(name, True)
            if not ok:
                errors.append(f"{path}: expected {name}, got {type(value).__name__} {value!r:.60}")
        elif kind == "lit":
            if value != node[1] or isinstance(value, bool) != isinstance(node[1], bool):
                errors.append(f"{path}: expected {node[1]!r}, got {value!r:.60}")
        elif kind == "union":
            for member in node[1]:
                trial: list[str] = []
                self._check(value, member, path, trial)
                if not trial:
                    return
            errors.append(f"{path}: expected {self._describe(node)}, got {type(value).__name__} {value!r:.80}")
        elif kind == "array":
            if not isinstance(value, list):
                errors.append(f"{path}: expected a list, got {type(value).__name__}")
                return
            for i, item in enumerate(value):
                self._check(item, node[1], f"{path}[{i}]", errors)
        elif kind == "tuple":
            if not isinstance(value, (list, tuple)) or len(value) != len(node[1]):
                errors.append(f"{path}: expected a list of {len(node[1])}, got {value!r:.60}")
                return
            for i, (item, member) in enumerate(zip(value, node[1])):
                self._check(item, member, f"{path}[{i}]", errors)
        elif kind == "object":
            self._object(value, node[1], path, errors)
        elif kind == "record":
            if not isinstance(value, dict):
                errors.append(f"{path}: expected an object, got {type(value).__name__}")
                return
            for key, item in value.items():
                self._check(key, node[1], f"{path} key", errors)
                self._check(item, node[2], f"{path}.{key}", errors)
        elif kind == "partial":
            self._check(value, node[1], path, errors)
        elif kind == "index":
            fields = self.fields(node[1])
            self._check(value, fields[node[2]][1], path, errors)
        elif kind == "ref":
            name = node[1]
            if name in self.interfaces:
                self._object(value, self.fields(name), path, errors)
            elif name in self.aliases:
                self._check(value, self.aliases[name], path, errors)
            else:
                errors.append(f"{path}: types.ts has no type {name}")

    def _object(self, value, fields: dict, path: str, errors: list[str]) -> None:
        if not isinstance(value, dict):
            errors.append(f"{path}: expected an object, got {type(value).__name__} {value!r:.60}")
            return
        for key, (optional, node) in fields.items():
            if key not in value:
                if not optional:
                    errors.append(f"{path}.{key}: missing")
                continue
            self._check(value[key], node, f"{path}.{key}", errors)
        for key in value:
            if key not in fields:
                errors.append(f"{path}.{key}: not in types.ts")
