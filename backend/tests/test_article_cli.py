import json
import stat
from types import SimpleNamespace

import pytest

from eval import articles


def sample():
    return {'id': 'example', 'text': '方案已经完膳。', 'domain': 'general', 'gold_status': 'candidate',
            'complete_gold': False, 'targets': [{'start': 4, 'end': 6, 'original': '完膳',
            'category': 'typo', 'expectation': 'report', 'accepted_suggestions': ['完善']}]}


@pytest.mark.parametrize('extra', [[], ['--config-id', '0'], ['--config-id', '1', '--rounds', '0'],
                                  ['--config-id', '1', '--timeout', 'nan'],
                                  ['--config-id', '1', '--depth', 'deep', 'deep']])
def test_cli_rejects_invalid_run_parameters(extra):
    with pytest.raises(SystemExit):
        articles.parse_args(['--input', 'input.json', '--output', 'result.json', *extra])


def test_rescore_does_not_require_model():
    args = articles.parse_args(['--input', 'input.json', '--output', 'result.json', '--rescore', 'old.json'])
    assert args.config_id is None


def saved_report(samples=None, rounds=1):
    samples = samples or [sample()]
    result = {'issues': [], 'coverage': {'status': 'complete', 'failed_chunks': []}}
    report = {'schema_version': 1, 'capture_input_sha256': articles.fingerprint(samples),
              'sample_fingerprints': {item['id']: articles.sample_fingerprint(item) for item in samples},
              'planned_runs': [{'sample_id': item['id'], 'depth': 'standard', 'round': number}
                               for item in samples for number in range(1, rounds + 1)],
              'runs': [{'sample_id': item['id'], 'depth': 'standard', 'round': 1, 'status': 'complete',
                        'elapsed_seconds': 1, 'result': result, 'score': articles.score_article(item, result)}
                       for item in samples]}
    articles.update_summary(report)
    return report


def test_cli_preserves_existing_output(tmp_path):
    source, output, previous = [tmp_path / name for name in ('input.json', 'result.json', 'previous.json')]
    source.write_text(json.dumps([sample()]))
    output.write_text('do not overwrite')
    previous.write_text(json.dumps(saved_report()))
    assert articles.main(['--input', str(source), '--output', str(output), '--rescore', str(previous)]) == 1
    assert output.read_text() == 'do not overwrite'


def test_cli_rescore_is_offline_and_private(tmp_path, monkeypatch):
    source, output, previous = [tmp_path / name for name in ('input.json', 'result.json', 'previous.json')]
    source.write_text(json.dumps([sample()]))
    report = saved_report()
    report['runs'][0].update(status='timeout', elapsed_seconds=45, score=None)
    previous.write_text(json.dumps(report))
    monkeypatch.setattr(articles, 'capture_runs', lambda *args: pytest.fail('offline must not call models'))
    assert articles.main(['--input', str(source), '--output', str(output), '--rescore', str(previous)]) == 1
    report = json.loads(output.read_text())
    assert report['runs'][0]['score']['status'] == 'error'
    assert report['runs'][0]['gold_status'] == 'candidate'
    group = report['summary']['groups'][0]
    assert group['gold_status'] == 'candidate'
    assert group['completion_rate'] == 0
    assert group['report']['total'] == 1
    assert group['report']['evaluated'] == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o600


@pytest.mark.parametrize('content,expected_status', [('[]', 'complete'), ('invalid self-check', 'error')])
async def test_capture_pins_config_and_restores_preparation(monkeypatch, content, expected_status):
    from app.services import proofread

    calls = []
    class Provider:
        config_id = 13
        model = 'test-model'
        default_temperature = 0.3
        api_base = 'https://example.com/v1'
        _endpoints = ['/chat/completions']

        async def chat(self, *args, **kwargs):
            calls.append(self.max_retries)
            return SimpleNamespace(finish_reason='stop', content=content)

        async def close(self):
            pass

    async def prepare(*args):
        return ({}, {}, 'rules'), Provider()

    async def run(text, domain, config_id, depth):
        _, provider = await proofread._gather_preparation(None, domain, config_id)
        await provider.chat([])
        return {'issues': [], 'coverage': {'status': 'complete', 'failed_chunks': []}}

    monkeypatch.setattr(proofread, '_gather_preparation', prepare)
    monkeypatch.setattr(proofread, 'proofread_text', run)
    args = SimpleNamespace(config_id=13, rounds=1, depth=['standard'], timeout=5, concurrency=1)
    report = await articles.capture_runs([sample()], args, lambda result: None)
    assert calls == [1]
    assert report['runs'][0]['status'] == expected_status
    assert report['runs'][0]['configuration']['model'] == 'test-model'
    assert report['runs'][0]['calls'][0]['content'] == content
    assert report['runs'][0]['calls'][0]['parse_valid'] == (expected_status == 'complete')
    assert report['collection_complete'] is True
    assert 'api_key' not in json.dumps(report)
    assert proofread._gather_preparation is prepare


