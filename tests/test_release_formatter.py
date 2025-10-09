import unittest
from release_formatter import ReleaseFormatter, _calc_minor

class TestReleaseFormatter(unittest.TestCase):
    def setUp(self):
        # Test data setup
        self.json_data = {
            "hard_num": 1,
            "variant_num": 2,
            "is_service_firmware": False
        }
        self.defs_data = {
            "proj_id": 1,
            "major_ver": 2,
            "minor_ver": 3,
            "revision_ver": 4
        }
        self.service_json_data = {
            "hard_num": 1,
            "variant_num": 2,
            "is_service_firmware": True
        }

    def test_calc_minor(self):
        """Test minor version calculation"""
        test_cases = [
            # variant_num, minor_ver, expected
            (1, 1, 1),           # First variant, first minor
            (2, 3, 19),          # Second variant, third minor
            (16, 15, 239),       # Max values
            (1, 15, 15),         # First variant, max minor
            (16, 1, 225)         # Max variant, first minor
        ]
        
        for variant_num, minor_ver, expected in test_cases:
            with self.subTest(variant_num=variant_num, minor_ver=minor_ver):
                result = _calc_minor(variant_num, minor_ver)
                self.assertEqual(result, expected)

    def test_make_tag_name_normal(self):
        """Test tag name generation for normal firmware"""
        expected = "v1.2.19.1-Rev4"  # 19 = ((2-1) << 4) | 3
        result = ReleaseFormatter.make_tag_name_from_dict(self.json_data, self.defs_data)
        self.assertEqual(result, expected)

    def test_make_tag_name_service(self):
        """Test tag name generation for service firmware"""
        expected = "v1.2.19.1-Rev255"  # Service firmware always has Rev255
        result = ReleaseFormatter.make_tag_name_from_dict(self.service_json_data, self.defs_data)
        self.assertEqual(result, expected)

    def test_make_container_name(self):
        """Test container name generation"""
        expected = "1.002.019.001.btl.bin"
        result = ReleaseFormatter.make_container_name_from_dict(self.json_data, self.defs_data)
        self.assertEqual(result, expected)

    def test_validate_tag_string(self):
        """Test tag string validation"""
        valid_tags = [
            "v1.2.3.4-Rev5",
            "v1.2.3.4-Rev5-release",
            "v255.255.255.255-Rev255",
            "v1.1.1.1-Rev1"
        ]
        invalid_tags = [
            "v1.2.3.4",                    # Missing Rev
            "v1.2.3.4-Rev",               # Missing Rev number
            "v1.2.3.4-Rev5-invalid",      # Invalid suffix
            "v256.1.1.1-Rev1",            # Number too large
            "v1.1.1.1-Rev256",            # Rev too large
            "1.2.3.4-Rev5",               # Missing v prefix
            "v1.2.3.4.5-Rev5",            # Too many version numbers
            "va.2.3.4-Rev5",              # Invalid version number
            "v1.2.3.4-RevA"               # Invalid Rev number
        ]

        for tag in valid_tags:
            with self.subTest(tag=tag):
                self.assertTrue(ReleaseFormatter.validate_tag_string(tag))

        for tag in invalid_tags:
            with self.subTest(tag=tag):
                self.assertFalse(ReleaseFormatter.validate_tag_string(tag))

    def test_version_ranges(self):
        """Test version number ranges"""
        # Test maximum values
        max_json = {
            "hard_num": 255,
            "variant_num": 16
        }
        max_defs = {
            "proj_id": 255,
            "major_ver": 255,
            "minor_ver": 15,
            "revision_ver": 255
        }
        max_tag = ReleaseFormatter.make_tag_name_from_dict(max_json, max_defs)
        self.assertTrue(ReleaseFormatter.validate_tag_string(max_tag))

        # Test minimum values
        min_json = {
            "hard_num": 1,
            "variant_num": 1
        }
        min_defs = {
            "proj_id": 1,
            "major_ver": 1,
            "minor_ver": 1,
            "revision_ver": 1
        }
        min_tag = ReleaseFormatter.make_tag_name_from_dict(min_json, min_defs)
        self.assertTrue(ReleaseFormatter.validate_tag_string(min_tag))

if __name__ == '__main__':
    unittest.main()