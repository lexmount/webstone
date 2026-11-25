"""Create an isomorphic English view of the generated Chinese report.

Measured material is intentionally left in its source language.  Only report chrome,
editorial copy, labels, and judgement vocabulary are localized.  Both editions are
therefore generated from the same rows rather than maintained as separate reports.
"""
from __future__ import annotations

import html
import json
import posixpath
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import cache, partial
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment

from harness.reporting.locales import catalog_text, translate
from harness.web_fetch.report_reading import archived_translations, render_archived_reading

NAV = [
    ("datasets/index.html", "Dataset"),
    ("methodology/index.html", "Methodology"),
    ("analysis/index.html", "Analysis"),
    ("review/index.html", "Review"),
    ("guide/index.html", "Interpretation"),
    ("adoption/index.html", "Methods"),
    ("reproduce/index.html", "Reproduction"),
    ("changes/index.html", "Changelog"),
    ("accounts/index.html", "Access & setup"),
]


# Exact editorial translations take priority.  Dynamic evidence (task names,
# references and provider output) is deliberately absent from this catalog.
_TRANSLATIONS = json.loads(Path(__file__).with_name("report-translations.json").read_text(encoding="utf-8"))

TEXT = dict(_TRANSLATIONS['TEXT'])
MESSAGES = Path(__file__).with_name("messages.json")


def report_text(language: str, *, legacy: str | None = None):
    """Return `t(id, english, **values)` for a renderer that writes one edition directly.

    A page set that renders one already-published report names it as ``legacy``;
    `t(id, **values)` then reads that report's frozen messages in both languages.
    """
    shared = catalog_text(MESSAGES, language)
    if legacy is None:
        return shared
    frozen = legacy_report_text(legacy)['messages']

    def text(key: str, source: str | None = None, /, **values) -> str:
        if source is None:
            return frozen[key][language].format_map(values)
        return shared(key, source, **values)
    return text


def t(key: str, source: str, /, **values) -> str:
    """Render one Chinese text node and declare its English for the post-render pass."""
    chinese = translate(MESSAGES, key, "zh", **values)
    TEXT.setdefault(chinese.strip(), source.format_map(values))
    return chinese


def legacy_t(report: str, key: str, /, **values) -> str:
    """The Chinese edition of one already-published report's frozen message.

    Its English comes only from that report's catalog in the English pass; the
    shared catalog never learns it.
    """
    return legacy_report_text(report)['messages'][key]['zh'].format_map(values)


def pair(key: str, source: str, /, **values) -> tuple[str, str]:
    """Return both editions of a text the page itself carries, as `model_text` does."""
    return translate(MESSAGES, key, "zh", **values), source.format_map(values)


# These strings are presentation copy shared by the overview, methodology,
# provider, review, release-note, and reproduction pages.  Keeping them here
# (rather than maintaining a second set of report builders) is intentional:
# both languages still render the same measured rows and evidence objects.


# Remaining interface copy is report prose rather than measured
# provider output, so the English top-level pages localize it.

# Reader-facing task-family copy from report_components.py. These strings are
# rendered on the methodology page and therefore need full English editions;
# measured task names and prompts remain unchanged.


# Some generated blocks concatenate several fixed sentences into one text node,
# or split a sentence around an inline link/code element.  These replacements
# are deliberately narrow report phrases; they never include task names,
# reference answers, or provider output.
PHRASES = dict(_TRANSLATIONS['PHRASES'])
PHRASES.update({
    '旧组遵守冻结的失败重试规则：是。': 'The baseline follows its frozen failure-retry policy: yes.',
    '旧组遵守冻结的失败重试规则：否（不参与择优）。': 'The baseline follows its frozen failure-retry policy: no (ineligible).',
    '旧组遵守冻结的失败重试规则：未核验。': 'The baseline follows its frozen failure-retry policy: unverified.',
    '两组还须遵守各自冻结的失败重试规则。': 'Both groups must also follow their frozen failure-retry policies.',
})


# The adoption page carries a large, evidence-bound capability and pricing
# inventory. Keep its exact-string translations in small data-only modules so
# the shared localization engine remains reviewable.


def _body_region_summary(match, catalog):
    regions = match[1]
    for source, target in (("开头", "Opening"), ("前部", "Early section"),
                           ("中部", "Middle"), ("后部", "Late section"),
                           ("结尾", "Ending"), ("位置", "Region")):
        regions = regions.replace(source + " ", target + " ")
    return ("Body coverage is incomplete. Regions below target: "
            + regions.replace("、", ", ")
            + ". The complete reference and actual output are shown side by side below.")


