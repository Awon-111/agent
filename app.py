"""Local demo server. Run: python app.py"""

from __future__ import annotations

import json
import mimetypes
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import agent
import evidence
from planner import analyze


ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "demo.sqlite3"
UPLOADS = DATA / "uploads"
SCENARIOS = {"spine": "脊柱侧弯", "joint": "关节置换", "trauma": "骨折内固定"}
RUN_LOCK = threading.RLock()


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    DATA.mkdir(exist_ok=True, parents=True)
    UPLOADS.mkdir(exist_ok=True)
    db = sqlite3.connect(DB, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS cases (
      id TEXT PRIMARY KEY, code TEXT NOT NULL, scenario TEXT NOT NULL,
      age INTEGER NOT NULL, sex TEXT NOT NULL, measurements TEXT NOT NULL,
      image_name TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS plans (
      id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
      result TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
      reviewer TEXT, review_note TEXT, reviewed_at TEXT, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS audit (
      id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT NOT NULL,
      action TEXT NOT NULL, detail TEXT NOT NULL, at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS agent_runs (
      id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id),
      question TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL,
      events TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', plan_id TEXT,
      error TEXT, usage TEXT NOT NULL DEFAULT '{}',
      provider TEXT NOT NULL, model TEXT NOT NULL,
      created_at TEXT NOT NULL, finished_at TEXT
    );
    CREATE INDEX IF NOT EXISTS agent_runs_case ON agent_runs(case_id,created_at);
    """)
    if 'clinical_summary' not in {r[1] for r in db.execute('PRAGMA table_info(cases)')}:
        db.execute("ALTER TABLE cases ADD COLUMN clinical_summary TEXT NOT NULL DEFAULT ''")
    try:
        with db:
            yield db
    finally:
        db.close()


def record(db, entity_id, action, detail):
    db.execute("INSERT INTO audit(entity_id,action,detail,at) VALUES(?,?,?,?)",
               (entity_id, action, detail, now()))


def case_dict(row):
    d = dict(row)
    d["measurements"] = json.loads(d["measurements"])
    d["scenario_label"] = SCENARIOS[d["scenario"]]
    d["image_url"] = f"/uploads/{d['image_name']}" if d["image_name"] else None
    return d


def plan_dict(row):
    d = dict(row)
    d["result"] = json.loads(d["result"])
    return d


def run_dict(row, case_context=None):
    d = dict(row)
    d["run_id"] = d.pop("id")
    d["events"] = json.loads(d["events"])
    d["usage"] = json.loads(d["usage"])
    d["mode"] = "rules_fallback" if d["provider"] == "local_rules" else "llm"
    d["fallback_reason"] = d["events"][0]["detail"] if d["provider"] == "local_rules" and d["events"] else None
    if case_context and d.get('answer'):
        try:
            agent.validate_case_claims(d['answer'], None, case_context)
        except agent.AgentError:
            d['quality_notice'] = '该历史回复复述的输入值与已确认参数不一致，请以最新报告及工具结果为准。'
    return d


def recover_interrupted_runs():
    """After a server restart, don't leave polling clients waiting forever."""
    with connect() as db:
        rows = db.execute("SELECT * FROM agent_runs WHERE status='running'").fetchall()
        for row in rows:
            events = json.loads(row["events"])
            error = "服务已重启，上次智能体任务未完成。请重新运行。"
            events.append({"type": "error", "title": "任务已中断", "detail": error, "at": now()})
            db.execute("UPDATE agent_runs SET status='failed',error=?,events=?,finished_at=? WHERE id=?",
                       (error, json.dumps(events, ensure_ascii=False), now(), row["id"]))


def execute_run(run_id, case, question, kind, config, history, previous_result):
    deadline = time.monotonic() + agent.RUN_TIMEOUT
    timeout_error = "智能体任务已达到总时间上限，已结束运行；迟到响应不会保存为方案，请重新运行。"

    def fail_in_transaction(db, row, error, title):
        """Caller holds RUN_LOCK; status check and transition are one transaction."""
        events = json.loads(row["events"])
        events.append({"type": "error", "title": title, "detail": error, "at": now()})
        changed = db.execute("UPDATE agent_runs SET status='failed',error=?,events=?,finished_at=? WHERE id=? AND status='running'",
                             (error, json.dumps(events[-100:], ensure_ascii=False), now(), run_id))
        if changed.rowcount:
            record(db, case["id"], "agent_failed", f"智能体任务 {run_id} 失败：{error}")

    def fail_if_running(error, title="智能体运行失败"):
        with RUN_LOCK:
            with connect() as db:
                row = db.execute("SELECT status,events FROM agent_runs WHERE id=?", (run_id,)).fetchone()
                if row and row["status"] == "running":
                    fail_in_transaction(db, row, error, title)

    def append_event(event):
        with RUN_LOCK:
            with connect() as db:
                row = db.execute("SELECT status,events FROM agent_runs WHERE id=?", (run_id,)).fetchone()
                if not row or row["status"] != "running":
                    raise agent.AgentError("任务已经结束，已停止处理迟到响应。", 504)
                if time.monotonic() >= deadline:
                    fail_in_transaction(db, row, timeout_error, "智能体任务超时")
                    # Return out of the transaction before raising, so failure
                    # status commits rather than being rolled back by connect().
                    expired = True
                else:
                    expired = False
                    events = json.loads(row["events"])
                    events.append(event)
                    db.execute("UPDATE agent_runs SET events=? WHERE id=? AND status='running'",
                               (json.dumps(events[-100:], ensure_ascii=False), run_id))
        if expired:
            raise agent.AgentError(timeout_error, 504)

    # Independent of the request thread: even a provider's keep-alive trickle or
    # an unresponsive transport cannot leave the public task running forever.
    watchdog = threading.Timer(agent.RUN_TIMEOUT, fail_if_running, args=(timeout_error, "智能体任务超时"))
    watchdog.daemon = True
    watchdog.start()
    try:
        local_mode = config["provider"] == "local_rules"
        if local_mode:
            output = agent.run_local_rules(case, question, config["fallback_reason"], run_id=run_id,
                                           on_event=append_event, require_plan=kind == "analyze",
                                           previous_result=previous_result)
        else:
            output = agent.run_agent(case, question, history=history, on_event=append_event,
                                     run_id=run_id, config=config, require_plan=kind == "analyze",
                                     previous_result=previous_result)
        # Serialize terminal transitions with the watchdog and new run creation.
        # A timed-out worker may return later, but can never publish a plan.
        with RUN_LOCK:
            with connect() as db:
                row = db.execute("SELECT status,events FROM agent_runs WHERE id=?", (run_id,)).fetchone()
                if not row or row["status"] != "running":
                    return
                if time.monotonic() >= deadline:
                    fail_in_transaction(db, row, timeout_error, "智能体任务超时")
                    return
                plan_id = None
                if output["result"] is not None:
                    plan_id = uuid.uuid4().hex[:12]
                    db.execute("INSERT INTO plans(id,case_id,result,created_at) VALUES(?,?,?,?)",
                               (plan_id, case["id"], json.dumps(output["result"], ensure_ascii=False), now()))
                    source_label = "本地规则演示" if local_mode else "DeepSeek 智能体"
                    record(db, case["id"], "plan_generated", f"{source_label}生成方案 {plan_id}；任务 {run_id}")
                else:
                    source_label = "本地规则演示" if local_mode else "DeepSeek 智能体"
                    record(db, case["id"], "agent_answered", f"{source_label}完成追问 {run_id}；未新增方案版本")
                db.execute("UPDATE agent_runs SET status='completed',answer=?,plan_id=?,usage=?,finished_at=? WHERE id=? AND status='running'",
                           (output["answer"], plan_id, json.dumps(output["usage"]), now(), run_id))
    except Exception as exc:
        error = str(exc) if isinstance(exc, agent.AgentError) else "智能体任务未完成，请检查服务设置后重试。"
        fail_if_running(error)
    finally:
        watchdog.cancel()


def start_run(case_id, kind, question, mode="auto"):
    if mode not in ("auto", "local_rules"):
        raise agent.AgentError("运行模式只支持 auto 或 local_rules。", 400)
    if mode == "local_rules":
        config = {"provider": "local_rules", "api_key": "", "model": "ORTHO-TOOLS 3.0",
                  "fallback_reason": "操作人选择本地规则演示。"}
    else:
        config = agent.snapshot_config()
        if not config["api_key"]:
            config = {"provider": "local_rules", "api_key": "", "model": "ORTHO-TOOLS 3.0",
                      "fallback_reason": "后端未配置 DeepSeek API Key，自动使用本地规则演示。"}
    local_mode = config["provider"] == "local_rules"
    with RUN_LOCK:
        with connect() as db:
            row = db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
            if not row:
                raise agent.AgentError("病例不存在。", 404)
            if db.execute("SELECT 1 FROM agent_runs WHERE case_id=? AND status='running'", (case_id,)).fetchone():
                raise agent.AgentError("该病例已有智能体任务正在运行，请等待完成。", 409)
            case = case_dict(row)
            previous = db.execute("SELECT result FROM plans WHERE case_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (case_id,)).fetchone()
            previous_result = json.loads(previous["result"]) if previous else None
            recent = db.execute("SELECT question,answer FROM agent_runs WHERE case_id=? AND status='completed' ORDER BY created_at DESC,rowid DESC LIMIT 6", (case_id,)).fetchall()
            history = [dict(r) for r in reversed(recent)]
            run_id = uuid.uuid4().hex[:16]
            events = [{"type": "local" if local_mode else "model",
                       "title": "本地规则任务已建立" if local_mode else "任务已建立",
                       "detail": config["fallback_reason"] if local_mode else "准备调用 DeepSeek，当前病例参数将作为工具计算依据。", "at": now()}]
            db.execute("INSERT INTO agent_runs(id,case_id,question,kind,status,events,provider,model,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (run_id, case_id, question, kind, "running", json.dumps(events, ensure_ascii=False), config["provider"], config["model"], now()))
            source_label = "本地规则演示" if local_mode else "DeepSeek 智能体"
            record(db, case_id, "agent_started", f"启动{source_label}任务 {run_id}")
        thread = threading.Thread(target=execute_run, args=(run_id, case, question, kind, config, history, previous_result), daemon=True)
        thread.start()
    return {"run_id": run_id, "status": "running", "mode": "rules_fallback" if local_mode else "llm",
            "provider": config["provider"], "model": config["model"]}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{now()}] {self.address_string()} {fmt % args}")

    def respond(self, status, data, headers=None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 <= length <= 1_000_000:
            raise ValueError("Request too large")
        payload = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(payload, dict):
            raise ValueError("Request must be a JSON object")
        return payload

    def local_request(self):
        """Reject DNS rebinding and cross-origin writes to local model settings."""
        try:
            host = urlparse("//" + self.headers.get("Host", ""))
            port = self.server.server_address[1]
            if host.hostname not in ("localhost", "127.0.0.1", "::1") or host.port != port:
                return False
            origin = self.headers.get("Origin")
            if origin:
                parsed = urlparse(origin)
                if parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1", "::1") or parsed.port != port:
                    return False
            return True
        except ValueError:
            return False

    def do_GET(self):
        if not self.local_request():
            return self.respond(403, {"error": "仅允许通过本机同源页面访问。"})
        path = urlparse(self.path).path
        try:
            if path in ("/", "/index.html") or path.startswith("/static/"):
                file = ROOT / "static/index.html" if path in ("/", "/index.html") else (ROOT / path.lstrip("/")).resolve()
                if not file.is_relative_to((ROOT / "static").resolve()):
                    return self.respond(404, {"error": "Not found"})
                self.file_response(file)
                return
            if path.startswith("/uploads/"):
                name = path.removeprefix("/uploads/")
                if name != Path(name).name:
                    return self.respond(404, {"error": "Not found"})
                self.file_response(UPLOADS / name)
                return
            if path == "/api/health":
                return self.respond(200, {"status": "ok", "mode": "deepseek_agent"})
            if path == "/api/catalog":
                return self.respond(200, {"scenarios": SCENARIOS})
            if path == '/api/evidence/status':
                return self.respond(200, evidence.status())
            if path == '/api/evidence/search':
                params = parse_qs(urlparse(self.path).query)
                scenario = params.get('scenario', ['spine'])[0]
                return self.respond(200, evidence.retrieve({'scenario': scenario}, params.get('q', [''])[0]))
            if path == "/api/agent/config":
                return self.respond(200, agent.get_config())
            with connect() as db:
                if path.startswith("/api/agent/runs/") and len(path.strip("/").split("/")) == 4:
                    row = db.execute("SELECT * FROM agent_runs WHERE id=?", (path.split("/")[4],)).fetchone()
                    if not row:
                        return self.respond(404, {"error": "Agent run not found"})
                    return self.respond(200, run_dict(row))
                if path == "/api/cases":
                    rows = db.execute("""SELECT cases.*,
                      (SELECT status FROM plans WHERE case_id=cases.id ORDER BY created_at DESC,rowid DESC LIMIT 1) AS latest_plan_status,
                      (SELECT COUNT(*) FROM plans WHERE case_id=cases.id) AS plan_count
                      FROM cases ORDER BY created_at DESC,rowid DESC""").fetchall()
                    return self.respond(200, {"cases": [case_dict(r) for r in rows]})
                if path.startswith("/api/cases/") and len(path.strip("/").split("/")) == 3:
                    case_id = path.split("/")[3]
                    row = db.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
                    if not row:
                        return self.respond(404, {"error": "Case not found"})
                    plans = db.execute("SELECT * FROM plans WHERE case_id=? ORDER BY created_at DESC,rowid DESC", (case_id,)).fetchall()
                    audit = db.execute("SELECT action,detail,at FROM audit WHERE entity_id=? ORDER BY id", (case_id,)).fetchall()
                    runs = db.execute("SELECT * FROM agent_runs WHERE case_id=? ORDER BY created_at,rowid", (case_id,)).fetchall()
                    case_context = case_dict(row)
                    return self.respond(200, {"case": case_context, "plans": [plan_dict(p) for p in plans], "audit": [dict(a) for a in audit], "agent_runs": [run_dict(r, case_context) for r in runs]})
                if path.startswith("/api/plans/") and path.endswith("/export"):
                    plan_id = path.split("/")[3]
                    row = db.execute("SELECT plans.*,cases.code,cases.scenario,cases.measurements FROM plans JOIN cases ON cases.id=plans.case_id WHERE plans.id=?", (plan_id,)).fetchone()
                    if not row:
                        return self.respond(404, {"error": "Plan not found"})
                    return self.export_plan(row)
            self.respond(404, {"error": "Not found"})
        except agent.AgentError as exc:
            self.respond(exc.status, {"error": str(exc)})
        except Exception as exc:
            self.respond(400, {"error": str(exc)})

    def file_response(self, file):
        if not file.is_file():
            return self.respond(404, {"error": "Not found"})
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.local_request():
            return self.respond(403, {"error": "仅允许通过本机同源页面提交。"})
        path = urlparse(self.path).path
        try:
            if path == "/api/agent/config":
                # Settings are owned by the server. Do not read or accept a Key
                # from request bodies, including requests from the local UI.
                # Drain a bounded body without decoding/processing credentials.
                # Closing a socket with unread bytes can reset Windows clients
                # before they receive the 405 response.
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    length = 0
                if 0 < length <= 1_000_000:
                    self.rfile.read(length)
                return self.respond(405, {"error": "模型配置为只读。请修改后端 .env 或环境变量并重启服务。"}, {"Allow": "GET"})
            if path == "/api/agent/test":
                self.read_json()
                return self.respond(200, agent.test_connection())
            if path == "/api/cases":
                payload = self.read_json()
                scenario = payload.get("scenario")
                if scenario not in SCENARIOS:
                    raise ValueError("Invalid scenario")
                code = str(payload.get("code", "")).strip()
                if not 2 <= len(code) <= 32:
                    raise ValueError("Case code must have 2–32 characters")
                age = payload.get('age', 0)
                if isinstance(age, bool) or not isinstance(age, int):
                    raise ValueError('年龄必须是整数。')
                if not 1 <= age <= 120:
                    raise ValueError("Age must be 1–120")
                sex = payload.get("sex")
                if sex not in ("female", "male", "other"):
                    raise ValueError("Invalid sex")
                measurements = payload.get("measurements")
                if not isinstance(measurements, dict):
                    raise ValueError("Measurements required")
                analyze(scenario, measurements)  # validate before saving
                clinical_summary = payload.get('clinical_summary', '')
                if not isinstance(clinical_summary, str) or len(clinical_summary) > 3000:
                    raise ValueError('匿名临床摘要最多 3000 字。')
                case_id = uuid.uuid4().hex[:12]
                with connect() as db:
                    db.execute("INSERT INTO cases(id,code,scenario,age,sex,measurements,created_at) VALUES(?,?,?,?,?,?,?)",
                               (case_id, code, scenario, age, sex, json.dumps(measurements, ensure_ascii=False), now()))
                    db.execute('UPDATE cases SET clinical_summary=? WHERE id=?', (clinical_summary.strip(), case_id))
                    record(db, case_id, "case_created", f"创建{SCENARIOS[scenario]}病例")
                return self.respond(201, {"id": case_id})
            parts = path.strip("/").split("/")
            if len(parts) == 4 and parts[:2] == ["api", "cases"] and parts[3] == "image":
                return self.upload_image(parts[2])
            if len(parts) == 4 and parts[:2] == ["api", "cases"] and parts[3] in ("analyze", "chat"):
                payload = self.read_json()
                case_id = parts[2]
                if parts[3] == "chat":
                    question = payload.get("message")
                    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 4000:
                        raise ValueError("追问内容需为 1–4000 字的文本。")
                    question = question.strip()
                else:
                    question = "请检查当前病例，选择并调用对应专科规划工具，结合单因素敏感性结果生成一份可复核的辅助规划报告。"
                return self.respond(202, start_run(case_id, parts[3], question, mode=payload.get("mode", "auto")))
            if len(parts) == 4 and parts[:2] == ["api", "plans"] and parts[3] == "review":
                payload = self.read_json()
                status = payload.get("status")
                reviewer = str(payload.get("reviewer", "")).strip()
                note = str(payload.get("note", "")).strip()
                if status not in ("approved", "rejected") or not 2 <= len(reviewer) <= 50 or not 2 <= len(note) <= 500:
                    raise ValueError("Review requires decision, reviewer and note")
                with connect() as db:
                    row = db.execute("SELECT case_id,status FROM plans WHERE id=?", (parts[2],)).fetchone()
                    if not row:
                        return self.respond(404, {"error": "Plan not found"})
                    if row["status"] != "pending":
                        raise ValueError("Plan already reviewed")
                    updated = db.execute("UPDATE plans SET status=?,reviewer=?,review_note=?,reviewed_at=? WHERE id=? AND status='pending'",
                               (status, reviewer, note, now(), parts[2]))
                    if updated.rowcount != 1:
                        raise ValueError("Plan already reviewed")
                    record(db, row["case_id"], "plan_reviewed", f"方案 {parts[2]}：{status}；{reviewer}；{note}")
                return self.respond(200, {"status": status})
            self.respond(404, {"error": "Not found"})
        except agent.AgentError as exc:
            self.respond(exc.status, {"error": str(exc)})
        except Exception as exc:
            self.respond(400, {"error": str(exc)})

    def upload_image(self, case_id):
        content_type = self.headers.get("Content-Type", "")
        ext = {"image/png": ".png", "image/jpeg": ".jpg"}.get(content_type)
        if not ext:
            raise ValueError("Only PNG or JPEG images are accepted")
        length = int(self.headers.get("Content-Length", "0"))
        if not 1 <= length <= 5_000_000:
            raise ValueError("Image must be 1 byte to 5 MB")
        data = self.rfile.read(length)
        if not ((ext == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n")) or
                (ext == ".jpg" and data.startswith(b"\xff\xd8\xff"))):
            raise ValueError("Image signature does not match type")
        with connect() as db:
            row = db.execute("SELECT image_name FROM cases WHERE id=?", (case_id,)).fetchone()
            if not row:
                return self.respond(404, {"error": "Case not found"})
            filename = uuid.uuid4().hex + ext
            (UPLOADS / filename).write_bytes(data)
            db.execute("UPDATE cases SET image_name=? WHERE id=?", (filename, case_id))
            record(db, case_id, "image_uploaded", "上传影像附件（仅存档，不自动识别）")
            if row["image_name"]:
                (UPLOADS / row["image_name"]).unlink(missing_ok=True)
        return self.respond(200, {"image_url": f"/uploads/{filename}"})

    def export_plan(self, row):
        result = json.loads(row["result"])
        measures = json.loads(row["measurements"])
        esc = lambda value: escape(str(value))
        option_rows = []
        for option in result["ranked_options"]:
            breakdown = "；".join(f"{part['label']}：{part['value']:+g}" for part in option.get("score_breakdown", []))
            detail = f"<br><small>{esc(breakdown)}；最终分按工具的取整与限幅规则计算。</small>" if breakdown else ""
            option_rows.append(f"<tr><td>{esc(option['name'])}</td><td>{esc(option['score'])}</td><td>{esc(option['reason'])}{detail}</td></tr>")
        options = "".join(option_rows)
        flags = "".join(f"<li>{esc(x)}</li>" for x in result["flags"])
        fields = "".join(f"<tr><td>{esc(k)}</td><td>{esc(v)}</td></tr>" for k, v in measures.items())
        with connect() as db:
            original = db.execute('SELECT clinical_summary FROM cases WHERE id=?', (row['case_id'],)).fetchone()
        if original and original['clinical_summary']:
            fields += '<tr><td>匿名临床摘要</td><td>' + esc(original['clinical_summary']) + '</td></tr>'
        status = {"pending": "待复核", "approved": "已通过", "rejected": "已退回"}[row["status"]]
        calculations = "".join(f"<li>{esc(x)}</li>" for x in result.get("calculation", []))
        sensitivity_html = ""
        if result.get("sensitivity"):
            sens = result["sensitivity"]
            points = "".join(f"<tr><td>{esc(p['x'])} {esc(sens['unit'])}</td><td>{esc(p['y'])}</td><td>{esc(p['option'])}</td></tr>" for p in sens["points"])
            sensitivity_html = f"<h2>参数敏感性分析</h2><p>{esc(sens['note'])}</p><table><caption>表 3　{esc(sens['label'])}变化与结果响应</caption><tr><th>输入值</th><th>{esc(sens['metric'])}</th><th>规则首选</th></tr>{points}</table>"
        report_html = "<p class='note'>历史规则方案：本版本未调用 LLM，不含模型生成的说明。</p>"
        metadata = result.get("agent")
        if metadata and result.get("agent_report"):
            report = result["agent_report"]
            local_report = metadata.get("source") == "local_rules"
            report_title = "本地规则演示说明" if local_report else "DeepSeek 智能体说明"
            report_source = ("本次未调用 LLM。说明由本地模板整理。原因：" + str(metadata.get("fallback_reason", "本地规则演示"))
                             if local_report else "以下为模型基于工具结果生成的解释，数值计算依据见后文。")
            report_html = f"<h2>{report_title}</h2><p class='note'>{esc(report_source)} 生成时间：{esc(metadata.get('generated_at', ''))}</p><p>{esc(report.get('overview', ''))}</p><h3>规划建议</h3><p>{esc(report.get('recommendation', ''))}</p>"
            for key, label in (("rationale", "依据"), ("limitations", "局限"), ("next_steps", "待复核事项")):
                items = "".join(f"<li>{esc(x)}</li>" for x in report.get(key, []))
                report_html += f"<h3>{label}</h3><ul>{items}</ul>"
            calls = "".join(f"<li><strong>{esc(x.get('label', x.get('name', '')))}</strong>（{esc(x.get('status', ''))}）：{esc(x.get('summary', ''))}</li>" for x in metadata.get("tool_calls", []))
            usage = metadata.get("usage", {})
            execution_title = "本地规则执行记录" if local_report else "智能体运行记录"
            report_html += f"<h2>{execution_title}</h2><p>服务：{esc(metadata.get('provider', ''))}；模型或工具版本：{esc(metadata.get('model', ''))}；任务：{esc(metadata.get('run_id', ''))}<br>Token 用量：输入 {esc(usage.get('prompt_tokens', 0))}，输出 {esc(usage.get('completion_tokens', 0))}，合计 {esc(usage.get('total_tokens', 0))}。</p><ol>{calls}</ol>"
        found = result.get('evidence', {})
        report_html += '<h2>公开病例参考与证据边界</h2><p>' + esc(found.get('notice', '历史版本未执行病例检索。')) + '</p>'
        report_html += '<p>检索库快照 SHA256：' + esc(found.get('corpus_sha256', '无')) + '</p>'
        for item in found.get('items', []):
            report_html += (f"<h3>[{esc(item['citation_id'])}] {esc(item['title'])}</h3>"
                            f"<p><a href='{esc(item['source_url'])}'>{esc(item['source_url'])}</a> · {esc(item['license'])}</p>"
                            f"<p>{esc(item['case_text'])}</p><p>{esc(item['applicability'])}</p>")
        for item in result.get('agent_report', {}).get('case_comparison', []):
            report_html += '<p>' + esc(item) + '</p>'
        html = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>骨科规划报告 {esc(row['code'])}</title>
<style>body{{font:15px/1.8 'Microsoft YaHei',sans-serif;max-width:850px;margin:50px auto;color:#24494c}}h1{{font-size:26px}}h2{{font-size:18px;margin-top:30px}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #dce9e5;padding:9px;text-align:left}}th{{background:#eaf5f0}}caption{{caption-side:top;font-weight:700;padding:10px;font-size:14px}}.note{{color:#7b9588}}.toolbar{{text-align:right}}@media print{{.toolbar{{display:none}}body{{margin:10mm}}table{{break-inside:avoid}}}}</style>
<div class="toolbar"><button onclick="window.print()">打印或保存为 PDF</button></div><h1>骨科智能体规划报告</h1><p><strong>病例编号：</strong>{esc(row['code'])}　<strong>场景：</strong>{esc(SCENARIOS[row['scenario']])}　<strong>状态：</strong>{esc(status)}</p>
<p class="note">辅助分析草稿，须独立人工复核；不可直接用于诊断、手术或器械选型。</p>{report_html}<h2>输入参数</h2><table><caption>表 1　病例测量输入参数</caption>{fields}</table><h2>工具计算摘要</h2><p>{esc(result['summary'])}</p><h2>候选方案（规则计算）</h2><table><caption>表 2　候选方案评分与依据</caption><tr><th>方案</th><th>规则分数</th><th>依据</th></tr>{options}</table>{sensitivity_html}<h2>工具风险提醒</h2><ul>{flags}</ul><h2>人工复核记录</h2><p>复核人：{esc(row['reviewer'] or '待复核')}　结论：{esc(status)}<br>备注：{esc(row['review_note'] or '暂无')}</p><h2>计算工具说明</h2><p>{esc(result['method'])}</p><ul>{calculations}</ul><p>{esc(result.get('model_version', 'ORTHO-RULES 1.0'))}</p></html>"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    recover_interrupted_runs()
    print("Orthopedics DeepSeek agent: http://127.0.0.1:8765")
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
