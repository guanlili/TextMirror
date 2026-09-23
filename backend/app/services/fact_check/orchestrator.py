import copy
import re

from pydantic import ValidationError

from app.schemas.fact_check import FactSearchRound, FactSearchSource
from app.services.fact_check_search import NativeSearchError, search_model

from .constants import EXTRACTION_PROMPT, JUDGMENT_PROMPT, MAX_PAGE_TEXT
from .errors import FactCheckError, _FetchError, _SearchFailure
from .extraction import _claims_from_model, _source_segments
from .fetching import _fetch_page, _normalize, _now
from .judgment import _judge_result, _same_material
from .llm import _chat_json
from .search import _search


async def run_fact_check(
    text: str, *, mode: str, sources: list[dict], provider, api_key: str = "",
    search_provider: str = "model", max_claims: int = 10, on_progress=None,
    extraction_only: bool = False, prepared_report: dict | None = None,
    selected_claim_ids: list[str] | None = None, depth: str = "standard", supplemental_urls: list[str] | None = None,
) -> dict:
    """Coverage measures attempted extracted claims, never the truth of the entire input."""
    if (mode not in {"web", "trusted"} or search_provider not in {"model", "tavily"}
            or type(max_claims) is not int or max_claims < 0 or not isinstance(text, str)):
        raise FactCheckError("INVALID_INPUT", "事实核查模式、搜索服务、文本或核查预算无效。")
    selected = [source for source in sources if source.get("is_enabled")] if mode == "trusted" else None
    if selected == []:
        raise FactCheckError("NO_TRUSTED_SOURCES", "可信模式必须选择至少一个已启用信源；不会回退开放网络。")
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "search_queries": 0, "pages_fetched": 0}
    report = {"claims": [], "coverage": {}, "usage": usage, "checked_at": _now()}
    issues = []

    def snapshot(running=True):
        checked = sum(claim["checked"] for claim in report["claims"])
        unverified = len(report["claims"]) - checked
        reasons = list(dict.fromkeys(issues))
        if running:
            reasons.append("工作流尚未完成。")
        reasons.append("覆盖仅针对已提取陈述；已核查不代表已证实，不保证全文所有事实均已提取或通过。")
        report["coverage"] = {"extracted": len(report["claims"]), "checked": checked, "unverified": unverified,
                              "status": "partial" if running or issues or unverified else "complete",
                              "reason": " ".join(reasons)}
        report["checked_at"] = _now()
        return copy.deepcopy(report)

    async def progress(percent, message, current=None):
        if on_progress is not None:
            await on_progress(percent, message, current)

    if depth not in {"standard", "deep"} or len(supplemental_urls or []) > 3:
        raise FactCheckError("INVALID_INPUT", "核查深度或补充链接数量无效。")
    if prepared_report is not None:
        from app.schemas.fact_check import FactCheckReport
        try:
            prepared = FactCheckReport.model_validate(prepared_report)
            if any(text[item.start:item.end] != item.original for item in prepared.claims):
                raise ValueError
        except (ValidationError, ValueError):
            raise FactCheckError("INVALID_INPUT", "确认的事实项与原文快照不匹配。") from None
        report["claims"] = [item.model_dump() for item in prepared.claims]
        usage.update({key: prepared.usage.get(key, 0) for key in usage})
        issues.extend(sentence + "。" for sentence in prepared.coverage.reason.split("。")
                      if any(marker in sentence for marker in ("提取上限", "30条", "坐标无效", "存在歧义")))
    else:
        await progress(0, "正在从全文提取可核查事实。")
        segments = [{"id": segment["id"], "text": segment["text"]} for segment in _source_segments(text)]
        extracted = await _chat_json(
            provider, EXTRACTION_PROMPT, {"segments": segments}, usage, extraction_text=text,
            on_retry=lambda: progress(0, "正在重新核对提取格式与原文定位。", snapshot()),
        )
        report["claims"], extraction_issues = _claims_from_model(extracted, text)
        issues.extend(extraction_issues)
        if not report["claims"] and extraction_issues:
            message = "事实提取失败：提取项均无法定位到原文，重试后仍未取得有效事实；未执行搜索，不代表原文没有事实。"
            await progress(10, message, snapshot(running=False))
            raise FactCheckError("EXTRACTION_LOCATION_FAILED", message)
    if extraction_only and report["claims"]:
        await progress(10, "事实已提取，等待选择核查范围。", snapshot())
        return snapshot()
    known = {claim["id"]: claim for claim in report["claims"]}
    chosen = selected_claim_ids if selected_claim_ids is not None else list(known)[:max_claims]
    if len(chosen) != len(set(chosen)) or not set(chosen) <= set(known) or len(chosen) > max_claims:
        raise FactCheckError("INVALID_INPUT", "选择的事实项重复、无效或超出预算。")
    if depth == "deep" and len(chosen) != 1:
        raise FactCheckError("INVALID_INPUT", "深入核查仅支持一条事实。")
    if not report["claims"]:
        final = snapshot(running=False)
        await progress(100, "未识别到可核查事实，未执行搜索；不代表全文事实正确。", final)
        return final
    budget = len(chosen)
    for claim in report["claims"]:
        claim["selected"] = claim["id"] in chosen
        if prepared_report is not None:
            query = _normalize(claim["statement"])[:350]
            claim.update(checked=False, verdict="insufficient", evidence=[], suggestion=None, reason="尚未核查。",
                         search_rounds=[FactSearchRound(kind=kind, query=value, status="pending", sources=[]).model_dump()
                                        for kind, value in (("initial", query), ("counter", query + " 更正 修订 原始公告"))])
        if not claim["selected"]:
            claim["reason"] = "未纳入本次选择或超出核查预算，未执行检索和证据判定。"
    if budget < len(report["claims"]):
        issues.append(f"本次选择核查{budget}条，其余已提取陈述尚未核查。")
    if depth == "deep":
        claim = known[chosen[0]]
        statement = _normalize(claim["statement"])[:280]
        claim["search_rounds"] = [FactSearchRound(kind=kind, query=query, status="pending", sources=[]).model_dump() for kind, query in (
            ("initial", statement + " 原始发布 公告"),
            ("counter", statement + " 修订 更正 不实"),
            ("followup", statement + " 数据来源 适用范围"),
        )]
    await progress(10, f"已提取{len(report['claims'])}条可定位陈述，本次核查{budget}条。", snapshot())
    if budget and search_provider == "tavily" and not api_key.strip():
        raise FactCheckError("SEARCH_AUTH_ERROR", "未配置 Tavily API 密钥。")
    for index, claim in enumerate(known[claim_id] for claim_id in chosen):
        pages, visited, technical = [], {}, []
        fetched_by_url, source_links = {}, []
        claim_search_successes = 0
        for round_index, search_round in enumerate(claim["search_rounds"]):
            if search_round["kind"] == "followup":
                focus = "统计口径 单位 数据修订" if re.search(r"\d|增长|人口|比例|金额", claim["statement"]) else "事件时间 原始公告 引用出处"
                if not pages:
                    focus = "原始出处 官方发布 " + focus
                search_round["query"] = _normalize(claim["statement"])[:280] + " " + focus
            query = search_round["query"]
            label = {"initial": "初始检索", "counter": "反证/更正检索", "followup": "原始来源与口径补充检索"}[search_round["kind"]]
            base = 10 + 85 * (index + round_index * (0.7 / len(claim["search_rounds"]))) / budget
            search_round["status"] = "searching"
            supplemental = (supplemental_urls or []) if search_round["kind"] == "followup" else []
            urls = list(supplemental)
            search_round["sources"] = [FactSearchSource(
                url=url[:4096], origin="supplemental", status="pending",
                reason="补充链接等待安全抓取，尚未评估正文。",
            ).model_dump() for url in supplemental]
            await progress(int(base), f"正在检索第{index + 1}条事实（第{round_index + 1}轮：{label}）。", snapshot())
            usage["search_queries"] += 1
            try:
                searched = (await search_model(query, provider, selected) if search_provider == "model"
                            else await _search(query, api_key, selected))
                usage["search_queries"] += searched.search_queries - 1
                for key, count in searched.usage.items():
                    usage[key] += count
                claim_search_successes += 1
            except (NativeSearchError, FactCheckError) as exc:
                search_round["status"] = "failed"
                search_round["error_codes"].append(exc.code)
                technical.append(exc.code)
                issues.append(f"技术原因：{claim['id']} {label}失败（{exc.code}）。")
                for source in search_round["sources"]:
                    source.update(status="skipped", error_code=exc.code,
                                  reason="本轮检索失败，未执行补充正文抓取；尚未评估。")
                await progress(int(base + 85 / budget * 0.3),
                               f"第{index + 1}条事实的{label}失败，不能作出确定结论。", snapshot())
                if isinstance(exc, _SearchFailure):
                    continue
                raise FactCheckError(exc.code, exc.message) from None
            urls.extend(searched.urls[:3])
            search_round["sources"].extend(FactSearchSource(
                url=url[:4096], title=searched.titles.get(url, ""), status="pending",
                reason="搜索返回的候选资料，等待安全抓取；标题仅为搜索元数据，尚未评估正文。",
            ).model_dump() for url in searched.urls[:3])
            search_round["status"] = "fetching"
            await progress(int(base + 85 / budget * 0.1),
                           f"正在安全抓取第{index + 1}条事实的候选正文（{label}）。", snapshot())
            # Repeated candidates still consume slots; supplemental URLs have priority.
            budget_urls = list(dict.fromkeys(urls))[:3] if search_round["kind"] == "followup" else urls[:3]
            for url, source in zip(urls, search_round["sources"]):
                if url in visited:
                    previous = visited[url]
                    source.update(status="duplicate", reason="重复链接，未重复抓取；不作为独立佐证。")
                    if url in fetched_by_url:
                        source["title"] = previous["title"]
                        source_links.append((source, fetched_by_url[url].id))
                    else:
                        source.update(error_code=previous["error_code"],
                                      reason=(source["reason"] + "此前抓取失败：" + previous["reason"])[:1000])
                elif url not in budget_urls:
                    source.update(status="skipped", reason="本轮最多处理3个候选链接，补充链接优先；此资料超出抓取预算，未读取或评估正文。")
                else:
                    visited[url] = source
                    try:
                        page = await _fetch_page(url, selected)
                    except _FetchError as exc:
                        source.update(status="failed", error_code=exc.code, reason=exc.message[:1000])
                        technical.append(exc.code)
                        search_round["error_codes"].append(exc.code)
                        issues.append(f"技术原因：{claim['id']} {label} {exc.message}（{exc.code}）。")
                    else:
                        usage["pages_fetched"] += 1
                        search_round["pages_fetched"] += 1
                        source["title"] = page.title
                        existing = next((item for item in pages if page.url == item.url or _same_material(page, item)), None)
                        if existing is not None:
                            source.update(status="duplicate", reason="正文已读取，与已读取材料相同或高度重合；不作为独立佐证。")
                        else:
                            page.id = f"{claim['id']}-e{len(pages) + 1}"
                            pages.append(page)
                            source.update(status="fetched", reason="正文已读取，等待本次核查的引用判定；尚未完成评估。")
                            if len(page.text) > MAX_PAGE_TEXT:
                                issues.append(f"{claim['id']} 部分正文超出模型阅读长度限制，仅核对可见正文。")
                        material = existing or page
                        fetched_by_url[url] = fetched_by_url[page.url] = material
                        visited.setdefault(page.url, source)
                        source_links.append((source, material.id))
                if len(url) > 4096:
                    source["reason"] = source["reason"][:900] + " 原始 URL 超过4096字符，展示已截断；安全校验仍使用原始 URL。"
                # Interruptions must preserve completed outcomes and leave untouched candidates pending.
                await progress(int(base + 85 / budget * 0.2),
                               f"已记录第{index + 1}条事实的一份候选资料处理结果（{label}）。", snapshot())
            search_round["error_codes"] = list(dict.fromkeys(search_round["error_codes"]))
            search_round["status"] = "partial" if search_round["error_codes"] else "complete"
            state = "未完整完成" if search_round["error_codes"] else "已完成检索与抓取，不代表已发现反证或证实原文"
            await progress(int(base + 85 / budget * 0.3), f"第{index + 1}条事实的{label}{state}。", snapshot())
        if not claim_search_successes and technical:
            raise FactCheckError("SEARCH_PROVIDER_ERROR", "检索连续失败，无法完成事实核查。")
        # Always gather both rounds before ONE judgment; no first-round affirmative shortcut.
        invalid = False
        if pages:
            await progress(int(10 + 85 * (index + 0.75) / budget),
                           f"正在依据两轮取得的正文合并判定第{index + 1}条事实。", snapshot())
            data = await _chat_json(provider, JUDGMENT_PROMPT, {
                "claim": {key: claim[key] for key in ("original", "statement")}, "checked_at": _now(),
                "pages": [{"id": page.id, "title": page.title, "url": page.url,
                           "publisher": page.publisher, "published_at": page.published_at,
                           "text": page.text[:MAX_PAGE_TEXT]} for page in pages],
            }, usage, on_retry=lambda: progress(
                int(10 + 85 * (index + 0.75) / budget), "正在重新核对正文判定的输出格式。", snapshot(),
            ))
            await progress(int(10 + 85 * (index + 0.9) / budget),
                           f"正在校验第{index + 1}条事实的逐字引用与结构化口径检查。", snapshot())
            result, invalid = _judge_result(data, pages)
            claim.update(result)
            if invalid:
                issues.append(f"技术原因：{claim['id']} 模型引用、结构化口径或证据结论校验未通过。")
        else:
            claim["reason"] = "未取得可引用的正文证据，不能使用搜索摘要或模型知识判真/假。"
        if technical:
            # Missing pages may contain contrary evidence: fail closed for this claim.
            claim.update(verdict="insufficient", suggestion=None)
            claim["reason"] = "技术原因导致检索或安全抓取不完整，无法作出可靠判定：" + ", ".join(dict.fromkeys(technical)) + "。"
        adopted_ids = {item["id"] for item in claim["evidence"]}
        for source, page_id in source_links:
            if page_id in adopted_ids:
                source["evidence_id"] = page_id
                if source["status"] == "duplicate":
                    source["reason"] += "与已采用的同一材料共享引用，不构成独立佐证；作用及口径检查见关联证据。"
                else:
                    source["reason"] = "已采用正文引用；支持、反驳或背景作用及口径检查见关联证据。"
            elif source["status"] == "fetched":
                source["reason"] = "正文已读取，但本轮未形成可用引用；未采用不代表内容无关或陈述为假。"
            else:
                source["reason"] += "本次未采用该材料的引用；不代表内容无关或陈述为假。"
        claim["checked"] = True
        detail = "（检索或校验不完整，结论为证据不足）" if technical or invalid else ""
        await progress(int(10 + 85 * (index + 1) / budget), f"已完成第{index + 1}条事实核查尝试{detail}。", snapshot())
    final = snapshot(running=False)
    detail = "部分完成，" if final["coverage"]["status"] == "partial" else ""
    await progress(100, f"事实核查完成（{detail}覆盖范围以报告为准）。", final)
    return final
