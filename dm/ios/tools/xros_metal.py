#!/usr/bin/env python3
"""The shader packaging chain's compile standard for visionOS, without a change to the chain.

A Metal library is tagged with its platform, so the libraries ios_metal.py compiles for the iPhone are not visionOS libraries.
The chain's tools take their iPhone compile standard from the module ios_metal (dmsh_build.py's `import ios_metal`; vk_shaders.py
and gl_shaders.py's `from ios_metal import IOS_FLAGS`); this file gives them a second copy of that module with one thing changed,
the target: visionOS (TARGET) in place of the iPhone's deployment target (IOS_MIN_FLAG), and the xros SDK. Every other flag is
read from ios_metal.py as it is, so a change there reaches visionOS too.

  xros_metal.py <tool.py> [arguments...]   runs the tool with that copy in place of ios_metal (IOSOUT, --ios: where its
                                           "iOS" libraries go, here the visionOS ones; give them a folder of their own)

What this relies on (all checked against ios_metal.py and the tools at this pin):
  · a tool imports ios_metal by module name, so replacing sys.modules['ios_metal'] reaches it; a tool that started Python again
    (a fresh interpreter) would not, but dmsh_build.py's worker processes are forked, so they inherit the copy too;
  · ios_metal.METAL and ios_metal.IOS_FLAGS each carry IOS_MIN_FLAG once, and METAL and METALLIB are the two `xcrun -sdk
    iphoneos` commands (this file refuses anything else);
  · RECORD is built from ' '.join(METAL) and ' '.join(METALLIB), so the target can be swapped inside it.

What follows from the changed record (RECORD names the compile, as ios_metal's does): the reflection files record the visionOS
compile, and the compiled-shader cache (dmsh_mtlcache.py, keyed by the record) keeps visionOS libraries apart from the iPhone's.
The tools still name the iphoneos SDK where they have it written out (vk_shaders.py and gl_shaders.py's xcrun): with a visionOS
target the Metal compiler writes a visionOS library from either SDK (dm/ios/tests/test_xros_metal.py checks both).

The rest of the module (METAL4 / METAL4_FLAG, DEPTH, compile) is carried unchanged in the copy."""
import importlib.util, os, runpy, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SDK = 'xros'
TARGET = 'air64-apple-xros26.0'   # visionOS 26.0, the app's deployment target (project.yml's iOS 26.0, written as visionOS by project_sdk.py); the Metal compiler has no -m<os>-version-min for visionOS


def variant():
    """ios_metal.py loaded a second time, with the visionOS target. The module ios_metal itself is not touched."""
    spec = importlib.util.spec_from_file_location('ios_metal', os.path.join(HERE, 'ios_metal.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    ios_min, target = m.IOS_MIN_FLAG, ['-target', TARGET]

    def retarget(cmd):   # one of its xcrun commands: the SDK, and the deployment target flag wherever it is
        if cmd[:3] != ['xcrun', '-sdk', 'iphoneos']: raise RuntimeError(f'ios_metal.py: an unexpected command {cmd}')
        out = ['xcrun', '-sdk', SDK]
        for a in cmd[3:]: out += target if a == ios_min else [a]
        return out

    if ios_min not in m.METAL or ios_min not in m.IOS_FLAGS: raise RuntimeError('ios_metal.py: IOS_MIN_FLAG is not in METAL and IOS_FLAGS')
    record = m.RECORD
    old_metal, old_lib = ' '.join(m.METAL), ' '.join(m.METALLIB)
    m.IOS_FLAGS = [x for a in m.IOS_FLAGS for x in (target if a == ios_min else [a])]
    m.METAL, m.METALLIB = retarget(m.METAL), retarget(m.METALLIB)
    if old_metal not in record or old_lib not in record: raise RuntimeError('ios_metal.py: RECORD does not name METAL and METALLIB')
    m.RECORD = record.replace(old_metal, ' '.join(m.METAL)).replace(old_lib, ' '.join(m.METALLIB))
    m.IOS_MIN_FLAG = ' '.join(target)   # for a reader of the one flag; the lists above carry it as two arguments
    return m


def install():
    """Put the visionOS copy where `import ios_metal` finds it."""
    m = variant()
    sys.modules['ios_metal'] = m
    return m


if __name__ == '__main__':
    if len(sys.argv) < 2: sys.exit(__doc__)
    install()
    tool = sys.argv[1]
    sys.argv = sys.argv[1:]
    sys.path.insert(0, os.path.dirname(os.path.abspath(tool)))   # as python <tool.py> does
    runpy.run_path(tool, run_name='__main__')
