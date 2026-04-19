from __future__ import annotations

import os
import re
import subprocess
import warnings
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from urllib.parse import urljoin

import requests
from lxml import etree, html
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from urllib3.exceptions import InsecureRequestWarning

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 15
DEFAULT_MAX_ROWS = 20
JOIN_SEPARATOR = " | "
LINK_ATTRIBUTES = {"href", "src", "data-src"}
EDGE_BINARY_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]
DRIVER_VERSION_URL_TEMPLATE = "https://msedgedriver.microsoft.com/LATEST_RELEASE_{major}_WINDOWS"
DRIVER_ZIP_URL_TEMPLATE = "https://msedgedriver.microsoft.com/{version}/edgedriver_win64.zip"


@dataclass(slots=True)
class FieldRule:
    name: str
    xpath: str
    extract: str = "text"
    attribute: str = ""
    include_in_result: bool = True
    is_link_follow: bool = False
    regex_enabled: bool = False
    regex_pattern: str = ""
    children: list["FieldRule"] = field(default_factory=list)


@dataclass(slots=True)
class ScrapeConfig:
    target_url: str
    row_xpath: str
    rules: list[FieldRule]
    user_agent: str = DEFAULT_USER_AGENT
    timeout: int = DEFAULT_TIMEOUT
    max_rows: int = DEFAULT_MAX_ROWS


@dataclass(slots=True)
class ScrapeResult:
    headers: list[str]
    rows: list[dict[str, str]]
    final_url: str
    status_code: int
    content_type: str
    elapsed_ms: int


@dataclass(slots=True)
class BrowserDocument:
    document: Any
    url: str
    content_type: str = "text/html; charset=utf-8"


def normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


def _evaluate_xpath(node: Any, xpath_expr: str) -> list[Any]:
    try:
        matches = node.xpath(xpath_expr)
    except etree.XPathError as exc:
        raise ValueError(f"XPath 表达式无效：{xpath_expr}\n{exc}") from exc

    if isinstance(matches, list):
        return matches
    return [matches]


def _render_match(match: Any, rule: FieldRule, base_url: str) -> str:
    if rule.extract == "html":
        if hasattr(match, "tag"):
            return html.tostring(match, encoding="unicode", with_tail=False).strip()
        return str(match).strip()

    if rule.extract == "attr":
        if hasattr(match, "get"):
            value = str(match.get(rule.attribute, "")).strip()
        else:
            value = str(match).strip()

        if value and rule.attribute.lower() in LINK_ATTRIBUTES:
            value = urljoin(base_url, value)
        return value

    if hasattr(match, "text_content"):
        return normalize_whitespace(match.text_content())

    return normalize_whitespace(str(match))


def _apply_regex(value: str, rule: FieldRule) -> str:
    if not value or not rule.regex_enabled:
        return value

    match = re.search(rule.regex_pattern, value)
    if not match or match.lastindex is None or match.lastindex < 1:
        return ""
    return match.group(1).strip()


def _extract_joined(node: Any, rule: FieldRule, base_url: str) -> str:
    matches = _evaluate_xpath(node, rule.xpath)
    values = [_render_match(match, rule, base_url) for match in matches]
    values = [_apply_regex(value, rule) for value in values]
    values = [value for value in values if value]
    return JOIN_SEPARATOR.join(values)


def _extract_column(root: Any, rule: FieldRule, base_url: str) -> list[str]:
    matches = _evaluate_xpath(root, rule.xpath)
    values = [_render_match(match, rule, base_url) for match in matches]
    values = [_apply_regex(value, rule) for value in values]
    return [value for value in values if value]


def _has_follow_rule(rules: list[FieldRule]) -> bool:
    return any(rule.is_link_follow or _has_follow_rule(rule.children) for rule in rules)


def _saved_headers(rules: list[FieldRule]) -> list[str]:
    headers: list[str] = []
    for rule in rules:
        if rule.include_in_result:
            headers.append(rule.name)
        headers.extend(_saved_headers(rule.children))
    return headers


def _first_wait_xpath(rules: list[FieldRule]) -> str | None:
    for rule in rules:
        if rule.xpath:
            return rule.xpath
        child_xpath = _first_wait_xpath(rule.children)
        if child_xpath:
            return child_xpath
    return None


def _edge_binary_path() -> str:
    for candidate in EDGE_BINARY_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    raise RuntimeError("未找到 Microsoft Edge 浏览器，请先安装 Edge。")


def _edge_version(binary_path: str) -> str:
    try:
        completed = subprocess.run(
            [binary_path, "--version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
    except Exception:
        completed = None

    stdout = completed.stdout if completed else ""
    match = re.search(r"(\d+\.\d+\.\d+\.\d+)", stdout)
    if match:
        return match.group(1)

    file_version = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-Item '{binary_path}').VersionInfo.ProductVersion",
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    ).stdout.strip()
    match = re.search(r"(\d+\.\d+\.\d+\.\d+)", file_version)
    if not match:
        raise RuntimeError("无法识别当前 Edge 浏览器版本。")
    return match.group(1)


def _download_edge_driver(version: str) -> str:
    cache_dir = Path(".browser-cache") / "msedgedriver" / version
    driver_path = cache_dir / "msedgedriver.exe"
    if driver_path.exists():
        return str(driver_path.resolve())

    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / "edgedriver_win64.zip"

    warnings.simplefilter("ignore", InsecureRequestWarning)
    zip_url = DRIVER_ZIP_URL_TEMPLATE.format(version=version)
    response = requests.get(zip_url, timeout=60, verify=False)
    response.raise_for_status()
    zip_path.write_bytes(response.content)

    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(cache_dir)

    if not driver_path.exists():
        raise RuntimeError(f"EdgeDriver 下载完成，但未找到驱动文件：{driver_path}")
    return str(driver_path.resolve())


