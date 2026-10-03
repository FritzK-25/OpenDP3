"""The default data directory survives the rename from OpenDP3."""
from openpowerstation import config


def use_root(monkeypatch, root):
    monkeypatch.setattr(config, "user_data_path",
                        lambda name, appauthor=False: root / name)


def test_fresh_install_uses_the_new_name(tmp_path, monkeypatch):
    use_root(monkeypatch, tmp_path)
    assert config.data_dir() == tmp_path / "OpenPowerstation"


def test_existing_install_keeps_its_legacy_directory(tmp_path, monkeypatch):
    use_root(monkeypatch, tmp_path)
    (tmp_path / "OpenDP3").mkdir()
    (tmp_path / "OpenDP3" / "config.json").write_text("{}", "utf-8")
    assert config.data_dir() == tmp_path / "OpenDP3"


def test_new_directory_wins_once_it_exists(tmp_path, monkeypatch):
    use_root(monkeypatch, tmp_path)
    (tmp_path / "OpenDP3").mkdir()
    (tmp_path / "OpenPowerstation").mkdir()
    assert config.data_dir() == tmp_path / "OpenPowerstation"


def test_a_legacy_file_is_not_mistaken_for_a_directory(tmp_path, monkeypatch):
    use_root(monkeypatch, tmp_path)
    (tmp_path / "OpenDP3").write_text("", "utf-8")
    assert config.data_dir() == tmp_path / "OpenPowerstation"