REGEX = [
    (re.compile(r"^新批次启用自身 DEBUG 并捕获网络库日志；旧 (\d+) 条不计入本报告。$"), r"The new batch enabled native DEBUG output and captured network-library logs; \1 earlier records are excluded from this report."),
    (re.compile(r"^(\$[\d,.]+)/月$"), r"\1/month"),
    (re.compile(r"^(\$[\d,.]+)/年；月均 (\$[\d,.]+)（较月付省 ([\d.]+%)）$"), r"\1/year; monthly average \2 (\3 less than monthly billing)"),
    (re.compile(r"^([\d,]+) 页/月总支出$"), r"Total monthly spend at \1 pages"),
    (re.compile(r"^官方 (.+) 文档 ↗$"), r"Official \1 documentation ↗"),
    (re.compile(r"^参数重测 (\d+) 项，已整组采用 (\d+) 项新结果。先检查整类配置是否一致，再比较符合条件的完整组通过数；不逐题拼接。两组还须遵守各自冻结的失败重试规则。配置不一致的组不能因通过数更高而入选。本轮不能证明参数导致速度提升。$"),
     r"Parameter reruns evaluated \1 cases; \2 new results were selected as complete groups. Check configuration uniformity across each family before comparing eligible complete groups by pass count; do not combine individual cases. Both groups must also follow their frozen failure-retry policies. A group with inconsistent settings cannot qualify through a higher pass count. These runs do not establish that parameter changes improved speed."),
    (re.compile(r"^每次物理调用预算为 ([\d.]+) 秒，重试后的累计耗时可能超过该预算。$"), r"Each physical call has a \1-second budget; accumulated latency after retries can exceed it."),
    (re.compile(r"^本报告包含 (\d+) 个任务、(\d+) 个逻辑结果，任务通过 (\d+)/(\d+)。结论仅适用于已登记的任务和规则。$"), r"This report contains \1 tasks and \2 logical results, with \3/\4 task passes. Conclusions apply only to the registered tasks and rules."),
    (re.compile(r"^目标资源已命中，但答案字段还返回了非目标资源：(.+)$"),
     r"The target resource was found, but answer-bearing fields also returned unexpected resources: \1"),
    (re.compile(r"^未命中资源：(.+)；同时返回非目标资源：(.+)$"),
     r"Missing resources: \1; also returned unexpected resources: \2"),
    (re.compile(r"^已找到目标资源，但答案字段还返回了非目标资源：(.+)$"),
     r"The target resource was found, but answer-bearing fields also returned unexpected resources: \1"),
    (re.compile(r"^最终报告纳入 ([\d,]+) 次调用。任务与参考冻结后执行；每个方法内部串行，不复用个人登录状态。$"), r"The final report includes \1 calls. Execution began after tasks and references were frozen; calls were sequential within each method and did not reuse personal login state."),
    (re.compile(r"^获取原图：(.+)$"), lambda match, catalog: "Download original: " + translate_text(match.group(1), catalog)),
    (re.compile(r"^图 (\d+) · (.+?)。(.+) 点击图像获取原图。$"),
     lambda match, catalog: f"Figure {match.group(1)} · {translate_text(match.group(2), catalog)}. "
                   f"{translate_text(match.group(3), catalog)} Select the image to download the original."),
    (re.compile(r"^同窗速度子集：预先声明 (\d+) 题，(\d+) 种方法使用同一完整任务样本。全调用耗时包含成功、失败和截止超时；通过调用耗时按各方法自己的成功子集计算，分母必须同时阅读。每种方法最多 (\d+) 个单次观察，P95 只作本次样本诊断。$"), r"Same-window latency cohort: \1 tasks were declared in advance and all \2 methods use the same complete task sample. All-call latency includes successes, failures, and deadline timeouts; successful-call latency uses each method's own successful subset, so its denominator must be read alongside it. Each method has at most \3 single observations; P95 is only a diagnostic for this sample."),
    (re.compile(r"^同窗速度子集：预先声明 (\d+) 题，(\d+) 种方法共同通过 (\d+) 题；所有方法使用完全相同的成功样本。P95 只作本次样本诊断。$"), r"Same-window latency cohort: \1 tasks were declared in advance and all \2 methods jointly passed \3; every method uses exactly the same successful sample. P95 is only a diagnostic for this sample."),
    (re.compile(r"^全部 (\d+) 种方法共同通过 (\d+)/(\d+) 题；共同通过数只作诊断，不用于筛选测速样本。报告保留预声明的全部 ([\d,]+) 次调用，不按结果重新选题；全调用耗时可在相同分母下阅读，通过调用耗时只描述各方法的条件分布，不能解释为同题速度排名。$"), r"All \1 methods jointly pass \2/\3 tasks; the shared-pass count is diagnostic only and is not used to select the latency sample. The report retains all \4 predeclared calls without reselecting tasks after observing outcomes. All-call latency uses a common denominator; successful-call latency only describes each method's conditional distribution and is not a matched-task speed ranking."),
    (re.compile(r"^全部 (\d+) 种受测配置共同通过$"), r"All \1 tested configurations pass"),
    (re.compile(r"^静态网站正文调用的互斥状态分解；(.+)。正常返回不等于源站 HTTP 200。$"), lambda match, catalog: f"Mutually exclusive outcomes for general-web calls; {translate_text(match.group(1), catalog)}. A normal provider response does not imply origin HTTP 200."),
    (re.compile(r"^有 ([\d,]+) 字符。$"), r"contains \1 characters."),
    (re.compile(r"^([\d]{4}-[\d]{2}-[\d]{2} [\d:]+) 至 ([\d]{4}-[\d]{2}-[\d]{2} [\d:]+) UTC$"), r"\1 to \2 UTC"),
    (re.compile(r"^评测集没有覆盖计划；存在 ([\d,]+) 个计划外调用(?:；存在 ([\d,]+) 个无法判定通过或失败的调用)?。本页排名不得用于结论或发布。$"),
     lambda match, catalog: f"The evaluation does not cover its plan; {match.group(1)} unplanned calls are present"
                   + (f"; {match.group(2)} calls have no pass/fail judgement" if match.group(2) else "")
                   + ". Rankings on this page must not be used for conclusions or publication."),
    (re.compile(r"^(\d+) 个案例 · (\d+) 种方法 · 本地未认证诊断快照。$"), r"\1 tasks · \2 methods · local uncertified diagnostic snapshot."),
    (re.compile(r"^(\d+) 个案例 · (\d+) 种方法 · (\d+)\s*类分榜与一个综合通过率总榜。$"), r"\1 tasks · \2 methods · \3 family rankings and one overall coverage ranking."),
    (re.compile(r"^(\d+)\s*类任务概览$"), r"\1 task families"),
    (re.compile(r"^(\d+)\s*类分榜$"), r"Rankings for \1 task families"),
    (re.compile(r"^(\d+)\s*类通过数$"), r"Passes across \1 task families"),
    (re.compile(r"^每种方法都执行当前评测集计划的 (\d+) 个任务，每题计一次通过或失败，直接相加；耗时单列展示，不参与通过率排序。总榜适合看整体交付覆盖，具体能力仍应回到 (\d+) 个分榜。$"), r"Every method runs the current dataset's \1 planned tasks. Each task contributes one pass or fail and the results are summed directly; latency is shown separately and does not affect pass-rate ranking. The overall ranking shows delivery coverage, while specific capabilities belong in the \2 family rankings."),
    (re.compile(r"^每种方法在同一组 (\d+) 个计划任务上的端到端耗时，包含通过与失败；三项不参与通过率排名。主矩阵调用跨日期、跨批次，不等同于同窗受控速度比较。$"), r"End-to-end latency for each method across the same \1 planned tasks, including both passes and failures. These metrics do not affect pass-rate ranking. Primary-matrix calls span dates and batches and are not a same-window controlled speed comparison."),
    (re.compile(r"^供应商明确声明返回内容已截断：(.*)$"), r"The provider explicitly declares that the returned body is truncated: \1"),
    (re.compile(r"^当前报告共 (\d+) 个案例、(\d+) 种方法和 ([\d,]+) 条权威逻辑结果；其中其他 ([\d,]+) 条未被本次文件补测改写。$"), r"The current report contains \1 tasks, \2 methods, and \3 authoritative logical results; the other \4 results were not changed by this file-task rerun."),
    (re.compile(r"^至少 (\d+) 条不同记录$"), r"At least \1 distinct records"),
    (re.compile(r"^排除：(.+)$"), r"Exclude: \1"),
    (re.compile(r"^至少 ([\d,]+) 字符$"), r"At least \1 characters"),
    (re.compile(r"^([\d,]+) 字符$"), r"\1 characters"),
    (re.compile(r"^至少 (\d+)/(\d+) 区达到 ([\d.]+%)；每区不低于 ([\d.]+%)$"), r"At least \1/\2 regions reach \3; every region is at least \4"),
    (re.compile(r"^(\d+) 个替换字符$"), r"\1 replacement characters"),
    (re.compile(r"^(\d+)/(\d+) 组命中$"), r"\1/\2 groups matched"),
    (re.compile(r"^至少 (\d+) 条，字段与链接规则合格$"), r"At least \1 records with valid fields and links"),
    (re.compile(r"^去重后至少 (\d+) 条$"), r"At least \1 after deduplication"),
    (re.compile(r"^至少 ([\d,]+) 个字符$"), r"At least \1 characters"),
    (re.compile(r"^([\d,]+)，达标$"), r"\1, meets requirement"),
    (re.compile(r"^区域 (\d+)$"), r"Region \1"),
    (re.compile(r"^文件获取 ([\d.]+) 秒；解析 ([\d.]+) 秒；端到端 ([\d.]+) 秒$"), r"File retrieval: \1 seconds; parsing: \2 seconds; end to end: \3 seconds"),
    (re.compile(r"^([\d,]+) / ([\d,]+) 个任务$"), r"\1 / \2 tasks"),
    (re.compile(r"^端到端 ([\d.]+) 秒$"), r"End to end: \1 seconds"),
    (re.compile(r"^代表案例：(.*)$"), r"Representative cases: \1"),
    (re.compile(r"^([\d,]+) 个字符$"), r"\1 characters"),
    (re.compile(r"^([\d,]+) 字节$"), r"\1 bytes"),
    (re.compile(r"^([\d.]+) 秒$"), r"\1 seconds"),
    (re.compile(r"^([\d,]+) 条$"), r"\1 records"),
    (re.compile(r"^原始字节 · ([\d.]+) 秒$"), r"Raw bytes · \1 seconds"),
    (re.compile(r"^提取文字 · ([\d.]+) 秒$"), r"Text extraction · \1 seconds"),
    (re.compile(r"^整条路线 ([\d.]+) 秒$"), r"Complete path: \1 seconds"),
    (re.compile(r"^([\d,]+) 个字符 · ([\d.]+) 秒$"), r"\1 characters · \2 seconds"),
    (re.compile(r"^(\d+) 个任务$"), r"\1 tasks"),
    (re.compile(r"^(\d+) 个案例 · (\d+) 种已测方法$"), r"\1 tasks · \2 measured methods"),
    (re.compile(r"^方法：(.*)$"), r"Method: \1"),
    (re.compile(r"^任务 ID：(.*)$"), r"Task ID: \1"),
    (re.compile(r"^运行 ID：(.*)$"), r"Run ID: \1"),
    (re.compile(r"^第 (\d+) 轮$"), r"Round \1"),
    (re.compile(r"^显示 (\d+) 条$"), r"Showing \1"),
    (re.compile(r"^找到 (\d+) 个任务$"), r"\1 tasks found"),
    (re.compile(r"^(\d+) 字符$"), r"\1 characters"),
    (re.compile(r"^(\d+) 字符 ·$"), r"\1 characters ·"),
    (re.compile(r"^(\d+) 条执行记录$"), r"\1 execution records"),
    (re.compile(r"^(\d+) 个案例，按任务类型查看。$"), r"\1 tasks, grouped by task family."),
    (re.compile(r"^(\d+) 个案例$"), r"\1 tasks"),
    (re.compile(r"^(\d+)/(\d+) 次均有结果。$"), r"\1/\2 calls have results."),
    (re.compile(r"^独立核验：主结果 ([\d,]+)/([\d,]+)；同窗测速 ([\d,]+)/([\d,]+)；未决 (\d+)$"), r"Independent review: primary results \1/\2; same-window latency \3/\4; \5 unresolved"),
    (re.compile(r"^独立核验：全量 ([\d,]+)/([\d,]+)；未决 (\d+)$"), r"Independent review: full matrix \1/\2; \3 unresolved"),
    (re.compile(r"^独立核验：机器失败 ([\d,]+)/([\d,]+)；未决 (\d+)$"), r"Independent review: machine failures \1/\2; \3 unresolved"),
    (re.compile(r"^主结果机器失败 ([\d,]+)/([\d,]+)；机器通过未逐条审查 ([\d,]+)；代理分歧 ([\d,]+)；已裁定 ([\d,]+)；未决 ([\d,]+)。$"), r"Primary machine failures reviewed: \1/\2; machine passes not reviewed individually: \3; reviewer disagreements: \4; adjudicated: \5; unresolved: \6."),
    (re.compile(r"^主结果全量 ([\d,]+)/([\d,]+)；代理分歧 ([\d,]+)；已裁定 ([\d,]+)；未决 ([\d,]+)。$"), r"Full primary-matrix review: \1/\2; reviewer disagreements: \3; adjudicated: \4; unresolved: \5."),
    (re.compile(r"^从结构化结果中读取标题与详情链接，共 ([\d,]+) 个标题。$"), r"Read titles and detail URLs from structured results: \1 returned titles."),
    (re.compile(r"^([\d,]+)/([\d,]+) 个标题找到独立名称证据；没有对应证据的不评分。$"), r"\1/\2 titles join to independent name evidence; titles without matching evidence are not scored."),
    (re.compile(r"^覆盖 (\d+)/(\d+) 个案例；这是标题诊断，不参与任务通过判定。$"), r"Coverage: \1/\2 tasks. This is a title diagnostic and does not determine task acceptance."),
    (re.compile(r"^(\d+)/(\d+) · (\d+)/(\d+) 例（无可评分标题）$"), r"\1/\2 · \3/\4 tasks (no scorable titles)"),
    (re.compile(r"^(\d+) 个任务中满足各自验收规则的数量；每题等权。$"), r"Number of the \1 tasks satisfying their own acceptance contracts; every task has equal weight."),
    (re.compile(r"^当前参评方法共同通过 (\d+) 个案例；仅在这个相同子集内比较耗时，不代表失败任务的成本或生产 SLA。$"), r"The evaluated methods jointly pass \1 tasks. Latency is compared only on this identical subset; it does not represent the cost of failed tasks or a production SLA."),
    (re.compile(r"^完整参考覆盖 (\d+)/(\d+)；缺少参考只影响全文指标，不影响任务结果。$"), r"Complete-reference coverage: \1/\2. A missing reference affects only full-text metrics, not the task verdict."),
    (re.compile(r"^文字核对范围：各方法均为 (\d+)/(\d+) 例。$"), r"Required-text coverage: \1/\2 tasks for every method."),
    (re.compile(r"^(\d+) 例完整参考$"), r"\1 tasks with complete references"),
    (re.compile(r"^(\d+) 例列表任务$"), r"\1 list tasks"),
    (re.compile(r"^文字 (\d+) 项$"), r"\1 text checks"),
    (re.compile(r"^资源 · (\d+) 项$"), r"\1 required resources"),
    (re.compile(r"^(\d+(?:\.\d+)?%) · (\d+)/(\d+) 组$"), r"\1 · \2/\3 groups"),
    (re.compile(r"^— · 0/0 组$"), "— · 0/0 groups"),
    (re.compile(r"^(\d+)\. 开头（标准答案 0–20%）$"), r"\1. Opening (reference 0–20%)"),
    (re.compile(r"^(\d+)\. 前部（标准答案 20–40%）$"), r"\1. Early section (reference 20–40%)"),
    (re.compile(r"^(\d+)\. 中部（标准答案 40–60%）$"), r"\1. Middle (reference 40–60%)"),
    (re.compile(r"^(\d+)\. 后部（标准答案 60–80%）$"), r"\1. Late section (reference 60–80%)"),
    (re.compile(r"^(\d+)\. 结尾（标准答案 80–100%）$"), r"\1. Ending (reference 80–100%)"),
    (re.compile(r"^开头（标准答案 0–20%）$"), "Opening (reference 0–20%)"),
    (re.compile(r"^前部（标准答案 20–40%）$"), "Early section (reference 20–40%)"),
    (re.compile(r"^中部（标准答案 40–60%）$"), "Middle (reference 40–60%)"),
    (re.compile(r"^后部（标准答案 60–80%）$"), "Late section (reference 60–80%)"),
    (re.compile(r"^结尾（标准答案 80–100%）$"), "Ending (reference 80–100%)"),
    (re.compile(r"^(\d+)\. 位置 (\d+)（标准答案 (\d+)–(\d+)%）$"), r"\1. Region \2 (reference \3–\4%)"),
    (re.compile(r"^位置 (\d+)（标准答案 (\d+)–(\d+)%）$"), r"Region \1 (reference \2–\3%)"),
    (re.compile(r"^必须出现的稳定文字 (\d+)（任一写法即可）$"), r"Required stable text \1 (any listed variant)"),
    (re.compile(r"^用于范围核对的稳定文字 (\d+)（任一写法即可）$"), r"Stable text for scope diagnosis \1 (any listed variant)"),
    (re.compile(r"^稳定文字 (\d+)$"), r"Stable text \1"),
    (re.compile(r"^正文核对点-(\d+)$"), r"Body check \1"),
    (re.compile(r"^至少 (\d+) 条完整且不重复的记录$"), r"At least \1 complete, unique records"),
    (re.compile(r"^实际交付 (\d+) 个文件$"), r"\1 files delivered"),
    (re.compile(r"^实际交付 (\d+) 个文件 ·$"), r"\1 files delivered ·"),
    (re.compile(r"^实际返回 (\d+) 个带详情链接的标题。$"), r"The output contains \1 titles with detail URLs."),
    (re.compile(r"^(\d+)/(\d+) 个标题能按详情链接找到参考名称。$"), r"\1/\2 titles have independent name references joined by detail URL."),
    (re.compile(r"^(\d+)/(\d+) 条；仅衡量已返回且有独立参考的标题$"), r"\1/\2; only returned titles with independent references are measured"),
    (re.compile(r"^未命中资源：(.+)$"), r"Missing resource: \1"),
    (re.compile(r"^展开 (\d+) 个较早版本$"), r"Expand \1 earlier releases"),
    (re.compile(r"^(\d+) 个仓库$"), r"\1 repositories"),
    (re.compile(r"^(\d+)/(\d+) · (\d+)/(\d+) 例$"), r"\1/\2 · \3/\4 tasks"),
    (re.compile(r"^(.+)（未纳入本轮）$"), r"\1 (not included in this benchmark)"),
    (re.compile(r"^(.+) · (\d+) 个仓库$"), r"\1 · \2 repositories"),
    (re.compile(r"^(.+) · 范围与官方依据$"), r"\1 · scope and official basis"),
    (re.compile(r"^(.+) · 官方文档（(\d{4}-\d{2}-\d{2}) 核对） ↗$"), r"\1 · official documentation (checked \2) ↗"),
    (re.compile(r"^(.+ [\d.]+) · 官方发布 ↗$"), r"\1 · official release ↗"),
    (re.compile(r"^取得至少 (\d+) 条完整、不重复的记录，每条包含(.+)。同时核对下方列明的稳定文字与页面要求。变化字段按声明的格式核验，不固定旧值。$"), r"Return at least \1 complete, unique records, each containing\2. Also satisfy the stable-text and page requirements below. Volatile fields are checked by their declared format, not frozen values."),
    (re.compile(r"^取得至少 (\d+) 条完整、不重复的记录，每条包含(.+)。变化字段按声明的格式核验，不固定旧值。$"), r"Return at least \1 complete, unique records, each containing\2. Volatile fields are checked by their declared format, not frozen values."),
    (re.compile(r"^检出 (\d+) 条，字段完整 (\d+) 条，完整且唯一 (\d+) 条，要求至少 (\d+) 条。稳定内容与结构检查通过。$"), r"Detected \1 records; \2 have complete fields and \3 are complete and unique, against a minimum of \4. Stable-content and structure checks passed."),
    (re.compile(r"^检出 (\d+) 条，字段完整 (\d+) 条，完整且唯一 (\d+) 条，要求至少 (\d+) 条。条目结构检查通过；本例未设置稳定文字核对点。$"), r"Detected \1 records; \2 have complete fields and \3 are complete and unique, against a minimum of \4. Record structure passed; this task has no stable-text checks."),
    (re.compile(r"^实际解析出 (\d+) 条完整且不重复的记录，要求至少 (\d+) 条；结构检查通过，稳定文字检查通过。$"), r"Parsed \1 complete, unique records against a minimum of \2; structure and stable-text checks passed."),
    (re.compile(r"^实际解析出 (\d+) 条完整且不重复的记录，要求至少 (\d+) 条；结构检查未通过，稳定文字检查未通过。$"), r"Parsed \1 complete, unique records against a minimum of \2; structure and stable-text checks failed."),
    (re.compile(r"^实际解析出 (\d+) 条完整且不重复的记录，要求至少 (\d+) 条；结构检查(通过|未通过)，稳定文字检查(通过|未通过)。$"), lambda m, catalog: f"Parsed {m.group(1)} complete, unique records against a minimum of {m.group(2)}; structure {'passed' if m.group(3) == '通过' else 'failed'} and stable-text checks {'passed' if m.group(4) == '通过' else 'failed'}."),
    (re.compile(r"^完整度 / 纯净度 / F1 为 ([^。]+)。$"), r"Recall / precision / F1: \1."),
    (re.compile(r"^正文没有抓全；低于目标的位置为：(.+)。下方会把完整标准答案与实际返回并排展示。$"), _body_region_summary),
    (re.compile(r"^这一段标准答案产生 ([\d,]+) 个连续六字符片段；在上方完整实际返回（([\d,]+) 字符）中命中 ([\d,]+) 个，所以覆盖率为 ([\d,]+) ÷ ([\d,]+) = ([\d.]+%)。目标 ([\d.]+%)，硬下限 ([\d.]+%)。$"), r"This reference region produces \1 contiguous six-character shingles. \3 are found in the complete output above (\2 characters), so coverage is \4 ÷ \5 = \6. Target: \7; hard floor: \8."),
    (re.compile(r"^· 采集时间 (.+)$"), r"· captured at \1"),
]


