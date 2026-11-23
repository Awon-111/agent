"""Run with: python -m unittest tests.py -v"""

import json
import copy
import tempfile
import threading
import time
import unittest
import sqlite3
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import app
import agent
from planner import analyze
from test_agent import ProtocolDouble, TEST_KEY, reply, report_content, tool_call


SAMPLES = {
    "spine": {"cobb_angle": 48, "flexibility_pct": 55, "coronal_balance_mm": 28, "apex_region": "thoracic"},
    "joint": {"joint_type": "hip", "bone_diameter_mm": 52, "canal_width_mm": 13, "offset_mm": 42, "bone_quality": "normal"},
    "trauma": {"fracture_site": "tibia", "fracture_pattern": "comminuted", "displacement_mm": 18, "articular_involvement": True, "soft_tissue": "compromised"},
}


class PlannerTests(unittest.TestCase):
    def test_all_scenarios_return_ranked_explainable_options(self):
        for scenario, measurements in SAMPLES.items():
            with self.subTest(scenario=scenario):
                result = analyze(scenario, measurements)
                self.assertGreaterEqual(len(result["ranked_options"]), 2)
                self.assertTrue(result["method"])
                self.assertTrue(result["required_review"])
                scores = [x["score"] for x in result["ranked_options"]]
                self.assertEqual(scores, sorted(scores, reverse=True))

    def test_invalid_measurements_rejected(self):
        with self.assertRaises(ValueError):
            analyze("spine", {**SAMPLES["spine"], "cobb_angle": 200})

    def test_stiff_spine_prioritizes_staged_assessment(self):
        result = analyze("spine", {**SAMPLES["spine"], "cobb_angle": 70, "flexibility_pct": 15})
        self.assertEqual(result["ranked_options"][0]["name"], "分期评估路径")
        self.assertEqual(result["metrics"][1]["value"], "46.2°")

    def test_exact_hip_and_knee_matches_expose_zero_deviation(self):
        for joint, diameter, canal, name in [("hip", 52, 13, "H52"), ("knee", 66, 12, "K66")]:
            r = analyze("joint", {**SAMPLES["joint"], "joint_type": joint, "bone_diameter_mm": diameter, "canal_width_mm": canal})
            top = r["ranked_options"][0]
            self.assertIn(name, top["name"])
            self.assertEqual(top["score"], 100)
            self.assertEqual(top["deviations"], {"diameter": 0, "canal": 0, "offset": 0})

    def test_sensitivity_holds_other_inputs_and_does_not_mutate_case(self):
        original = dict(SAMPLES["spine"])
        r = analyze("spine", original)
        self.assertEqual(original, SAMPLES["spine"])
        ys = [p["y"] for p in r["sensitivity"]["points"]]
        self.assertEqual(ys, sorted(ys, reverse=True))
        for scenario in SAMPLES:
            self.assertGreaterEqual(len(analyze(scenario, SAMPLES[scenario])["sensitivity"]["points"]), 5)

    def test_boolean_measurements_require_boolean_type(self):
        with self.assertRaises(ValueError):
            analyze("trauma", {**SAMPLES["trauma"], "articular_involvement": "false"})

    def test_score_explanations_reconcile_with_the_returned_scores(self):
        for scenario in ("joint", "trauma"):
            for option in analyze(scenario, SAMPLES[scenario])["ranked_options"]:
                explained = sum(item["value"] for item in option["score_breakdown"])
                if scenario == "trauma":
                    self.assertEqual(option["raw_score"], explained)
                    self.assertEqual(option["score"], min(99, max(0, explained)))
                else:
                    self.assertEqual(option["score"], max(0, round(explained)))


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.original_paths = app.DATA, app.DB, app.UPLOADS
        app.DATA = Path(cls.temp.name)
        app.DB = app.DATA / "test.sqlite3"
        app.UPLOADS = app.DATA / "uploads"
        with app.connect():
            pass
        class QuietHandler(app.Handler):
            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(2)
        app.DATA, app.DB, app.UPLOADS = cls.original_paths
        cls.temp.cleanup()

    def setUp(self):
        self.provider = ProtocolDouble()
        self.config_temp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.config_temp.name) / ".env"
        self.config_path.write_text("DEEPSEEK_API_KEY=" + TEST_KEY + "\nDEEPSEEK_MODEL=deepseek-flash\n", encoding="utf-8")
        self.addCleanup(self.config_temp.cleanup)
        self.addCleanup(agent.reload_config)
        agent.reload_config(env_file=self.config_path, environ={})
        transport = patch.object(agent, "complete", side_effect=self.provider)
        transport.start()
        self.addCleanup(transport.stop)

    def request(self, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(self.base + path, body, headers={"Content-Type": "application/json"} if body else {})
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read())

    def create_case(self, scenario="spine", code="TEST-AGENT"):
        return self.request("/api/cases", {"code": code, "age": 35, "sex": "female",
                                           "scenario": scenario, "measurements": SAMPLES[scenario]})["id"]

    def wait_run(self, run_id):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            run = self.request("/api/agent/runs/" + run_id)
            if run["status"] != "running":
                return run
            time.sleep(0.02)
        self.fail("Agent run did not reach a terminal state: " + run_id)

    def generate_plan(self, case_id, mode=None):
        payload = {} if mode is None else {"mode": mode}
        request = Request(self.base + f"/api/cases/{case_id}/analyze", json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 202)
            started = json.loads(response.read())
        run = self.wait_run(started["run_id"])
        self.assertEqual(run["status"], "completed", run.get("error"))
        self.assertTrue(run["plan_id"])
        return {"id": run["plan_id"], "run": run}

    def test_closed_loop_for_all_three_scenarios(self):
        for scenario, measurements in SAMPLES.items():
            with self.subTest(scenario=scenario):
                created = self.request("/api/cases", {"code": f"TEST-{scenario}", "age": 35,
                                                       "sex": "female", "scenario": scenario,
                                                       "measurements": measurements})
                case_id = created["id"]
                plan = self.generate_plan(case_id)
                result = self.request(f"/api/plans/{plan['id']}/review",
                                      {"status": "approved", "reviewer": "Test Reviewer", "note": "Parameters checked"})
                self.assertEqual(result["status"], "approved")
                detail = self.request(f"/api/cases/{case_id}")
                self.assertEqual(detail["plans"][0]["status"], "approved")
                actions = [a["action"] for a in detail["audit"]]
                self.assertEqual([a for a in actions if a in ("case_created", "plan_generated", "plan_reviewed")],
                                 ["case_created", "plan_generated", "plan_reviewed"])
                self.assertIn("agent_started", actions)
                self.assertEqual(detail["agent_runs"][0]["status"], "completed")
                self.assertTrue(any(event["type"] == "tool" for event in plan["run"]["events"]))
                with urlopen(self.base + f"/api/plans/{plan['id']}/export") as response:
                    html = response.read().decode()
                self.assertIn("打印或保存为 PDF", html)
                self.assertIn(f"TEST-{scenario}", html)
                self.assertEqual(html.count("<caption>"), 3)
                self.assertIn("参数敏感性分析", html)
                self.assertIn("DeepSeek 智能体说明", html)
                self.assertIn("deepseek-flash", html)
                self.assertIn("规则计算", html)
                self.assertNotIn(TEST_KEY, html)
                self.assertNotIn("PRIVATE_REASONING_NOT_FOR_PUBLIC_TRACE", json.dumps(detail))

    def test_duplicate_review_rejected(self):
        created = self.request("/api/cases", {"code": "TEST-DUP", "age": 50, "sex": "male",
                                                   "scenario": "spine", "measurements": SAMPLES["spine"]})
        plan = self.generate_plan(created["id"])
        payload = {"status": "rejected", "reviewer": "Test Reviewer", "note": "Need more images"}
        self.request(f"/api/plans/{plan['id']}/review", payload)
        with self.assertRaises(HTTPError) as raised:
            self.request(f"/api/plans/{plan['id']}/review", payload)
        self.assertEqual(raised.exception.code, 400)

    def test_evidence_api_and_snapshot_are_exported(self):
        status = self.request('/api/evidence/status')
        self.assertTrue(status['available'])
        self.assertFalse(status['trained_weights'])
        search = self.request('/api/evidence/search?scenario=spine')
        self.assertTrue(search['items'])
        case_id = self.create_case(code='TEST-EVIDENCE')
        plan = self.generate_plan(case_id)
        result = self.request(f'/api/cases/{case_id}')['plans'][0]['result']
        self.assertEqual(result['agent_report']['evidence_ids'], ['E1'])
        self.assertTrue(result['evidence']['corpus_sha256'])
        with urlopen(self.base + f"/api/plans/{plan['id']}/export") as response:
            html = response.read().decode()
        self.assertIn('公开病例参考与证据边界', html)
        self.assertIn('https://pmc.ncbi.nlm.nih.gov/articles/', html)

    def test_clinical_summary_persists_and_reaches_model_without_identifiers(self):
        created = self.request('/api/cases', {'code':'TEST-SUMMARY', 'scenario':'spine', 'age':35,
                               'sex':'female','measurements':SAMPLES['spine'], 'clinical_summary':'匿名摘要：持续背痛，检查待补充。'})
        case_id = created['id']
        self.assertIn('持续背痛', self.request('/api/cases/'+case_id)['case']['clinical_summary'])
        self.generate_plan(case_id)
        current = json.loads(self.provider.requests[0]['messages'][-1]['content'])['current_case']
        self.assertIn('持续背痛', current['clinical_summary'])
        self.assertNotIn('code', current)

    def test_age_requires_integer_instead_of_truncating_decimal_or_boolean(self):
        for age in (True, 35.9, '35'):
            with self.subTest(age=age), self.assertRaises(HTTPError):
                self.request('/api/cases', {'code':'TEST-AGE', 'scenario':'spine', 'age':age,
                                          'sex':'female', 'measurements':SAMPLES['spine']})

    def test_image_attachment_round_trip(self):
        created = self.request("/api/cases", {"code": "TEST-IMAGE", "age": 42, "sex": "female",
                                                   "scenario": "joint", "measurements": SAMPLES["joint"]})
        png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c636000020000050001a5f645400000000049454e44ae426082")
        request = Request(self.base + f"/api/cases/{created['id']}/image", png,
                          headers={"Content-Type": "image/png"})
        with urlopen(request) as response:
            image_url = json.loads(response.read())["image_url"]
        detail = self.request(f"/api/cases/{created['id']}")
        self.assertEqual(detail["case"]["image_url"], image_url)
        self.assertIn("image_uploaded", [a["action"] for a in detail["audit"]])
        with urlopen(self.base + image_url) as response:
            self.assertEqual(response.read(), png)
        for content_type, body in [("text/plain", png), ("image/png", b"not-a-png"), ("image/jpeg", png)]:
            with self.subTest(content_type=content_type), self.assertRaises(HTTPError) as raised:
                urlopen(Request(self.base + f"/api/cases/{created['id']}/image", body,
                                headers={"Content-Type": content_type}))
            self.assertEqual(raised.exception.code, 400)
        unchanged = self.request(f"/api/cases/{created['id']}")
        self.assertEqual(unchanged["case"]["image_url"], image_url)

    def test_new_version_preserves_reviewed_snapshot(self):
        created = self.request("/api/cases", {"code": "TEST-VERSIONS", "age": 30, "sex": "female",
                                             "scenario": "joint", "measurements": SAMPLES["joint"]})
        first = self.generate_plan(created["id"])
        original_result = self.request(f"/api/cases/{created['id']}")["plans"][0]["result"]
        self.request(f"/api/plans/{first['id']}/review", {"status": "approved", "reviewer": "Reviewer", "note": "Checked dimensions"})
        second = self.generate_plan(created["id"])
        detail = self.request(f"/api/cases/{created['id']}")
        self.assertEqual([p["id"] for p in detail["plans"]], [second["id"], first["id"]])
        self.assertEqual([p["status"] for p in detail["plans"]], ["pending", "approved"])
        self.assertEqual(detail["plans"][1]["result"], original_result)

    def test_no_key_automatically_runs_explicitly_labelled_local_rules_without_a_model_request(self):
        case_id = self.create_case(code="TEST-NO-KEY")
        before = len(self.provider.requests)
        agent.reload_config(env_file=self.config_path, environ={"DEEPSEEK_API_KEY": ""})
        plan = self.generate_plan(case_id)
        detail = self.request(f"/api/cases/{case_id}")
        self.assertEqual(len(detail["plans"]), 1)
        run = plan["run"]
        self.assertEqual(run["provider"], "local_rules")
        self.assertEqual(run["mode"], "rules_fallback")
        self.assertEqual(run["usage"].get("total_tokens", 0), 0)
        self.assertFalse(any(event["type"] == "model" for event in run["events"]))
        result = detail["plans"][0]["result"]
        self.assertEqual(result["metrics"], analyze("spine", SAMPLES["spine"])["metrics"])
        self.assertEqual(result["agent"]["source"], "local_rules")
        self.assertEqual(result["agent"]["mode"], "rules_fallback")
        self.assertTrue(result["agent"]["fallback_reason"])
        self.assertFalse(agent.get_config()["connected"])
        self.assertEqual(len(self.provider.requests), before)

    def test_explicit_local_mode_works_with_a_key_and_exports_its_real_source(self):
        case_id = self.create_case(code="TEST-EXPLICIT-LOCAL")
        before = len(self.provider.requests)
        plan = self.generate_plan(case_id, mode="local_rules")
        self.assertEqual(len(self.provider.requests), before)
        self.assertEqual(plan["run"]["provider"], "local_rules")
        self.assertEqual(plan["run"]["mode"], "rules_fallback")
        self.assertFalse(agent.get_config()["connected"])
        with urlopen(self.base + f"/api/plans/{plan['id']}/export") as response:
            html = response.read().decode("utf-8")
        self.assertIn("本地", html)
        self.assertIn("规则", html)
        self.assertNotIn("DeepSeek 智能体说明", html)
        self.assertNotIn(TEST_KEY, html)

    def test_explicit_local_mode_still_runs_when_dotenv_is_invalid(self):
        case_id = self.create_case(code="TEST-INVALID-CONFIG-LOCAL")
        self.config_path.write_text('DEEPSEEK_API_KEY="' + TEST_KEY, encoding="utf-8")
        with self.assertRaises(agent.AgentError):
            agent.reload_config(env_file=self.config_path, environ={})
        before = len(self.provider.requests)
        plan = self.generate_plan(case_id, mode="local_rules")
        self.assertEqual(plan["run"]["provider"], "local_rules")
        self.assertEqual(len(self.provider.requests), before)

    def test_cloud_failure_remains_visible_and_local_retry_is_a_separate_run(self):
        case_id = self.create_case(code="TEST-CLOUD-FAIL-LOCAL-RETRY")
        with patch.object(agent, "complete", side_effect=agent.AgentError("服务不可用")):
            started = self.request(f"/api/cases/{case_id}/analyze", {})
            failed = self.wait_run(started["run_id"])
        self.assertEqual(failed["status"], "failed")
        self.assertIsNone(failed["plan_id"])
        self.assertEqual(self.request(f"/api/cases/{case_id}")["plans"], [])
        before = len(self.provider.requests)
        retried = self.generate_plan(case_id, mode="local_rules")
        self.assertNotEqual(retried["run"]["run_id"], failed["run_id"])
        self.assertEqual(retried["run"]["provider"], "local_rules")
        self.assertEqual(len(self.provider.requests), before)
        detail = self.request(f"/api/cases/{case_id}")
        self.assertEqual([run["status"] for run in detail["agent_runs"]], ["failed", "completed"])
        self.assertEqual(len(detail["plans"]), 1)

    def test_local_followup_keeps_the_existing_plan_and_never_marks_the_model_connected(self):
        case_id = self.create_case(code="TEST-LOCAL-FOLLOWUP")
        agent.reload_config(env_file=self.config_path, environ={"DEEPSEEK_API_KEY": ""})
        first = self.generate_plan(case_id)
        started = self.request(f"/api/cases/{case_id}/chat", {"message": "说明评分依据"})
        run = self.wait_run(started["run_id"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["provider"], "local_rules")
        self.assertEqual(run["mode"], "rules_fallback")
        self.assertIsNone(run["plan_id"])
        self.assertEqual(run["usage"].get("total_tokens", 0), 0)
        self.assertFalse(self.provider.requests)
        self.assertFalse(agent.get_config()["connected"])
        self.assertEqual([plan["id"] for plan in self.request(f"/api/cases/{case_id}")["plans"]], [first["id"]])

    def test_invalid_run_mode_is_rejected_before_starting_a_task_or_model_request(self):
        case_id = self.create_case(code="TEST-INVALID-MODE")
        for mode in ("unknown-provider", None, {"api_key": "not-accepted"}):
            with self.subTest(mode=mode), self.assertRaises(HTTPError) as caught:
                self.request(f"/api/cases/{case_id}/analyze", {"mode": mode})
            self.assertEqual(caught.exception.code, 400)
        self.assertFalse(self.provider.requests)
        detail = self.request(f"/api/cases/{case_id}")
        self.assertEqual(detail["agent_runs"], [])
        self.assertEqual(detail["plans"], [])

    def test_failed_model_run_keeps_failure_trace_and_never_saves_a_partial_plan(self):
        case_id = self.create_case(code="TEST-MODEL-FAIL")
        with patch.object(agent, "complete", side_effect=[reply(calls=[tool_call("analyze_spine")])] + [reply("broken JSON")] * (agent.MAX_REPORT_RETRIES + 1)):
            started = self.request(f"/api/cases/{case_id}/analyze", {})
            run = self.wait_run(started["run_id"])
        self.assertEqual(run["status"], "failed")
        self.assertIsNone(run["plan_id"])
        self.assertTrue(run["error"])
        self.assertIn('自动纠正', run['error'])
        self.assertTrue(any(e["type"] == "tool" for e in run["events"]))
        self.assertEqual(run["events"][-1]["type"], "error")
        detail = self.request(f"/api/cases/{case_id}")
        self.assertEqual(detail["plans"], [])
        self.assertIn("agent_failed", [a["action"] for a in detail["audit"]])

    def test_report_format_recovers_within_same_run_and_saves_one_pending_plan(self):
        case_id = self.create_case(code='TEST-FORMAT-RECOVERY')
        with patch.object(agent, 'complete', side_effect=[reply(calls=[tool_call('analyze_spine')]),
                                                        reply('not valid JSON'), reply(report_content())]):
            plan = self.generate_plan(case_id)
        detail = self.request('/api/cases/'+case_id)
        self.assertEqual(len(detail['agent_runs']), 1)
        self.assertEqual(len(detail['plans']), 1)
        self.assertEqual(detail['plans'][0]['status'], 'pending')
        self.assertEqual(detail['plans'][0]['result']['agent']['report_retries'], 1)
        self.assertEqual(plan['run']['usage']['total_tokens'], 45)
        self.assertTrue(any(e['type']=='repair' for e in plan['run']['events']))

    def test_followup_keeps_history_and_existing_plan_without_adding_a_version(self):
        case_id = self.create_case(code="TEST-FOLLOWUP")
        first = self.generate_plan(case_id)
        started = self.request(f"/api/cases/{case_id}/chat", {"message": "解释一下矫正比例的依据"})
        run = self.wait_run(started["run_id"])
        self.assertEqual(run["status"], "completed")
        self.assertIsNone(run["plan_id"])
        detail = self.request(f"/api/cases/{case_id}")
        self.assertEqual([p["id"] for p in detail["plans"]], [first["id"]])
        self.assertEqual(len(detail["agent_runs"]), 2)
        submitted = self.provider.requests[-1]["messages"]
        self.assertTrue(any(m["role"] == "assistant" for m in submitted))
        last_input = json.loads(submitted[-1]["content"])
        self.assertEqual(last_input["request"], "解释一下矫正比例的依据")
        self.assertEqual(last_input["previous_result"]["metrics"][1]["value"], "24.0°")

    def test_same_case_cannot_start_two_parallel_runs(self):
        case_id = self.create_case(code="TEST-CONCURRENT")
        entered, release = threading.Event(), threading.Event()

        def blocking_provider(payload, **kwargs):
            entered.set()
            if not release.wait(5):
                raise agent.AgentError("Test release timed out")
            return self.provider(payload, **kwargs)

        with patch.object(agent, "complete", side_effect=blocking_provider):
            started = self.request(f"/api/cases/{case_id}/analyze", {})
            try:
                self.assertTrue(entered.wait(3))
                with self.assertRaises(HTTPError) as caught:
                    self.request(f"/api/cases/{case_id}/analyze", {})
                self.assertEqual(caught.exception.code, 409)
            finally:
                release.set()
                run = self.wait_run(started["run_id"])
        self.assertEqual(run["status"], "completed")
        self.assertEqual(len(self.request(f"/api/cases/{case_id}")["plans"]), 1)

    def test_watchdog_expires_blocked_run_and_late_worker_cannot_overwrite_retry(self):
        case_id = self.create_case(code="TEST-WATCHDOG")
        case = self.request(f"/api/cases/{case_id}")["case"]
        genuine_runner = agent.run_agent
        valid_output = genuine_runner(case, "生成报告", config=agent.snapshot_config())
        entered, release = threading.Event(), threading.Event()
        old_workers = []

        def blocked_first_run(*args, **kwargs):
            if not entered.is_set():
                old_workers.append(threading.current_thread())
                entered.set()
                if not release.wait(5):
                    raise agent.AgentError("Test release timed out")
                # Intentionally bypass run_agent's internal deadline check:
                # the outer persistence guard must still reject this late result.
                kwargs["on_event"]({"type": "report", "title": "迟到的模型答复",
                                    "detail": "不得覆盖已超时任务。", "at": app.now()})
                return copy.deepcopy(valid_output)
            return genuine_runner(*args, **kwargs)

        with patch.object(agent, "RUN_TIMEOUT", 0.2), patch.object(agent, "run_agent", side_effect=blocked_first_run):
            started = self.request(f"/api/cases/{case_id}/analyze", {})
            try:
                self.assertTrue(entered.wait(2))
                expired = self.wait_run(started["run_id"])
                self.assertEqual(expired["status"], "failed")
                self.assertIsNone(expired["plan_id"])
                self.assertTrue(expired["error"])
                self.assertEqual(self.request(f"/api/cases/{case_id}")["plans"], [])
                expired_events = copy.deepcopy(expired["events"])
                retried = self.generate_plan(case_id)
                self.assertNotEqual(retried["run"]["run_id"], started["run_id"])
            finally:
                release.set()
                if old_workers:
                    old_workers[0].join(3)
        self.assertFalse(old_workers[0].is_alive())
        unchanged = self.request("/api/agent/runs/" + started["run_id"])
        self.assertEqual(unchanged["status"], "failed")
        self.assertIsNone(unchanged["plan_id"])
        self.assertEqual(unchanged["events"], expired_events)
        self.assertEqual([p["id"] for p in self.request(f"/api/cases/{case_id}")["plans"]], [retried["id"]])

    def test_model_text_is_escaped_in_export_and_secrets_never_persist(self):
        self.provider.text = "<script>alert('model')</script>"
        case_id = self.create_case(code="TEST-ESCAPE")
        plan = self.generate_plan(case_id)
        with urlopen(self.base + f"/api/plans/{plan['id']}/export") as response:
            html = response.read().decode()
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)
        config = self.request("/api/agent/config")
        self.assertTrue(config["configured"])
        self.assertTrue(config["connected"])
        self.assertNotIn(TEST_KEY, json.dumps(config))
        with app.connect() as db:
            stored = "\n".join(db.iterdump())
        self.assertNotIn(TEST_KEY, stored)
        self.assertNotIn("PRIVATE_REASONING_NOT_FOR_PUBLIC_TRACE", stored)

    def test_cross_origin_cannot_modify_model_configuration(self):
        request = Request(self.base + "/api/agent/config", b'{"clear_key":true}',
                          headers={"Content-Type": "application/json", "Origin": "https://other.invalid"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 403)
        self.assertTrue(self.request("/api/agent/config")["configured"])

    def test_model_configuration_is_read_only_over_http_for_every_payload(self):
        original = agent.snapshot_config()
        supplied_key = "sk-untrusted-http-test-only"
        for payload in ({"api_key": supplied_key}, {"model": "other-model"}, {"clear_key": True}, {}):
            with self.subTest(payload=payload), self.assertRaises(HTTPError) as caught:
                self.request("/api/agent/config", payload)
            self.assertEqual(caught.exception.code, 405)
            self.assertEqual(caught.exception.headers.get("Allow"), "GET")
            self.assertNotIn(supplied_key, caught.exception.read().decode("utf-8"))
            self.assertEqual(agent.snapshot_config(), original)
        config = self.request("/api/agent/config")
        self.assertEqual(config["key_source"], "dotenv")
        self.assertNotIn("api_key", config)
        self.assertNotIn(TEST_KEY, json.dumps(config))
        self.assertNotIn(supplied_key, app.DB.read_bytes().decode("utf-8", errors="ignore"))

    def test_dotenv_file_cannot_be_downloaded_through_any_public_file_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "static").mkdir()
            (root / "static" / "probe.txt").write_text("public-asset", encoding="utf-8")
            (root / ".env").write_text("DEEPSEEK_API_KEY=" + TEST_KEY, encoding="utf-8")
            with patch.object(app, "ROOT", root):
                with urlopen(self.base + "/static/probe.txt") as response:
                    self.assertEqual(response.read(), b"public-asset")
                for path in ("/.env", "/static/../.env", "/static/%2e%2e/.env", "/static/%2E%2E/%2Eenv",
                             "/static/..%2f.env", "/uploads/../.env"):
                    with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                        urlopen(self.base + path)
                    self.assertIn(caught.exception.code, (403, 404))
                    self.assertNotIn(TEST_KEY, caught.exception.read().decode("utf-8"))

    def test_frontend_has_no_credential_form_or_configuration_write_code(self):
        html = (app.ROOT / "static" / "index.html").read_text(encoding="utf-8")
        script = (app.ROOT / "static" / "app.js").read_text(encoding="utf-8")
        self.assertNotRegex(html, r"(?i)type\s*=\s*['\"]password['\"]")
        for obsolete in ("settings.api_key", "submitSettings", "clearKey", "saveSettings"):
            self.assertNotIn(obsolete, html + script)
        self.assertNotRegex(script, r"post\s*\(\s*['\"]/api/agent/config['\"]")
        self.assertIn("/api/agent/test", script)

    def test_legacy_schema_and_rule_plan_remain_readable_after_migration(self):
        # Original cases/plans/audit definitions have no agent_runs table.
        with tempfile.TemporaryDirectory() as location:
            legacy_db = Path(location) / "legacy.sqlite3"
            with closing(sqlite3.connect(legacy_db)) as db, db:
                db.executescript("""
                    CREATE TABLE cases (id TEXT PRIMARY KEY, code TEXT NOT NULL, scenario TEXT NOT NULL,
                      age INTEGER NOT NULL, sex TEXT NOT NULL, measurements TEXT NOT NULL,
                      image_name TEXT, created_at TEXT NOT NULL);
                    CREATE TABLE plans (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
                      result TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',reviewer TEXT,
                      review_note TEXT,reviewed_at TEXT,created_at TEXT NOT NULL);
                    CREATE TABLE audit (id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL,
                      action TEXT NOT NULL,detail TEXT NOT NULL,at TEXT NOT NULL);
                """)
                db.execute("INSERT INTO cases VALUES(?,?,?,?,?,?,?,?)", (
                    "legacy", "LEGACY-CASE", "spine", 30, "female", json.dumps(SAMPLES["spine"]), None, app.now()))
                db.execute("INSERT INTO plans(id,case_id,result,status,created_at) VALUES(?,?,?,?,?)", (
                    "legacy-plan", "legacy", json.dumps(analyze("spine", SAMPLES["spine"])), "approved", app.now()))
            with patch.object(app, "DB", legacy_db), app.connect() as db:
                self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='agent_runs'").fetchone())
                plan = app.plan_dict(db.execute("SELECT * FROM plans WHERE id='legacy-plan'").fetchone())
                self.assertEqual(plan["status"], "approved")
                self.assertNotIn("agent", plan["result"])
                self.assertEqual(plan["result"]["metrics"][1]["value"], "24.0°")

    def test_frontend_and_local_assets_are_available(self):
        for path in ["/", "/index.html", "/static/app.js", "/static/style.css", "/static/vendor/vue.global.prod.js"]:
            with self.subTest(path=path), urlopen(self.base + path) as response:
                self.assertEqual(response.status, 200)
                self.assertGreater(len(response.read()), 100)
        with self.assertRaises(HTTPError):
            urlopen(self.base + "/static/../app.py")


if __name__ == "__main__":
    unittest.main()
