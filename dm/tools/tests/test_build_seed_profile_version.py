#!/usr/bin/env python3
"""Regression tests for stale build-seed profile versions across save_seed, adopt_legacy_work, and --from-seed."""

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
if str(REPO / 'dm' / 'games') not in sys.path:
    sys.path.insert(0, str(REPO / 'dm' / 'games'))

import game as gamemod  # noqa: E402
from dm.tools import build_seed  # noqa: E402


def _load_nsp_script():
    spec = importlib.util.spec_from_file_location('build_ipa_from_nsp_profile_ver_test', REPO / 'scripts' / 'build_ipa_from_nsp.py')
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class BuildSeedProfileVersionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='test_seed_prof_ver_')
        self.root = Path(self._tmp.name)
        self._saved_env = dict(os.environ)
        os.environ.pop('DM_GAMES_DIR', None)
        os.environ.pop('DM_GAMES_FIRST', None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved_env)
        self._tmp.cleanup()

    def _make_balatro_work(self, tid='0100CD801CE5E000', pkg_ver='v851968', stale_prof_ver=None, name=None):
        work = self.root / (name or f'{tid}_build')
        ver_dir = work / 'extract' / pkg_ver
        (ver_dir / 'ExeFS').mkdir(parents=True)
        (ver_dir / 'ExeFS' / 'main').write_bytes(b'BALATRO_MAIN')
        (ver_dir / 'ExeFS' / 'sdk').write_bytes(b'BALATRO_SDK')
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

        if stale_prof_ver:
            prof = work / 'profile' / tid
            prof.mkdir(parents=True)
            (prof / 'game.tsv').write_text(
                f'# Derived from game profile {tid} by scripts/build_ipa_from_nsp.py for {stale_prof_ver} (1.0.0)\n'
                f'title_id\t{tid}\n'
                f'program_id\t{tid}\n'
                f'name\tBalatro\n'
                f'ipa\tBalatro.ipa\n'
                f'build\t{stale_prof_ver}\t1.0.0\tmain=0000000000000000\tsdk=1111111111111111\n'
                f'package\t{stale_prof_ver}\n'
                f'shader_versions\t{stale_prof_ver}\n'
                f'modules\tmain sdk\n'
                f'romfs_marker\tresources/fonts/NotoSansSC-Bold.ttf\n'
                f'mem_reserve\t0xf0000000\n'
                f'mem_alias\t0x10000000\n'
                f'mem_stack\t0x10000000\n'
                f'app_pool\t0xCD500000\n',
                encoding='utf-8',
            )
            (work / 'profile' / f'{tid}.derived.key').write_text('stale-key\n', encoding='utf-8')
        return work

    def test_read_game_tsv_build_versions_extracts_all_build_rows(self):
        tsv = self.root / 'multi_build.tsv'
        tsv.write_text(
            '# header\n'
            'title_id\t0100CD801CE5E000\n'
            'build\tv0\t1.0.0\tmain=aaa\n'
            'build\tv851968\t1.1.3\tmain=bbb\n'
            '# build\tv999\tignored\n',
            encoding='utf-8',
        )
        self.assertEqual(build_seed._read_game_tsv_build_versions(str(tsv)), {'v0', 'v851968'})

    def test_adopt_legacy_work_ignores_stale_v0_work_profile_when_registered_profile_matches(self):
        tid = '0100CD801CE5E000'
        work = self._make_balatro_work(tid=tid, pkg_ver='v851968', stale_prof_ver='v0')
        seed_root = self.root / 'seeds_registered_heal'

        res = build_seed.adopt_legacy_work(seed_root, tid, work)
        self.assertTrue(res['valid'], res.get('reason'))
        self.assertEqual(res['seed']['pkg_ver'], 'v851968')
        self.assertEqual(res['seed']['profile_kind'], 'registered')
        self.assertFalse((Path(res['path']) / 'profile').exists())

    def test_adopt_legacy_work_rejects_mismatched_work_profile_without_matching_registered_build(self):
        tid = '0100CD801CE5E000'
        # Balatro's registered profile only defines v851968; adopting a v65536 work folder
        # with a leftover v0 work/profile must fail instead of saving a stale derived copy.
        work = self._make_balatro_work(tid=tid, pkg_ver='v65536', stale_prof_ver='v0')
        seed_root = self.root / 'seeds_mismatch_reject'

        with self.assertRaises(ValueError):
            build_seed.adopt_legacy_work(seed_root, tid, work)
        self.assertFalse((seed_root / tid).exists())

    def test_from_seed_self_heals_stale_derived_profile_without_polluting_dm_games_dir(self):
        tid = '0100CD801CE5E000'
        work = self._make_balatro_work(tid=tid, pkg_ver='v851968', stale_prof_ver='v0')
        seed_root = self.root / 'seeds_corrupt'
        seed_dir = seed_root / tid

        # Build a clean v851968 seed first, then manually inject the pre-fix stale v0 profile copy.
        clean_work = self._make_balatro_work(tid=tid, pkg_ver='v851968', stale_prof_ver=None, name='clean_work')
        res = build_seed.save_seed(
            seed_root,
            tid,
            {'title_id': tid, 'pkg_ver': 'v851968', 'gfx_api': 'gl', 'profile_kind': 'registered'},
            clean_work,
        )
        self.assertTrue(res['valid'], res.get('reason'))
        (seed_dir / 'profile' / tid).mkdir(parents=True)
        (seed_dir / 'profile' / tid / 'game.tsv').write_text(
            (work / 'profile' / tid / 'game.tsv').read_text(encoding='utf-8'),
            encoding='utf-8',
        )
        seed_json = seed_dir / 'seed.json'
        seed_data = json.loads(seed_json.read_text(encoding='utf-8'))
        seed_data['profile_kind'] = 'derived'
        seed_json.write_text(json.dumps(seed_data, indent=2) + '\n', encoding='utf-8')

        nsp = _load_nsp_script()
        refresh_work = self.root / 'refresh_work'
        args = nsp.parse_args([
            '--from-seed',
            '--title-id', tid,
            '--seed-cache-dir', str(seed_root),
            '--work-dir', str(refresh_work),
        ])
        env = {}
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            prep = nsp._prepare_from_seed(args, 'auto', seed_root, [], gamemod, env, False)

        self.assertEqual(prep['pkg_ver'], 'v851968')
        self.assertEqual(prep['g'].id, tid)
        self.assertEqual(prep['g'].dir.resolve(), (REPO / 'dm' / 'games' / tid).resolve())
        self.assertEqual(prep['reg'].id, tid)
        self.assertFalse(prep['derived'])
        self.assertFalse(prep['provisional'])
        self.assertFalse((refresh_work / 'profile' / tid).exists())
        self.assertNotIn('DM_GAMES_DIR', env)
        self.assertNotIn('DM_GAMES_FIRST', env)
        self.assertNotIn('DM_GAMES_DIR', os.environ)
        self.assertNotIn('DM_GAMES_FIRST', os.environ)
        self.assertIn('falling back to registered profile', buf.getvalue())

        # Subsequent status report must succeed without KeyError.
        report_buf = io.StringIO()
        with contextlib.redirect_stdout(report_buf):
            nsp.report_profile_status(prep['g'], prep['reg'], prep['pkg_ver'], prep['derived'], prep['provisional'], 'Mac app')

    def test_from_seed_raises_clean_build_error_when_repo_lacks_seed_version(self):
        tid = '0100CD801CE5E000'
        work = self._make_balatro_work(tid=tid, pkg_ver='v65536', stale_prof_ver='v0')
        seed_root = self.root / 'seeds_unregistered_ver'
        seed_dir = seed_root / tid

        clean_work = self.root / 'clean_v65536'
        work.rename(clean_work)
        stale_tsv_text = (clean_work / 'profile' / tid / 'game.tsv').read_text(encoding='utf-8')
        (clean_work / 'profile' / tid / 'game.tsv').unlink()
        (clean_work / 'profile' / tid).rmdir()
        res = build_seed.save_seed(
            seed_root,
            tid,
            {'title_id': tid, 'pkg_ver': 'v65536', 'gfx_api': 'gl', 'profile_kind': 'registered'},
            clean_work,
        )
        self.assertTrue(res['valid'], res.get('reason'))
        (seed_dir / 'profile' / tid).mkdir(parents=True)
        (seed_dir / 'profile' / tid / 'game.tsv').write_text(stale_tsv_text, encoding='utf-8')
        seed_json = seed_dir / 'seed.json'
        seed_data = json.loads(seed_json.read_text(encoding='utf-8'))
        seed_data['profile_kind'] = 'derived'
        seed_json.write_text(json.dumps(seed_data, indent=2) + '\n', encoding='utf-8')

        nsp = _load_nsp_script()
        refresh_work = self.root / 'refresh_fail_work'
        args = nsp.parse_args([
            '--from-seed',
            '--title-id', tid,
            '--seed-cache-dir', str(seed_root),
            '--work-dir', str(refresh_work),
        ])
        env = {}
        with self.assertRaises(nsp.BuildError) as cm:
            nsp._prepare_from_seed(args, 'auto', seed_root, [], gamemod, env, False)
        msg = str(cm.exception)
        self.assertIn('build seed profile describes v0, but seed package is v65536', msg)
        self.assertNotIn(';', msg)
        self.assertFalse((refresh_work / 'profile' / tid).exists())
        self.assertNotIn('DM_GAMES_DIR', env)
        self.assertNotIn('DM_GAMES_FIRST', env)
        self.assertNotIn('DM_GAMES_DIR', os.environ)
        self.assertNotIn('DM_GAMES_FIRST', os.environ)

    def test_report_profile_status_handles_missing_build_key_and_persist_cleans_stale_work_profile(self):
        tid = '0100CD801CE5E000'
        nsp = _load_nsp_script()
        reg_g = gamemod.load(tid)

        # Construct a stale v0 profile that lacks v851968 and verify report_profile_status does not KeyError.
        stale_dir = self.root / 'stale_report' / tid
        stale_dir.mkdir(parents=True)
        (stale_dir / 'game.tsv').write_text(
            f'title_id\t{tid}\nprogram_id\t{tid}\nname\tBalatro\nipa\tBalatro.ipa\n'
            'build\tv0\t1.0.0\tmain=0000000000000000\nmodules\tmain\nromfs_marker\tREADME\n'
            'mem_reserve\t0xf0000000\nmem_alias\t0x10000000\nmem_stack\t0x10000000\napp_pool\t0xCD500000\n',
            encoding='utf-8',
        )
        stale_g = gamemod.Game(stale_dir)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            nsp.report_profile_status(stale_g, reg_g, 'v851968', 'seeded derived profile', False, 'Mac app')
        self.assertIn('v851968', buf.getvalue())

        # Full registered build persisting seed must clean up leftover stale work/profile/<gid>.
        work = self._make_balatro_work(tid=tid, pkg_ver='v851968', stale_prof_ver='v0')
        self.assertTrue((work / 'profile' / tid).is_dir())
        seed_root = self.root / 'seeds_clean_stale'
        nsp._persist_build_seed(
            seed_root,
            int(tid, 16),
            reg_g,
            False,
            False,
            'v851968',
            'Balatro',
            '1.1.3',
            f'com.nxnative.g{tid.lower()}',
            'en-US',
            ['en-US'],
            'gl',
            REPO,
            work,
            work / 'extract' / 'v851968',
            work / 'shader',
            None,
            False,
            None,
            None,
            None,
        )
        insp = build_seed.inspect_seed(seed_root, title_id=tid)
        self.assertTrue(insp['valid'], insp.get('reason'))
        self.assertEqual(insp['seed']['profile_kind'], 'registered')
        self.assertFalse((work / 'profile' / tid).exists())
        self.assertFalse((work / 'profile' / f'{tid}.derived.key').exists())


if __name__ == '__main__':
    unittest.main()