# Current standalone article: authored prose only.


def release_translations(history: dict) -> dict[str, str]:
    """Localize only the structured release records included in this report."""
    translations = {}

    def add(source, target):
        if source in translations and translations[source] != target:
            raise ValueError('Conflicting English release translation for ' + repr(source))
        translations[source] = target

    # Releases of these published comparisons carry no English records to localize.
    untranslated = set(legacy_report_text('web-fetch-published')['release_kinds_without_english'])
    for release in history['releases']:
        if untranslated & set(release.get('comparison_kinds', [])):
            continue
        english = release.get('i18n', {}).get('en', {})
        if (not english.get('title') or not english.get('summary')
                or len(release.get('changes', [])) != len(english.get('changes', []))):
            raise ValueError('Web Fetch release requires complete English text: ' + release['id'])
        for key in ('title', 'summary'):
            add(release[key], english[key])
        for source_row, target_row in zip(release['changes'], english['changes'], strict=True):
            if len(source_row) != 4 or len(target_row) != 4:
                raise ValueError('Web Fetch release change rows must have four columns: ' + release['id'])
            for source, target in zip(source_row, target_row, strict=True):
                add(source, target)

    return translations


REGEX.insert(0, (
    re.compile(r"^(?:.+：)?\d{4}-\d{2}-\d{2}（\d+ 题）(?:、\d{4}-\d{2}-\d{2}（\d+ 题）)*$"),
    lambda m, catalog: m.group().replace("：", ": ").replace("（", " (").replace(" 题）", " tasks)").replace("、", "; "),
))




