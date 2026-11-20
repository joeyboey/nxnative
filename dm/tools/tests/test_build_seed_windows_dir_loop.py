#!/usr/bin/env python3
"""Regression tests for Windows DirEntry (0, 0) stats, exFAT st_ino == 0, reparse points, and real directory cycles."""

import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from dm.tools import build_seed, shader_lib  # noqa: E402

_REPARSE_ATTR = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x0400)


class _StatProxy:
    """Lightweight os.stat_result proxy with explicit st_dev/st_ino/st_file_attributes overrides."""

    def __init__(self, real_st, *, st_dev=None, st_ino=None, st_file_attributes=0):
        self.st_mode = real_st.st_mode
        self.st_dev = real_st.st_dev if st_dev is None else st_dev
        self.st_ino = real_st.st_ino if st_ino is None else st_ino
        self.st_nlink = real_st.st_nlink
        self.st_uid = real_st.st_uid
        self.st_gid = real_st.st_gid
        self.st_size = real_st.st_size
        self.st_atime = real_st.st_atime
        self.st_mtime = real_st.st_mtime
        self.st_ctime = real_st.st_ctime
        self.st_file_attributes = st_file_attributes


class _DirEntryProxy:
    """Wraps a real os.DirEntry while overriding stat() to emulate Windows FindFirstFileW behavior."""

    def __init__(self, real_entry, *, zero_dir_stat=False, force_zero_all=False):
        self._entry = real_entry
        self._zero_dir_stat = zero_dir_stat
        self._force_zero_all = force_zero_all
        self.name = real_entry.name
        self.path = real_entry.path

    def is_symlink(self):
        return self._entry.is_symlink()

    def is_dir(self, *, follow_symlinks=True):
        return self._entry.is_dir(follow_symlinks=follow_symlinks)

    def is_file(self, *, follow_symlinks=True):
        return self._entry.is_file(follow_symlinks=follow_symlinks)

    def stat(self, *, follow_symlinks=True):
        real_st = self._entry.stat(follow_symlinks=follow_symlinks)
        if not follow_symlinks and (self._force_zero_all or (self._zero_dir_stat and stat.S_ISDIR(real_st.st_mode))):
            return _StatProxy(real_st, st_dev=0, st_ino=0)
        return real_st

    def __fspath__(self):
        return self.path


class _ScandirCtx:
    def __init__(self, real_ctx, **proxy_kw):
        self._real_ctx = real_ctx
        self._proxy_kw = proxy_kw

    def __iter__(self):
        try:
            for e in self._real_ctx:
                yield _DirEntryProxy(e, **self._proxy_kw)
        finally:
            self._real_ctx.close()

    def __enter__(self):
        self._real_ctx.__enter__()
        return self

    def __exit__(self, exc_type, exc, tb):
        return self._real_ctx.__exit__(exc_type, exc, tb)

    def close(self):
        self._real_ctx.close()


class BuildSeedWindowsDirLoopTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix='test_seed_win_loop_')
        self.root = Path(self._tmp.name)
        self.root_abs = os.path.normpath(os.path.abspath(self.root))
        self.root_real = os.path.normpath(os.path.realpath(self.root))

    def tearDown(self):
        self._tmp.cleanup()

    def _in_root(self, path):
        # Never call os.path.realpath here because fake_lstat delegates to _in_root.
        norm_p = os.path.normpath(os.path.abspath(os.fspath(path)))
        for base in (self.root_abs, self.root_real):
            try:
                if os.path.commonpath([base, norm_p]) == base:
                    return True
            except ValueError:
                pass
        return False

    def _make_work_tree(self, name='work'):
        work = self.root / name
        (work / 'extract' / 'v0' / 'ExeFS').mkdir(parents=True)
        (work / 'extract' / 'v0' / 'ExeFS' / 'main').write_bytes(b'MAIN_BIN_0123')
        (work / 'extract' / 'v0' / 'html' / 'sub').mkdir(parents=True)
        (work / 'extract' / 'v0' / 'html' / 'index.html').write_text('<html></html>\n', encoding='utf-8')
        (work / 'extract' / 'v0' / 'html' / 'sub' / 'manual.html').write_text('<p>ok</p>\n', encoding='utf-8')
        (work / 'shader' / 'pkg').mkdir(parents=True)
        (work / 'shader' / 'pkg' / 'pkg.bin').write_bytes(b'PKG_PAYLOAD')
        (work / 'shader' / 'tables').mkdir(parents=True)
        (work / 'shader' / 'tables' / 'c1_full.tsv').write_text('# c1\n', encoding='utf-8')
        (work / 'shader' / 'tables' / 'cb_extent.tsv').write_text('# cb\n', encoding='utf-8')
        (work / 'shader' / 'ios').mkdir(parents=True)
        shader_lib.write(
            {'0000000000000001_b.metallib': b'IOS_METALLIB', 'inputs.tsv': b'inputs\tst\n'},
            str(work / 'shader' / 'ios' / 'b-1111222233334444.img'),
        )
        return work

    def _expected_tree_bytes(self, seed_dir):
        total = 0
        for dp, _, fns in os.walk(seed_dir):
            for fn in fns:
                total += os.path.getsize(os.path.join(dp, fn))
        return total

    def test_windows_scandir_zero_inode_dirs_pass_without_false_loop_and_preserve_byte_quota(self):
        tid = '010015100B514000'
        work = self._make_work_tree('win_work')
        seed_root = self.root / 'seeds'
        orig_scandir = os.scandir

        def fake_scandir(path='.'):
            ctx = orig_scandir(path)
            if self._in_root(path):
                return _ScandirCtx(ctx, zero_dir_stat=True)
            return ctx

        with patch('os.scandir', side_effect=fake_scandir):
            res = build_seed.adopt_legacy_work(seed_root, tid, work)

        self.assertTrue(res['valid'], res.get('reason'))
        seed_dir = Path(res['path'])
        self.assertTrue((seed_dir / 'ExeFS' / 'main').is_file())
        self.assertTrue((seed_dir / 'html' / 'sub' / 'manual.html').is_file())

        with patch('os.scandir', side_effect=fake_scandir):
            inspected = build_seed.inspect_seed(seed_root, title_id=tid)
        self.assertTrue(inspected['valid'], inspected.get('reason'))
        self.assertEqual(inspected['size_bytes'], self._expected_tree_bytes(seed_dir))

    def test_exfat_zero_lstat_inode_nested_dirs_pass_stage_and_inspect(self):
        tid = '010015100B514000'
        work = self._make_work_tree('exfat_work')
        seed_root = self.root / 'exfat_seeds'
        orig_scandir = os.scandir
        orig_lstat = os.lstat

        def fake_scandir(path='.'):
            ctx = orig_scandir(path)
            if self._in_root(path):
                return _ScandirCtx(ctx, force_zero_all=True)
            return ctx

        def fake_lstat(path, *, dir_fd=None):
            real_st = orig_lstat(path, dir_fd=dir_fd)
            if dir_fd is None and self._in_root(path):
                return _StatProxy(real_st, st_dev=0, st_ino=0)
            return real_st

        with patch('os.scandir', side_effect=fake_scandir), patch('os.lstat', side_effect=fake_lstat):
            res = build_seed.adopt_legacy_work(seed_root, tid, work)
            inspected = build_seed.inspect_seed(seed_root, title_id=tid)

        self.assertTrue(res['valid'], res.get('reason'))
        self.assertTrue(inspected['valid'], inspected.get('reason'))
        self.assertEqual(inspected['size_bytes'], self._expected_tree_bytes(res['path']))

    def test_real_symlink_windows_reparse_point_and_directory_loops_are_still_rejected(self):
        # 1. Real POSIX symlink inside tree must fail both inspect and _stage_tree.
        sym_tree = self.root / 'sym_tree'
        (sym_tree / 'real_dir').mkdir(parents=True)
        (sym_tree / 'real_dir' / 'f.txt').write_text('x', encoding='utf-8')
        os.symlink(sym_tree / 'real_dir', sym_tree / 'link_dir')
        ok, reason, _ = build_seed._verify_no_symlinks_or_unsafe_names(sym_tree)
        self.assertFalse(ok)
        self.assertIn('symlink', reason)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            build_seed._stage_tree(sym_tree, self.root / 'sym_dst', build_seed._Quota())

        # 2. Windows NTFS directory junction / reparse point (S_ISLNK=False, FILE_ATTRIBUTE_REPARSE_POINT set).
        junc_tree = self.root / 'junc_tree'
        junc_sub = junc_tree / 'junction_dir'
        junc_sub.mkdir(parents=True)
        (junc_sub / 'a.txt').write_text('a', encoding='utf-8')
        orig_lstat = os.lstat
        junc_targets = {
            os.path.normpath(os.path.abspath(junc_sub)),
            os.path.normpath(os.path.realpath(junc_sub)),
        }

        def fake_lstat_reparse(path, *, dir_fd=None):
            real_st = orig_lstat(path, dir_fd=dir_fd)
            if dir_fd is None and os.path.normpath(os.path.abspath(os.fspath(path))) in junc_targets:
                return _StatProxy(real_st, st_file_attributes=_REPARSE_ATTR)
            return real_st

        with patch('os.lstat', side_effect=fake_lstat_reparse):
            ok, reason, _ = build_seed._verify_no_symlinks_or_unsafe_names(junc_tree)
            self.assertFalse(ok)
            self.assertIn('symlink', reason)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                build_seed._stage_tree(junc_tree, self.root / 'junc_dst', build_seed._Quota())

        # 3. Real non-zero inode directory loop must fail both inspect and _stage_tree.
        ino_tree = self.root / 'ino_loop_tree'
        ino_loop = ino_tree / 'html' / 'loop'
        ino_loop.mkdir(parents=True)
        (ino_loop / 'a.txt').write_text('a', encoding='utf-8')
        root_st = orig_lstat(ino_tree)
        ino_loop_targets = {
            os.path.normpath(os.path.abspath(ino_loop)),
            os.path.normpath(os.path.realpath(ino_loop)),
        }

        def fake_lstat_ino_loop(path, *, dir_fd=None):
            real_st = orig_lstat(path, dir_fd=dir_fd)
            if dir_fd is None and os.path.normpath(os.path.abspath(os.fspath(path))) in ino_loop_targets:
                return _StatProxy(real_st, st_dev=root_st.st_dev, st_ino=root_st.st_ino)
            return real_st

        with patch('os.lstat', side_effect=fake_lstat_ino_loop):
            ok, reason, _ = build_seed._verify_no_symlinks_or_unsafe_names(ino_tree)
            self.assertFalse(ok)
            self.assertIn('directory loop', reason)
            with self.assertRaisesRegex(ValueError, 'directory cycle'):
                build_seed._stage_tree(ino_tree, self.root / 'ino_dst', build_seed._Quota())

        # 4. Zero-inode (exFAT) directory loop via realpath pointing back to an ancestor.
        path_loop_tree = self.root / 'path_loop_tree'
        path_loop = path_loop_tree / 'html' / 'loop'
        path_loop.mkdir(parents=True)
        (path_loop / 'a.txt').write_text('a', encoding='utf-8')
        orig_realpath = os.path.realpath
        try:
            tree_real = orig_realpath(path_loop_tree)
            loop_real = orig_realpath(path_loop)

            def fake_lstat_zero(path, *, dir_fd=None):
                real_st = orig_lstat(path, dir_fd=dir_fd)
                if dir_fd is None and self._in_root(path):
                    return _StatProxy(real_st, st_dev=0, st_ino=0)
                return real_st

            def fake_realpath(path, *, strict=False):
                res = orig_realpath(path, strict=strict)
                if res == loop_real:
                    return tree_real
                return res

            with patch('os.lstat', side_effect=fake_lstat_zero), patch('os.path.realpath', side_effect=fake_realpath):
                ok, reason, _ = build_seed._verify_no_symlinks_or_unsafe_names(path_loop_tree)
                self.assertFalse(ok)
                self.assertIn('directory loop', reason)
                with self.assertRaisesRegex(ValueError, 'directory cycle'):
                    build_seed._stage_tree(path_loop_tree, self.root / 'path_dst', build_seed._Quota())
        finally:
            shutil.rmtree(self.root / 'sym_dst', ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
