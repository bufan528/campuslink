"""凭据存储：失败路径必须显式失败，不得退化成明文文件。"""

from __future__ import annotations

import os

import pytest

from campuslink.auth.errors import CredentialStoreError, CredentialsMissing
from campuslink.credentials import store as store_module
from campuslink.credentials.store import (
    InMemoryCredentialStore,
    NullCredentialStore,
    WindowsCredentialStore,
)


def test_in_memory_roundtrip() -> None:
    store = InMemoryCredentialStore()
    assert store.load() is None
    store.save("00000000", "secret")
    assert store.load() == ("00000000", "secret")
    store.delete()
    assert store.load() is None


def test_in_memory_rejects_empty() -> None:
    store = InMemoryCredentialStore()
    with pytest.raises(CredentialsMissing):
        store.save("", "x")
    with pytest.raises(CredentialsMissing):
        store.save("00000000", "")


def test_null_store_refuses_to_save() -> None:
    store = NullCredentialStore()
    with pytest.raises(CredentialStoreError):
        store.save("00000000", "secret")
    assert store.load() is None


def test_windows_store_rejects_empty_before_touching_system() -> None:
    store = WindowsCredentialStore()
    with pytest.raises(CredentialsMissing):
        store.save("", "secret")


def test_missing_pywin32_is_explicit_failure(monkeypatch) -> None:
    def boom():
        raise CredentialStoreError("系统凭据存储不可用")

    monkeypatch.setattr(store_module, "_win32cred", boom)
    store = WindowsCredentialStore()
    with pytest.raises(CredentialStoreError):
        store.save("00000000", "secret")
    with pytest.raises(CredentialStoreError):
        store.load()


def test_no_plaintext_fallback_file_is_created(tmp_path, monkeypatch) -> None:
    """凭据存储失败时，不应在磁盘上留下任何含密码的文件。"""

    def boom():
        raise CredentialStoreError("系统凭据存储不可用")

    monkeypatch.setattr(store_module, "_win32cred", boom)
    store = WindowsCredentialStore()
    with pytest.raises(CredentialStoreError):
        store.save("00000000", "TOP-SECRET-PW")
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"TOP-SECRET-PW" not in path.read_bytes()


@pytest.mark.skipif(
    os.environ.get("CAMPUSLINK_TEST_CREDSTORE") != "1",
    reason="真实写入系统凭据管理器需显式开启（设置 CAMPUSLINK_TEST_CREDSTORE=1）",
)
def test_real_credential_manager_roundtrip() -> None:
    """手动验收用：虚构凭据，用后立即删除。"""
    store = WindowsCredentialStore(target="CampusLink/test-roundtrip")
    try:
        store.save("test-account", "test-password")
        assert store.load() == ("test-account", "test-password")
    finally:
        store.delete()
    assert store.load() is None
