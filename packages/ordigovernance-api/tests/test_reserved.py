"""Reserved payload keys: the shared plumbing vocabulary (api layer)."""

import pytest

from ordigovernance.api.tools import (
    RESERVED_PAYLOAD_KEYS,
    check_reserved_payload_keys,
)


def test_clean_inputs_pass():
    check_reserved_payload_keys("search", {"q": "ok"})


@pytest.mark.parametrize("key", sorted(RESERVED_PAYLOAD_KEYS))
def test_every_reserved_key_fails_loudly(key):
    with pytest.raises(ValueError, match="collide"):
        check_reserved_payload_keys("search", {key: 1})