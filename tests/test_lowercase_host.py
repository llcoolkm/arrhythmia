"""Case-insensitive card input with canonical lowercase names; no MIDI device is opened."""
import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import module


class LowercaseHost(unittest.TestCase):
    def test_names_and_defaults(self):
        for name in ('fm', 'FM', 'Fm', 'fM'):
            self.assertEqual(module.engine_name(name), b'FM' + bytes(6))
        for name in ('Stars', 'STARS', 'stars'):
            self.assertEqual(module.card_name(name), b'STARS' + bytes(3))
        self.assertEqual(module.default_name('/tmp/Fm.DSE'), 'fm')
        self.assertEqual(module.default_name('/tmp/01PAINT.MOD'), '01paint')
        for name in ('default', 'DEFAULT', 'DeFaUlT', 'fm.dse', '_fm', 'bad name', ''):
            with self.assertRaises(ValueError):
                module.engine_name(name)

    def cli(self, *args):
        card = Mock()
        card.list.return_value = [('STARS.MOD', 35)]
        card.engine_verify.return_value = (35, 0x1234)
        card.verify.return_value = (35, 0x1234)
        card.engine_put.return_value = (35, 0x1234)
        out = io.StringIO()
        with patch.object(module.os, 'open', return_value=42), patch.object(module.os, 'close'), \
                patch.object(module, 'Card', return_value=card), contextlib.redirect_stdout(out):
            module.card_main([args[0], '/dev/fake', *args[1:]])
        return card, out.getvalue()

    def test_cli_engine_and_none(self):
        card, text = self.cli('engine', 'DeFaUlT', 'NoNe')
        card.engine.assert_called_once_with(None, None)
        self.assertIn('1:/engines/default.cfg: none', text)
        card, text = self.cli('engine', '2', 'Fm')
        card.engine.assert_called_once_with(2, 'fm')
        self.assertIn("pack 2's engine.cfg: fm.dse", text)

    def test_cli_verify_and_delete(self):
        card, text = self.cli('engine-verify', 'Fm')
        card.engine_verify.assert_called_once_with('fm')
        self.assertIn('1:/engines/fm.dse:', text)
        self.assertIn('fm.dst / fm.dso', text)
        card, text = self.cli('verify', 'StArS')
        card.verify.assert_called_once_with('stars')
        self.assertIn('1:/modules/stars.mod:', text)
        card, text = self.cli('delete', 'STARS')
        card.delete.assert_called_once_with('stars')
        self.assertIn('1:/modules/stars.mod', text)

    def test_cli_list_renders_legacy_names_lowercase(self):
        _, text = self.cli('list')
        self.assertIn('stars.mod', text)
        self.assertNotIn('STARS.MOD', text)

    def test_cli_put_default_and_explicit_names(self):
        for names in ((), ('FM',), ('fM',)):
            with patch('builtins.open', unittest.mock.mock_open(read_data=b'engine')), \
                    patch.object(module, 'engine_check'), patch.object(module, 'app_build', return_value=7388):
                card, text = self.cli('engine-put', '/tmp/Fm.DSE', *names)
            card.engine_put.assert_called_once_with(b'engine', 'fm')
            self.assertIn('1:/engines/fm.dse:', text)


if __name__ == '__main__':
    unittest.main()
