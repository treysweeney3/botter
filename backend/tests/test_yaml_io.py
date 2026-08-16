"""The comment-preserving config writer.

`~/.hermes/config.yaml` is a file the user maintains by hand. botterd only ever
sets one nested key in it, so a load-then-dump must give back the same bytes.
Hermes' own writer runs the document through `yaml.dump` and loses every
comment; these tests pin the settings that avoid that.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from botterd.yaml_io import detect_indent_style, dump_yaml, load_yaml, write_yaml_atomic


SAMPLE = """\
# ── Model ──────────────────────────────────────────────
# Pick the default with `hermes model`.
model:
  default: provider/model  # trailing comment
platforms:
  slack:
    enabled: true
gateway:
  multiplex_profiles: true
  toolsets:
    - terminal
    - web
    - memory
quoted: "keep the quotes"
"""


def test_round_trip_is_byte_identical(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE, encoding="utf-8")

    assert dump_yaml(load_yaml(path)) == SAMPLE


def test_edit_changes_only_the_touched_key(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE, encoding="utf-8")

    document = load_yaml(path)
    document["mcp_servers"] = {"composio": {"url": "https://connect.composio.dev/mcp"}}
    assert write_yaml_atomic(path, document) is True

    text = path.read_text(encoding="utf-8")
    assert "# ── Model ──────────────────────────────────────────────" in text
    assert "# Pick the default with `hermes model`." in text
    assert "# trailing comment" in text
    assert 'quoted: "keep the quotes"' in text
    # Every original line survives; only the new block is added.
    added = [line for line in text.splitlines() if line not in SAMPLE.splitlines()]
    assert added == ["mcp_servers:", "  composio:", "    url: https://connect.composio.dev/mcp"]


# `hermes profile create` emits a different sequence style than the
# hand-maintained main config. Writing one style over the other reindents about
# a hundred list lines per bot, which is exactly the noise this module exists to
# avoid.
HERMES_EMITTED = """\
gateway:
  multiplex_profiles: true
  toolsets:
  - terminal
  - web
  - memory
"""

HAND_MAINTAINED = """\
gateway:
  multiplex_profiles: true
  toolsets:
    - terminal
    - web
    - memory
"""


@pytest.mark.parametrize(
    "source,expected", [(HAND_MAINTAINED, (4, 2)), (HERMES_EMITTED, (2, 0))]
)
def test_indent_style_is_detected_per_file(source, expected):
    assert detect_indent_style(source) == expected
    assert dump_yaml(load_yaml_text(source), expected) == source


def load_yaml_text(text: str):
    import io

    from ruamel.yaml import YAML

    handler = YAML()
    handler.preserve_quotes = True
    handler.width = 4096
    return handler.load(io.StringIO(text))


@pytest.mark.parametrize("source", [HAND_MAINTAINED, HERMES_EMITTED])
def test_edit_adds_only_new_lines_whatever_the_file_style(tmp_path, source):
    path = tmp_path / "config.yaml"
    path.write_text(source, encoding="utf-8")

    document = load_yaml(path)
    document["mcp_servers"] = {"composio": {"url": "https://connect.composio.dev/mcp"}}
    write_yaml_atomic(path, document)

    old, new = source.splitlines(), path.read_text(encoding="utf-8").splitlines()
    assert [line for line in old if line not in new] == []
    assert [line for line in new if line not in old] == [
        "mcp_servers:",
        "  composio:",
        "    url: https://connect.composio.dev/mcp",
    ]


def test_write_is_skipped_when_nothing_changed(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE, encoding="utf-8")
    before = path.stat().st_mtime_ns

    assert write_yaml_atomic(path, load_yaml(path)) is False
    assert path.stat().st_mtime_ns == before


def test_write_preserves_file_mode(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(SAMPLE, encoding="utf-8")
    os.chmod(path, 0o640)

    document = load_yaml(path)
    document["added"] = True
    write_yaml_atomic(path, document)

    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_write_refuses_a_symlinked_config(tmp_path):
    target = tmp_path / "real.yaml"
    target.write_text(SAMPLE, encoding="utf-8")
    link = tmp_path / "config.yaml"
    link.symlink_to(target)

    with pytest.raises(OSError, match="not a regular file"):
        write_yaml_atomic(link, {"model": {"default": "x"}})
    assert target.read_text(encoding="utf-8") == SAMPLE


def test_long_values_are_never_rewrapped(tmp_path):
    path = tmp_path / "config.yaml"
    long_url = "https://example.com/" + "segment/" * 40 + "mcp"
    path.write_text(f"url: {long_url}\n", encoding="utf-8")

    document = load_yaml(path)
    document["added"] = True
    write_yaml_atomic(path, document)

    assert f"url: {long_url}" in path.read_text(encoding="utf-8")


@pytest.mark.skipif(
    not Path(os.path.expanduser("~/.hermes/config.yaml")).exists(),
    reason="no local Hermes install",
)
def test_round_trips_the_real_hermes_config_byte_for_byte():
    path = Path(os.path.expanduser("~/.hermes/config.yaml"))

    assert dump_yaml(load_yaml(path)) == path.read_text(encoding="utf-8")
