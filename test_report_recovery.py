import copy
import json
import unittest
from unittest.mock import patch

import agent
from planner import analyze
from test_agent import TEST_KEY, reply, tool_call, report_content, sample_case


class ReportRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.config = {'provider': 'deepseek', 'api_key': TEST_KEY,
                       'base_url': 'https://api.deepseek.com', 'model': 'deepseek-flash'}

    def run_responses(self, responses):
        self.requests, self.events = [], []
        queue = list(responses)
        def complete(payload, **kwargs):
            self.requests.append(copy.deepcopy(payload))
            return queue.pop(0)
        with patch.object(agent, 'complete', side_effect=complete):
            return agent.run_agent(sample_case(), '生成病例报告', config=self.config, on_event=self.events.append)

    def test_finalization_uses_json_mode_without_tools(self):
        result = self.run_responses([reply(calls=[tool_call('analyze_spine')]), reply(report_content())])
        self.assertIn('tools', self.requests[0])
        self.assertNotIn('tools', self.requests[1])
        self.assertNotIn('tool_choice', self.requests[1])
        self.assertEqual(self.requests[1]['response_format'], {'type': 'json_object'})
        self.assertEqual(result['result']['agent']['report_retries'], 0)

    def test_invalid_or_blank_final_is_retried_without_recalculating(self):
        for invalid in ('not JSON', '   ', '{"answer": "truncated'):
            with self.subTest(invalid=invalid):
                output = self.run_responses([reply(calls=[tool_call('analyze_spine')]),
                                             reply(invalid), reply(report_content())])
                self.assertEqual(output['result']['agent']['report_retries'], 1)
                self.assertEqual(len(output['result']['agent']['tool_calls']), 1)
                self.assertEqual(output['usage']['total_tokens'], 45)
                self.assertEqual(output['result']['metrics'], analyze('spine', sample_case()['measurements'])['metrics'])
                self.assertTrue(any(e['type']=='repair' for e in self.events))

    def test_truncated_final_gets_a_larger_output_budget_and_no_partial_plan(self):
        truncated = reply('{"answer":"cut off')
        truncated['choices'][0]['finish_reason'] = 'length'
        output = self.run_responses([reply(calls=[tool_call('analyze_spine')]), truncated, reply(report_content())])
        self.assertGreater(self.requests[2]['max_tokens'], self.requests[1]['max_tokens'])
        self.assertEqual(output['result']['agent']['report_retries'], 1)

    def test_invalid_citations_are_corrected_but_never_accepted(self):
        invalid = report_content().replace('E1', 'E999')
        output = self.run_responses([reply(calls=[tool_call('analyze_spine')]), reply(invalid), reply(report_content())])
        self.assertEqual(output['result']['agent_report']['evidence_ids'], ['E1'])
        self.assertNotIn('E999', json.dumps(output))

    def test_retries_are_bounded_and_no_failure_becomes_a_local_rules_success(self):
        with self.assertRaisesRegex(agent.AgentError, '自动纠正'):
            self.run_responses([reply(calls=[tool_call('analyze_spine')])] +
                               [reply('bad JSON')] * (agent.MAX_REPORT_RETRIES + 1))
        self.assertEqual(len(self.requests), agent.MAX_REPORT_RETRIES + 2)
        self.assertEqual(sum(e['type']=='repair' for e in self.events), agent.MAX_REPORT_RETRIES)
        self.assertFalse(any(e['type']=='report' for e in self.events))

    def test_explicit_wrong_input_restatement_is_retried(self):
        invalid = report_content('工具按柔韧性70%线性换算。')
        result = self.run_responses([reply(calls=[tool_call('analyze_spine')]), reply(invalid),
                                     reply(report_content('工具按柔韧性55%线性换算。'))])
        self.assertEqual(result['result']['agent']['report_retries'], 1)
        self.assertNotIn('70%', result['answer'])

    def test_sensitivity_parameter_changes_are_not_treated_as_current_inputs(self):
        agent.validate_case_claims('本例敏感性分析中柔韧性30%时结果改变。', None, sample_case())
        agent.validate_case_claims('[E1] 病例柔韧性70%。', None, sample_case())

    def test_fences_bom_and_literal_newline_are_normalized_without_changing_text(self):
        original = json.loads(report_content('第一行\n第二行，48°。'))
        raw = json.dumps(original, ensure_ascii=False).replace('第一行\\n第二行', '第一行\n第二行')
        content = '\ufeff```JSON\n' + raw + '\n```'
        answer, report = agent._parse_final(content, True)
        found = agent.retrieve_evidence(sample_case(), '')
        agent.validate_citations(content, report, found)
        self.assertEqual(answer, original['answer'])
        self.assertEqual(report['overview'], original['report']['overview'])

    def test_ambiguous_objects_duplicate_keys_and_non_json_literals_are_rejected(self):
        for raw in ('{"answer":"one","answer":"two"}', '{"x":NaN}',
                    '{"x":1}{"x":2}', "{'answer':'python literal'}"):
            with self.subTest(raw=raw), self.assertRaises(agent.AgentError):
                agent._decode_final(raw)


if __name__ == '__main__':
    unittest.main()
