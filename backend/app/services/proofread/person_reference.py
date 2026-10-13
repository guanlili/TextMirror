import asyncio
import json
import re
from difflib import SequenceMatcher

from loguru import logger

from app.services.llm.usage import usage_operation

from .constants import PERSON_CONSISTENCY_PROMPT

_PRONOUN = re.compile(r"(?<!其)[他她](?![们人日乡国处方物山])")
_GENDER_TERM = re.compile(r"先生|女士|夫人|小姐|丈夫|妻子|父亲|母亲|爸爸|妈妈|儿子|女儿|(?<!其)[他她](?![人日乡国处方物山])|男|女")
_MALE_TERMS = {"先生", "丈夫", "父亲", "爸爸", "儿子", "他", "男"}
_GENDER_REASON = re.compile(r"性别|男性|女性|男士|女士|男名|女名|男[性生]姓名|女[性生]姓名|他.*她|她.*他")
_OTHER_PERSON_CONTEXT = re.compile(r"陪同|见到|遇到|会见|告诉|询问|邀请|采访了|介绍了?[^，。！？]*(?:先生|女士)|和|与|向|对|让|请|提到|谈到|说起")
_TRUSTED_SOURCES = {"dict_scan", "consistency", "format_rule", "person_consistency", "numeric_rule", "numeric_guard"}


def _changes_gender(original: str, suggestion: str) -> bool:
    if original == suggestion:
        return False
    before = list(_GENDER_TERM.finditer(original))
    after = list(_GENDER_TERM.finditer(suggestion))
    if not before and not after:
        return False
    for tag, i, j, a, b in SequenceMatcher(None, original, suggestion, autojunk=False).get_opcodes():
        if tag not in ("insert", "replace"):
            continue
        old = [m.group() in _MALE_TERMS for m in before if m.start() < j and m.end() > i]
        new = [m.group() in _MALE_TERMS for m in after if m.start() < b and m.end() > a]
        if new and old != new:
            return True
    return False


def filter_gender_inferences(issues: list[dict]) -> list[dict]:
    kept = []
    for issue in issues:
        if issue.get("source") in _TRUSTED_SOURCES:
            kept.append(issue)
            continue
        original = issue.get("original") or ""
        suggestion = issue.get("suggestion") or ""
        if suggestion and _changes_gender(original, suggestion):
            continue
        if (_GENDER_REASON.search(issue.get("explanation") or "")
                and (_GENDER_TERM.search(original) or not suggestion or suggestion == original)):
            continue
        kept.append(issue)
    return kept


def needs_person_consistency(text: str) -> bool:
    return {match.group() for match in _PRONOUN.finditer(text)} == {"他", "她"}


def _evidence_span(text: str, quote: str, name: str) -> tuple[int, int] | None:
    if not 1 <= len(quote) <= 200 or not quote.lstrip().startswith(name):
        return None
    name_end = quote.index(name) + len(name)
    pronoun = _PRONOUN.search(quote, name_end)
    if pronoun is None or _OTHER_PERSON_CONTEXT.search(quote[name_end:pronoun.start()]):
        return None
    start = text.find(quote)
    if start < 0 or text.find(quote, start + 1) >= 0:
        return None
    # 不让模型截去句首或句尾的另一位人物，制造虚假的单人指代证据。
    if start and text[start - 1] not in "。！？；\r\n":
        return None
    end = start + len(quote)
    if end < len(text) and quote[-1] not in "。！？；\r\n" and text[end] not in "\r\n":
        return None
    return start, end


def parse_person_conflicts(content: str, text: str) -> list[dict]:
    if not isinstance(content, str):
        raise ValueError("人物一致性响应必须为JSON文本")
    content = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
    if fenced:
        content = fenced.group(1)
    items = json.loads(content)
    if not isinstance(items, list):
        raise ValueError("人物一致性响应必须为数组")
    findings, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or any(not isinstance(item.get(key), str) for key in ("name", "first", "second")):
            raise ValueError("人物一致性证据字段非法")
        name, first, second = (item[key] for key in ("name", "first", "second"))
        if not 2 <= len(name) <= 40 or name in seen:
            continue
        spans = [_evidence_span(text, quote, name) for quote in (first, second)]
        if any(span is None for span in spans):
            continue
        if spans[0][0] > spans[1][0]:
            first, second = second, first
            spans.reverse()
        if spans[0][1] > spans[1][0]:
            continue
        pronouns = [{m.group() for m in _PRONOUN.finditer(quote.replace(name, ""))} for quote in (first, second)]
        if not (pronouns == [{"她"}, {"他"}] or pronouns == [{"他"}, {"她"}]):
            continue
        if any(char in first + second for char in '“”「」『』"'):
            continue
        seen.add(name)
        findings.append({
            "original": second, "suggestion": "", "type": "logic", "severity": "warning",
            "explanation": f"人物「{name}」指代不一致，需人工核对：①「{first}」；②「{second}」。不据此判断性别。",
            "source": "person_consistency", "start": spans[1][0], "end": spans[1][1],
        })
    return findings


async def check_person_consistency(text: str, provider) -> tuple[list[dict], dict, bool]:
    if not needs_person_consistency(text):
        return [], {}, True
    usage = {}
    try:
        with usage_operation("person_consistency"):
            async with asyncio.timeout(provider.timeout):
                response = await provider.chat(
                    [{"role": "system", "content": PERSON_CONSISTENCY_PROMPT},
                     {"role": "user", "content": text}],
                    temperature=provider.default_temperature, thinking=False,
                    max_tokens=4096, timeout=provider.timeout,
                )
        usage = dict(response.usage)
        if getattr(response, "finish_reason", None) in ("length", "content_filter"):
            raise ValueError("人物一致性响应不完整")
        return parse_person_conflicts(response.content, text), usage, True
    except Exception as exc:
        logger.warning(f"[人物一致性] 全文核对未完成，保留指代改写保护: {type(exc).__name__}")
        return [], usage, False
