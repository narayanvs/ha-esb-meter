"""Release safety checks: prevent publishing the wrong code under a version tag."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('release_metadata',Path(__file__).parents[1]/'.github/scripts/release_metadata.py')
r=importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

class ReleaseTests(unittest.TestCase):
    def test_beta_is_prerelease_with_only_its_notes(self):
        result=r.prepare_release('v0.1.0-beta.8','0.1.0-beta.8','# Changelog\n\n## 0.1.0-beta.8 — unreleased\n\n- New feature.\n\n## 0.1.0-beta.7\n\n- Old feature.\n')
        self.assertTrue(result['prerelease'])
        self.assertIn('New feature.',result['notes'])
        self.assertNotIn('Old feature.',result['notes'])

    def test_stable_is_not_prerelease(self):
        self.assertFalse(r.prepare_release('v1.0.0','1.0.0','## 1.0.0\n\n- Stable.\n')['prerelease'])

    def test_tag_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            r.prepare_release('v0.1.0-beta.7','0.1.0-beta.8','## 0.1.0-beta.8\n- Feature.')

    def test_invalid_version_or_unprefixed_tag_is_rejected(self):
        for tag,version in [('v01.0.0','01.0.0'),('1.0.0','1.0.0'),('v1.0.0-injected\n','1.0.0-injected\n')]:
            with self.subTest(tag=tag),self.assertRaises(ValueError):
                r.prepare_release(tag,version,'## '+version+'\n- Feature.')

    def test_missing_or_empty_notes_is_rejected(self):
        for notes in ('## 0.9.0\n- Other.', '## 1.0.0\n\n## 0.9.0\n- Other.'):
            with self.assertRaises(ValueError):r.prepare_release('v1.0.0','1.0.0',notes)