REGEX.insert(0, (
    re.compile(r"^(\d+) 份真实日志文件已保存并通过 SHA-256 校验。$"),
    r"\1 real log files were retained and verified by SHA-256.",
))
REGEX.insert(0, (
    re.compile(r"^([\d,]+) 字节 · SHA-256：$"),
    r"\1 bytes · SHA-256: ",
))
REGEX.insert(0, (
    re.compile(r"^· ([\d,]+) 字节 · SHA-256：$"),
    r"· \1 bytes · SHA-256: ",
))
REGEX.insert(0, (
    re.compile(r"^(\d+) 份真实日志文件可以读取；其中至少一份没有在运行记录中声明 SHA-256。$"),
    r"\1 real log files are readable; at least one has no SHA-256 declared in the run record.",
))
REGEX.insert(0, (
    re.compile(r"^正文判定：本例分为 (\d+) 段。每段至少达到 ([\d.]+)%，其中至少 (\d+) 段达到 ([\d.]+)%。先看下表；需要核对原文时，再展开下面的左右对照。$"),
    r"Body-text rule: this case has \1 regions. Every region must reach at least \2%, and at least \3 regions must reach \4%. Check the table first, then expand the side-by-side evidence when needed.",
))
REGEX.insert(0, (
    re.compile(r"^本例分为 (\d+) 段。每段至少达到 ([\d.]+)%，其中至少 (\d+) 段达到 ([\d.]+)%。先看下表；需要核对原文时，再展开下面的左右对照。$"),
    r"This case has \1 regions. Every region must reach at least \2%, and at least \3 regions must reach \4%. Check the table first, then expand the side-by-side evidence when needed.",
))
REGEX.insert(0, (
    re.compile(r"^本段覆盖率为 ([\d.]+)%。每段至少达到 ([\d.]+)%；达到 ([\d.]+)% 时算作主要完整段落。$"),
    r"This region has \1% coverage. Every region must reach at least \2%; reaching \3% counts as a substantially complete region.",
))
REGEX.insert(0, (
    re.compile(r"^共 (\d+) 组稳定文字，全部属于标准答案；当前区域显示约 5 组高度，向下滚动查看全部。$"),
    r"All \1 stable-text groups belong to the reference answer. This area shows about 5 groups at a time; scroll to view all.",
))
REGEX.insert(0, (
    re.compile(r"^共 (\d+) 组稳定文字，只用于范围核对，不影响是否通过；当前区域显示约 5 组高度，向下滚动查看全部。$"),
    r"All \1 stable-text groups are scope diagnostics and do not affect acceptance. This area shows about 5 groups at a time; scroll to view all.",
))
REGEX.insert(0, (
    re.compile(r"^共 (\d+) 项稳定文字判定，全部参与评分；当前区域显示约 5 项高度，向下滚动查看全部。$"),
    r"All \1 stable-text checks contribute to scoring. This area shows about 5 at a time; scroll to view all.",
))
REGEX.insert(0, (
    re.compile(r"^共 (\d+) 项稳定文字判定，只用于范围核对，不影响是否通过；当前区域显示约 5 项高度，向下滚动查看全部。$"),
    r"All \1 stable-text checks are scope diagnostics and do not affect acceptance. This area shows about 5 at a time; scroll to view all.",
))

REGEX.insert(0, (
    re.compile(r"^(\d+) 个物理调用日志已保存并通过 SHA-256 校验。$"),
    r"Logs for \1 physical calls were retained and verified by SHA-256.",
))
REGEX.insert(0, (
    re.compile(r"^物理调用 (\d+)$"),
    r"Physical call \1",
))
REGEX.insert(0, (
    re.compile(r"^下载物理调用 (\d+)记录$"),
    r"Download physical call \1 record",
))
REGEX.insert(0, (
    re.compile(r"^本题的最终交付物是 (\d+) 个原始文件；文本输出为空不表示没有交付，文件字节在详情页下方单独列出。$"),
    r"This task delivered \1 original files. Empty text does not mean nothing was delivered; the file bytes are listed separately below.",
))
REGEX.insert(0, (
    re.compile(r"^最终输出确实为空。适配器记录的原因：(.+)$"),
    r"The final output is genuinely empty. The adapter recorded this reason: \1",
))
REGEX.insert(0, (
    re.compile(r"^最终输出确实为空。请求未成功，错误类型为 (.+)；历史记录没有保存更多错误文字。$"),
    r"The final output is genuinely empty. The request failed with error type \1; the historical record retained no further error text.",
))
REGEX.insert(0, (
    re.compile(r"^(成功|失败) · HTTP (.+)$"),
    lambda match, catalog: (("Success" if match.group(1) == "成功" else "Failure") + " · HTTP "
                   + ("not provided" if match.group(2) == "未提供" else match.group(2))),
))


