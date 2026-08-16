"""Comment-preserving reads and writes for Hermes YAML config files.

`~/.hermes/config.yaml` is a file the user maintains by hand. It carries 36
comment lines on this machine. Hermes' own `atomic_yaml_write` runs the document
through `yaml.dump`, which drops every comment — acceptable for the dashboard,
which owns whole forms, but not for botterd, which only ever sets one nested
key (`mcp_servers.<name>`).

So botterd round-trips with ruamel instead. The indent settings below were
tuned against the real `~/.hermes/config.yaml` until a load-then-dump was
byte-identical; see `test_yaml_io.py`.
"""

from __future__ import annotations

import io
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


# Sequence indent styles seen in the wild. `~/.hermes/config.yaml` is
# hand-maintained and uses (4, 2); the profile configs that `hermes profile
# create` emits use (2, 0). Writing one style over the other reindents about a
# hundred list lines per file, so the writer matches whatever the file already
# uses. First entry is the fallback for a new file.
_INDENT_STYLES: tuple[tuple[int, int], ...] = ((4, 2), (2, 0), (4, 0), (2, 2))


def _writer(style: tuple[int, int] = _INDENT_STYLES[0]) -> YAML:
    handler = YAML()
    handler.preserve_quotes = True
    # Long lines must not be re-wrapped; a wrapped value is a changed value to
    # anyone reading a diff.
    handler.width = 4096
    sequence, offset = style
    handler.indent(mapping=2, sequence=sequence, offset=offset)
    return handler


def detect_indent_style(text: str) -> tuple[int, int]:
    """Return the sequence indent style that reproduces `text` unchanged.

    Determined by round-tripping rather than by parsing indentation by eye: the
    style that reproduces the file exactly is the file's style, by definition.
    """
    for style in _INDENT_STYLES:
        try:
            handler = _writer(style)
            buffer = io.StringIO()
            handler.dump(handler.load(io.StringIO(text)), buffer)
            if buffer.getvalue() == text:
                return style
        except YAMLError:
            continue
    return _INDENT_STYLES[0]


def load_yaml(path: Path) -> Any:
    """Load a YAML document, keeping comments and formatting for a later dump."""
    text = path.read_text(encoding="utf-8")
    return _writer().load(io.StringIO(text))


def dump_yaml(document: Any, style: tuple[int, int] = _INDENT_STYLES[0]) -> str:
    buffer = io.StringIO()
    _writer(style).dump(document, buffer)
    return buffer.getvalue()


def write_yaml_atomic(path: Path, document: Any) -> bool:
    """Write the document only when it changes the file. Returns True if written.

    The emitted style is taken from the file already on disk, so an edit adds
    the lines it means to add and touches nothing else.
    """
    if path.exists():
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"Hermes config path is not a regular file: {path}")
        existing = path.read_text(encoding="utf-8")
        rendered = dump_yaml(document, detect_indent_style(existing))
        if existing == rendered:
            return False
        mode = stat.S_IMODE(metadata.st_mode)
    else:
        rendered = dump_yaml(document)
        mode = 0o600

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.botter-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return True


__all__ = ["YAMLError", "detect_indent_style", "load_yaml", "dump_yaml", "write_yaml_atomic"]
