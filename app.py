from __future__ import annotations

import csv
import configparser
import io
import json
import os
import re
import socket
import subprocess
import threading
import webbrowser
from pathlib import Path
from typing import Any

import requests
from flask import Flask, Response, redirect, render_template, request, session, url_for

from scraper import (
    DEFAULT_MAX_ROWS,
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    FieldRule,
    ScrapeConfig,
    crawl,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "change-me")
EXPORT_CACHE_KEY = "last_preview_result"
PREVIEW_DISPLAY_KEY = "preview_result_for_display"
TOAST_KEY = "ui_toast"
CONFIG_FILE_PATH = Path("saved_config.ini")
LEGACY_CONFIG_FILE_PATH = Path("saved_config.txt")

ALLOWED_EXTRACTS = {"text", "attr", "html"}


def blank_rules() -> list[dict[str, Any]]:
    return [
        {
            "name": "标题",
            "xpath": "",
            "extract": "text",
            "attribute": "",
            "include_in_result": True,
            "is_link_follow": False,
            "regex_enabled": False,
            "regex_pattern": "",
            "children": [],
        },
        {
            "name": "链接",
            "xpath": "",
            "extract": "attr",
            "attribute": "href",
            "include_in_result": True,
            "is_link_follow": False,
            "regex_enabled": False,
            "regex_pattern": "",
            "children": [],
        },
        {
            "name": "摘要",
            "xpath": "",
            "extract": "text",
            "attribute": "",
            "include_in_result": True,
            "is_link_follow": False,
            "regex_enabled": False,
            "regex_pattern": "",
            "children": [],
        },
    ]


def default_form_state() -> dict[str, Any]:
    return {
        "target_url": "",
        "row_xpath": "",
        "timeout": DEFAULT_TIMEOUT,
        "max_rows": DEFAULT_MAX_ROWS,
        "user_agent": DEFAULT_USER_AGENT,
        "rules": blank_rules(),
    }


def load_form_state_from_file() -> dict[str, Any]:
    if not CONFIG_FILE_PATH.exists() and LEGACY_CONFIG_FILE_PATH.exists():
        try:
            payload = json.loads(LEGACY_CONFIG_FILE_PATH.read_text(encoding="utf-8"))
        except Exception:
            payload = None
        if isinstance(payload, dict):
            state = default_form_state()
            state["target_url"] = str(payload.get("target_url", "")).strip()
            state["row_xpath"] = str(payload.get("row_xpath", "")).strip()
            state["timeout"] = payload.get("timeout", DEFAULT_TIMEOUT)
            state["max_rows"] = payload.get("max_rows", DEFAULT_MAX_ROWS)
            state["user_agent"] = str(payload.get("user_agent", DEFAULT_USER_AGENT)).strip() or DEFAULT_USER_AGENT
            rules = payload.get("rules")
            if isinstance(rules, list) and rules:
                state["rules"] = [_normalize_rule_dict(item) for item in rules]
            save_form_state_to_file(state)
            return state

    if not CONFIG_FILE_PATH.exists():
        return default_form_state()

    try:
        parser = configparser.ConfigParser()
        parser.read(CONFIG_FILE_PATH, encoding="utf-8")
    except Exception:
        return default_form_state()

    state = default_form_state()
    if parser.has_section("form"):
        state["target_url"] = parser.get("form", "target_url", fallback="").strip()
        state["row_xpath"] = parser.get("form", "row_xpath", fallback="").strip()
        state["timeout"] = parser.get("form", "timeout", fallback=str(DEFAULT_TIMEOUT)).strip() or DEFAULT_TIMEOUT
        state["max_rows"] = parser.get("form", "max_rows", fallback=str(DEFAULT_MAX_ROWS)).strip() or DEFAULT_MAX_ROWS
        state["user_agent"] = parser.get("form", "user_agent", fallback=DEFAULT_USER_AGENT).strip() or DEFAULT_USER_AGENT
        raw_rules = parser.get("form", "rules_json", fallback="").strip()
        if raw_rules:
            try:
                rules = json.loads(raw_rules)
            except json.JSONDecodeError:
                rules = []
            if isinstance(rules, list) and rules:
                state["rules"] = [_normalize_rule_dict(item) for item in rules]
    return state


