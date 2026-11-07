"""``network.proxy_password`` never lands on disk as a plain password."""

from __future__ import annotations

import json
import logging

import pytest

from kiro_crew.config.loader_document import ConfigWriteRefused, _refuse_unpublishable
from kiro_crew.config.sections_network import proxy_password_is_plaintext


@pytest.mark.parametrize(
    "value, plain",
    [
        (None, False),
        ("", False),
        ("   ", False),
        ("secret://CORP_PROXY", False),
        ("hunter2", True),
        ("secret://", True),
        ("secret:// padded", True),
        (12345, True),
    ],
)
def test_what_counts_as_a_plain_password(value, plain):
    assert proxy_password_is_plaintext(value) is plain


def test_a_write_that_introduces_a_plain_password_is_refused(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"network": {"proxy_enabled": True}}), encoding="utf-8")
    with pytest.raises(ConfigWriteRefused) as refused:
        _refuse_unpublishable({"network": {"proxy_password": "hunter2"}}, path)
    assert "hunter2" not in str(refused.value)
    assert "secret://NAME" in str(refused.value)


def test_a_vault_reference_is_written(tmp_path):
    path = tmp_path / "config.json"
    _refuse_unpublishable({"network": {"proxy_password": "secret://CORP_PROXY"}}, path)


def test_a_plain_password_already_on_disk_does_not_block_unrelated_writes(tmp_path, caplog):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"network": {"proxy_password": "hunter2"}}), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="kiro_crew.config.loader"):
        _refuse_unpublishable({"timezone": "local", "network": {"proxy_password": "hunter2"}}, path)
    assert "already holds a plain network.proxy_password" in caplog.text
    assert "hunter2" not in caplog.text
