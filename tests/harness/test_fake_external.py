# tests/harness/test_fake_external.py
def test_pikpak_is_globally_neutered():
    from tests.harness.fake_external import assert_pikpak_neutered
    assert_pikpak_neutered()  # raises if conftest's pikpakapi MagicMock is gone


def test_neuter_rclone_patches_install_probe(monkeypatch):
    import javdb.integrations.rclone.manager.service as rclone_service
    from tests.harness.fake_external import neuter_rclone
    neuter_rclone(monkeypatch)
    # The install probe now reports present without an rclone binary on PATH.
    # check_rclone_installed() -> Tuple[bool, str]; callers unpack the tuple.
    ok, _msg = rclone_service.check_rclone_installed()
    assert ok is True
