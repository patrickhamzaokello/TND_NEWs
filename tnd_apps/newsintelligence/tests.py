import json
from unittest.mock import MagicMock, patch

from django.test import TestCase

from .openai_client import parse_json_response
from .agents import ArticleAnalysisAgent


class ParseJsonResponseTests(TestCase):
    """Tests for openai_client.parse_json_response()"""

    def test_plain_json(self):
        raw = '{"summary": "Test summary", "sentiment": "positive"}'
        result = parse_json_response(raw)
        self.assertEqual(result['summary'], 'Test summary')
        self.assertEqual(result['sentiment'], 'positive')

    def test_strips_json_code_fence(self):
        raw = '```json\n{"summary": "Test", "score": 7}\n```'
        result = parse_json_response(raw)
        self.assertEqual(result['summary'], 'Test')
        self.assertEqual(result['score'], 7)

    def test_strips_plain_code_fence(self):
        raw = '```\n{"key": "value"}\n```'
        result = parse_json_response(raw)
        self.assertEqual(result['key'], 'value')

    def test_handles_whitespace_padding(self):
        raw = '   \n  {"key": "value"}  \n  '
        result = parse_json_response(raw)
        self.assertEqual(result['key'], 'value')

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError) as ctx:
            parse_json_response('')
        self.assertIn('empty', str(ctx.exception).lower())

    def test_whitespace_only_raises(self):
        with self.assertRaises(ValueError) as ctx:
            parse_json_response('   \n  ')
        self.assertIn('empty', str(ctx.exception).lower())

    def test_invalid_json_raises_with_preview(self):
        raw = 'this is not json at all'
        with self.assertRaises(ValueError) as ctx:
            parse_json_response(raw)
        error_msg = str(ctx.exception)
        self.assertIn('JSON parse failed', error_msg)
        self.assertIn('this is not json', error_msg)

    def test_truncated_json_raises(self):
        raw = '{"summary": "Test", "sentiment":'
        with self.assertRaises(ValueError) as ctx:
            parse_json_response(raw)
        self.assertIn('JSON parse failed', str(ctx.exception))

    def test_nested_json(self):
        raw = json.dumps({
            'summary': 'Test',
            'entities': {'people': ['Alice'], 'organizations': [], 'locations': ['Kampala']},
            'themes': ['governance', 'health'],
        })
        result = parse_json_response(raw)
        self.assertEqual(result['entities']['people'], ['Alice'])
        self.assertEqual(result['entities']['locations'], ['Kampala'])

    def test_code_fence_with_extra_whitespace_inside(self):
        raw = '```json\n\n{"key": "value"}\n\n```'
        result = parse_json_response(raw)
        self.assertEqual(result['key'], 'value')


class ArticleAnalysisAgentValidationTests(TestCase):
    """Tests for _save_enrichment key validation and sentiment guard."""

    def _make_enrichment(self):
        enrichment = MagicMock()
        enrichment.article_id = 1
        return enrichment

    def _valid_data(self):
        return {
            'summary': 'A summary.',
            'sentiment': 'positive',
            'sentiment_score': 0.8,
            'importance_score': 7,
            'themes': ['governance'],
            'key_facts': ['Fact 1'],
            'related_themes': [],
            'entities': {'people': [], 'organizations': [], 'locations': []},
            'audience_relevance': {'business': 0.5, 'general_public': 0.8, 'government': 0.3, 'youth': 0.4},
            'follow_up_worthy': False,
            'controversy_flag': False,
            'is_breaking_candidate': False,
            '_meta': {'input_tokens': 100, 'output_tokens': 200, 'model': 'gpt-4o-mini'},
        }

    def test_missing_required_key_raises_value_error(self):
        agent = ArticleAnalysisAgent()
        enrichment = self._make_enrichment()
        data = self._valid_data()
        del data['summary']
        with self.assertRaises(ValueError) as ctx:
            agent._save_enrichment(enrichment, data)
        self.assertIn('summary', str(ctx.exception))

    def test_missing_entities_raises_value_error(self):
        agent = ArticleAnalysisAgent()
        enrichment = self._make_enrichment()
        data = self._valid_data()
        del data['entities']
        with self.assertRaises(ValueError) as ctx:
            agent._save_enrichment(enrichment, data)
        self.assertIn('entities', str(ctx.exception))

    def test_invalid_sentiment_defaults_to_neutral(self):
        agent = ArticleAnalysisAgent()
        enrichment = self._make_enrichment()
        data = self._valid_data()
        data['sentiment'] = 'uncertain'
        agent._save_enrichment(enrichment, data)
        self.assertEqual(enrichment.sentiment, 'neutral')

    def test_valid_data_saves_without_error(self):
        agent = ArticleAnalysisAgent()
        enrichment = self._make_enrichment()
        data = self._valid_data()
        agent._save_enrichment(enrichment, data)
        self.assertEqual(enrichment.sentiment, 'positive')
        self.assertEqual(enrichment.importance_score, 7)
        self.assertEqual(enrichment.status, 'completed')
