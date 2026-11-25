"""File discovery, scope boundaries, and per-file translation isolation."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from events import event_payload, render
from events_server import EventFiles


class FileSwitchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.folder = self.root / 'runs'
        self.folder.mkdir()
        self.first = self.write('one/events.jsonl', 'one', 'First thought')
        self.second = self.write('two/events.jsonl', 'two', 'Second thought')

    def write(self, relative, run, thought):
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        events = [{'type': 'run/begin', 'run': run}, {'type': 'agent/thinking', 'text': thought}]
        path.write_text('\n'.join(json.dumps(event) for event in events), encoding='utf-8')
        return path

    def test_discovery_refresh_and_payload_switch(self):
        (self.folder / 'training.jsonl').write_text('{"messages": []}\n')
        (self.folder / 'empty.jsonl').touch()
        (self.folder / 'broken.jsonl').write_text('not json')
        files = EventFiles(self.first, self.folder)
        records = files.scan()
        self.assertEqual({record['label'] for record in records}, {'one/events.jsonl', 'two/events.jsonl'})
        selected = files.path_for(files.file_id(self.second))
        payload = event_payload(selected, files.file_id(selected))
        self.assertEqual(payload['events'][0]['run'], 'two')
        self.assertEqual(payload['fileId'], files.file_id(selected))
        third = self.write('three/events.jsonl', 'three', 'Third')
        self.assertEqual(len(files.scan()), 3)
        self.second.unlink()
        self.assertEqual(len(files.scan()), 2)
        with self.assertRaises(FileNotFoundError):
            files.path_for(files.file_id(self.second))
        self.assertEqual(files.path_for(files.file_id(third)), third)

    def test_paths_outside_scope_and_symlinks_are_not_opened(self):
        outside = self.root / 'private.jsonl'
        outside.write_text('{"type":"run/begin"}')
        (self.folder / 'link.jsonl').symlink_to(outside)
        files = EventFiles(self.first, self.folder)
        self.assertEqual(len(files.scan()), 2)
        for identifier in ['../../private.jsonl', str(outside), files.file_id(outside)]:
            with self.assertRaises(FileNotFoundError):
                files.path_for(identifier)

    def test_translations_stay_with_original_file_when_other_file_is_opened(self):
        files = EventFiles(self.first, self.folder)
        first = files.translator_for(files.file_id(self.first))
        second = files.translator_for(files.file_id(self.second))
        with patch.object(first, 'request_translation', return_value='第一段译文'):
            self.assertEqual(first.translate('First thought'), ('第一段译文', False))
        with patch.object(second, 'request_translation', return_value='第二段译文'):
            self.assertEqual(second.translate('Second thought'), ('第二段译文', False))
        self.assertEqual(event_payload(self.first)['translations'], [{'text': 'First thought', 'translation': '第一段译文'}])
        self.assertEqual(event_payload(self.second)['translations'], [{'text': 'Second thought', 'translation': '第二段译文'}])
        restarted = EventFiles(self.first, self.folder)
        self.assertEqual(restarted.translator_for(restarted.file_id(self.first)).translate('First thought'), ('第一段译文', True))
        self.assertNotIn('Second thought', first.cache)

    def test_rendered_file_identity_is_kept_for_reload_without_writing_html(self):
        file_id = EventFiles.file_id(self.second)
        page, count = render(self.second, file_id)
        payload = json.loads(page.split('id="event-data" type="application/json">')[1].split('</script>')[0])
        self.assertEqual(payload['fileId'], file_id)
        self.assertEqual(count, 2)
        self.assertFalse(self.second.with_suffix('.html').exists())


if __name__ == '__main__':
    unittest.main()
