"""本地文章评测：原文、标注和逐条结果应留在私有目录，不随代码提交。"""
import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from eval.article_metrics import score_article, summarize_runs, validate_samples


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def sample_fingerprint(sample):
    return fingerprint({key: sample[key] for key in ('text', 'domain')})


def evaluator_fingerprint():
    return hashlib.sha256(Path(__file__).with_name('article_metrics.py').read_bytes()).hexdigest()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True, help='本地文章及候选/已确认标注 JSON')
    parser.add_argument('--output', type=Path, required=True, help='新的私有结果 JSON，不覆盖已有文件')
    parser.add_argument('--config-id', type=int, help='显式固定模型配置 ID；会产生模型费用')
    parser.add_argument('--depth', nargs='+', choices=['quick', 'standard', 'deep'], default=['standard'])
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--timeout', type=float, default=45, help='单篇端到端秒数，超时不计作零错误')
    parser.add_argument('--concurrency', type=int, default=1)
    parser.add_argument('--sample-ids', nargs='+')
    parser.add_argument('--rescore', type=Path, help='仅重新计分本工具已有结果，不调用模型')
    args = parser.parse_args(argv)
    if args.rescore is None and (args.config_id is None or args.config_id <= 0):
        parser.error('在线评测必须显式指定正整数 --config-id')
    if not 1 <= args.rounds <= 10 or not 1 <= args.concurrency <= 4:
        parser.error('rounds 必须为 1..10，concurrency 必须为 1..4')
    if not math.isfinite(args.timeout) or not 1 <= args.timeout <= 180:
        parser.error('timeout 必须为 1..180 秒')
    if len(args.depth) != len(set(args.depth)):
        parser.error('depth 不能重复')
    return args


def write_report(path, report):
    with path.open('w', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)


def update_summary(report):
    fields = ('sample_id', 'depth', 'round')
    planned = [tuple(run[key] for key in fields) for run in report['planned_runs']]
    actual = {tuple(run[key] for key in fields) for run in report['runs']}
    if not planned or len(set(planned)) != len(planned) or not actual <= set(planned):
        raise ValueError('invalid_run_plan')
    report['missing_runs'] = [dict(zip(fields, key)) for key in planned if key not in actual]
    report['collection_complete'] = not report['missing_runs']
    report['summary'] = summarize_runs(report['runs'])
    report['summary'].update(
        expected_runs=len(planned), missing_runs=len(report['missing_runs']),
        collection_complete=report['collection_complete'],
        planned_completion_rate=sum(run['status'] == 'complete' for run in report['runs']) / len(planned),
    )


def rescore(samples, report, *, selected=False):
    if not isinstance(report, dict) or report.get('schema_version') != 1:
        raise ValueError('unsupported_report')
    source_hash = fingerprint(report)
    by_id = {sample['id']: sample for sample in samples}
    captured = report['sample_fingerprints']
    if not isinstance(captured, dict) or not captured:
        raise ValueError('missing_sample_identity')
    if (selected and not by_id.keys() <= captured.keys()) or (not selected and not captured.keys() <= by_id.keys()):
        raise ValueError('sample_selection_mismatch')
    selected_ids = by_id.keys() & captured.keys()
    if any(captured[key] != sample_fingerprint(by_id[key]) for key in selected_ids):
        raise ValueError('article_text_or_domain_changed')
    update_summary(report)
    if any(run['sample_id'] not in captured for run in report['planned_runs']):
        raise ValueError('unknown_planned_sample')
    report['planned_runs'] = [run for run in report['planned_runs'] if run['sample_id'] in selected_ids]
    report['runs'] = [run for run in report['runs'] if run['sample_id'] in selected_ids]
    report['sample_fingerprints'] = {key: captured[key] for key in sorted(selected_ids)}
    for run in report['runs']:
        sample = by_id[run['sample_id']]
        run['gold_status'] = sample['gold_status']
        run['score'] = score_article(sample, run.get('result') if run['status'] == 'complete' else None)
        if run['status'] == 'complete' and run['score']['status'] != 'complete':
            run['status'] = 'error'
    report.update(source_report_sha256=source_hash, rescored_at=datetime.now(timezone.utc).isoformat(),
                  annotation_sha256=fingerprint([by_id[key] for key in sorted(selected_ids)]),
                  evaluator_sha256=evaluator_fingerprint())
    update_summary(report)
    return report


