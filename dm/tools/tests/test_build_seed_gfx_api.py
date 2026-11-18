#!/usr/bin/env python3
"""Regression tests for GL/Vulkan gfx_api inference and self-healing in build_seed.py and --from-seed."""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from dm.tools import build_seed  # noqa: E402


def _load_nsp_script():
    spec = importlib.util.spec_from_file_location('build_ipa_from_nsp_seed_test', REPO / 'scripts' / 'build_ipa_from_nsp.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BuildSeedGfxApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _make_legacy_balatro_work(self, tid='0100CD801CE5E000', pkg_ver='v851968'):
        work = self.root / f'{tid}_build'
        ver_dir = work / 'extract' / pkg_ver
        (ver_dir / 'ExeFS').mkdir(parents=True)
        (ver_dir / 'ExeFS' / 'main').write_bytes(b'DUMMY_MAIN')
        (ver_dir / 'ExeFS' / 'sdk').write_bytes(b'DUMMY_SDK')
        (ver_dir / 'gl_capture').mkdir()
        (ver_dir / 'gl_capture' / '0123456789abcdef.glsl').write_text('void main() {}\n', encoding='utf-8')
        (ver_dir / 'RomFS.img.id').write_text(
            f'DMROMFSID1 title={tid} version={pkg_ver} build=- size=56066346 index=3129a67f6072f1be\n',
            encoding='utf-8',
        )

        (work / 'shader' / 'pkg').mkdir(parents=True)
        (work / 'shader' / 'gl').mkdir(parents=True)
        (work / 'shader' / 'gl' / 'gl_shaders.metallib').write_bytes(b'MAC_GL_METALLIB')
        (work / 'shader' / 'gl.key').write_text('gl-key\n1 shaders\n', encoding='utf-8')
        (work / 'shader' / 'ios' / 'gl').mkdir(parents=True)
        (work / 'shader' / 'ios' / 'gl' / 'gl_shaders.metallib').write_bytes(b'IOS_GL_METALLIB')
        (work / 'shader' / 'ios' / 'gl.key').write_text('gl-key\n1 shaders\n', encoding='utf-8')
        # Simulate stale empty NVN b-*.img left by an earlier misclassified run
        (work / 'shader' / 'ios' / 'b-deadbeef1234.img').write_bytes(b'NOT_A_VALID_NVN_LIB')
        return work

    def test_adopt_legacy_gl_work_infers_gl_drops_stale_b_img_and_restores_mac_gl_and_captures(self):
        tid = '0100CD801CE5E000'
        work = self._make_legacy_balatro_work(tid)
        seed_root = self.root / 'seeds'

        info = build_seed.adopt_legacy_work(seed_root, tid, work)
        self.assertTrue(info['valid'], info.get('reason'))
        self.assertEqual(info['seed']['gfx_api'], 'gl')
        self.assertEqual(info['seed']['chain'], '')

        seed_dir = Path(info['path'])
        self.assertTrue((seed_dir / 'shader' / 'ios' / 'gl' / 'gl_shaders.metallib').is_file())
        self.assertTrue((seed_dir / 'shader' / 'gl' / 'gl_shaders.metallib').is_file())
        self.assertTrue((seed_dir / 'shader' / 'gl.key').is_file())
        self.assertTrue((seed_dir / 'captures' / 'gl_capture' / '0123456789abcdef.glsl').is_file())
        self.assertFalse(any(p.name.startswith('b-') for p in (seed_dir / 'shader' / 'ios').iterdir()))

        fresh_work = self.root / 'fresh_work'
        staged = build_seed.stage_seed_to_work(seed_dir, fresh_work)
        self.assertTrue((fresh_work / 'shader' / 'ios' / 'gl' / 'gl_shaders.metallib').is_file())
        self.assertTrue((fresh_work / 'shader' / 'gl' / 'gl_shaders.metallib').is_file())
        self.assertTrue((fresh_work / 'shader' / 'gl.key').is_file())
        self.assertTrue((Path(staged['extract_dir']) / 'gl_capture' / '0123456789abcdef.glsl').is_file())

    def test_inspect_and_prepare_from_seed_self_heal_corrupted_nvn_seed_for_gl_game(self):
        tid = '0100CD801CE5E000'
        work = self._make_legacy_balatro_work(tid)
        seed_root = self.root / 'seeds_corrupt'
        info = build_seed.adopt_legacy_work(seed_root, tid, work)
        seed_dir = Path(info['path'])

        # Corrupt seed.json back to the pre-fix rc4 state ("gfx_api": "nvn", "chain": "d254f45e8002") and remove NVN tables
        seed_json = seed_dir / 'seed.json'
        corrupted = json.loads(seed_json.read_text(encoding='utf-8'))
        corrupted['gfx_api'] = 'nvn'
        corrupted['chain'] = 'd254f45e8002'
        seed_json.write_text(json.dumps(corrupted, indent=2) + '\n', encoding='utf-8')
        for tname in ('c1_full.tsv', 'cb_extent.tsv'):
            tp = seed_dir / 'shader' / 'tables' / tname
            if tp.exists():
                tp.unlink()

        inspected = build_seed.inspect_seed(seed_dir, title_id=tid)
        self.assertTrue(inspected['valid'], inspected.get('reason'))
        self.assertEqual(inspected['seed']['gfx_api'], 'gl')
        self.assertEqual(inspected['seed']['chain'], '')

        nsp = _load_nsp_script()
        sys.path.insert(0, str(REPO / 'dm' / 'games'))
        import game as gamemod

        out_work = self.root / 'refresh_work'
        args = nsp.parse_args(['--from-seed', '--title-id', tid, '--seed-cache-dir', str(seed_root), '--work-dir', str(out_work)])
        prep = nsp._prepare_from_seed(args, 'auto', seed_root, [], gamemod, dict(os.environ), False)
        self.assertEqual(prep['gfx'], 'gl')
        self.assertTrue((prep['game'] / 'gl_capture' / '0123456789abcdef.glsl').is_file())

    def test_gl_and_vulkan_seeds_allow_empty_shader_outputs_while_nvn_rejects(self):
        # Well Dweller (010076E026AC4000) is an OpenGL game with no NVN metal-sources or shader/ios outputs on Mac builds.
        for api, tid in (('gl', '010076E026AC4000'), ('vulkan', '0100535012974000')):
            work = self.root / f'{tid}_{api}_empty_shaders'
            ver_dir = work / 'extract' / 'v0'
            (ver_dir / 'ExeFS').mkdir(parents=True)
            (ver_dir / 'ExeFS' / 'main').write_bytes(b'DUMMY_MAIN')
            (work / 'shader' / 'pkg').mkdir(parents=True)
            seed_root = self.root / f'seeds_{api}'

            info = build_seed.save_seed(
                seed_root,
                tid,
                {'title_id': tid, 'pkg_ver': 'v0', 'gfx_api': api},
                work,
            )
            self.assertTrue(info['valid'], info.get('reason'))
            self.assertEqual(info['seed']['gfx_api'], api)
            self.assertFalse(info['has_shader_ios'])
            self.assertFalse(info['has_metal_sources'])

        # NVN seeds without shader/ios or metal-sources must still be rejected.
        nvn_tid = '01007EF00011E000'
        nvn_work = self.root / 'nvn_empty_shaders'
        (nvn_work / 'extract' / 'v0' / 'ExeFS').mkdir(parents=True)
        (nvn_work / 'extract' / 'v0' / 'ExeFS' / 'main').write_bytes(b'DUMMY_MAIN')
        (nvn_work / 'shader' / 'pkg').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'neither shader/ios outputs nor metal-sources'):
            build_seed.save_seed(
                self.root / 'seeds_nvn_empty',
                nvn_tid,
                {'title_id': nvn_tid, 'pkg_ver': 'v0', 'gfx_api': 'nvn'},
                nvn_work,
            )


if __name__ == '__main__':
    unittest.main()