REGEX.insert(0, (
    re.compile(r"^资源表覆盖预先确定的全部 (\d+) 个普通网页，包含失败调用。这组任务的通过数：(.*)。各项平均值、P50、P95 使用同一完整范围。$"),
    lambda match, catalog: (
        f"The resource table covers all {match.group(1)} predeclared general pages, including failed calls. "
        "Pass counts: " + match.group(2).replace("，", ", ")
        + ". Mean, P50, and P95 share this complete population."
    ),
))

REGEX.insert(0, (
    re.compile(r"^(\d+)/(\d+)$"),
    r"\1/\2",
))
REGEX.insert(0, (
    re.compile(r"^。当前报告的 (.+) 时延纠正仅替换 (\d+) 个耗时值，统一取配对实验的第 (\d+) 轮；旧耗时和原始物理调用保留在离线证据，当前投影的$"),
    r". In the current report, the latency correction for \1 replaces only \2 timings and consistently uses round \3 of the paired experiment. Previous timings and physical calls remain in the offline evidence; the projected",
))
REGEX.insert(0, (
    re.compile(r"^接收 (.+) / 发送 (.+)$"),
    lambda match, catalog: "Received " + match.group(1).replace("、", ", ")
    + " / sent " + match.group(2).replace("、", ", "),
))
REGEX.insert(0, (
    re.compile(r"^(.+) 是本轮资源效率领先者，在 (\d+) 个可按低消耗比较的指标中取得 (\d+) 项最低。这里的资源领先不等于综合任务通过率领先。$"),
    r"\1 is the resource-efficiency leader in this run, with the lowest use in \3 of \2 lower-is-better metrics. Resource leadership is not the same as leadership in overall task pass rate.",
))

REGEX.insert(0, (
    re.compile(r"^主结果 ([\d,]+)/([\d,]+)；同窗测速 ([\d,]+)/([\d,]+)；代理分歧 (\d+)；已裁定 (\d+)；未决 (\d+)。$"),
    r"Primary results \1/\2; same-window latency \3/\4; agent disagreements \5; adjudicated \6; unresolved \7.",
))
REGEX.insert(0, (
    re.compile(r"^全部 (\d+) 种受测配置共同通过$"),
    r"All \1 tested configurations pass",
))
REGEX.insert(0, (
    re.compile(r"^从结构化结果中读取标题与详情链接，共 (\d+) 个标题。$"),
    r"Read titles and detail URLs from structured results: \1 returned titles.",
))
REGEX.insert(0, (
    re.compile(r"^(\d+)/(\d+) 个标题找到独立名称证据；没有对应证据的不评分。$"),
    r"\1/\2 titles join to independent name evidence; titles without matching evidence are not scored.",
))


REGEX[:0] = [
    (re.compile(r"^(\d+) 个案例 · (\d+) 次调用，机器判分。本轮未重新核验来源。$"), r"\1 cases · \2 calls, machine-scored. Sources were not reverified for this run."),
    (re.compile(r"^每种方法均使用同一组 (\d+) 个任务，包含通过、未通过、错误和超时。耗时是单次调用从输入到返回的时间，不包含调用之间的统一冷却间隔。各方法串行执行，仍受本地与远程环境、网络和产品内部限制影响。$"), r"Each method uses the same \1 tasks, including passes, failures, errors, and timeouts. Latency runs from input to return for a single call, excluding shared cooldown intervals. Each method runs serially and remains subject to local and remote environments, networks, and product limits."),
]

REGEX.extend([
    (re.compile(r"^核验于 (\d{4}-\d{2}-\d{2})。算力、网络、存储、代理和维护成本自担；没有测量这些成本，不把本地调用等同于零成本。官方托管产品或服务价格不属于本地执行模式。$"),
     r"Checked on \1. The caller pays compute, network, storage, proxy, and maintenance costs. These costs were not measured, so local calls are not treated as free. Official managed-product or service prices do not apply to local execution."),
    (re.compile(r"^必需文字组 (\d+)（任一写法即可）$"), r"Required text group \1 (any alternative may match)"),
    (re.compile(r"^· 原始采集记录 ([\d,]+) 字节（可含协议返回） · 原始 SHA-256：$"), r"· Original capture: \1 bytes (may include protocol responses) · Original SHA-256:"),
    (re.compile(r"^本报告纳入 ([\d,]+) 个逻辑结果。运行参数见下表；保留的物理调用与输出见各结果详情。$"),
     r"This report includes \1 logical results. Run parameters are listed below; each result detail contains its retained physical calls and outputs."),
    (re.compile(r"^· 请求：(成功|失败) · 耗时：([\d,.]+) ms · 实际输出：([\d,]+) 字符$"),
     lambda m, catalog: f"· Request: {'success' if m[1] == '成功' else 'failure'} · Duration: {m[2]} ms · Actual output: {m[3]} characters"),
])


@dataclass(frozen=True)
class Catalog:
    """What one English pass may use beyond the shared interface catalog.

    ``exact`` holds a report's own bound translations (release history, an archived
    article). ``text``, ``phrases`` and ``patterns`` hold the frozen text of an
    already-published report, present only when the build is identified as that report.
    """

    exact: Mapping[str, str] = field(default_factory=dict)
    text: Mapping[str, str] = field(default_factory=dict)
    phrases: Mapping[str, str] = field(default_factory=dict)
    patterns: tuple = ()


LEGACY_REPORT_TEXT = Path(__file__).with_name('data') / 'legacy-report-text.json'


def _legacy_pattern(row):
    pattern = re.compile(row['pattern'])
    if 'replacement' in row:
        return pattern, row['replacement']

    def replace(match, catalog):
        values = _GroupValues(match, catalog)
        result = row['format'].format_map(values)
        for source, target in row.get('replace', ()):
            result = result.replace(source, target)
        return result
    return pattern, replace


class _GroupValues(dict):
    """``g<n>``/``<name>`` give a match group as written; ``t<n>`` gives it translated."""

    def __init__(self, match, catalog):
        super().__init__()
        self.match, self.catalog = match, catalog

    def __missing__(self, key):
        if key[0] in 'gt' and key[1:].isdigit():
            value = self.match.group(int(key[1:])) or ''
            return translate_text(value, self.catalog) if key[0] == 't' else value
        return self.match.group(key)


@cache
def _legacy_reports() -> dict:
    return json.loads(LEGACY_REPORT_TEXT.read_text(encoding='utf-8'))['reports']


def legacy_report_text(report: str) -> dict:
    """The frozen content of one already-published report, keyed by its identity."""
    reports = _legacy_reports()
    if report not in reports:
        raise ValueError('Unregistered legacy report text: ' + report)
    return reports[report]


def _historical_release_ids() -> set[str]:
    """Releases of the frozen Web Fetch history; packaged with the frozen text, not read from a checkout."""
    return set(legacy_report_text('web-fetch-published')['release_ids'])


def published_report_catalog(out: Path, exact=None) -> Catalog:
    """The English-pass catalog for the report in ``out``.

    A report whose bound release history names a release of the frozen Web Fetch
    history (``release_ids``) is one of the already-published reports; only it reads their legacy
    text. Any other report localizes with the shared interface catalog alone.
    """
    exact = dict(exact or {})
    # The price catalog the report ships owns the English of the text it renders.
    pricing = out / 'adoption/pricing.json'
    bound = json.loads(pricing.read_text(encoding='utf-8')).get('english_pass', {}) if pricing.is_file() else {}
    history = out / 'report-changes.json'
    releases = ({row.get('id') for row in json.loads(history.read_text(encoding='utf-8')).get('releases', [])}
                if history.is_file() else set())
    if not releases & _historical_release_ids():
        return Catalog(exact=exact, text=bound)
    legacy = legacy_report_text('web-fetch-published')
    return Catalog(exact=exact, text={**bound, **legacy['text']}, phrases=legacy['phrases'],
                   patterns=tuple(_legacy_pattern(row) for row in legacy['patterns']))


def _registered_patterns():
    """English-pass rules a registered method declares for the text its registry entry renders."""
    from harness.web_fetch.methods import report_method_catalog
    return tuple(_legacy_pattern(row) for method in report_method_catalog()['methods'].values()
                 for row in method.get('english_pass', ()))


REGISTERED_PATTERNS = _registered_patterns()
_SHARED = Catalog()