async def capture_runs(samples, args, on_update):
    import app.main  # noqa: F401
    from app.services import proofread
    from app.services.proofread import orchestrator

    trace = ContextVar('article_evaluation_trace')
    original_prepare = orchestrator._gather_preparation
    configurations = {}
    slots = asyncio.Semaphore(args.concurrency)
    service_dir = Path(proofread.__file__).parent.parent
    source_files = sorted((service_dir / 'proofread').glob('*.py')) + [
        service_dir / 'format_rules.py', service_dir / 'consistency.py',
    ]
    report = {
        'schema_version': 1,
        'created_at': datetime.now(timezone.utc).isoformat(),
        'capture_input_sha256': fingerprint(samples),
        'annotation_sha256': fingerprint(sorted(samples, key=lambda sample: sample['id'])),
        'sample_fingerprints': {sample['id']: sample_fingerprint(sample) for sample in samples},
        'evaluator_sha256': evaluator_fingerprint(),
        'config_id': args.config_id,
        'rounds': args.rounds,
        'depths': args.depth,
        'timeout_seconds': args.timeout,
        'concurrency': args.concurrency,
        'attempt_policy': 'single_attempt',
        'source_sha256': {path.relative_to(service_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in source_files},
        'planned_runs': [{'sample_id': sample['id'], 'depth': depth, 'round': round_number}
                         for round_number in range(1, args.rounds + 1)
                         for depth in args.depth for sample in samples],
        'runs': [],
    }
    update_summary(report)
    on_update(report)

    async def prepare(user_id, domain, config_id):
        data, provider = await original_prepare(user_id, domain, config_id)
        run = trace.get()
        snapshot = {
            'config_id': provider.config_id,
            'model': provider.model,
            'temperature': provider.default_temperature,
            'domain': domain,
            'endpoint_sha256': fingerprint(provider.api_base),
            'context_sha256': fingerprint(data),
            'prompt_sha256': fingerprint([proofread.PROOFREAD_SYSTEM_PROMPT, proofread.SELF_CHECK_PROMPT, data[2]]),
        }
        run['configuration'] = snapshot
        key = fingerprint(snapshot)
        if domain in configurations and configurations[domain] != key:
            await provider.close()
            raise ValueError('configuration_changed')
        configurations[domain] = key
        provider.max_retries = 1
        provider._endpoints = provider._endpoints[:1]
        provider.usage_business = 'evaluation'
        original_chat = provider.chat

        async def chat(*positional, **keywords):
            call = {'finish_reason': None, 'parse_valid': False}
            run['calls'].append(call)
            begin = time.perf_counter()
            try:
                response = await original_chat(*positional, **keywords)
                call.update(finish_reason=response.finish_reason, content=response.content)
                if response.finish_reason != 'stop':
                    raise ValueError('incomplete_response')
                try:
                    proofread.parse_proofread_result(response.content)
                    call['parse_valid'] = True
                except proofread.InvalidProofreadResponse:
                    pass
                return response
            finally:
                call['elapsed_seconds'] = round(time.perf_counter() - begin, 3)

        provider.chat = chat
        return data, provider

    async def one(sample, depth, round_number):
        async with slots:
            run = {'sample_id': sample['id'], 'gold_status': sample['gold_status'], 'depth': depth,
                   'round': round_number, 'calls': [], 'score': score_article(sample, None)}
            token = trace.set(run)
            begin = time.perf_counter()
            try:
                async with asyncio.timeout(args.timeout):
                    result = await proofread.proofread_text(
                        sample['text'], domain=sample['domain'], config_id=args.config_id, depth=depth,
                    )
                run['result'] = result
                run['score'] = score_article(sample, result)
                complete = (run['score']['status'] == 'complete'
                            and all(call['finish_reason'] == 'stop' and call['parse_valid'] for call in run['calls']))
                run['status'] = 'complete' if complete else 'error'
                if not complete:
                    run['score'] = score_article(sample, None)
            except TimeoutError:
                run['status'] = 'timeout'
            except Exception as exc:
                run.update(status='error', error_type=type(exc).__name__)
            finally:
                run['elapsed_seconds'] = round(time.perf_counter() - begin, 3)
                trace.reset(token)
            report['runs'].append(run)
            update_summary(report)
            on_update(report)
            print(f"{sample['id']} {depth} #{round_number}: {run['status']} {run['elapsed_seconds']}s", flush=True)

    orchestrator._gather_preparation = prepare
    try:
        await asyncio.gather(*(one(sample, depth, round_number)
                              for round_number in range(1, args.rounds + 1)
                              for depth in args.depth for sample in samples))
    finally:
        orchestrator._gather_preparation = original_prepare
    return report


def main(argv=None):
    args = parse_args(argv)
    try:
        samples = validate_samples(json.loads(args.input.read_text()))
        if not samples:
            raise ValueError('empty_samples')
        if args.sample_ids:
            requested = set(args.sample_ids)
            if len(requested) != len(args.sample_ids) or not requested <= {sample['id'] for sample in samples}:
                raise ValueError('unknown_or_duplicate_sample_ids')
            samples = [sample for sample in samples if sample['id'] in requested]
        report = None
        if args.rescore:
            report = rescore(samples, json.loads(args.rescore.read_text()), selected=bool(args.sample_ids))
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        if report is None:
            report = asyncio.run(capture_runs(samples, args, lambda value: write_report(args.output, value)))
        write_report(args.output, report)
    except (ValueError, KeyError, OSError, TypeError):
        print('评测失败：请检查输入标注、结果格式及输出路径（不能覆盖已有文件）。', file=sys.stderr)
        return 1
    return 0 if report['collection_complete'] and all(run['status'] == 'complete' for run in report['runs']) else 1


if __name__ == '__main__':
    from loguru import logger

    logger.disable('app')
    sys.exit(main())
