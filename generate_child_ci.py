#!/usr/bin/env python3
import argparse, json, sys, pathlib

TAG_ONLY_TEMPLATE = """\
stages:
  - upd-to-release

tag-only:
  stage: upd-to-release
  tags:
    - windows
  variables:
    GIT_STRATEGY: clone
    GIT_SUBMODULE_STRATEGY: none
  script:
    - git submodule update --init --depth 1 .\\script\\release_generator\\
    - python -m pip install -r .\\script\\release_generator\\requirements.txt
    - python -m script.release_generator.release .\\release.json .\\Project\\version\\defs.h
"""

BUILD_TEMPLATE = """\
stages:
  - build-and-push

build-and-push-job:
  stage: build-and-push
  tags:
    - windows
  variables:
    GIT_STRATEGY: clone
    GIT_SUBMODULE_STRATEGY: recursive
  script:
    - python -m pip install -r .\\script\\release_generator\\requirements.txt
    - python -m script.release_generator.release .\\release.json .\\Project\\version\\defs.h
"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--release-json", default="release.json")
    ap.add_argument("--out", default="generated-ci.yml")
    ap.add_argument("--override", choices=["tag-only", "build"], help="форсировать режим")
    args = ap.parse_args()

    # читаем release.json
    p = pathlib.Path(args.release_json)
    if not p.exists():
        print(f"ERROR: {p} not found", file=sys.stderr)
        sys.exit(1)

    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)

    tag_only = bool(data.get("upgrade_to_release", False))
    if args.override == "tag-only":
        tag_only = True
    elif args.override == "build":
        tag_only = False

    tpl = TAG_ONLY_TEMPLATE if tag_only else BUILD_TEMPLATE

    outp = pathlib.Path(args.out)
    outp.write_text(tpl, encoding="utf-8")
    print(f"generated {outp} (mode={'tag-only' if tag_only else 'build'})")
    
    # Output the contents of the generated file
    print("\nGenerated file contents:")
    print("-" * 40)
    print(tpl)
    print("-" * 40)

if __name__ == "__main__":
    main()