def translate_text(value: str, catalog: Catalog | None = None) -> str:
    catalog = catalog or _SHARED
    leading = value[:len(value) - len(value.lstrip())]
    trailing = value[len(value.rstrip()):]
    core = value.strip()
    if not core:
        return value
    translated = next((source[core] for source in (catalog.exact, catalog.text, TEXT) if core in source), None)
    if translated is None:
        for marker in (" ↕", " ↑", " ↓"):
            stem = core[:-len(marker)]
            if core.endswith(marker) and (stem in catalog.text or stem in TEXT):
                translated = catalog.text.get(stem, TEXT.get(stem)) + marker
                break
    if translated is None:
        for pattern, replacement in (*catalog.patterns, *REGEX, *REGISTERED_PATTERNS):
            if pattern.fullmatch(core):
                translated = pattern.sub(replacement if isinstance(replacement, str)
                                         else partial(replacement, catalog=catalog), core)
                break
    if translated is None:
        translated = re.sub(
            r"^完整参考覆盖 (\d+)/(\d+)；缺少参考只影响全文指标，不影响任务结果。",
            r"Complete-reference coverage: \1/\2. A missing reference affects only full-text metrics, not task acceptance. ",
            core,
        )
        translated = re.sub(
            r"文字核对范围：各方法均为 ([\d,]+/[\d,]+) 例。",
            r"Text-check coverage: \1 cases for every method. ", translated,
        )
        for source, target in sorted({**PHRASES, **catalog.phrases}.items(), key=lambda item: len(item[0]), reverse=True):
            translated = translated.replace(source, target)
        if translated == core:
            translated = None
    return leading + (translated if translated is not None else core) + trailing


class Localizer(HTMLParser):
    def __init__(self, source: PurePosixPath | None = None, catalog: Catalog | None = None) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.stack: list[str] = []
        self.source = source
        self.catalog = catalog
        self.translate_stack = []
        self.model_text_stack = []

    def _allows_translation(self, attrs):
        mode = (dict(attrs).get("translate") or "").lower()
        if mode in {"yes", "no"}:
            return mode == "yes"
        return self.translate_stack[-1] if self.translate_stack else True

    def _start_tag(self, attrs) -> str:
        original = self.get_starttag_text()
        if (not self._allows_translation(attrs)
                or any(tag in self.stack for tag in ("pre", "code", "script", "style"))):
            return original
        values = dict(attrs)
        article = self.source in {PurePosixPath("guide/index.html"), PurePosixPath("guide/article.html")}

        def replace_attribute(markup, name, value):
            pattern = re.compile(r'''(?<![\w:-])(''' + re.escape(name)
                                 + r'''\s*=\s*)(?:"[^"]*"|'[^']*'|[^\s>]+)''', re.S)
            return pattern.sub(lambda match: match.group(1) + '"'
                               + html.escape(value, quote=True) + '"', markup)

        if article or self.source == PurePosixPath("methodology/index.html"):
            # The renderer supplies both language assets. Localization only selects
            # an existing URL; it never reads files or modifies measured evidence.
            for name in ("src", "href"):
                alternate = values.get("data-en-" + name)
                if alternate is not None:
                    original = replace_attribute(original, name, alternate)
            original = re.sub(r'''\s+data-en-[\w-]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?''',
                              "", original, flags=re.S)
        original = re.sub(r'''\s+data-i18n-attrs(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?''',
                          "", original, flags=re.S)
        for name in ("aria-label", "alt", "placeholder", "title"):
            label = values.get(name)
            if label is not None:
                original = replace_attribute(original, name, translate_text(label, self.catalog))
        return original

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def handle_starttag(self, tag: str, attrs) -> None:
        localized = dict(attrs).get('data-report-en') if tag in {'span', 'option'} else None
        parent_model = any(self.model_text_stack)
        self.model_text_stack.append(localized is not None or parent_model)
        self.translate_stack.append(self._allows_translation(attrs))
        self.stack.append(tag)
        self.parts.append(self._start_tag(attrs))
        if localized is not None and not parent_model:
            self.parts.append(html.escape(localized))

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.parts.append(self._start_tag(attrs))

    def handle_endtag(self, tag: str) -> None:
        if tag in self.stack:
            self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]
            self.translate_stack = self.translate_stack[:len(self.stack)]
            self.model_text_stack = self.model_text_stack[:len(self.stack)]
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if any(self.model_text_stack):
            return
        if ((self.translate_stack and not self.translate_stack[-1])
                or any(tag in self.stack for tag in ("pre", "code", "script", "style"))):
            self.parts.append(data)
        else:
            self.parts.append(translate_text(data, self.catalog))

    def handle_entityref(self, name: str) -> None:
        if any(self.model_text_stack):
            return
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if any(self.model_text_stack):
            return
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")


class EnglishNarrativeAudit(HTMLParser):
    """Find CJK left in UI/prose while excluding preserved measured evidence."""

    def __init__(self, allowed_source_text=()) -> None:
        super().__init__(convert_charrefs=False)
        self.skip = 0
        self.allowed = sorted(set(filter(None, allowed_source_text)), key=len, reverse=True)
        self.untranslated: list[str] = []

    def _check(self, value: str) -> None:
        remainder = html.unescape(value).strip()
        for allowed in self.allowed:
            remainder = remainder.replace(allowed, "")
        if re.search(r"[\u4e00-\u9fff]", remainder):
            self.untranslated.append(html.unescape(value).strip())

    def handle_starttag(self, tag: str, attrs) -> None:
        if not self.skip:
            for name, value in attrs:
                if name in {"alt", "aria-label", "placeholder", "title"} and value:
                    self._check(value)
        if tag in {"pre", "code", "script", "style"}:
            self.skip += 1

    def handle_startendtag(self, tag: str, attrs) -> None:
        if not self.skip:
            for name, value in attrs:
                if name in {"alt", "aria-label", "placeholder", "title"} and value:
                    self._check(value)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"pre", "code", "script", "style"}:
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self._check(data)


def untranslated_english_narrative(page: str, allowed_source_text=()) -> list[str]:
    parser = EnglishNarrativeAudit(allowed_source_text)
    parser.feed(page)
    parser.close()
    return parser.untranslated


def _relative(source: PurePosixPath, target: PurePosixPath) -> str:
    return posixpath.relpath(str(target), str(source.parent))


def _rewrite_url(value: str, source: PurePosixPath, english: PurePosixPath, route_targets=None) -> str:
    parsed = urlsplit(html.unescape(value))
    if parsed.scheme or parsed.netloc or value.startswith(("mailto:", "data:", "javascript:")):
        return value
    if not parsed.path:
        return value
    resolved = PurePosixPath(posixpath.normpath(posixpath.join(str(source.parent), parsed.path)))
    target = PurePosixPath("en") / resolved if resolved.suffix.lower() in {".html", ".htm"} else resolved
    if route_targets and str(resolved) in route_targets:
        target = PurePosixPath(route_targets[str(resolved)])
    rewritten = _relative(english, target)
    return html.escape(urlunsplit(("", "", rewritten, parsed.query, parsed.fragment)), quote=True)


def _rewrite_links(page: str, source: PurePosixPath, english: PurePosixPath, route_targets=None) -> str:
    pattern = re.compile(r'(?P<prefix>\b(?:href|src)=)(?P<quote>["\'])(?P<url>.*?)(?P=quote)')
    return pattern.sub(lambda match: match.group("prefix") + match.group("quote")
                       + _rewrite_url(match.group("url"), source, english, route_targets) + match.group("quote"), page)


def _header(source: PurePosixPath, english: PurePosixPath, *, primary_english=False) -> str:
    brand = _relative(english, PurePosixPath("en/index.html"))
    chinese = _relative(english, source)
    if primary_english:
        chinese += '?report-language=zh'
    links = "".join(
        f'<a href="{html.escape(_relative(english, PurePosixPath("en") / path), quote=True)}">{label}</a>'
        for path, label in NAV
    )
    return (f'<header class="top"><nav class="wrap"><a href="{brand}">Web Fetch</a>'
            f'{links}<a href="{chinese}" hreflang="zh-CN">中文</a>'
            '<button class="theme-toggle" onclick="theme()">Toggle theme</button></nav></header>')