def save_form_state_to_file(state: dict[str, Any]) -> None:
    parser = configparser.ConfigParser()
    parser["form"] = {
        "target_url": str(state.get("target_url", "")),
        "row_xpath": str(state.get("row_xpath", "")),
        "timeout": str(state.get("timeout", DEFAULT_TIMEOUT)),
        "max_rows": str(state.get("max_rows", DEFAULT_MAX_ROWS)),
        "user_agent": str(state.get("user_agent", DEFAULT_USER_AGENT)),
        "rules_json": json.dumps(
            [_normalize_rule_dict(rule) for rule in state.get("rules", [])],
            ensure_ascii=False,
        ),
    }
    with CONFIG_FILE_PATH.open("w", encoding="utf-8") as file:
        parser.write(file)


def parse_positive_int(value: str, label: str, fallback: int) -> int:
    raw = (value or "").strip()
    if not raw:
        return fallback

    try:
        parsed = int(raw)
    except ValueError as exc:
        raise ValueError(f"{label}必须是整数。") from exc

    if parsed <= 0:
        raise ValueError(f"{label}必须大于 0。")
    return parsed


def _normalize_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "off", "no"}
    return bool(value)


def _normalize_rule_dict(rule: Any) -> dict[str, Any]:
    if not isinstance(rule, dict):
        return {
            "name": "",
            "xpath": "",
            "extract": "text",
            "attribute": "",
            "include_in_result": True,
            "is_link_follow": False,
            "regex_enabled": False,
            "regex_pattern": "",
            "children": [],
        }

    children = rule.get("children")
    if not isinstance(children, list):
        children = []

    return {
        "name": str(rule.get("name", "")).strip(),
        "xpath": str(rule.get("xpath", "")).strip(),
        "extract": str(rule.get("extract", "text") or "text").strip() or "text",
        "attribute": str(rule.get("attribute", "")).strip(),
        "include_in_result": _normalize_bool(rule.get("include_in_result"), True),
        "is_link_follow": _normalize_bool(rule.get("is_link_follow"), False),
        "regex_enabled": _normalize_bool(rule.get("regex_enabled"), False),
        "regex_pattern": str(rule.get("regex_pattern", "")).strip(),
        "children": [_normalize_rule_dict(child) for child in children],
    }


def _rule_path_label(path: list[int]) -> str:
    return " > ".join(f"第 {index} 行" for index in path)


def _parse_rule(rule_data: dict[str, Any], path: list[int]) -> FieldRule:
    rule = _normalize_rule_dict(rule_data)
    label = _rule_path_label(path)

    if not rule["name"] or not rule["xpath"]:
        raise ValueError(f"{label} 字段配置不完整，列名和 XPath 都要填写。")
    if rule["extract"] not in ALLOWED_EXTRACTS:
        raise ValueError(f"{label} 的提取方式不支持。")
    if rule["extract"] == "attr" and not rule["attribute"]:
        raise ValueError(f"{label} 选择了属性提取，但没有填写属性名。")

    if rule["regex_enabled"]:
        if not rule["regex_pattern"]:
            raise ValueError(f"{label} regex is enabled but pattern is empty.")
        try:
            compiled = re.compile(rule["regex_pattern"])
        except re.error as exc:
            raise ValueError(f"{label} invalid regex pattern: {exc}") from exc
        if compiled.groups < 1:
            raise ValueError(f"{label} regex must contain at least one capture group.")

    children = [_parse_rule(child, [*path, index]) for index, child in enumerate(rule["children"], start=1)]
    if rule["is_link_follow"] and not children:
        raise ValueError(f"{label} 勾选了跳转链接，必须至少配置一个子字段。")

    return FieldRule(
        name=rule["name"],
        xpath=rule["xpath"],
        extract=rule["extract"],
        attribute=rule["attribute"],
        include_in_result=rule["include_in_result"],
        is_link_follow=rule["is_link_follow"],
        regex_enabled=rule["regex_enabled"],
        regex_pattern=rule["regex_pattern"],
        children=children,
    )


def parse_rules(form) -> list[FieldRule]:
    raw_rules = form.get("rules_json", "").strip()
    if not raw_rules:
        raise ValueError("至少需要配置一个字段。")

    try:
        payload = json.loads(raw_rules)
    except json.JSONDecodeError as exc:
        raise ValueError("字段配置解析失败，请重新编辑后再提交。") from exc

    if not isinstance(payload, list):
        raise ValueError("字段配置格式无效。")

    rules = [_parse_rule(item, [index]) for index, item in enumerate(payload, start=1)]
    if not rules:
        raise ValueError("至少需要配置一个字段。")
    return rules


