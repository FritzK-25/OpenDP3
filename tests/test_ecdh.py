from pathlib import Path

import pytest

from openpowerstation.ecdh import EphemeralKey


# SEC 2 secp160r1 generator, uncompressed wire coordinates (no SEC1 prefix).
GENERATOR = bytes.fromhex('4a96b5688ef573284664698968c38bb913cbfc82'
                          '23a628553168947d59dcc912042351377ac5fb32')


def test_generator_peer_produces_own_x_coordinate():
    with EphemeralKey() as key:
        assert len(key.public_bytes()) == 40
        assert key.exchange(GENERATOR) == key.public_bytes()[:20]


def test_two_native_peers_agree_and_use_new_ephemeral_keys():
    with EphemeralKey() as first, EphemeralKey() as second:
        assert first.public_bytes() != second.public_bytes()
        shared = first.exchange(second.public_bytes())
        assert len(shared) == 20
        assert shared == second.exchange(first.public_bytes())


@pytest.mark.parametrize('peer', [b'', bytes(39), bytes(40), bytes(41), b'\xff' * 40])
def test_invalid_wire_points_fail_closed(peer):
    with EphemeralKey() as key:
        with pytest.raises(ValueError):
            key.exchange(peer)


def test_closed_key_cannot_be_reused():
    with EphemeralKey() as key:
        pass
    key.close()
    with pytest.raises(ValueError):
        key.exchange(GENERATOR)


def test_vulnerable_dependency_is_absent_from_every_installer():
    root = Path(__file__).resolve().parents[1]
    for path in ['pyproject.toml', 'requirements.txt',
                 'home-assistant/opendp3/requirements-headless.in',
                 'home-assistant/opendp3/requirements-headless.txt']:
        assert 'ecdsa' not in (root / path).read_text('utf-8').lower(), path
