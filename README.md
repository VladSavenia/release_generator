# release_generator

## Общее описание

Этот репозиторий содержит Python-скрипт, который автоматизирует процесс сборки и публикации релизов прошивок.  
Скрипт используется как Git-сабмодуль внутри целевого проекта и вызывается из GitLab CI/CD пайплайнов.  

Основные задачи:
1. Чтение и валидация файла `release.json` (либо извлечение версий из `defs.h`, если проект так настроен).
2. Проверка корректности имени ветки и формата тега.
3. Сборка прошивки с помощью `CMake`/`Ninja`.
4. Коммит и пуш собранных бинарных файлов обратно в репозиторий.
5. Создание тега релиза.

---

## Подключение в проект

1. Добавьте скрипт как сабмодуль в свой проект:

```bash
   git submodule add git@gitlab.neroelectronics.by:unic-lab/projects/energymeter/utils/release_generator.git release_generator
   git submodule update --init --recursive
```

2. В `.gitlab-ci.yml` подключите вызов скрипта. Пример:

```yaml
stages:
  - release-automations

variables:
  GIT_SUBMODULE_STRATEGY: recursive

generate-release-binaries:
  stage: release-automations
  tags:
    - windows
  rules:
    - if: '$CI_COMMIT_BRANCH == "release" || $CI_COMMIT_BRANCH == "hotfix"'
      changes:
        - release.json
      when: on_success
    - when: never
  script:
    - python -m pip install -r .\script\release_generator\requirements.txt
    - python -m script.release_generator.release .\release.json .\Project\version\defs.h

```

Для оптимизации процесса рекомендуется использовать следующую схему:

```yaml
stages:
  - check-release-gen
  - dispatch-release-gen

detect-release-gen-mode:
  stage: check-release-gen
  tags:
    - windows
  variables:
    GIT_STRATEGY: clone
    GIT_SUBMODULE_STRATEGY: none
  rules:
    - if: '$CI_COMMIT_BRANCH == "release" || $CI_COMMIT_BRANCH == "hotfix"'
      changes:
        - release.json
      when: on_success
    - when: never
  script:
    - git submodule update --init --depth 1 .\\script\\release_generator\\
    - python -m script.release_generator.generate_child_ci --release-json release.json --out generated-ci.yml
  artifacts:
    paths:
      - generated-ci.yml

run-child:
  stage: dispatch-release-gen
  rules:
    - if: '$CI_COMMIT_BRANCH == "release" || $CI_COMMIT_BRANCH == "hotfix"'
      changes:
        - release.json
      when: on_success
    - when: never
  needs:
  - job: detect-release-gen-mode
    artifacts: true
  trigger:
    include:
      - artifact: generated-ci.yml
        job: detect-release-gen-mode
    strategy: depend
```

---

## Переменные окружения

Для корректной работы в настройках GitLab CI/CD (**Settings → CI/CD → Variables**) необходимо задать:

* **`RELEASE_TOKEN`** — персональный или проектный токен с правами `write_repository`.
* (опционально) **`EXPECTED_TARGETS`** — список разрешённых целей (через запятую), если требуется ограничить набор поддерживаемых таргетов.

Пример **`EXPECTED_TARGETS`**:<br>
```
l2_radio_energymeter_hard1_var1,l2_radio_energymeter_hard1_var2,l2_radio_energymeter_hard1_var3,l2_radio_energymeter_hard2_var1,l2_radio_energymeter_hard2_var2,l2_radio_energymeter_hard2_var3
```

---

## Пример `release.json`

Ниже приведён пример конфигурационного файла `release.json`, который используется скриптом для сборки и генерации тегов:

```json
{
    "cmake_project_name":"l2_radio_energymeter",
    "git_project_id": 2138,
    "branch_name": "release",
    "targets": [
        {
            "hard_num": 2,
            "variant_num": 1
        },
        {
            "hard_num": 2,
            "variant_num": 2
        }
    ],
    "is_service_firmware": false,
    "upgrade_to_release": true,
    "features": [],
    "bug_fixes": [],

    "release_count": 7
}
```

Описание полей:
- `cmake_project_name` - имя проекта (из главного `CMakeLists.txt`), используется для формирования имени таргета
- `git_project_id` - ID проекта в GitLab
- `branch_name` - имя ветки (должно начинаться с 'release' или 'hotfix')
- `targets` - массив конфигураций для сборки, каждая с уникальной комбинацией:
  - `hard_num` - номер аппаратной версии
  - `variant_num` - номер варианта исполнения
- `is_service_firmware` - если true, собирает сервисное ПО и устанавливает revision_ver в 255
- `upgrade_to_release` - если true, находит существующий beta-тег и создает release-тег на том же коммите (без сборки)
- `features` - список новых функций для описания в теге
- `bug_fixes` - список исправлений для описания в теге
- `release_count` - счетчик для принудительного запуска pipeline

---

## Использование

1. При изменении `release.json` и коммите в ветку `release` автоматически запускается пайплайн.
2. В стандартном режиме скрипт:
   - Соберёт прошивку для каждой конфигурации из массива `targets`
   - Закоммитит бинарные файлы в указанный репозиторий
   - Создаст beta-теги для каждой конфигурации
3. В режиме `upgrade_to_release`:
   - Найдет существующие beta-теги для каждой конфигурации
   - Создаст соответствующие release-теги на тех же коммитах
   - Создаст относительно основной ветки проекта новую ветку и добавит обновление `CHANGELOG.md`
   - Добавит в артефакты пайплайна файлы с текстом для передачи релизов
   - Пропустит этапы сборки и коммита файлов
4. Имена тегов и бинарных контейнеров формируются автоматически по правилам проекта (учитываются `proj_id`, `variant_num`, `hard_num`, `revision_ver` и др.).

Примечание: пайплайн настроен только на изменениях в `release.json`, однако иногда изменения по основным полям могут не понадобиться, для этого есть счетчик релизов, который достаточно увеличить на +1 и это запустит работу пайплайна.

---
