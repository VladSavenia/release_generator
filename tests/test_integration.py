# import os
# import unittest
# from unittest.mock import Mock, patch
# import tempfile
# import json
# from pathlib import Path

# from ..release import main as release_main
# from ..release_parser import ReleaseParser, ReleaseInfo
# from ..gitlab_rep import GitlabRep
# from ..generator_tag import GeneratorTag
# from ..changelog_generator import ChangelogGenerator

# class MockGitlabProject:
#     def __init__(self, web_url="https://gitlab.example.com/project"):
#         self.web_url = web_url
#         self.tags = Mock()
#         self.files = Mock()
#         self.branches = Mock()
#         self.default_branch = "main"

#     def get_file(self, file_path, ref):
#         if file_path == "CHANGELOG.md":
#             return Mock(decode=lambda: "# Changelog\n".encode())
#         return None

# class MockGitlab:
#     def __init__(self, url, private_token):
#         self.url = url
#         self.private_token = private_token
#         self._project = MockGitlabProject()

#     def projects(self):
#         return Mock(get=lambda id: self._project)

# class IntegrationTests(unittest.TestCase):
#     @classmethod
#     def setUpClass(cls):
#         # Создаем временную директорию для тестов
#         cls.temp_dir = tempfile.mkdtemp()
#         cls.build_dir = os.path.join(cls.temp_dir, "build")
#         os.makedirs(cls.build_dir, exist_ok=True)

#     def setUp(self):
#         # Подготавливаем тестовые файлы
#         self.release_json = {
#             "cmake_project_name": "test_project",
#             "git_project_id": 123,
#             "branch_name": "release/v1.0",
#             "targets": [
#                 {"hard_num": 1, "variant_num": 1},
#                 {"hard_num": 1, "variant_num": 2}
#             ],
#             "features": ["[TEST-1] New feature"],
#             "bug_fixes": ["[TEST-2] Bug fix"],
#             "is_service_firmware": False,
#             "upgrade_to_release": False
#         }

#         self.defs_h_content = """
# #define PRODUCT_ID 1
# #define PRODUCT_VERSION 2
# #define PRODUCT_VARIANT_MINOR_VER 3
# #define PRODUCT_REVISION 4
#         """

#         # Создаем тестовые файлы
#         self.release_json_path = os.path.join(self.temp_dir, "release.json")
#         self.defs_h_path = os.path.join(self.temp_dir, "defs.h")

#         with open(self.release_json_path, 'w') as f:
#             json.dump(self.release_json, f)

#         with open(self.defs_h_path, 'w') as f:
#             f.write(self.defs_h_content)

#     def test_full_release_process(self):
#         """
#         Тестирование полного процесса релиза:
#         1. Парсинг конфигурации
#         2. Инициализация GitLab
#         3. Валидация данных
#         4. Сборка проекта
#         5. Создание тегов
#         6. Обновление changelog
#         """
#         with patch('gitlab.Gitlab', MockGitlab), \
#              patch('subprocess.run') as mock_run, \
#              patch.dict(os.environ, {'RELEASE_TOKEN': 'fake_token', 
#                                    'EXPECTED_TARGETS': 'test_project_hard1_var1,test_project_hard1_var2'}):

#             # 1. Тестируем парсинг
#             parser = ReleaseParser(self.release_json_path, self.defs_h_path)
#             info = parser.parse()
#             self.assertIsInstance(info, ReleaseInfo)
#             self.assertEqual(len(info.targets), 2)

#             # 2. Тестируем инициализацию GitLab
#             rep = GitlabRep('https://gitlab.example.com', 123, 'fake_token', info, self.build_dir)
#             project = rep.get_project_obj()
#             self.assertIsNotNone(project)

#             # 3. Тестируем сборку
#             rep.build_project()
#             mock_run.assert_any_call(["cmake", "-B", self.build_dir, "-G", "Ninja"], check=True)

#             # 4. Тестируем создание тегов
#             for target in info.targets:
#                 tag_generator = GeneratorTag(
#                     rep, 
#                     target.tag_name,
#                     info.features,
#                     info.bug_fixes,
#                     "main"
#                 )
#                 url = tag_generator.generate()
#                 self.assertTrue(url.startswith("https://"))

#             # 5. Тестируем обновление changelog
#             changelog = ChangelogGenerator()
#             changelog.update_changelog_and_push(
#                 info, 
#                 rep, 
#                 "changelog-update", 
#                 "Update changelog for release"
#             )

#     def test_upgrade_to_release_process(self):
#         """
#         Тестирование процесса обновления до релизной версии
#         """
#         self.release_json["upgrade_to_release"] = True
        
#         with open(self.release_json_path, 'w') as f:
#             json.dump(self.release_json, f)

#         with patch('gitlab.Gitlab', MockGitlab), \
#              patch('subprocess.run') as mock_run, \
#              patch.dict(os.environ, {'RELEASE_TOKEN': 'fake_token'}):

#             parser = ReleaseParser(self.release_json_path, self.defs_h_path)
#             info = parser.parse()
#             self.assertTrue(info.upgrade_to_release)

#             rep = GitlabRep('https://gitlab.example.com', 123, 'fake_token', info, self.build_dir)
            
#             # В режиме upgrade_to_release не должно быть сборки
#             rep.build_project()
#             mock_run.assert_not_called()

#     def test_error_handling(self):
#         """
#         Тестирование обработки ошибок
#         """
#         # Тест с некорректным JSON
#         invalid_json_path = os.path.join(self.temp_dir, "invalid.json")
#         with open(invalid_json_path, 'w') as f:
#             f.write("{invalid json")

#         with self.assertRaises(json.JSONDecodeError):
#             ReleaseParser(invalid_json_path, self.defs_h_path).parse()

#         # Тест с отсутствующими обязательными полями
#         invalid_release = self.release_json.copy()
#         del invalid_release["git_project_id"]
        
#         with open(self.release_json_path, 'w') as f:
#             json.dump(invalid_release, f)

#         with self.assertRaises(ValueError):
#             ReleaseParser(self.release_json_path, self.defs_h_path).parse()

#     def tearDown(self):
#         # Очистка временных файлов
#         for file in [self.release_json_path, self.defs_h_path]:
#             if os.path.exists(file):
#                 os.remove(file)

#     @classmethod
#     def tearDownClass(cls):
#         # Удаление временной директории
#         import shutil
#         shutil.rmtree(cls.temp_dir)

# if __name__ == '__main__':
#     unittest.main()