"""Read server-only settings from the project .env and process environment.

This intentionally supports a small, non-executable KEY=value format. It does
not evaluate shell commands, interpolate variables, or write os.environ.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse


ENV_FILE = Path(__file__).resolve().parent / ".env"
DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"
SETTING_NAMES = frozenset(("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"))
MAX_ENV_BYTES = 64_000


class SettingsError(ValueError):
    """A safe configuration error, without configuration values or raw lines."""


def _line_error(line_number):
    return SettingsError(f"后端 .env 第 {line_number} 行格式无效，请使用单行 KEY=value 配置。")


def _parse_value(raw, line_number):
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] not in ("'", '"'):
        # A hash within an unquoted token remains literal; whitespace + # starts
        # an inline comment, as in DEEPSEEK_MODEL=deepseek-flash # default.
        return re.split(r"\s+#", raw, maxsplit=1)[0].rstrip() if not raw.startswith("#") else ""
    quote = raw[0]
    chars, index = [], 1
    while index < len(raw):
        char = raw[index]
        if char == quote:
            tail = raw[index + 1:].strip()
            if tail and not tail.startswith("#"):
                raise _line_error(line_number)
            return "".join(chars)
        if quote == '"' and char == "\\" and index + 1 < len(raw):
            index += 1
            escaped = raw[index]
            chars.append({"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}.get(escaped, "\\" + escaped))
        else:
            chars.append(char)
        index += 1
    raise _line_error(line_number)


def _read_dotenv(env_file):
    try:
        with Path(env_file).open("rb") as stream:
            data = stream.read(MAX_ENV_BYTES + 1)
        if len(data) > MAX_ENV_BYTES:
            raise SettingsError("后端 .env 文件过大，请仅保留所需的配置项。")
        contents = data.decode("utf-8-sig")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        raise SettingsError("无法读取后端 .env，请检查文件权限并使用 UTF-8 编码。") from None
    values = {}
    for number, line in enumerate(contents.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.fullmatch(r"(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", stripped)
        if not match:
            raise _line_error(number)
        name, raw = match.groups()
        if name in SETTING_NAMES:
            values[name] = _parse_value(raw, number)
    return values


def load_settings(env_file=ENV_FILE, environ=None):
    """Return a private, validated snapshot; process values override .env.

    An explicitly empty environment variable also wins over the file. Pass a
    temporary env_file and environ={} for isolated tests; no OS state is changed.
    """
    process = os.environ if environ is None else environ
    file_values = _read_dotenv(env_file)

    def value(name, default=""):
        selected = process[name] if name in process else file_values.get(name, default)
        if not isinstance(selected, str):
            raise SettingsError("后端配置值必须是文本，请检查 .env 或环境变量。")
        return selected

    key = value("DEEPSEEK_API_KEY")
    if len(key) > 512 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in key):
        raise SettingsError("后端 DEEPSEEK_API_KEY 格式无效，不得包含空白、控制字符或超过 512 个字符。")
    model = value("DEEPSEEK_MODEL", DEFAULT_MODEL).strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", model):
        raise SettingsError("后端 DEEPSEEK_MODEL 格式无效，请检查 .env 或环境变量。")
    base = value("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/")
    try:
        parsed = urlparse(base)
        parsed.port  # Validate invalid or out-of-range port values as well.
        loopback = parsed.hostname in ("127.0.0.1", "localhost", "::1")
        invalid = (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
                   or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in base)
                   or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback)))
    except ValueError:
        invalid = True
    if invalid:
        raise SettingsError("后端 DEEPSEEK_BASE_URL 必须是 HTTPS 地址，或用于本地测试的回环 HTTP 地址。")
    return {"provider": "deepseek", "api_key": key, "model": model, "base_url": base,
            "key_source": ("environment" if "DEEPSEEK_API_KEY" in process else "dotenv") if key else "none"}
