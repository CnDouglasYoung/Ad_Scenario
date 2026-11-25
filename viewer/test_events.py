"""Run with: python3 -m unittest discover -s viewer -p 'test_events.py'"""
import json
import tempfile
import unittest
from pathlib import Path
from events import build, read_events


class ExportTests(unittest.TestCase):
    def test_embedded_log_cannot_escape_script_or_change_template(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'events.jsonl'
            output = Path(directory) / 'events.html'
            event = {'type': 'agent/text', 'text': '</script><script>alert(1)</script><!-- EVENT_APP -->\u2028'}
            source.write_text(json.dumps(event), encoding='utf-8')
            self.assertEqual(build(source, output), 1)
            page = output.read_text()
            payload = page.split('<script id="event-data" type="application/json">')[1].split('</script>')[0]
            self.assertNotIn('<', payload)
            self.assertEqual(json.loads(payload)['events'], [event])
            self.assertEqual(page.count('<script>'), 2)

    def test_error_reports_line_and_does_not_replace_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'bad.jsonl'
            output = Path(directory) / 'events.html'
            source.write_text('{"type":"ok"}\nnope', encoding='utf-8')
            output.write_text('keep me')
            with self.assertRaisesRegex(ValueError, ':2:'):
                build(source, output)
            self.assertEqual(output.read_text(), 'keep me')

    def test_invalid_event_types(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'events.jsonl'
            for content in ['[]', 'null', '{}', '{"type": 1}', '   \n']:
                source.write_text(content)
                with self.assertRaises(ValueError):
                    read_events(source)


if __name__ == '__main__':
    unittest.main()
