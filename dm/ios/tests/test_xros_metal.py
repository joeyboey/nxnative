"""dm/ios/tools/xros_metal.py: the chain's iPhone compile standard, retargeted to visionOS without a change to ios_metal.py."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[3]
TOOLS = REPO / 'dm/ios/tools'
sys.path.insert(0, str(TOOLS))
import ios_metal
import xros_metal

MSL = '#include <metal_stdlib>\nusing namespace metal;\nfragment float4 f() { return float4(1); }\n'


def has_metal(sdk):
    return sys.platform == 'darwin' and subprocess.run(['xcrun', '-sdk', sdk, 'metal', '--version'], capture_output=True).returncode == 0


def platform_of(lib):
    out = subprocess.run(['xcrun', 'metal-readobj', '--file-headers', str(lib)], capture_output=True, text=True, check=True).stdout
    return next(line.split()[1] for line in out.splitlines() if line.strip().startswith('Platform:'))


class Variant(unittest.TestCase):
    def test_only_the_target_differs_from_the_iphone_standard(self):
        v = xros_metal.variant()
        ios_min = ios_metal.IOS_MIN_FLAG
        strip = lambda cmd, drop: [a for a in cmd[3:] if a not in drop]
        self.assertEqual(v.METAL[:3], ['xcrun', '-sdk', 'xros'])
        self.assertEqual(v.METALLIB[:3], ['xcrun', '-sdk', 'xros'])
        self.assertEqual(strip(v.METAL, ('-target', xros_metal.TARGET)), strip(ios_metal.METAL, (ios_min,)))
        self.assertEqual(v.METALLIB[3:], ios_metal.METALLIB[3:])
        self.assertEqual([a for a in v.IOS_FLAGS if a not in ('-target', xros_metal.TARGET)], [a for a in ios_metal.IOS_FLAGS if a != ios_min])
        self.assertIn(xros_metal.TARGET, v.METAL)
        self.assertNotIn(ios_min, v.METAL)

    def test_the_record_names_the_visionos_compile(self):
        v = xros_metal.variant()
        self.assertIn(' '.join(v.METAL), v.RECORD)
        self.assertIn(' '.join(v.METALLIB), v.RECORD)
        self.assertNotIn('iphoneos', v.RECORD)
        self.assertNotEqual(v.RECORD, ios_metal.RECORD)   # the compiled-shader cache is keyed by it: no iPhone library is taken for a visionOS one

    def test_the_iphone_module_is_left_as_it_was(self):
        before = (list(ios_metal.METAL), list(ios_metal.METALLIB), list(ios_metal.IOS_FLAGS), ios_metal.RECORD, ios_metal.IOS_MIN_FLAG)
        xros_metal.variant()
        self.assertEqual(before, (ios_metal.METAL, ios_metal.METALLIB, ios_metal.IOS_FLAGS, ios_metal.RECORD, ios_metal.IOS_MIN_FLAG))
        self.assertIs(sys.modules['ios_metal'], ios_metal)

    def test_a_tool_run_through_it_imports_the_visionos_standard(self):
        with tempfile.TemporaryDirectory() as tmp:
            tool = Path(tmp) / 'tool.py'   # imports the module the way dmsh_build.py does, also in a forked worker
            tool.write_text('import os, sys, multiprocessing\n'
                            f'sys.path.insert(0, {str(TOOLS)!r}); import ios_metal\n'
                            'def child(q): import ios_metal as again; q.put(again.RECORD)\n'
                            'if __name__ == "__main__":\n'
                            '    ctx = multiprocessing.get_context("fork"); q = ctx.Queue(); p = ctx.Process(target=child, args=(q,)); p.start()\n'
                            '    print(sys.argv[1:]); print(ios_metal.RECORD); print(q.get()); p.join()\n')
            r = subprocess.run([sys.executable, str(TOOLS / 'xros_metal.py'), str(tool), 'a', 'b'], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            args, record, forked = r.stdout.splitlines()
            self.assertEqual(args, "['a', 'b']")
            self.assertEqual(record, xros_metal.variant().RECORD)
            self.assertEqual(forked, record)
            plain = subprocess.run([sys.executable, str(tool)], capture_output=True, text=True)
            self.assertEqual(plain.stdout.splitlines()[1], ios_metal.RECORD)


@unittest.skipUnless(has_metal('xros') and has_metal('iphoneos'), 'needs the Metal toolchain with the iPhone and the visionOS SDK')
class Compile(unittest.TestCase):
    def test_the_library_is_a_visionos_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            msl = Path(tmp) / 'f.metal'; msl.write_text(MSL)
            xros_metal.variant().compile(str(msl), str(Path(tmp) / 'x.air'), str(Path(tmp) / 'x.metallib'))
            ios_metal.compile(str(msl), str(Path(tmp) / 'i.air'), str(Path(tmp) / 'i.metallib'))
            self.assertEqual(platform_of(Path(tmp) / 'x.metallib'), 'METALLIB_PLATFORM_XROS')
            self.assertEqual(platform_of(Path(tmp) / 'i.metallib'), 'METALLIB_PLATFORM_IOS')

    def test_the_iphoneos_sdk_writes_a_visionos_library_for_the_visionos_target(self):
        """vk_shaders.py and gl_shaders.py run `xcrun -sdk iphoneos metal` with IOS_FLAGS: with the visionOS target the library is a visionOS one."""
        flags = xros_metal.variant().IOS_FLAGS
        with tempfile.TemporaryDirectory() as tmp:
            msl = Path(tmp) / 'f.metal'; msl.write_text(MSL)
            for sdk in ('iphoneos', 'xros'):
                lib = Path(tmp) / f'{sdk}.metallib'
                subprocess.run(['xcrun', '-sdk', sdk, 'metal', '-w', *flags, '-o', str(lib), str(msl)], check=True, capture_output=True)
                self.assertEqual(platform_of(lib), 'METALLIB_PLATFORM_XROS')


if __name__ == '__main__':
    unittest.main()