def localize_html(page: str, source: PurePosixPath, *, catalog: Catalog | None = None, route_targets=None) -> str:
    english = PurePosixPath("en") / source
    page = page.replace('<html lang="zh-CN">', '<html lang="en">', 1)
    primary_english = bool(re.search(r'<script\b[^>]*\bdata-report-language-entry\b', page))
    page = re.sub(r'<script\b[^>]*\bdata-report-language-entry\b[^>]*>.*?</script>', '', page, flags=re.S)
    page = re.sub(r'<header class="top">.*?</header>', _header(source, english, primary_english=primary_english), page, count=1, flags=re.S)
    # Raw evidence and executable/style blocks are preserved, never translated.
    # Mask them before parsing so large responses do not incur entity-by-entity work.
    preserved = []
    def preserve(match):
        preserved.append(match.group(0))
        return f"<!--LOCALIZATION-PRESERVED-{len(preserved)-1}-->"
    page = re.sub(r"<(pre|script|style)\b[^>]*>.*?</\1>", preserve, page, flags=re.S | re.I)
    parser = Localizer(source, catalog)
    parser.feed(page)
    parser.close()
    localized = "".join(parser.parts)
    for index, block in enumerate(preserved):
        localized = localized.replace(f"<!--LOCALIZATION-PRESERVED-{index}-->", block)
    return _rewrite_links(localized, source, english, route_targets)


REGEX.insert(0, (re.compile(r"^源码提交：(.+)$"), r"Source commit: \1"))


REGEX.insert(0, (re.compile(r"^(\d+) 个计划任务中的通过数 / 可判定数；每题等权，无法判定结果不参与分母。$"), r"Passed / decidable results among \1 planned tasks; each task has equal weight, and undecidable results are excluded from the denominator."))
REGEX.insert(0, (re.compile('^每种方法均使用同一组 (\\d+) 个任务，包含通过、未通过、错误和超时。耗时是单次调用从输入到返回的时间，不包含调用之间的统一冷却间隔。每种方法内部按案例依次执行；方法之间的并发安排和限制见调用链检查。耗时仍受本地与远程环境、网络和产品内部限制影响。$'), 'Each method uses the same \\1 tasks, including passes, failures, errors, and timeouts. Latency runs from input to return and excludes the common cooldown between calls. Cases execute sequentially within each method; inter-method scheduling and limits are documented in the call-chain audit. Local and remote environments, networks, and product limits still affect latency.'))
REGEX.insert(0, (re.compile(r"^(\d+)/(\d+) · 复核记录$"), r"\1/\2 · review records"))
REGEX.insert(0, (re.compile(r"^(\d+) 条通过结果的参考词精确率低于 30%。此比例用于定位检查对象，不是统一失败线；通过不保证干净正文，正文参考的范围和时间也会影响该指标。$"), r"\1 passing results have reference word precision below 30%. This locates inspection candidates, not a universal failure threshold. Passing does not establish clean body text; reference scope and time also affect this metric."))


def write_english_report(out: Path, *, source_routes=None) -> int:
    """Mirror Chinese HTML routes; a scoped refresh preserves other editions."""
    if source_routes is None:
        sources = sorted(path for path in out.rglob("*.html") if "en" not in path.relative_to(out).parts)
    else:
        from lexbench_webfetch.paths import contained_path
        sources = []
        for name in sorted(set(source_routes)):
            route = PurePosixPath(name)
            if route.is_absolute() or '..' in route.parts or not route.parts or route.parts[0] == 'en' or route.suffix != '.html':
                raise ValueError('Scoped English routes must be relative Chinese HTML paths')
            path = contained_path(out, str(route), reject_symlinks=True)
            contained_path(out, 'en/' + str(route), reject_symlinks=True)
            if not path.is_file():
                raise FileNotFoundError('Missing Chinese route: ' + str(route))
            sources.append(out / str(route))
    history_path = out / "report-changes.json"
    translations = release_translations(json.loads(history_path.read_text(encoding="utf-8"))) if history_path.is_file() else {}
    catalog = published_report_catalog(out, translations)
    article_catalog = replace(catalog, exact=archived_translations(out))
    english_root = out / "en"
    if source_routes is None and english_root.exists():
        shutil.rmtree(english_root)
    dataset_path = out / "dataset.json"
    allowed_source_text = {"中文"}
    if dataset_path.is_file():
        dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
        allowed_source_text.update(
            case.get("name") for case in dataset.get("cases", []) if case.get("name")
        )
    archived_manifest = out / "archived-reading.json"
    if archived_manifest.is_file():
        allowed_source_text.update(json.loads(archived_manifest.read_text(encoding="utf-8")).get("preserved_source_names", []))
    for source_path in sources:
        source = PurePosixPath(source_path.relative_to(out).as_posix())
        target = out / "en" / Path(str(source))
        target.parent.mkdir(parents=True, exist_ok=True)
        raw=source_path.read_text(encoding="utf-8")
        if str(source)=='guide/index.html' and (out/'archived-reading.json').is_file():
            parsed = BeautifulSoup(raw, 'html.parser')
            article = parsed.select_one('[data-archived-reading="true"]')
            if article is None:
                raise ValueError('Historical article route lacks its scoped wrapper')
            article.replace_with(Comment('ARCHIVED-ARTICLE'))
            shell = localize_html(str(parsed), source, catalog=catalog)
            body = localize_html(render_archived_reading(out,'en'), source,
                                 catalog=article_catalog)
            localized = shell.replace('<!--ARCHIVED-ARTICLE-->', body)
        else:
            localized=localize_html(raw,source,catalog=catalog)
        if source in {PurePosixPath("guide/index.html"), PurePosixPath("guide/article.html")}:
            untranslated = untranslated_english_narrative(localized, allowed_source_text)
            if untranslated:
                raise ValueError(
                    "Untranslated English article UI or narrative: " + repr(untranslated[:20])
                )
        target.write_text(localized, encoding="utf-8")
    return len(sources)


