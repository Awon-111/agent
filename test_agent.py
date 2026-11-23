"""Agent protocol tests; all model responses here are explicit test doubles.

No real provider is contacted and no real credentials are needed.
"""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import agent
import settings
from planner import analyze


TEST_KEY = "sk-local-protocol-test-only"
SAMPLES = {
    "spine": {"cobb_angle": 48, "flexibility_pct": 55, "coronal_balance_mm": 28, "apex_region": "thoracic"},
    "joint": {"joint_type": "hip", "bone_diameter_mm": 52, "canal_width_mm": 13, "offset_mm": 42, "bone_quality": "normal"},
    "trauma": {"fracture_site": "tibia", "fracture_pattern": "comminuted", "displacement_mm": 18,
               "articular_involvement": True, "soft_tissue": "compromised"},
}


def sample_case(scenario="spine"):
    return {"id": "case-test", "code": "ANONYMOUS-TEST", "scenario": scenario,
            "age": 32, "sex": "female", "measurements": copy.deepcopy(SAMPLES[scenario]),
            "image_url": "/uploads/not-sent.png"}


def reply(content=None, calls=None, **extra):
    message = {"role": "assistant", "content": content}
    if calls is not None:
        message["tool_calls"] = calls
    message.update(extra)
    return {"choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def tool_call(name, arguments="{}", call_id="call-test-1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def report_content(text="根据工具结果整理的测试说明。", **extra):
    return json.dumps({"answer": text, "report": {
        "overview": text, "recommendation": "以工具结果为方案讨论依据。",
        "rationale": ["引用已执行的规划工具；病例类比见 [E1]。"], "limitations": ["既定规则，非临床成功率。"],
        "evidence_ids": ["E1"], "case_comparison": ["[E1] 场景相似；年龄、分型及合并症仍需核实。"],
        "next_steps": ["交由人工复核。"]}, **extra}, ensure_ascii=False)


class ProtocolDouble:
    """A deterministic provider *test double*, never used by production code."""

    def __init__(self, text="根据工具结果整理的测试说明。"):
        self.requests = []
        self.text = text

    def __call__(self, payload, config=None, timeout=60):
        self.requests.append(copy.deepcopy(payload))
        if payload.get("tool_choice") == "required":
            current = next(json.loads(m["content"])["current_case"]
                           for m in reversed(payload["messages"])
                           if m["role"] == "user" and m["content"].startswith("{"))
            return reply(calls=[tool_call(agent.SCENARIO_TOOL[current["scenario"]])],
                         reasoning_content="PRIVATE_REASONING_NOT_FOR_PUBLIC_TRACE")
        if any(m["role"] == "tool" for m in payload["messages"]):
            return reply(report_content(self.text, ranked_options=[{"name": "FORGED", "score": 9999}]))
        return reply(json.dumps({"answer": self.text, "report": None}, ensure_ascii=False))


class AgentProtocolTests(unittest.TestCase):
    def setUp(self):
        self.config = {"provider": "deepseek", "model": "deepseek-flash", "base_url": "https://api.deepseek.com",
                       "api_key": TEST_KEY}

    def test_model_tool_model_loop_preserves_all_three_tools_numerical_results(self):
        for scenario in SAMPLES:
            with self.subTest(scenario=scenario):
                provider, events = ProtocolDouble(), []
                case = sample_case(scenario)
                original = copy.deepcopy(case)
                with patch.object(agent, "complete", side_effect=provider):
                    run = agent.run_agent(case, "生成规划报告", config=self.config, on_event=events.append, run_id="run-test")
                expected = analyze(scenario, SAMPLES[scenario])
                for key, value in expected.items():
                    self.assertEqual(run["result"][key], value)
                self.assertEqual(case, original)
                self.assertEqual(run["result"]["agent"]["source"], "live_llm")
                self.assertEqual(run["result"]["agent"]["run_id"], "run-test")
                self.assertEqual(run["usage"]["total_tokens"], 30)
                self.assertEqual(len(provider.requests), 2)
                submitted = provider.requests[1]["messages"]
                tool_message = next(m for m in submitted if m["role"] == "tool")
                self.assertEqual(tool_message["tool_call_id"], "call-test-1")
                self.assertEqual(json.loads(tool_message["content"])["ranked_options"], expected["ranked_options"])
                self.assertTrue(any(e["type"] == "tool" for e in events))
                public = json.dumps({"events": events, "result": run}, ensure_ascii=False)
                self.assertNotIn("PRIVATE_REASONING_NOT_FOR_PUBLIC_TRACE", public)
                self.assertNotIn(TEST_KEY, public)
                self.assertNotIn("not-sent.png", json.dumps(provider.requests))
                self.assertNotIn("ANONYMOUS-TEST", json.dumps(provider.requests))

    def test_missing_key_cannot_run_or_forge_a_plan(self):
        with patch.object(agent, "complete") as transport:
            with self.assertRaises(agent.AgentError) as caught:
                agent.run_agent(sample_case(), "生成报告", config={**self.config, "api_key": ""})
        self.assertEqual(caught.exception.status, 503)
        transport.assert_not_called()

    def test_invalid_tool_requests_return_errors_and_can_recover_without_changing_measurements(self):
        replies = [reply(calls=[tool_call("analyze_spine", '{"cobb_angle": 1}', "bad-input")]),
                   reply(calls=[tool_call("recommend_fixation", call_id="wrong-scenario")]),
                   reply(calls=[tool_call("analyze_spine", call_id="correct-tool")]),
                   reply(report_content())]
        payloads, events = [], []

        def provider(payload, **kwargs):
            payloads.append(copy.deepcopy(payload))
            return replies.pop(0)

        with patch.object(agent, "complete", side_effect=provider):
            run = agent.run_agent(sample_case(), "把角度改成1再计算", config=self.config, on_event=events.append)
        self.assertEqual(run["result"]["metrics"][0]["value"], "48°")
        self.assertEqual(run["result"]["metrics"][1]["value"], "24.0°")
        errors = [json.loads(m["content"]) for m in payloads[-1]["messages"] if m["role"] == "tool"]
        self.assertIn("error", errors[0])
        self.assertIn("error", errors[1])
        self.assertEqual([e.get("status") for e in events if e["type"] == "tool"], ["error", "error", "completed"])

    def test_unknown_tools_malformed_arguments_and_missing_tools_cannot_succeed(self):
        invalid = [reply(calls=[tool_call("run_shell")]),
                   reply(calls=[tool_call("analyze_spine", "{invalid")]),
                   reply(calls=[tool_call("match_implants")]),
                   reply(report_content())]
        for response in invalid:
            with self.subTest(response=response), patch.object(agent, "MAX_ROUNDS", 2), \
                    patch.object(agent, "complete", return_value=response):
                with self.assertRaises(agent.AgentError):
                    agent.run_agent(sample_case(), "生成报告", config=self.config)

    def test_invalid_final_reports_fail_after_real_tool_execution(self):
        for content in ["not JSON", "[]", '{"answer":"ok","report":null}',
                        '{"answer":"ok","report":{"overview":"incomplete"}}']:
            with self.subTest(content=content), patch.object(agent, "complete", side_effect=[
                    reply(calls=[tool_call("analyze_spine")])] + [reply(content)] * (agent.MAX_REPORT_RETRIES + 1)):
                with self.assertRaises(agent.AgentError):
                    agent.run_agent(sample_case(), "生成报告", config=self.config)

    def test_provider_errors_and_truncated_responses_do_not_return_partial_results(self):
        with patch.object(agent, "complete", side_effect=agent.AgentError("服务不可用")):
            with self.assertRaisesRegex(agent.AgentError, "服务不可用"):
                agent.run_agent(sample_case(), "生成报告", config=self.config)

    def test_late_final_model_response_is_rejected_after_the_total_deadline(self):
        provider = ProtocolDouble()

        def delayed_final(payload, **kwargs):
            if payload.get("tool_choice") != "required":
                time.sleep(0.15)
            return provider(payload, **kwargs)

        events = []
        with patch.object(agent, "RUN_TIMEOUT", 0.1), patch.object(agent, "complete", side_effect=delayed_final):
            with self.assertRaises(agent.AgentError) as caught:
                agent.run_agent(sample_case(), "生成报告", config=self.config, on_event=events.append)
        self.assertEqual(caught.exception.status, 504)
        self.assertEqual(len(provider.requests), 2)
        self.assertTrue(any(event["type"] == "tool" for event in events))
        self.assertFalse(any(event["type"] == "report" for event in events))
        response = reply(report_content())
        response["choices"][0]["finish_reason"] = "length"
        with patch.object(agent, "complete", return_value=response):
            with self.assertRaisesRegex(agent.AgentError, "长度上限"):
                agent.run_agent(sample_case(), "生成报告", config=self.config)

    def test_chat_receives_history_and_previous_grounded_result_without_creating_a_plan(self):
        provider = ProtocolDouble("50%来自柔韧性公式。")
        previous = analyze("spine", SAMPLES["spine"])
        previous["agent_report"] = {"private": "should-not-be-a-new-grounding-source"}
        with patch.object(agent, "complete", side_effect=provider):
            run = agent.run_agent(sample_case(), "矫正比例怎么来的？", require_plan=False, config=self.config,
                                  previous_result=previous,
                                  history=[{"question": "上一条问题", "answer": "上一条回答"}])
        self.assertIsNone(run["result"])
        self.assertIn("50%", run["answer"])
        messages = provider.requests[0]["messages"]
        self.assertTrue(any(m["content"] == "上一条问题" for m in messages))
        context = json.loads(messages[-1]["content"])
        self.assertEqual(context["previous_result"]["metrics"], previous["metrics"])
        self.assertNotIn("agent_report", context["previous_result"])


class DotenvSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env_file = Path(self.temp.name) / ".env"
        self.addCleanup(self.temp.cleanup)

    def test_default_dotenv_path_is_project_relative_even_from_another_working_directory(self):
        # Import an isolated copy of the real loader, so this exercises its
        # default path without reading or writing the operator's actual .env.
        project = Path(self.temp.name) / "project"
        elsewhere = Path(self.temp.name) / "another-cwd"
        project.mkdir()
        elsewhere.mkdir()
        shutil.copy2(settings.__file__, project / "settings.py")
        (project / ".env").write_text("DEEPSEEK_API_KEY=" + TEST_KEY + "\nDEEPSEEK_MODEL=project-model\n", encoding="utf-8")
        (elsewhere / ".env").write_text("DEEPSEEK_MODEL=wrong-working-directory\n", encoding="utf-8")
        script = (
            "import json,sys;sys.path.insert(0,sys.argv[1]);import settings;"
            "c=settings.load_settings(environ={});"
            "assert c['api_key']=='sk-local-protocol-test-only';"
            "print(json.dumps({'model':c['model'],'key_source':c['key_source']}))"
        )
        result = subprocess.run([sys.executable, "-B", "-c", script, str(project)], cwd=elsewhere,
                                capture_output=True, text=True, timeout=10, check=True)
        self.assertEqual(json.loads(result.stdout), {"model": "project-model", "key_source": "dotenv"})
        self.assertNotIn(TEST_KEY, result.stdout + result.stderr)

    def test_bom_comments_quotes_and_export_are_supported(self):
        self.env_file.write_text(
            "\ufeff# Local backend configuration\n\n"
            "export DEEPSEEK_API_KEY='" + TEST_KEY + "' # kept server side\n"
            'DEEPSEEK_MODEL="deepseek-flash" # current model\n'
            "DEEPSEEK_BASE_URL=https://api.deepseek.com/ # official endpoint\n",
            encoding="utf-8",
        )
        config = settings.load_settings(self.env_file, environ={})
        self.assertEqual(config["api_key"], TEST_KEY)
        self.assertEqual(config["model"], "deepseek-flash")
        self.assertEqual(config["base_url"], "https://api.deepseek.com")
        self.assertEqual(config["key_source"], "dotenv")

    def test_process_environment_overrides_file_without_mutating_the_environment(self):
        self.env_file.write_text("DEEPSEEK_API_KEY=file-test-only\nDEEPSEEK_MODEL=file-model\n", encoding="utf-8")
        process = {"DEEPSEEK_API_KEY": TEST_KEY, "DEEPSEEK_MODEL": "environment-model",
                   "DEEPSEEK_BASE_URL": "http://127.0.0.1:12345"}
        original = dict(process)
        original_os_environment = dict(os.environ)
        config = settings.load_settings(self.env_file, environ=process)
        self.assertEqual(config["api_key"], TEST_KEY)
        self.assertEqual(config["model"], "environment-model")
        self.assertEqual(config["base_url"], "http://127.0.0.1:12345")
        self.assertEqual(config["key_source"], "environment")
        self.assertEqual(process, original)
        # Avoid dumping the operator's real environment into assertion output.
        self.assertTrue(dict(os.environ) == original_os_environment)

    def test_explicitly_empty_process_key_disables_a_file_key(self):
        self.env_file.write_text("DEEPSEEK_API_KEY=" + TEST_KEY + "\n", encoding="utf-8")
        config = settings.load_settings(self.env_file, environ={"DEEPSEEK_API_KEY": ""})
        self.assertEqual(config["api_key"], "")
        self.assertEqual(config["key_source"], "none")

    def test_invalid_file_and_key_errors_never_echo_configuration_values(self):
        invalid_lines = (
            'DEEPSEEK_API_KEY "' + TEST_KEY + '"',
            'DEEPSEEK_API_KEY="' + TEST_KEY,
            'DEEPSEEK_API_KEY="' + TEST_KEY + ' whitespace"',
            'DEEPSEEK_API_KEY="' + TEST_KEY + '\\n"',
            "DEEPSEEK_API_KEY=" + TEST_KEY + "x" * 513,
        )
        for line in invalid_lines:
            with self.subTest(kind=invalid_lines.index(line)):
                self.env_file.write_text(line, encoding="utf-8")
                with self.assertRaises(settings.SettingsError) as caught:
                    settings.load_settings(self.env_file, environ={})
                self.assertNotIn(TEST_KEY, str(caught.exception))
                self.assertNotIn(line, str(caught.exception))

    def test_values_are_literal_and_unknown_variables_are_not_evaluated(self):
        self.env_file.write_text("DEEPSEEK_API_KEY=sk-${NOT_EXPANDED}\nUNRELATED=$(should-not-run)\n", encoding="utf-8")
        process = {"NOT_EXPANDED": "must-not-substitute"}
        config = settings.load_settings(self.env_file, environ=process)
        self.assertEqual(config["api_key"], "sk-${NOT_EXPANDED}")
        self.assertEqual(process, {"NOT_EXPANDED": "must-not-substitute"})

    def test_missing_file_has_safe_defaults_and_no_configured_key(self):
        config = settings.load_settings(self.env_file, environ={})
        self.assertEqual(config["api_key"], "")
        self.assertEqual(config["key_source"], "none")
        self.assertEqual(config["model"], "deepseek-flash")
        self.assertEqual(config["base_url"], "https://api.deepseek.com")

    def test_invalid_model_and_service_address_are_rejected_without_echoing_values(self):
        self.env_file.write_text("DEEPSEEK_API_KEY=" + TEST_KEY, encoding="utf-8")
        for environment in ({"DEEPSEEK_MODEL": "invalid model"},
                            {"DEEPSEEK_BASE_URL": "http://public.invalid"},
                            {"DEEPSEEK_BASE_URL": "https://user:" + TEST_KEY + "@public.invalid"},
                            {"DEEPSEEK_BASE_URL": "https://public.invalid/?key=" + TEST_KEY}):
            with self.subTest(field=next(iter(environment))), self.assertRaises(settings.SettingsError) as caught:
                settings.load_settings(self.env_file, environ=environment)
            self.assertNotIn(TEST_KEY, str(caught.exception))
            self.assertNotIn("public.invalid", str(caught.exception))


class AgentConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env_file = Path(self.temp.name) / ".env"
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(agent.reload_config)
        self.configure()

    def configure(self, model="deepseek-flash", environ=None):
        self.env_file.write_text("DEEPSEEK_API_KEY=" + TEST_KEY + "\nDEEPSEEK_MODEL=" + model + "\n", encoding="utf-8")
        return agent.reload_config(env_file=self.env_file, environ={} if environ is None else environ)

    def test_public_config_redacts_key_and_does_not_claim_a_verified_connection(self):
        config = agent.get_config()
        self.assertTrue(config["configured"])
        self.assertFalse(config["connected"])
        self.assertNotIn(TEST_KEY, json.dumps(config))
        self.assertNotIn("api_key", config)
        self.assertEqual(config["key_source"], "dotenv")
        self.configure(environ={"DEEPSEEK_API_KEY": ""})
        self.assertFalse(agent.get_config()["configured"])
        self.assertEqual(agent.snapshot_config()["api_key"], "")

    def test_connection_test_tracks_real_outcome_and_reloading_resets_verification(self):
        with patch.object(agent, "complete", return_value=reply("OK")) as transport:
            self.assertTrue(agent.test_connection()["connected"])
            transport.assert_called_once()
        self.assertTrue(agent.get_config()["connected"])
        with patch.object(agent, "complete", side_effect=agent.AgentError("服务失败")):
            with self.assertRaises(agent.AgentError):
                agent.test_connection()
        self.assertFalse(agent.get_config()["connected"])
        self.configure(model="deepseek-v4-pro")
        self.assertEqual(agent.get_config()["connection_status"], "untested")

    def test_completed_run_verifies_its_configuration_but_not_settings_changed_mid_run(self):
        with patch.object(agent, "complete", side_effect=ProtocolDouble()):
            agent.run_agent(sample_case(), "生成报告")
        self.assertTrue(agent.get_config()["connected"])
        self.configure()
        self.assertFalse(agent.get_config()["connected"])
        provider = ProtocolDouble()

        def change_settings_during_request(payload, **kwargs):
            if not provider.requests:
                self.configure(model="deepseek-v4-pro")
            return provider(payload, **kwargs)

        with patch.object(agent, "complete", side_effect=change_settings_during_request):
            result = agent.run_agent(sample_case(), "生成报告")
        self.assertEqual(result["result"]["agent"]["model"], "deepseek-flash")
        self.assertEqual(agent.get_config()["model"], "deepseek-v4-pro")
        self.assertFalse(agent.get_config()["connected"])

    def test_dotenv_changes_take_effect_only_after_internal_reload_or_restart(self):
        self.env_file.write_text("DEEPSEEK_API_KEY=" + TEST_KEY + "\nDEEPSEEK_MODEL=changed-model\n", encoding="utf-8")
        self.assertEqual(agent.snapshot_config()["model"], "deepseek-flash")
        self.assertEqual(agent.get_config()["model"], "deepseek-flash")
        agent.reload_config(env_file=self.env_file, environ={})
        self.assertEqual(agent.snapshot_config()["model"], "changed-model")
        self.assertFalse(agent.get_config()["connected"])

    def test_invalid_reload_disables_the_old_key_and_returns_a_safe_configuration_error(self):
        self.env_file.write_text('DEEPSEEK_API_KEY="' + TEST_KEY, encoding="utf-8")
        with self.assertRaises(agent.AgentError) as caught:
            agent.reload_config(env_file=self.env_file, environ={})
        self.assertEqual(caught.exception.status, 503)
        self.assertNotIn(TEST_KEY, str(caught.exception))
        for get in (agent.get_config, agent.snapshot_config):
            with self.assertRaises(agent.AgentError) as unavailable:
                get()
            self.assertNotIn(TEST_KEY, str(unavailable.exception))
        self.configure()
        self.assertTrue(agent.get_config()["configured"])
        self.assertFalse(agent.get_config()["connected"])


class AgentHttpTransportTests(unittest.TestCase):
    """Exercise actual urllib serialization against a loopback protocol server."""

    @classmethod
    def setUpClass(cls):
        cls.received, cls.response_status, cls.response_body, cls.location = [], 200, b"", None
        owner = cls

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                owner.received.append({"path": self.path, "authorization": self.headers.get("Authorization"),
                                       "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])) )})
                self.send_response(owner.response_status)
                self.send_header("Content-Type", "application/json")
                if owner.location:
                    self.send_header("Location", owner.location)
                self.end_headers()
                self.wfile.write(owner.response_body)

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)

    def setUp(self):
        type(self).received = []
        type(self).response_status = 200
        type(self).response_body = json.dumps(reply("OK")).encode()
        type(self).location = None
        self.config = {"api_key": TEST_KEY, "base_url": self.base, "model": "deepseek-flash"}

    def test_http_sends_bearer_header_and_json_to_completion_endpoint(self):
        response = agent.complete({"model": "deepseek-flash", "messages": [{"role": "user", "content": "测试"}]},
                                  config=self.config)
        self.assertEqual(response["choices"][0]["message"]["content"], "OK")
        self.assertEqual(self.received[0]["path"], "/chat/completions")
        self.assertEqual(self.received[0]["authorization"], "Bearer " + TEST_KEY)
        self.assertEqual(self.received[0]["body"]["messages"][0]["content"], "测试")

    def test_http_errors_never_echo_provider_body_or_key(self):
        for status in (401, 402, 429, 500):
            with self.subTest(status=status):
                type(self).response_status = status
                type(self).response_body = ("provider echoed " + TEST_KEY).encode()
                with self.assertRaises(agent.AgentError) as caught:
                    agent.complete({}, config=self.config)
                self.assertNotIn(TEST_KEY, str(caught.exception))
                self.assertNotIn("provider echoed", str(caught.exception))

    def test_invalid_json_and_redirects_are_rejected(self):
        type(self).response_body = b"not JSON"
        with self.assertRaises(agent.AgentError):
            agent.complete({}, config=self.config)
        type(self).response_status = 307
        type(self).location = self.base + "/must-not-follow"
        before = len(self.received)
        with self.assertRaises(agent.AgentError):
            agent.complete({}, config=self.config)
        self.assertEqual(len(self.received), before + 1)


if __name__ == "__main__":
    unittest.main()
