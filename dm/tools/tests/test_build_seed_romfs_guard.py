#!/usr/bin/env python3
"""Regression tests for --from-seed RomFS image identity verification against seed_romfs_id."""

import contextlib
import importlib.util
import io
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

from dm.tools import build_seed, romfs_image  # noqa: E402


def _load_nsp_script():
    spec = importlib.util.spec_from_file_location('build_ipa_from_nsp_romfs_guard_test', REPO / 'scripts' / 'build_ipa_from_nsp.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BuildSeedRomfsGuardTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _pack_romfs(self, name, files_dict):
        src = self.root / f'src_{name}'
        src.mkdir(parents=True)
        for rel, data in files_dict.items():
            p = src / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        img = self.root / f'{name}.img'
        romfs_image.pack(src, img)
        return img

    def _create_stardew_like_seed(self, seed_root, tid, v1_id):
        work = self.root / 'seed_src_work'
        ex = work / 'extract' / 'v1048576'
        (ex / 'ExeFS').mkdir(parents=True)
        (ex / 'ExeFS' / 'main').write_bytes(b'STARDEW_V1615_MAIN')
        (ex / 'ExeFS' / 'sdk').write_bytes(b'STARDEW_V1615_SDK')
        (work / 'shader' / 'pkg').mkdir(parents=True)
        (work / 'shader' / 'ios' / 'gl').mkdir(parents=True)
        (work / 'shader' / 'ios' / 'gl' / 'gl_shaders.metallib').write_bytes(b'IOS_GL_LIB')
        (work / 'shader' / 'gl').mkdir(parents=True)
        (work / 'shader' / 'gl' / 'gl_shaders.metallib').write_bytes(b'MAC_GL_LIB')

        info = build_seed.save_seed(
            seed_root,
            tid,
            {
                'title_id': tid,
                'pkg_ver': 'v1048576',
                'app_name': 'Stardew Valley',
                'app_version': '1.6.15',
                'gfx_api': 'gl',
                'romfs_identity': v1_id,
            },
            work,
        )
        self.assertTrue(info['valid'], info.get('reason'))
        return Path(info['path'])

    def test_from_seed_rejects_mismatched_romfs_from_and_app_romfs_without_polluting_seed(self):
        tid = '0100E65002BB8000'
        v1_img = self._pack_romfs('v1615', {
            'Content/Data/Achievements.xnb': b'v1.6.15-achievements',
            'Content/Strings/1_6_Strings.xnb': b'new-1.6-content',
            'Content/TileSheets/crops.xnb': b'crops-v1615',
        })
        v0_stale_img = self._pack_romfs('v0_base', {
            'Content/Data/Achievements.xnb': b'v1.0-old-base-only-payload-' + (b'X' * 512),
        })
        v1_id = romfs_image.identity(str(v1_img), tid, '1.6.15', 'v1048576')
        v0_id = romfs_image.identity(str(v0_stale_img), tid, '1.0.0', 'v0')
        self.assertFalse(romfs_image.same_identity(v1_id, v0_id))

        seed_root = self.root / 'seeds'
        seed_dir = self._create_stardew_like_seed(seed_root, tid, v1_id)

        # Also place the stale v0 image inside an existing .app bundle to test both ROMFS_FROM and .app candidates.
        stale_app = self.root / 'Stardew Valley.app'
        app_res = stale_app / 'Contents' / 'Resources'
        app_res.mkdir(parents=True)
        (app_res / 'romfs.img').write_bytes(v0_stale_img.read_bytes())

        nsp = _load_nsp_script()
        nsp.check_prereqs = lambda *a, **kw: None
        nsp.check_native_python = lambda *a, **kw: None
        nsp.apple_silicon = lambda: True
        nsp.rewrite = lambda py, repo, exe, patched, env, logs, g, provisional: g
        nsp.mac_host = lambda *a, **kw: (self.root / 'host_dummy', self.root / 'font_dummy')
        nsp.mac_app = lambda *a, **kw: None
        nsp.glslang_probe_bypass = True

        out_work = self.root / 'refresh_mac_work'
        buf = io.StringIO()
        orig_which = nsp.shutil.which
        nsp.shutil.which = lambda cmd, *a, **kw: '/usr/bin/true' if cmd == 'glslangValidator' else orig_which(cmd, *a, **kw)
        nsp.layer_shaders = lambda py, repo, api, name, roots, sh, key, logs, env, mac, options=(), from_seed=False: sh if mac else (sh / 'ios')
        with contextlib.redirect_stdout(buf):
            with self.assertRaises(nsp.BuildError):
                nsp.main(nsp.parse_args([
                    '--from-seed',
                    '--platform', 'mac-app',
                    '--title-id', tid,
                    '--seed-cache-dir', str(seed_root),
                    '--work-dir', str(out_work),
                    '--romfs-from', str(v0_stale_img),
                    '-o', str(stale_app),
                    '--app-name', 'Stardew Valley',
                ]))

        output = buf.getvalue()
        self.assertIn('identity mismatch', output)
        self.assertFalse((out_work / 'extract' / 'v1048576' / 'RomFS.img').exists())

        # seed.json's romfs_identity MUST remain intact (never overwritten with v0_stale_img's identity).
        after_seed = json.loads((seed_dir / 'seed.json').read_text(encoding='utf-8'))
        self.assertEqual(after_seed['romfs_identity'], v1_id)

    def test_from_seed_accepts_matching_romfs_image_with_dmmsltr1_trailer(self):
        tid = '0100E65002BB8000'
        v1_img = self._pack_romfs('v1615_trailer', {
            'Content/Data/Achievements.xnb': b'v1.6.15-achievements',
            'Content/Strings/1_6_Strings.xnb': b'new-1.6-content',
        })
        v1_id = romfs_image.identity(str(v1_img), tid, '1.6.15', 'v1048576')

        # Append a real DMMSLTR1 shader trailer to the image and verify identity still matches seed_romfs_id.
        msl_dir = self.root / 'msl_gl'
        (msl_dir / 'gl').mkdir(parents=True)
        (msl_dir / 'gl' / 'gl_shaders.metallib').write_bytes(b'DUMMY_METALLIB')
        romfs_image.append_msl(str(v1_img), str(msl_dir), gfx_api='gl')
        self.assertTrue(romfs_image.inspect_msl(str(v1_img))['has_trailer'])

        seed_root = self.root / 'seeds_trailer'
        seed_dir = self._create_stardew_like_seed(seed_root, tid, v1_id)

        nsp = _load_nsp_script()
        nsp.check_prereqs = lambda *a, **kw: None
        nsp.check_native_python = lambda *a, **kw: None
        nsp.apple_silicon = lambda: True
        orig_which = nsp.shutil.which
        nsp.shutil.which = lambda cmd, *a, **kw: '/usr/bin/true' if cmd == 'glslangValidator' else orig_which(cmd, *a, **kw)
        nsp.layer_shaders = lambda py, repo, api, name, roots, sh, key, logs, env, mac, options=(), from_seed=False: sh if mac else (sh / 'ios')

        def _stop_at_mac_host(*a, **kw):
            raise RuntimeError('REACHED_MAC_HOST_AFTER_ROMFS_REUSE')

        nsp.mac_host = _stop_at_mac_host
        nsp.rewrite = lambda py, repo, exe, patched, env, logs, g, provisional: g
        nsp.uses_jit = lambda *a, **kw: False

        out_work = self.root / 'refresh_ok_work'
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            with self.assertRaisesRegex(RuntimeError, 'REACHED_MAC_HOST_AFTER_ROMFS_REUSE'):
                nsp.main(nsp.parse_args([
                    '--from-seed',
                    '--platform', 'mac',
                    '--title-id', tid,
                    '--seed-cache-dir', str(seed_root),
                    '--work-dir', str(out_work),
                    '--romfs-from', str(v1_img),
                    '--app-name', 'Stardew Valley',
                ]))

        self.assertIn('reusing the RomFS image from', buf.getvalue())
        self.assertTrue((out_work / 'extract' / 'v1048576' / 'RomFS.img').is_file())
        after_seed = json.loads((seed_dir / 'seed.json').read_text(encoding='utf-8'))
        self.assertEqual(after_seed['romfs_identity'], v1_id)

    def test_from_seed_shell_framework_ignores_mismatched_romfs_from_and_preserves_seed_identity(self):
        tid = '0100E65002BB8000'
        v1_img = self._pack_romfs('v1615_fw', {
            'Content/Data/Achievements.xnb': b'v1.6.15-achievements',
            'Content/Strings/1_6_Strings.xnb': b'new-1.6-content',
        })
        v0_stale_img = self._pack_romfs('v0_stale_fw', {
            'Content/Data/Achievements.xnb': b'v1.0-old-base-only-' + (b'Z' * 256),
        })
        v1_id = romfs_image.identity(str(v1_img), tid, '1.6.15', 'v1048576')
        seed_root = self.root / 'seeds_fw'
        seed_dir = self._create_stardew_like_seed(seed_root, tid, v1_id)

        nsp = _load_nsp_script()
        nsp.check_prereqs = lambda *a, **kw: None
        nsp.check_native_python = lambda *a, **kw: None
        nsp.rewrite = lambda py, repo, exe, patched, env, logs, g, provisional: g
        nsp.uses_jit = lambda *a, **kw: False
        nsp.resolve_team_id = lambda *a, **kw: 'TEAM123456'
        nsp.layer_shaders = lambda py, repo, api, name, roots, sh, key, logs, env, mac, options=(), from_seed=False: sh if mac else (sh / 'ios')

        captured_ipa_env = {}
        fw_out = self.root / 'fw_out'

        def _fake_run(cmd, log, env=None, on_line=None, fatal=True):
            if env and 'DM_FRAMEWORK_ONLY' in env:
                captured_ipa_env.update(env)
                fw_dir = Path(env['DM_FRAMEWORK_ONLY']) / f'DmGame{tid}.framework'
                fw_dir.mkdir(parents=True, exist_ok=True)
                (fw_dir / 'game.manifest.json').write_text(
                    json.dumps({'title_id': tid, 'romfs_identity': env.get('DM_ROMFS_IDENTITY', '')}),
                    encoding='utf-8',
                )
            return 0

        nsp.run = _fake_run
        out_work = self.root / 'refresh_fw_work'
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            nsp.main(nsp.parse_args([
                '--from-seed',
                '--shell-framework', str(fw_out),
                '--title-id', tid,
                '--seed-cache-dir', str(seed_root),
                '--work-dir', str(out_work),
                '--romfs-from', str(v0_stale_img),
                '--app-name', 'Stardew Valley',
            ]))

        self.assertIn('identity mismatch', buf.getvalue())
        self.assertFalse((out_work / 'extract' / 'v1048576' / 'RomFS.img').exists())
        self.assertEqual(captured_ipa_env.get('DM_ROMFS_IDENTITY'), v1_id)
        after_seed = json.loads((seed_dir / 'seed.json').read_text(encoding='utf-8'))
        self.assertEqual(after_seed['romfs_identity'], v1_id)


if __name__ == '__main__':
    unittest.main()
