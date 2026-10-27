import base64
import hashlib
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import release_core as c


class ReleaseFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.key = ec.generate_private_key(ec.SECP256R1())
        pub = self.key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        self.patch = patch.object(c, 'PUBLISHER_PUBLIC_SPKI', base64.b64encode(pub).decode('ascii'))
        self.patch.start();self.addCleanup(self.patch.stop)
        (self.root/'site').mkdir()
        (self.root/'site'/'index.html').write_text('<html>skryon</html>', encoding='utf-8')
        self.store = c.ReleaseStore(self.root/'stage',self.root/'site',enabled=True)
        self.binary = b'MZ' + b'linux-safe-test-not-a-real-exe' * 50
        self.digest = hashlib.sha256(self.binary).hexdigest()
        self.version='1.0.1';self.code=20261027;self.build='26K101'
        self.filename=f'SkryonRelease-{self.build}.json'
        self.blob=self.make_manifest()

    def make_manifest(self, **changes):
        data = dict(versionCode=self.code,version=self.version,
                    downloadUrl=f'https://skryon.ru/downloads/windows/SkryonSetup-{self.version}.exe',
                    sha256=self.digest)
        data.update(changes)
        data['signature']=base64.b64encode(self.key.sign(c._canonical(data),ec.ECDSA(hashes.SHA256()))).decode()
        return json.dumps(data).encode()

    def upload(self):
        self.store.stage_manifest(self.blob,self.filename)
        f=self.root/'upload'
        f.write_bytes(self.binary)
        name=f'SkryonSetup-{self.version}.exe.{self.digest}.part001of001.skpart'
        self.store.stage_chunk_file(name,f)

    def test_valid_signature_and_url(self):
        m=c.validate_manifest(self.blob,self.filename)
        self.assertEqual((m.version,m.version_code,m.build),(self.version,self.code,self.build))

    def test_tampered_signed_data_rejected(self):
        d=json.loads(self.blob)
        d['versionCode']=99
        with self.assertRaisesRegex(c.ReleaseError,'signature'):
            c.validate_manifest(json.dumps(d).encode(),self.filename)

    def test_malicious_url_rejected_even_if_signed(self):
        bad=self.make_manifest(downloadUrl='https://skryon.ru.evil.example/downloads/windows/SkryonSetup-1.0.1.exe')
        with self.assertRaisesRegex(c.ReleaseError,'official'):
            c.validate_manifest(bad,self.filename)

    def test_unsigned_altered_digest_blocked(self):
        d=json.loads(self.blob);d['sha256']='a'*64
        with self.assertRaises(c.ReleaseError):
            c.validate_manifest(json.dumps(d).encode(),self.filename)

    def test_filename_path_traversal_rejected(self):
        with self.assertRaises(c.ReleaseError):
            self.store.chunk_target('../evil.skpart',16)

    def test_publish_requires_valid_pe_version(self):
        self.upload()
        with patch.object(c,'inspect_exe_version',return_value=('26K101','1.0.2')):
            with self.assertRaisesRegex(c.ReleaseError,'ProductVersion|FileVersion'):
                self.store.publish(self.digest)
        self.assertFalse(self.store.public_manifest.exists())

    def test_atomic_publication_and_anti_rollback(self):
        self.upload()
        with patch.object(c,'inspect_exe_version',return_value=(self.build,self.version)):
            published=self.store.publish(self.digest)
        self.assertEqual(published.build,self.build)
        self.assertEqual((self.store.public_installer_dir/published.installer_name).read_bytes(),self.binary)
        self.assertEqual(self.store.public_manifest.read_bytes(),self.blob)
        with self.assertRaisesRegex(c.ReleaseError,'Only newer'):
            self.store.publish(self.digest)

    def test_remote_website_is_not_silently_created(self):
        self.upload()
        (self.root/'site'/'index.html').unlink()
        with patch.object(c,'inspect_exe_version',return_value=(self.build,self.version)):
            with self.assertRaisesRegex(c.ReleaseError,'website root'):
                self.store.publish(self.digest)
        self.assertFalse(self.store.public_manifest.exists())

    def test_disabled_publish_is_fail_closed(self):
        self.upload()
        self.store.enabled=False
        with self.assertRaisesRegex(c.ReleaseError,'disabled'):
            self.store.publish(self.digest)
        self.assertFalse(self.store.public_manifest.exists())

    def test_chunk_duplicate_is_idempotent(self):
        self.upload()
        f=self.root/'again';f.write_bytes(self.binary)
        n=f'SkryonSetup-{self.version}.exe.{self.digest}.part001of001.skpart'
        self.store.stage_chunk_file(n,f)
        self.assertFalse(f.exists())
        self.assertEqual(self.store.progress(self.digest)[1:],(1,1))

    def test_published_name_cannot_be_overwritten(self):
        self.upload()
        d=self.store.public_installer_dir
        d.mkdir(parents=True,exist_ok=True)
        (d/f'SkryonSetup-{self.version}.exe').write_bytes(b'other installer')
        with patch.object(c,'inspect_exe_version',return_value=(self.build,self.version)):
            with self.assertRaisesRegex(c.ReleaseError,'different content'):
                self.store.publish(self.digest)
        self.assertFalse(self.store.public_manifest.exists())


if __name__=='__main__': unittest.main()