class BrowserFetcher:
    def __init__(self, user_agent: str, timeout: int):
        binary_path = _edge_binary_path()
        browser_version = _edge_version(binary_path)
        driver_path = _download_edge_driver(browser_version)

        options = Options()
        options.binary_location = binary_path
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1600,2400")
        options.add_argument(f"--user-agent={user_agent or DEFAULT_USER_AGENT}")

        service = Service(driver_path)
        self.driver = webdriver.Edge(service=service, options=options)
        self.driver.set_page_load_timeout(max(timeout, 30))
        self.timeout = timeout

    def close(self) -> None:
        self.driver.quit()

    def fetch_document(self, url: str, wait_xpath: str | None = None) -> BrowserDocument:
        self.driver.get(url)
        WebDriverWait(self.driver, self.timeout).until(
            lambda driver: driver.execute_script("return document.readyState") == "complete"
        )

        if wait_xpath:
            try:
                WebDriverWait(self.driver, self.timeout).until(
                    EC.presence_of_element_located((By.XPATH, wait_xpath))
                )
            except TimeoutException:
                # 动态页面不一定总能在指定时间内出现目标节点，保留当前页面源码继续解析。
                pass

        sleep(1.5)
        document = html.fromstring(self.driver.page_source, base_url=self.driver.current_url)
        return BrowserDocument(document=document, url=self.driver.current_url)


def _extract_follow_link(node: Any, rule: FieldRule, base_url: str) -> str:
    link_rule = FieldRule(
        name=rule.name,
        xpath=rule.xpath,
        extract="attr",
        attribute=rule.attribute or "href",
    )
    matches = _evaluate_xpath(node, link_rule.xpath)
    values = [_render_match(match, link_rule, base_url) for match in matches]
    for value in values:
        if value:
            return value
    return ""


def _extract_rule_data(
    node: Any,
    rule: FieldRule,
    base_url: str,
    fetcher: BrowserFetcher,
) -> dict[str, str]:
    data: dict[str, str] = {}

    if rule.is_link_follow:
        detail_url = _extract_follow_link(node, rule, base_url)
        if rule.include_in_result:
            data[rule.name] = _apply_regex(detail_url, rule)

        if not detail_url:
            return data

        wait_xpath = _first_wait_xpath(rule.children)
        detail_page = fetcher.fetch_document(detail_url, wait_xpath=wait_xpath)
        for child_rule in rule.children:
            child_data = _extract_rule_data(detail_page.document, child_rule, detail_page.url, fetcher)
            data.update(child_data)
        return data

    if rule.include_in_result:
        data[rule.name] = _extract_joined(node, rule, base_url)
    return data


def _extract_row_from_node(
    node: Any,
    rules: list[FieldRule],
    base_url: str,
    fetcher: BrowserFetcher,
) -> dict[str, str]:
    row: dict[str, str] = {}
    for rule in rules:
        row.update(_extract_rule_data(node, rule, base_url, fetcher))
    return row


def extract_rows(
    browser_document: BrowserDocument,
    config: ScrapeConfig,
    fetcher: BrowserFetcher,
) -> list[dict[str, str]]:
    document = browser_document.document
    base_url = browser_document.url

    if config.row_xpath:
        row_nodes = _evaluate_xpath(document, config.row_xpath)
        if row_nodes and not hasattr(row_nodes[0], "xpath"):
            raise ValueError("行 XPath 必须返回节点集合，不能返回字符串或数字。")

        rows: list[dict[str, str]] = []
        for row_node in row_nodes[: config.max_rows]:
            rows.append(_extract_row_from_node(row_node, config.rules, base_url, fetcher))
        return rows

    if _has_follow_rule(config.rules):
        return [_extract_row_from_node(document, config.rules, base_url, fetcher)]

    output_rules = [rule for rule in config.rules if rule.include_in_result]
    columns = [_extract_column(document, rule, base_url) for rule in output_rules]
    row_count = max((len(column) for column in columns), default=0)
    row_count = min(row_count, config.max_rows)

    rows: list[dict[str, str]] = []
    headers_list = [rule.name for rule in output_rules]
    for index in range(row_count):
        row: dict[str, str] = {}
        for header, column in zip(headers_list, columns, strict=True):
            row[header] = column[index] if index < len(column) else ""
        rows.append(row)
    return rows


def crawl(config: ScrapeConfig) -> ScrapeResult:
    started_at = perf_counter()
    fetcher = BrowserFetcher(config.user_agent or DEFAULT_USER_AGENT, config.timeout)
    try:
        wait_xpath = config.row_xpath or _first_wait_xpath(config.rules)
        browser_document = fetcher.fetch_document(config.target_url, wait_xpath=wait_xpath)
        rows = extract_rows(browser_document, config, fetcher)
    finally:
        fetcher.close()

    elapsed_ms = int((perf_counter() - started_at) * 1000)
    return ScrapeResult(
        headers=_saved_headers(config.rules),
        rows=rows,
        final_url=browser_document.url,
        status_code=200,
        content_type=browser_document.content_type,
        elapsed_ms=elapsed_ms,
    )
