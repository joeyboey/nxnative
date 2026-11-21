"""scripts/build_ipa_from_nsp.py --platform visionos: the 'ios' build with visionOS shader libraries and folders of its own.

Not run on a game: this checks the option, the wrapper the shader steps go through, the output name, and the
Vulkan and GL steps' command and folder for both platforms. The chain tools themselves are mocked, as in test_nsp_incremental."""
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

from test_nsp_incremental import Base, REPO, game, nsp


class Helpers(unittest.TestCase):
    def test_the_platform_is_an_option(self):
        self.assertEqual(nsp.parse_args(['--platform', 'visionos']).platform, 'visionos')

    def test_chain_tools_run_through_the_visionos_wrapper_only_for_visionos(self):
        tool = 'dm/gfx/tools/own_compilers.py'
        self.assertEqual(nsp.chain_tool('py', REPO, tool), ['py', REPO / tool])
        with mock.patch.object(nsp, 'VISION', True):
            self.assertEqual(nsp.chain_tool('py', REPO, tool), ['py', REPO / 'dm/ios/tools/xros_metal.py', REPO / tool])
        self.assertTrue((REPO / 'dm/ios/tools/xros_metal.py').is_file())

    def test_the_default_ipa_never_replaces_the_iphone_one(self):
        g = types.SimpleNamespace(get=lambda key: {'ipa': 'Game.ipa'}[key])
        self.assertEqual(nsp.device_ipa(g), 'Game.ipa')
        with mock.patch.object(nsp, 'VISION', True):
            self.assertEqual(nsp.device_ipa(g), 'Game-visionOS.ipa')


class LayerShadersForVisionOS(Base):
    def build(self, api, vision):
        """One Vulkan or GL shader step for a device → (the command, the folder returned)."""
        self.derive(); g = game.Game(self.pdir)
        logs = self.tmp / f'logs-{api}-{vision}'; logs.mkdir(); cmds = []
        sh = self.tmp / f'sh-{api}-{vision}'
        def translate(cmd, log, **kw):
            cmds.append([str(c) for c in cmd])
            ios = Path(cmd[cmd.index('--ios') + 1]); mac = Path(cmd[cmd.index('--ios') - 1])
            for d in (mac, ios):
                (d / api).mkdir(parents=True); (d / api / f'{api}_shaders.metallib').write_bytes(b'library')
            Path(log).write_text('macosx: 1\niphoneos: 1\n')
        roots = [self.ex / 'RomFS'] + ([self.ex / 'ExeFS/main'] if api == 'gl' else [])
        build = nsp.build_vk_shaders if api == 'vk' else nsp.build_gl_shaders
        with mock.patch.object(nsp, 'run', side_effect=translate), mock.patch.object(nsp, 'toolchain', return_value=()), \
             mock.patch.object(nsp.shutil, 'which', return_value='/glslangValidator'), \
             mock.patch.object(nsp.subprocess, 'run', return_value=mock.Mock(stdout='compiler', stderr='')), \
             mock.patch.object(nsp, 'VISION', vision), mock.patch.object(nsp, 'IOS_LIBS', 'xros' if vision else 'ios'):
            out = build(sys.executable, REPO, roots, sh, {'WORK': str(self.tmp), 'PATH': ''}, logs, g, mac=False)
        self.assertEqual(len(cmds), 1)
        return cmds[0], sh, out

    def test_the_libraries_go_to_a_folder_of_their_own_through_the_wrapper(self):
        for api in ('vk', 'gl'):
            with self.subTest(api=api):
                cmd, sh, out = self.build(api, True)
                self.assertEqual(cmd[1], str(REPO / 'dm/ios/tools/xros_metal.py'))
                self.assertEqual(cmd[2], str(REPO / f'dm/gfx/tools/{api}_shaders.py'))
                self.assertEqual(cmd[cmd.index('--ios') + 1], str(sh / 'xros'))
                self.assertEqual(out, sh / 'xros')
                self.assertFalse((sh / 'ios').exists())

    def test_an_iphone_build_runs_the_tool_itself_into_ios(self):
        for api in ('vk', 'gl'):
            with self.subTest(api=api):
                cmd, sh, out = self.build(api, False)
                self.assertEqual(cmd[1], str(REPO / f'dm/gfx/tools/{api}_shaders.py'))
                self.assertEqual(cmd[cmd.index('--ios') + 1], str(sh / 'ios'))
                self.assertEqual(out, sh / 'ios')


if __name__ == '__main__':
    unittest.main()
