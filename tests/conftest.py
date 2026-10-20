"""pytest 夹具。共享设施见 tests/helpers.py。"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest

from helpers import FakeTransport, PortalMockServer, wire_session


@pytest.fixture()
def portal() -> Iterator[PortalMockServer]:
    server = PortalMockServer().start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture()
def wired_session(portal: PortalMockServer):
    session = wire_session(portal)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def fake_transport() -> Callable[..., FakeTransport]:
    return FakeTransport