def test_rescore_rejects_changed_article_but_allows_annotation_changes():
    report = saved_report()
    changed = sample()
    changed['text'] += '新增正文'
    with pytest.raises(ValueError, match='article_text_or_domain_changed'):
        articles.rescore([changed], report)
    changed = sample()
    changed['gold_status'] = 'confirmed'
    source_hash = articles.fingerprint(report)
    output = articles.rescore([changed], report)
    assert output['source_report_sha256'] == source_hash
    assert output['annotation_sha256'] == articles.fingerprint([changed])
    assert output['capture_input_sha256'] == articles.fingerprint([sample()])
    assert output['summary']['groups'][0]['gold_status'] == 'confirmed'


def test_rescore_failed_sample_stays_in_known_gold_group():
    samples = [sample(), {**sample(), 'id': 'failed'}]
    report = saved_report(samples)
    report['runs'][1].update(status='error', score=None)
    result = articles.rescore(samples, report)
    assert len(result['summary']['groups']) == 1
    group = result['summary']['groups'][0]
    assert group['completion_rate'] == 0.5
    assert group['report']['total'] == 2
    assert group['report']['unevaluated'] == 1


@pytest.mark.parametrize('mutation', ['missing', 'empty', 'failed_result', 'chunk_mismatch'])
def test_cli_does_not_succeed_for_incomplete_reports(tmp_path, mutation):
    source, output, previous = [tmp_path / name for name in ('input.json', 'result.json', 'previous.json')]
    source.write_text(json.dumps([sample()]))
    report = saved_report(rounds=3 if mutation == 'missing' else 1)
    if mutation == 'empty':
        report['runs'] = []
    elif mutation == 'failed_result':
        report['runs'][0]['result']['success'] = False
    elif mutation == 'chunk_mismatch':
        report['runs'][0]['result']['coverage'].update(total_chunks=2, completed_chunks=1)
    previous.write_text(json.dumps(report))
    assert articles.main(['--input', str(source), '--output', str(output), '--rescore', str(previous)]) == 1
    result = json.loads(output.read_text())
    assert result['summary']['planned_completion_rate'] < 1


def test_cli_subset_rescore_filters_planned_and_completed_runs(tmp_path):
    samples = [sample(), {**sample(), 'id': 'second'}]
    source, output, previous = [tmp_path / name for name in ('input.json', 'result.json', 'previous.json')]
    source.write_text(json.dumps(samples))
    previous.write_text(json.dumps(saved_report(samples)))
    assert articles.main(['--input', str(source), '--output', str(output), '--rescore', str(previous),
                          '--sample-ids', 'second']) == 0
    result = json.loads(output.read_text())
    assert [run['sample_id'] for run in result['runs']] == ['second']
    assert [run['sample_id'] for run in result['planned_runs']] == ['second']
    assert result['summary']['expected_runs'] == 1


def test_empty_input_never_starts_capture(tmp_path, monkeypatch):
    source, output = tmp_path / 'input.json', tmp_path / 'result.json'
    source.write_text('[]')
    monkeypatch.setattr(articles, 'capture_runs', lambda *args: pytest.fail('empty input must not call models'))
    assert articles.main(['--input', str(source), '--output', str(output), '--config-id', '1']) == 1
    assert not output.exists()


async def test_capture_failure_retains_gold_and_logger_state(monkeypatch):
    from loguru import logger

    from app.services import proofread

    async def fail(*args, **kwargs):
        raise TimeoutError

    monkeypatch.setattr(proofread, 'proofread_text', fail)
    original_prepare = proofread._gather_preparation
    args = SimpleNamespace(config_id=13, rounds=1, depth=['standard'], timeout=5, concurrency=1)
    report = await articles.capture_runs([sample()], args, lambda result: None)
    assert report['runs'][0]['status'] == 'timeout'
    assert report['summary']['groups'][0]['report']['total'] == 1
    assert proofread._gather_preparation is original_prepare
    messages = []
    sink = logger.add(lambda message: messages.append(str(message)))
    try:
        proofread.logger.info('evaluation logger restoration probe')
    finally:
        logger.remove(sink)
    assert any('evaluation logger restoration probe' in message for message in messages)
