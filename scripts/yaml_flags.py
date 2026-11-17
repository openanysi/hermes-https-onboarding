"""Minimal, conservative scalar editing for Hermes' ordinary block-style YAML.

Never deserialize or reserialize the whole config. Refuse aliases/flow mappings
that could hide API enablement instead of reporting a successful configuration.
"""
import re

from common import Failure

API_PATHS = {("platforms", "api_server"), ("gateway", "platforms", "api_server"), ("gateway", "api_server")}
CONTAINERS = {(), ("platforms",), ("gateway",), ("gateway", "platforms")}
MAPPING = re.compile(r'''^( *)([A-Za-z_][A-Za-z0-9_-]*|"[A-Za-z_][A-Za-z0-9_-]*"|'[A-Za-z_][A-Za-z0-9_-]*'|<<)\s*:\s*(.*)$''')


def enable_api_flags(text):
    lines = text.splitlines(keepends=True)
    stack, changed, block_indent = [], [], None
    for index, line in enumerate(lines):
        content = line.rstrip("\r\n")
        if not content.strip() or content.lstrip().startswith("#"):
            continue
        if re.match(r"^ *\t", content):
            raise Failure("config.yaml uses tab indentation; normalize it before API setup.")
        indent = len(content) - len(content.lstrip(" "))
        if block_indent is not None:
            if indent > block_indent:
                continue
            block_indent = None
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parents = tuple(item[1] for item in stack)
        match = MAPPING.match(content)
        if not match:
            continue
        key = match.group(2).strip("\"'")
        value = match.group(3)
        path = parents + (key,)
        if path in CONTAINERS and value.split("#", 1)[0].strip():
            raise Failure("API configuration containers must be plain block mappings. Normalize YAML anchors, aliases or flow mappings through Dashboard before setup.")
        if key == "<<" and (parents in CONTAINERS or any(parents[:len(p)] == p for p in API_PATHS)):
            raise Failure("API enablement may depend on a YAML merge. Use Dashboard Save & Enable or normalize this API section to a plain block mapping first.")
        if parents in CONTAINERS and re.search(r'''["']?api_server["']?\s*:''', value):
            raise Failure("Flow-style API YAML requires manual normalization through Dashboard Save & Enable before running this tool.")
        if path in API_PATHS and value.split("#", 1)[0].strip():
            raise Failure("API YAML must be a plain block mapping, without flow mappings, anchors or aliases. Use Dashboard Save & Enable first.")
        if parents in API_PATHS and key == "enabled":
            scalar, marker, comment = value.partition("#")
            token = scalar.strip()
            if len(token) >= 2 and token[0] in "\"'" and token[-1] == token[0]:
                token = token[1:-1]
            if token.lower() not in {"true", "yes", "on", "1", "false", "no", "off", "0"}:
                raise Failure("API YAML enabled value is not a literal boolean. Use Dashboard Save & Enable first.")
            if token.lower() in {"false", "no", "off", "0"}:
                trailing = scalar[len(scalar.rstrip()):]
                suffix = trailing + (marker + comment if marker else "")
                lines[index] = content[:match.start(3)] + "true" + suffix + line[len(content):]
                changed.append(".".join(path))
        if re.match(r"[|>][+-]?[0-9]?\s*(?:#.*)?$", value):
            block_indent = indent
        if not value.split("#", 1)[0].strip():
            stack.append((indent, key))
    return "".join(lines), changed
