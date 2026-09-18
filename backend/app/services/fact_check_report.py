from html import escape

VERDICTS = {"supported": "证据支持", "refuted": "证据反驳", "insufficient": "证据不足", "conflicting": "证据冲突"}
STANCES = {"supports": "支持本事实项", "refutes": "反驳本事实项", "context": "仅作背景"}
ROUND_KINDS = {"initial": "初始检索", "counter": "反证检索", "followup": "补充检索"}
ROUND_STATUSES = {"pending": "待检索", "searching": "检索中", "fetching": "抓取中",
                  "complete": "已完成", "partial": "部分完成", "failed": "检索失败"}
SOURCE_STATUSES = {"pending": "待抓取", "fetched": "已抓取", "failed": "抓取失败",
                   "duplicate": "重复材料", "skipped": "已跳过"}
SOURCE_REASONS = {"pending": "等待抓取", "fetched": "正文已抓取",
                  "failed": "抓取失败，未记录具体原因", "duplicate": "重复材料，跳过重复抓取",
                  "skipped": "已跳过，未记录具体原因"}
CHECK_STATUSES = {"match": "一致", "mismatch": "不一致", "unknown": "无法确认", "not_applicable": "不适用"}


def render_report(data: dict) -> str:
    def text(value):
        return escape(str(value if value is not None else "未记录"))

    report = data.get("result") or {}
    sections = []
    for claim_index, claim in enumerate(report.get("claims", []), 1):
        evidence = []
        evidence_by_id = {}
        for evidence_index, item in enumerate(claim.get("evidence", []), 1):
            # Positional IDs avoid interpreting untrusted claim/evidence IDs as fragments.
            anchor = f"claim-{claim_index}-evidence-{evidence_index}"
            if item.get("id") is not None:
                evidence_by_id[item["id"]] = (item, anchor)
            checks = "".join(f"<li>{text(label)}：{text(CHECK_STATUSES.get(check.get('status'), check.get('status')))} — {text(check.get('reason'))}</li>"
                             for key, label in (("subject", "主体"), ("event_time", "事件时间"), ("scope_unit", "统计范围与单位"))
                             if (check := (item.get("checks") or {}).get(key)))
            evidence.append(f"<article id='{text(anchor)}'><h4>{text(item['title'])}</h4><p>{text(item['url'])}</p>"
                            f"<p>证据 {text(item.get('id'))} · 立场：{text(STANCES.get(item.get('stance'), '未记录'))}</p>"
                            f"<p>发布方：{text(item.get('publisher'))} · 发布：{text(item.get('published_at'))} · 抓取：{text(item.get('retrieved_at'))}</p>"
                            f"<blockquote>{text(item['quote'])}</blockquote><ul>{checks}</ul>"
                            f"<details><summary>当次模型可见正文快照</summary><pre>{text(item.get('body_text') or '历史报告未保存正文快照')}</pre></details>"
                            f"<p class='small'>SHA-256：{text(item.get('body_sha256'))}</p></article>")
        rounds = []
        for round_index, search_round in enumerate(claim.get("search_rounds", []), 1):
            sources = search_round.get("sources")
            materials = []
            if sources is None:
                materials.append("<p>历史报告未记录候选材料轨迹（不代表未检索到材料）</p>")
            elif not sources:
                status = search_round.get("status")
                if status in ("pending", "searching", "fetching"):
                    message = "本轮尚无候选材料记录，检索或抓取尚未完成"
                elif status == "complete":
                    message = "本轮无候选材料记录（检索已完成）"
                else:
                    message = "本轮未记录候选材料；检索未完整完成，不代表没有相关材料"
                materials.append(f"<p>{message}</p>")
            for source in sources or []:
                status = source.get("status")
                matched = evidence_by_id.get(source.get("evidence_id"))
                if matched:
                    item, anchor = matched
                    relation = "关联已采纳证据" if status == "duplicate" else "已采纳为证据"
                    adoption = (f"{relation}：<a href='#{text(anchor)}'>证据 {text(item['id'])}</a>"
                                f" · {text(STANCES.get(item.get('stance'), '未记录'))}")
                elif status == "fetched":
                    adoption = "未作为本次结论的引用依据（不代表材料无关或事实为假）"
                elif status == "pending":
                    adoption = "尚未采纳，等待处理"
                else:
                    adoption = "未采纳为证据"
                if source.get("evidence_id") is not None and not matched:
                    adoption += "；未找到本事实项对应证据"
                if status == "duplicate":
                    adoption += "；重复材料不计为独立佐证"
                error = f" · 错误代码：{text(source['error_code'])}" if source.get("error_code") else ""
                materials.append(f"<article><h4>{text(source.get('title') or '未记录标题')}</h4>"
                                 f"<p>网址：{text(source.get('url'))}</p>"
                                 f"<p>来源：{text({'search': '检索发现', 'supplemental': '补充材料'}.get(source.get('origin'), '未记录'))}"
                                 f" · 状态：{text(SOURCE_STATUSES.get(status, status))}</p>"
                                 f"<p>处理说明：{text(source.get('reason') or SOURCE_REASONS.get(status, '未记录原因'))}{error}</p>"
                                 f"<p>采纳关系：{adoption}</p></article>")
            errors = search_round.get("error_codes") or []
            round_errors = f"<p>本轮错误代码：{'、'.join(text(code) for code in errors)}</p>" if errors else ""
            rounds.append(f"<div><h4>第 {round_index} 轮 · {text(ROUND_KINDS.get(search_round.get('kind'), search_round.get('kind')))}</h4>"
                          f"<p>检索词：{text(search_round.get('query'))}</p>"
                          f"<p>状态：{text(ROUND_STATUSES.get(search_round.get('status'), search_round.get('status')))}"
                          f" · 已抓取正文：{text(search_round.get('pages_fetched', 0))}</p>"
                          f"{round_errors}{''.join(materials)}</div>")
        reviews = "".join(f"<li>{text(item['created_at'])} · 复核人 #{text(item['user_id'])} · "
                          f"{text({'agree': '认可', 'disagree': '异议', 'unresolved': '待核实'}.get(item['decision']))}：{text(item['note'])}</li>"
                          for item in data.get("reviews", []) if item["claim_id"] == claim["id"])
        sections.append(f"<section><h2>{text(claim['id'])} · {text(claim['statement'])}</h2>"
                        f"<p><strong>{text(VERDICTS[claim['verdict']] if claim['checked'] else '未核查')}</strong> · {text(claim['reason'])}</p>"
                        f"<p>原文：{text(claim['original'])}</p><h3>检索轮次与候选材料</h3>"
                        "<p>这里只展示检索接口返回的候选资料，不代表模型内部访问的全部网页；标题仅为资料线索，不是正文证据。</p>"
                        f"{''.join(rounds) or '<p>未记录检索轮次与候选材料轨迹</p>'}"
                        f"<h3>已采纳证据</h3>{''.join(evidence) or '<p>暂无已采纳证据</p>'}"
                        f"<h3>人工复核</h3><ul>{reviews or '<li>尚未复核</li>'}</ul></section>")
    coverage = report.get("coverage", {})
    status = data.get("status")
    status_label = {"PENDING": "等待执行", "RUNNING": "核查中", "WAITING_CONFIRMATION": "待确认事实",
                    "SUCCESS": "执行完成", "FAILURE": "执行失败", "CANCELLED": "已取消"}.get(status, "未记录")
    message = data.get("message", "")
    if (status == "FAILURE" and data.get("error_code") == "EXTRACTION_LOCATION_FAILED") or (status == "SUCCESS" and report and not report.get("claims") and coverage.get("status") == "partial"):
        status_label = "事实提取失败"
        message = "未能获得可准确定位到原文的事实项；未完成事实核查，不代表全文没有事实或事实正确。"
    elif status == "SUCCESS" and report and not report.get("claims") and coverage.get("status") == "complete":
        status_label = "未识别到可核查事实"
        message = "本次未进行搜索或证据核查；不代表全文事实正确。"
    elif status == "SUCCESS" and report.get("usage", {}).get("pages_fetched") == 0 and any(
        search_round.get("error_codes") for claim in report.get("claims", []) for search_round in claim.get("search_rounds", [])
    ):
        status_label = "核查受阻"
        message = "搜索或正文读取受阻，未取得可核对的正文；任务已结束，但不能形成可靠结论。"
    elif status == "SUCCESS" and coverage.get("status") == "partial":
        status_label = "核查不完整"
    error = f"<p>错误代码：{text(data['error_code'])}</p>" if data.get("error_code") else ""
    return ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            f"<title>{text(data['title'])} · 事实核查报告</title>"
            "<style>body{max-width:980px;margin:40px auto;padding:24px;font:15px/1.8 serif;color:#23333d}"
            "h1{font-size:30px}h2{font-size:20px}section{border-top:1px solid #ccd5d9;padding-top:20px;margin-top:32px}"
            "article{border-left:3px solid #247f77;padding:8px 20px;margin:16px 0}blockquote{margin:16px 0;background:#f2f5f4;padding:16px}"
            "pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}p,li{overflow-wrap:anywhere}.small{font-size:11px;color:#52646b}"
            "@media print{body{margin:0;padding:0}details{display:block}article{break-inside:avoid}}</style>"
            f"<h1>{text(data['title'])}</h1><p>事实核查报告 #{text(data['id'])} · {text(report.get('checked_at'))}</p>"
            f"<p><strong>任务状态：{text(status_label)}</strong></p><p>{text(message)}</p>{error}"
            f"<p>来源：{text(data['source_kind'])} · 检索范围：{text(data['mode'])} · 深度：{text(data['depth'])}</p>"
            f"<p>模型：{text(data.get('model', {}).get('name'))} / {text(data.get('model', {}).get('model'))}</p>"
            f"<p>识别 {text(coverage.get('extracted', 0))} · 已检查 {text(coverage.get('checked', 0))} · 未检查 {text(coverage.get('unverified', 0))}</p>"
            f"<p>{text(data['limitations'])}</p><p>{text(coverage.get('reason', ''))}</p>"
            f"<details><summary>核查原文快照</summary><pre>{text(data['source_text'])}</pre></details>"
            f"{''.join(sections)}<p class='small'>原文指纹：{text(data['source_hash'])}</p></html>")
