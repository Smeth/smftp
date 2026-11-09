import base64
import json
import pytest
from smftp.models.entries import Connection
from smftp.utils.profiles import read_profiles, identity, merge_profiles, store_credential, XMLCorrectionRequired, XMLImportError
from smftp.utils import storage


def test_filezilla_import_nested_passwords_and_unsupported(tmp_path):
    path = tmp_path / "sites.xml"
    password = base64.b64encode("sëcret".encode()).decode()
    path.write_text(f'''<FileZilla3><Servers><Folder>Production
      <Server><Host>sftp.test</Host><Port>22</Port><Protocol>1</Protocol>
      <User>alice</User><Name>Production SSH</Name><Keyfile>/keys/id_ed25519</Keyfile>
      <Pass encoding="base64">{password}</Pass></Server>
      <Server><Host>ftp.test</Host><Protocol>4</Protocol><User>bob</User><Pass>plain-secret</Pass></Server>
      <Server><Host>encrypted.test</Host><Protocol>0</Protocol><User>eve</User><Pass encoding="crypt">encrypted</Pass></Server>
      <Server><Host>implicit.test</Host><Protocol>3</Protocol><User>bob</User></Server>
      <Server><Host>invalid.test</Host><Port>99999</Port><User>bob</User></Server>
    </Folder></Servers></FileZilla3>''', encoding="utf-8")
    result = read_profiles(str(path))
    assert len(result.profiles) == 3
    assert result.profiles[0]["name"] == "Production SSH"
    assert result.profiles[0]["key_file"] == "/keys/id_ed25519"
    assert result.passwords[identity(result.profiles[0])] == "sëcret"
    assert result.passwords[identity(result.profiles[1])] == "plain-secret"
    assert len(result.warnings) == 3
    assert all("password" not in site for site in result.profiles)
    assert "sëcret" not in repr(result)


def test_anonymous_and_malformed_xml(tmp_path):
    path = tmp_path / "sites.xml"
    path.write_text('<FileZilla3><RecentServers><Server><Host>public.test</Host><Logontype>0</Logontype></Server></RecentServers></FileZilla3>')
    assert read_profiles(str(path)).profiles[0]["user"] == "anonymous"
    path.write_text("<broken>")
    with pytest.raises(Exception):
        read_profiles(str(path))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xml_dtd_rejected(tmp_path, encoding):
    path = tmp_path / "sites.xml"
    path.write_bytes('<!DOCTYPE x [<!ENTITY secret "test">]><FileZilla3/>'.encode(encoding))
    with pytest.raises(ValueError, match="DTD"):
        read_profiles(str(path))


def test_profile_storage_filters_all_secrets_and_merges(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "data_dir", lambda: tmp_path)
    site = dict(host="test.local", user="alice", protocol="SFTP", port=22,
                name="Original", password="private", passphrase="private", token="private")
    storage.save_sites([site])
    text = (tmp_path / "sites.json").read_text(encoding="utf-8")
    assert "private" not in text
    updated = merge_profiles(storage.load_sites(), [dict(site, name="Updated")])
    assert len(updated) == 1
    assert updated[0]["name"] == "Updated"
    storage.save_sites(updated)
    assert storage.load_sites() == updated
    assert not list(tmp_path.glob("*.tmp"))


def test_vault_save_and_forget(monkeypatch):
    import keyring
    vault = {}
    monkeypatch.setattr(keyring, "set_password", lambda service, key, secret: vault.__setitem__((service, key), secret))
    monkeypatch.setattr(keyring, "get_password", lambda service, key: vault.get((service, key)))
    monkeypatch.setattr(keyring, "delete_password", lambda service, key: vault.pop((service, key)))
    connection = Connection("test.local", "alice", password="secret", remember=True)
    store_credential(connection)
    assert vault["SMFTP", connection.identity] == "secret"
    assert "secret" not in repr(connection)
    connection.remember = False
    store_credential(connection)
    assert not vault


def test_json_import_ignores_secrets(tmp_path):
    path = tmp_path / "sites.json"
    path.write_text(json.dumps([dict(host="test.local", user="alice", password="private")]))
    result = read_profiles(str(path))
    assert not result.passwords
    assert "password" not in result.profiles[0]


@pytest.mark.parametrize("standalone,expected", [("true", "yes"), ("false", "no")])
@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-16-be"])
def test_invalid_standalone_requires_consent_and_preserves_file(tmp_path, standalone, expected, encoding):
    path = tmp_path / "filezilla.xml"
    declaration_encoding = "UTF-16" if encoding.startswith("utf-16") else "UTF-8"
    text = f'''<?xml version="1.0" encoding="{declaration_encoding}" standalone='{standalone}'?>
<FileZilla3><Servers><Server><Host>ftp.exemple.fr</Host><Port>21</Port>
<Protocol>0</Protocol><User>alice</User><Name>Été</Name><Logontype>2</Logontype>
ftp.exemple.fr</Server></Servers></FileZilla3>'''
    original = text.encode(encoding)
    path.write_bytes(original)
    with pytest.raises(XMLCorrectionRequired, match=f"standalone=\"{expected}\""):
        read_profiles(str(path))
    result = read_profiles(str(path), repair_header=True)
    assert result.profiles[0]["name"] == "Été"
    assert not result.passwords
    assert result.warnings
    assert path.read_bytes() == original


def test_header_repair_does_not_modify_document_values(tmp_path):
    path = tmp_path / "sites.xml"
    path.write_text('''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<FileZilla3><Servers><Server><Host>test.local</Host><User>alice</User>
<Pass>standalone="true"</Pass></Server></Servers></FileZilla3>''', encoding="utf-8")
    result = read_profiles(str(path))
    assert not result.warnings
    assert next(iter(result.passwords.values())) == 'standalone="true"'


def test_other_xml_errors_remain_explicit_after_repair(tmp_path):
    from smftp.core.errors import describe_error
    path = tmp_path / "broken.xml"
    path.write_text('<?xml version="1.0" standalone="true"?>\n<FileZilla3><Servers></FileZilla3>')
    with pytest.raises(XMLImportError, match="ligne 2") as caught:
        read_profiles(str(path), repair_header=True)
    assert "ligne 2" in describe_error(caught.value).summary