# Saved-source presentation uses the shared localization owner.
_SAVED_MATRIX_SOURCES = {'review_coverage_unresolved': '{reviewed}/{total} · review records · {unresolved} unresolved',
 'task_rules_by_method': 'Task requirements by method',
 'extension_index': 'No.',
 'extension_audit_title': 'Saved results by method',
 'extension_audit_basis': 'Recorded source results are retained; scoring differences are explained below.',
 'extension_method_details': 'View method details and per-task evidence →',
 'extension_rank': 'Rank',
 'extension_model_fetch_title': 'Model-provided web retrieval tool or API',
 'extension_model_fetch': 'Model company',
 'extension_price_units': 'Prices are in USD.',
 'extension_ranking_title': 'Task pass-rate ranking',
 'extension_shared_latency': 'Latency on tasks passed by every method',
 'extension_ordering': 'Ordered by task pass rate.',
 'extension_scoring_versions': 'Scoring sources and versions',
 'extension_price_column': 'Reference price',
 'extension_price_hour_amount': '{amount} / hour',
 'extension_price_duration': 'Billed by browser runtime',
 'extension_price_tokens': 'Input {incoming} · Output {outgoing}',
 'extension_price_token_unit': 'Per million tokens',
 'extension_price_difficulty': 'Price varies by page processing tier',
 'extension_price_pages': '{amount} / 1,000 pages',
 'extension_price_requests': '{amount} / 1,000 requests',
 'extension_price_unpublished': 'Price not published',
 'extension_local': 'Local',
 'extension_unrecorded': 'Not recorded',
 'extension_prices_headers': ['Method', 'Price', 'Billing', 'Source'],
 'parameter_rerun_title': 'Parameter reruns and result selection',
 'changes_scope': 'Parameter reruns and offline revisions are recorded separately. Parameter reruns include '
                  'new calls; offline revisions use retained responses only.',
 'offline_review_scope': 'This records rule review of known machine results, scored offline from saved '
                         'answers, not independent blind review. New calls and result selection from '
                         'parameter reruns are documented separately in Changes.',
 'parameter_rerun_banner': 'Parameter reruns evaluated {evaluated} tasks; {selected} new results were '
                           'adopted as whole method/family groups by pass count. Adopted groups include '
                           'regressions; old and new results are not combined task by task. Ties or lower '
                           'pass counts retain the baseline. These runs do not establish that parameters '
                           'improved speed.',
 'parameter_rerun_note': 'Categories and parameters are set before calls. A first failure permits at most '
                         'one retry; the final call is retained. Compare whole method/family groups by pass '
                         'count and adopt the complete improved group, including regressions. Ties or lower '
                         'counts retain the baseline. Supplemental dynamic and popular tasks are selected '
                         'with their respective specialist groups, not independently.',
 'parameter_family_banner': 'Parameter reruns evaluated {evaluated} cases; {selected} new results were '
                            'selected as complete groups. Check configuration uniformity across each family '
                            'before comparing eligible complete groups by pass count; do not combine '
                            'individual cases. Both groups must also follow their frozen failure-retry '
                            'policies. A group with inconsistent settings cannot qualify through a higher '
                            'pass count. These runs do not establish that parameter changes improved speed.',
 'parameter_family_note': 'Check configuration uniformity across each family before comparing eligible '
                          'complete groups by pass count. If only one group qualifies, select it even if its '
                          'pass count is lower. Both groups must also follow their frozen failure-retry '
                          'policies. If both groups qualify, retain the baseline when the candidate pass '
                          'count is equal or lower.',
 'parameter_family_eligibility': 'Whole-family configuration uniformity: baseline {before}, candidate '
                                 '{candidate}.',
 'parameter_baseline_retry_eligibility': 'The baseline follows its frozen failure-retry policy: {valid}.',
 'parameter_eligible': 'yes',
 'parameter_ineligible': 'no (ineligible)',
 'parameter_unverified': 'unverified',
 'parameter_rerun_selected': 'Rerun selected',
 'parameter_rerun_retained': 'Baseline retained',
 'parameter_rerun_group': 'This family contains {count} tasks: baseline passed {before_passed}, candidate '
                          'passed {candidate_passed}.',
 'parameter_rerun_before': 'Baseline',
 'parameter_rerun_candidate': 'Rerun',
 'adapter_rejected': 'Not called · adapter rejected',
 'incomplete': 'Incomplete',
 'uncommitted': 'Called · result not saved',
 'undecidable': 'Called · undecidable',
 'not_called': 'Not called',
 'adapter_rejected_note': 'The adapter rejected the request before dispatch, so the product was not called. '
                          'Original records and scores remain, but count as neither product failures nor '
                          'pass-rate denominators.',
 'provider': 'Method',
 'limitations': 'Scope and limitations',
 'certification_note': 'This page summarizes saved results; evidence and file verification are not benchmark '
                       'certification. Final presentation does not complete historical certification '
                       'requirements or constitute a new independent blind review.',
 'record_link': 'Original result record',
 'answer_link': 'Original answer',
 'receipt_link': 'Evidence receipt',
 'completion_title': 'Completion and incomplete positions',
 'completion_note': 'Identically named tasks are distinguished by their original suite. Saved scores remain '
                    'unchanged; uncommitted calls have no score. Adapter rejections are not product calls or '
                    'failures and do not enter pass-rate denominators. Suite prefixes on detail-page IDs '
                    'only avoid link collisions; original tasks and results remain in evidence files.',
 'completion_headers': ['Method',
                        'Planned',
                        'Actual calls',
                        'Saved',
                        'Normal returns',
                        'Pass',
                        'Adapter rejected',
                        'Called but not saved',
                        'Not called'],
 'missing_headers': ['Suite', 'Method', 'Original task ID', 'Status', 'Recorded reason', 'Evidence'],
 'suite_denominator_note': 'Counts use repository task suites. Pass rate is saved passes from actual calls '
                           'divided by saved, decidable actual-call results. Missing and uncalled positions '
                           'are not failures. Adapter rejections are not calls and do not enter the '
                           'denominator; original records and scores remain unchanged.',
 'suite_result_headers': ['Method',
                          'Planned tasks',
                          'Saved results',
                          'Not saved or not called',
                          'Normal returns',
                          'Pass',
                          'Pass rate (explicit denominator)'],
 'history_title': 'Records before the correction',
 'history_note': 'Original records before the correction are retained below, without counting them again. '
                 'Current results use replacement calls within a predeclared scope, not selection by score.',
 'history_scope': 'Replacement scope and original-record binding',
 'continuation_title': 'Completion of one failure retry',
 'continuation_note': 'One call already occurred. This completes its previously authorized retry, for two '
                      'calls in total. Current scores and latency use the final call regardless of outcome; '
                      'first-call evidence is retained.',
 'continuation_cost': 'Accumulated latency of both calls:',
 'continuation_unknown_cost': 'Unavailable: the exact first-call latency was not saved.',
 'continuation_prior': 'View first-call evidence',
 'coverage_banner_title': 'Saved-result coverage',
 'coverage_banner_counts': 'Planned {planned}; saved {saved}; called but not saved {uncommitted}; not called '
                           '(including requested skips) {not_called}; adapter rejected {rejected}.',
 'coverage_banner_note': 'No scores are invented for missing or uncalled results; adapter rejections are not '
                         'product failures. This report presents saved evidence, not complete acceptance or '
                         'formal publication.',
 'final_call_timing_headers': ['Mean final-call latency', 'P50 final-call latency', 'P95 final-call latency'],
 'passing_final_call_headers': ['Mean latency (passed)', 'P50 latency (passed)', 'P95 latency (passed)'],
 'passing_final_call_title': 'Final-call latency for passing tasks',
 'passing_final_call_help': 'Only passing tasks are included. Green bold values are the shortest in their columns. Each method passed a different mix of tasks, so these values do not rank speed on the same tasks.',
 'all_final_call_timing_title': 'Final-call latency for all calls',
 'final_call_count': 'Saved calls',
 'final_call_timing_title': 'Mean / P50 / P95 final-call latency',
 'final_call_timing_help': 'All three statistics use the final call of each retained result, including '
                           'passes and failures. Uncommitted, uncalled and adapter-rejected entries are '
                           'excluded; sample sizes can differ. Latency does not affect pass-rate ordering. '
                           'Calls from different dates and batches are not a controlled same-window speed '
                           'comparison.',
 'final_call_timing_note': 'Each logical result uses only its final call latency, not the accumulated cost '
                           'of prior retries and waits. Continued retries disclose the known combined cost '
                           'separately in their details; a missing first-call duration remains unavailable.'}


def saved_matrix_text(key, **values):
    """Use the shared English-source catalog for saved-result interface text."""
    source = _SAVED_MATRIX_SOURCES[key]
    if isinstance(source, list):
        return [t(f'saved_matrix_{key}_{index}', text) for index, text in enumerate(source)]
    result = t('saved_matrix_' + key, source, **values)
    if values:
        # Localized status labels need their English forms inside the sentence.
        english_values = {name: translate_text(value) if isinstance(value, str) else value
                          for name, value in values.items()}
        TEXT[result.strip()] = source.format_map(english_values)
    return result


def _register_saved_matrix_fallbacks():
    """Localize frozen Chinese pages from the same catalog and English sources."""
    texts = []
    for key, source in _SAVED_MATRIX_SOURCES.items():
        entries = [(f'saved_matrix_{key}_{index}', text) for index, text in enumerate(source)] if isinstance(source, list) else [('saved_matrix_' + key, source)]
        for message_id, english in entries:
            fields = re.findall(r'\{([a-z_]+)\}', english)
            chinese = translate(MESSAGES, message_id, 'zh', **{name: '{' + name + '}' for name in fields})
            texts.append((chinese, english))
    # Frozen pages used one text node for the counts and their scope note.
    # Derive that legacy grammar from the same catalog, preserving source bytes.
    keys = ('coverage_banner_counts', 'coverage_banner_note')
    english = ' '.join(_SAVED_MATRIX_SOURCES[key] for key in keys)
    fields = re.findall(r'\{([a-z_]+)\}', english)
    values = {name: '{' + name + '}' for name in fields}
    texts.append((' '.join(translate(MESSAGES, 'saved_matrix_' + key, 'zh', **values) for key in keys), english))
    for chinese, english in texts:
        fields = re.findall(r'\{([a-z_]+)\}', english)
        if not fields:
            TEXT.setdefault(chinese.strip(), english)
            continue
        pattern = re.escape(chinese)
        for name in fields:
            pattern = pattern.replace(re.escape('{' + name + '}'), '(?P<' + name + '>.+?)', 1)
        def replacement(match, catalog, english=english):
            return english.format_map({name: translate_text(value, catalog)
                                       for name, value in match.groupdict().items()})
        REGEX.append((re.compile('^' + pattern + '$'), replacement))


_register_saved_matrix_fallbacks()
# Registered names listed with the Chinese enumeration comma; tried after every other rule.
REGEX.append((re.compile(r"^[A-Za-z][\w.\- ]*(?:、[A-Za-z][\w.\- ]*)+$"),
              lambda match, catalog: match.group(0).replace("、", ", ")))
