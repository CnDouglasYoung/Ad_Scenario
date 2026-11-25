"""Translation contracts, persistence, and failure behavior, without network calls."""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from events import build
from events_translation import Translator, TranslationError, read_cache, split_chunks


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache_path = Path(self.directory.name) / 'events.translations.json'

    def translator(self):
        return Translator(self.cache_path, {'TRANSLATION_PROVIDER': 'model', 'ANTHROPIC_AUTH_TOKEN': 'test-secret',
                          'ANTHROPIC_BASE_URL': 'https://test.invalid/v1',
                          'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'configured-model'})

    @patch('events_translation.urllib.request.urlopen')
    def test_only_source_text_sent_and_result_cached_across_restarts(self, urlopen):
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({
            'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': '预算是 ¥150。'}]
        }).encode()
        translator = self.translator()
        text = 'Budget is ¥150.'
        self.assertEqual(translator.translate(text), ('预算是 ¥150。', False))
        request = urlopen.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, 'https://test.invalid/v1/messages')
        self.assertEqual(body['model'], 'configured-model')
        self.assertEqual(body['messages'], [{'role': 'user', 'content': text}])
        self.assertEqual(self.translator().translate(text), ('预算是 ¥150。', True))
        self.assertEqual(urlopen.call_count, 1)
        self.assertNotIn('test-secret', self.cache_path.read_text())

    @patch('events_translation.urllib.request.urlopen')
    def test_chat_configuration_and_truncated_results_are_not_cached(self, urlopen):
        translator = Translator(self.cache_path, {'TRANSLATION_PROVIDER': 'model', 'MODEL_API_KEY': 'key',
                                'MODEL_BASE_URL': 'https://test.invalid/v1',
                                'MODEL_NAME': 'test-chat'})
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps({
            'choices': [{'finish_reason': 'length', 'message': {'content': 'partial'}}]
        }).encode()
        with self.assertRaisesRegex(TranslationError, '不完整'):
            translator.translate('A long thought')
        self.assertFalse(self.cache_path.exists())
        self.assertEqual(urlopen.call_args.args[0].full_url, 'https://test.invalid/v1/chat/completions')

    @patch('events_translation.urllib.request.urlopen')
    def test_error_does_not_reflect_upstream_secrets(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError('https://test.invalid', 401,
            'test-secret', {}, io.BytesIO(b'test-secret'))
        with self.assertRaises(TranslationError) as caught:
            self.translator().translate('Hello')
        self.assertIn('401', str(caught.exception))
        self.assertNotIn('test-secret', str(caught.exception))
        self.assertEqual(read_cache(self.cache_path), {})

    def test_missing_config_still_allows_cached_translations(self):
        translator = Translator(self.cache_path, {'TRANSLATION_PROVIDER': 'model'})
        self.assertFalse(translator.available)
        translator.cache['Hello'] = '你好'
        self.assertEqual(translator.translate('Hello'), ('你好', True))
        with self.assertRaisesRegex(TranslationError, '未找到翻译服务配置'):
            translator.translate('New thought')

    def test_long_google_requests_cover_every_character(self):
        for text in ['word ' * 3000, ('A paragraph.\n\n' * 800), 'x' * 8188, '多语言内容' * 2000]:
            chunks = split_chunks(text)
            self.assertEqual(''.join(chunk + separator for chunk, separator in chunks), text)
            self.assertTrue(all(0 < len(chunk) <= 1800 for chunk, _ in chunks))

    @patch('events_translation.urllib.request.urlopen')
    def test_google_default_uses_no_model_keys_and_reassembles_all_chunks(self, urlopen):
        import urllib.parse
        def respond(request, **kwargs):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
            chunk = query['q'][0]
            self.assertEqual(query['tl'], ['zh-CN'])
            self.assertNotIn('Authorization', request.headers)
            from unittest.mock import MagicMock
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps([[[chunk, chunk]]]).encode()
            return response
        urlopen.side_effect = respond
        text = 'Synthetic text.\n\n' * 300
        translator = Translator(self.cache_path, {'ANTHROPIC_AUTH_TOKEN': 'unused'})
        self.assertTrue(translator.available)
        self.assertEqual(translator.translate(text), (text.strip(), False))
        self.assertGreater(urlopen.call_count, 1)

    def test_html_embeds_only_matching_thoughts_and_escapes_translations(self):
        source = Path(self.directory.name) / 'events.jsonl'
        output = source.with_suffix('.html')
        source.write_text(json.dumps({'type': 'agent/thinking', 'text': 'Hello'}))
        self.cache_path.write_text(json.dumps({'version': 1, 'translations': [
            {'text': 'Hello', 'translation': '</script>你好'},
            {'text': 'Other private trace', 'translation': '不应嵌入'},
        ]}))
        build(source, output)
        payload = output.read_text().split('id="event-data" type="application/json">')[1].split('</script>')[0]
        self.assertNotIn('<', payload)
        self.assertEqual(json.loads(payload)['translations'], [{'text': 'Hello', 'translation': '</script>你好'}])


if __name__ == '__main__':
    unittest.main()
