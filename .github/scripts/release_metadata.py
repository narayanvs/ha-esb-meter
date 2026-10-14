"""Validate a release tag and derive release notes from committed metadata."""
import argparse
import json
import os
from pathlib import Path
import re

VERSION = re.compile(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(alpha|beta|rc)\.(0|[1-9]\d*))?')


def prepare_release(tag, version, changelog):
    if not VERSION.fullmatch(version) or tag != f'v{version}':
        raise ValueError('Release tag must exactly match v + manifest version (SemVer).')
    heading = re.search(r'^## '+re.escape(version)+r'(?:\s[^\n]*)?$',changelog,re.M)
    if not heading:
        raise ValueError('Add a changelog section for the manifest version before releasing.')
    section = changelog[heading.end():]
    section = re.split(r'^## ',section,maxsplit=1,flags=re.M)[0].strip()
    if not section:
        raise ValueError('The release changelog section must contain release notes.')
    prerelease = '-' in version
    if prerelease:
        section = '**Pre-release:** development/testing build; see the README for validation status.\n\n'+section
    return {'tag':tag,'version':version,'prerelease':prerelease,'notes':section+'\n'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('tag')
    parser.add_argument('--notes-file',type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root/'custom_components/esb_meter/manifest.json').read_text())
    result = prepare_release(args.tag,manifest['version'],(root/'CHANGELOG.md').read_text())
    if args.notes_file:
        args.notes_file.write_text(result['notes'])
    if output := os.environ.get('GITHUB_OUTPUT'):
        with open(output,'a') as stream:
            for key in ('version','tag','prerelease'):
                value = str(result[key]).lower() if isinstance(result[key],bool) else result[key]
                stream.write(f'{key}={value}\n')
    print(f"Validated {result['tag']} ({'pre-release' if result['prerelease'] else 'stable'})")


if __name__ == '__main__':
    main()
