import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent
import evidence
from test_agent import sample_case, report_content, ProtocolDouble


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'cases.jsonl'
        self.rows = []
        for index, (scenario, title, text) in enumerate([
            ('spine', 'Scoliosis report', 'scoliosis spinal fusion'),
            ('trauma', 'Tibial fracture report', 'tibia comminuted fracture plate fixation'),
            ('trauma', 'Femoral fracture report', 'femur fracture intramedullary nail'),
            ('joint', 'Hip arthroplasty', 'hip replacement arthroplasty infection')], 1):
            pmcid = 'PMC' + str(index)
            self.rows.append({'id': pmcid, 'scenario': scenario, 'title': title,
                              'case_text': text, 'source_url': f'https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/',
                              'split': 'train', 'license': 'CC-BY-4.0'})
        self.save()

    def save(self):
        self.path.write_text(''.join(json.dumps(r) + '\n' for r in self.rows), encoding='utf-8')

    def test_retrieval_filters_scenario_and_anatomy(self):
        result = evidence.retrieve(sample_case('trauma'), '胫骨钢板', path=self.path)
        self.assertEqual([r['id'] for r in result['items']], ['PMC2'])
        self.assertIn('plate', result['query_terms'])
        self.assertEqual(len(result['corpus_sha256']), 64)

    def test_uncovered_anatomy_returns_no_fabricated_evidence(self):
        case = sample_case('joint')
        case['measurements']['joint_type'] = 'knee'
        self.assertEqual(evidence.retrieve(case, path=self.path)['status'], 'insufficient')

    def test_missing_corpus_is_explicitly_insufficient(self):
        self.assertEqual(evidence.retrieve(sample_case(), path=self.path.with_name('absent'))['items'], [])

    def test_training_only_and_sources_are_validated(self):
        for key, value in [('split', 'test'), ('source_url', 'https://example.com'), ('license', 'unknown')]:
            with self.subTest(key=key):
                old = self.rows[0][key]
                self.rows[0][key] = value
                self.save()
                with self.assertRaises(ValueError):
                    evidence.load_records(self.path)
                self.rows[0][key] = old

    def test_duplicate_source_article_is_rejected(self):
        self.rows.append(copy.deepcopy(self.rows[0]))
        self.save()
        with self.assertRaises(ValueError):
            evidence.load_records(self.path)

    def test_snapshot_changes_when_corpus_changes(self):
        first = evidence.retrieve(sample_case(), path=self.path)['corpus_sha256']
        self.rows[0]['case_text'] += ' follow up'
        self.save()
        self.assertNotEqual(first, evidence.retrieve(sample_case(), path=self.path)['corpus_sha256'])

    def test_caller_cannot_mutate_the_cached_reference_corpus(self):
        loaded = evidence.load_records(self.path)
        loaded[0]['case_text'] = 'tampered data'
        self.assertNotEqual(evidence.load_records(self.path)[0]['case_text'], 'tampered data')

    def test_valid_citations_and_comparison_survive_validation(self):
        content = report_content()
        _, report = agent._parse_final(content, True)
        found = evidence.retrieve(sample_case(), path=self.path)
        agent.validate_citations(content, report, found)
        self.assertEqual(report['evidence_ids'], ['E1'])
        self.assertTrue(report['case_comparison'])

    def test_fabricated_or_missing_references_fail_closed(self):
        found = evidence.retrieve(sample_case(), path=self.path)
        for change in ('invent', 'missing', 'no_comparison', 'uncited'):
            data = json.loads(report_content())
            if change == 'invent':
                data['report']['evidence_ids'] = ['E999']
            elif change == 'missing':
                del data['report']['evidence_ids']
            elif change == 'no_comparison':
                data['report']['case_comparison'] = []
            else:
                data['report']['rationale'] = ['no marker']
                data['report']['case_comparison'] = ['no marker']
            content = json.dumps(data)
            _, report = agent._parse_final(content, True)
            with self.subTest(change=change), self.assertRaises(agent.AgentError):
                agent.validate_citations(content, report, found)

    def test_empty_evidence_requires_empty_references(self):
        data = json.loads(report_content())
        data['report'].update(evidence_ids=[], case_comparison=['证据不足；不推断临床结论。'], rationale=['规则计算'])
        content = json.dumps(data)
        _, report = agent._parse_final(content, True)
        agent.validate_citations(content, report, {'items': []})

    def test_retrieved_text_is_passed_to_real_agent_protocol(self):
        provider = ProtocolDouble()
        found = evidence.retrieve(sample_case(), path=self.path)
        config = {'provider': 'deepseek', 'api_key': 'local-test-only', 'model': 'test', 'base_url': 'https://api.deepseek.com'}
        with patch.object(agent, 'retrieve_evidence', return_value=found), patch.object(agent, 'complete', side_effect=provider):
            output = agent.run_agent(sample_case(), '参考病例生成讨论方案', config=config)
        submitted = json.loads(provider.requests[0]['messages'][-1]['content'])
        self.assertEqual(submitted['retrieved_evidence'], found)
        self.assertEqual(output['result']['evidence'], found)
        self.assertIn('PMC1', output['answer'])

    def test_local_mode_has_retrieval_but_never_calls_model(self):
        found = evidence.retrieve(sample_case(), path=self.path)
        with patch.object(agent, 'retrieve_evidence', return_value=found), patch.object(agent, 'complete') as network:
            output = agent.run_local_rules(sample_case(), '生成讨论方案', 'test')
        network.assert_not_called()
        self.assertEqual(output['result']['evidence'], found)


if __name__ == '__main__':
    unittest.main()