def form_state_from_request(form) -> dict[str, Any]:
    state = default_form_state()
    state["target_url"] = form.get("target_url", "").strip()
    state["row_xpath"] = form.get("row_xpath", "").strip()
    state["timeout"] = form.get("timeout", str(DEFAULT_TIMEOUT)).strip() or DEFAULT_TIMEOUT
    state["max_rows"] = form.get("max_rows", str(DEFAULT_MAX_ROWS)).strip() or DEFAULT_MAX_ROWS
    state["user_agent"] = form.get("user_agent", DEFAULT_USER_AGENT).strip() or DEFAULT_USER_AGENT

    raw_rules = form.get("rules_json", "").strip()
    if raw_rules:
        try:
            payload = json.loads(raw_rules)
        except json.JSONDecodeError:
            state["rules"] = blank_rules()
        else:
            if isinstance(payload, list) and payload:
                state["rules"] = [_normalize_rule_dict(item) for item in payload]
    return state


def _has_follow_rule(rules: list[FieldRule]) -> bool:
    return any(rule.is_link_follow or _has_follow_rule(rule.children) for rule in rules)


def build_config(form) -> ScrapeConfig:
    target_url = form.get("target_url", "").strip()
    if not target_url:
        raise ValueError("目标网址不能为空。")

    row_xpath = form.get("row_xpath", "").strip()
    rules = parse_rules(form)

    return ScrapeConfig(
        target_url=target_url,
        row_xpath=row_xpath,
        rules=rules,
        user_agent=form.get("user_agent", DEFAULT_USER_AGENT).strip() or DEFAULT_USER_AGENT,
        timeout=parse_positive_int(form.get("timeout", ""), "请求超时", DEFAULT_TIMEOUT),
        max_rows=parse_positive_int(form.get("max_rows", ""), "最大结果行数", DEFAULT_MAX_ROWS),
    )


def export_rows_as_csv(headers: list[str], rows: list[dict[str, str]]) -> Response:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)

    csv_bytes = io.BytesIO(buffer.getvalue().encode("utf-8-sig"))
    return Response(
        csv_bytes.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=scrape_result.csv"},
    )


def cache_preview_result(result) -> None:
    payload = {
        "headers": result.headers,
        "rows": result.rows,
        "final_url": result.final_url,
        "status_code": result.status_code,
        "content_type": result.content_type,
        "elapsed_ms": result.elapsed_ms,
    }
    session[EXPORT_CACHE_KEY] = payload
    session[PREVIEW_DISPLAY_KEY] = payload


def get_cached_preview_result() -> dict[str, Any] | None:
    payload = session.get(EXPORT_CACHE_KEY)
    if not isinstance(payload, dict):
        return None

    headers = payload.get("headers")
    rows = payload.get("rows")
    if not isinstance(headers, list) or not isinstance(rows, list):
        return None
    return payload




def set_toast(message: str, level: str = "info") -> None:
    session[TOAST_KEY] = {"message": message, "level": level}


def open_browser_later(url: str, host: str, port: int, delay: float = 0.5) -> None:
    def _open() -> None:
        for _ in range(60):
            try:
                with socket.create_connection((host, port), timeout=0.5):
                    break
            except OSError:
                threading.Event().wait(0.25)

        threading.Event().wait(delay)

        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return
        except Exception:
            pass

        try:
            subprocess.Popen(["cmd", "/c", "start", "", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except Exception:
            pass

        webbrowser.open(url, new=2)

    threading.Timer(delay, _open).start()


@app.route("/", methods=["GET", "POST"])
def index():
    errors: list[str] = []
    result = session.pop(PREVIEW_DISPLAY_KEY, None)
    toast = session.pop(TOAST_KEY, None)
    form_state = load_form_state_from_file()
    submitted = request.method == "POST"

    if submitted:
        form_state = form_state_from_request(request.form)
        save_form_state_to_file(form_state)
        try:
            if request.form.get("form_action") == "export":
                cached_result = get_cached_preview_result()
                if not cached_result:
                    raise ValueError("请先执行抓取预览，再导出当前结果。")
                return export_rows_as_csv(cached_result["headers"], cached_result["rows"])

            config = build_config(request.form)
            result = crawl(config)
            cache_preview_result(result)
            set_toast(f"Preview completed. {len(result.rows)} rows loaded.", "success")
            return redirect(url_for("index"))
        except ValueError as exc:
            errors.append(str(exc))
        except requests.RequestException as exc:
            errors.append(f"请求失败：{exc}")
        except Exception as exc:
            errors.append(f"处理失败：{exc}")

    return render_template(
        "index.html",
        errors=errors,
        result=result,
        form_state=form_state,
        toast=toast,
        submitted=submitted,
    )


if __name__ == "__main__":
    host = "127.0.0.1"
    port = 5000
    open_browser_later(f"http://{host}:{port}", host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False)
