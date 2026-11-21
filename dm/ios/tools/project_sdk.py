#!/usr/bin/env python3
"""Write an iOS project template (project.yml, project-framework.yml) for another SDK named by DM_SDK.

Usage: project_sdk.py <sdk> <template> <out>
  iphoneos  the template as it is
  xros      visionOS: every target's platform, the deployment target and device family 7 (Apple Vision)
The templates stay the iOS ones: build_ipa.sh and build_smoke.py write the visionOS project from them, so a change
to a template reaches both platforms. A template this does not recognise is an error, not a silent iOS project.
"""
import re
import sys

SDKS = ('iphoneos', 'xros')

# xros rewrites: the deployment target's SDK bucket, each target's platform, the device family. re.subn replaces every
# match (project-framework.yml has one platform: iOS per target); `not n` below turns an unmatched pattern into an
# error, so a template that stops carrying one of these lines is noticed here instead of producing an iOS project.
XROS = ((r'^(\s+)iOS: "(\d+\.\d+)"', r'\1visionOS: "\2"'),
        (r'^(\s+)platform: iOS$', r'\1platform: visionOS'),
        (r'TARGETED_DEVICE_FAMILY: "1,2"', 'TARGETED_DEVICE_FAMILY: "7"'))


def convert(text, sdk):
    """The iOS template `text` as the project for `sdk` ('iphoneos' unchanged, 'xros' for visionOS)."""
    if sdk == 'iphoneos':
        return text
    if sdk not in SDKS:
        raise ValueError(f'DM_SDK={sdk}: one of {", ".join(SDKS)}')
    for pattern, new in XROS:
        text, n = re.subn(pattern, new, text, flags=re.M)
        if not n:
            raise ValueError(f'no line matches {pattern!r}')
    return text


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    try:
        out = convert(open(sys.argv[2], encoding='utf-8').read(), sys.argv[1])
    except (OSError, ValueError) as e:
        sys.exit(f'project_sdk.py: {sys.argv[2]}: {e}')
    open(sys.argv[3], 'w', encoding='utf-8').write(out)
