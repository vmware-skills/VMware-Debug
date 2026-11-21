"""Report projections preserve uncertainty, data and the legacy response."""
import asyncio
import json
import pytest
from vmware_debug.ops.cases.api import get_case
from vmware_debug.ops.cases.evidence import Evidence, Gap, record_evidence, record_gap
from vmware_debug.ops.cases.hypotheses import add_hypothesis
from vmware_debug.ops.cases.model import Scope
from vmware_debug.ops.cases.store import CaseError, case_dir, create_case

@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv('OPS_HOME', str(tmp_path))
    return create_case(Scope(summary='VM slow', determined_by='user report', objects=('vm-test',)),
                       at='2026-11-20T00:00:00Z').case_id

def snapshot(case):
    return {str(p): p.read_bytes() for p in case_dir(case).rglob('*') if p.is_file()}

def test_views_preserve_uncertainty_and_falsification_without_writes(case):
    h = add_hypothesis(case, 'CPU contention')
    for skill, summary, falsifies in [('vmware-monitor', 'CPU pressure', ()),
                                      ('vmware-aria', 'CPU pressure not corroborated', (h.hypothesis_id,))]:
        record_evidence(case, Evidence(source_skill=skill, source_tool='test',
                        query={'target': 'lab', 'password': 'test-secret'},
                        fetched_at='2026-11-20T00:01:00Z', summary=summary,
                        falsifies=falsifies), payload={'raw': 'raw-only-marker'})
    record_gap(case, Gap(what='memory samples', why='permission denied',
                        how_to_close='Obtain read access', blocks=(h.hypothesis_id,)))
    before = snapshot(case)
    legacy = get_case(case)
    assert 'report' not in legacy
    views = [get_case(case, v)['report'] for v in ('brief', 'operator', 'investigation')]
    for key in ('scope', 'grade', 'recorded_grade', 'evidence_ids', 'gaps', 'hypotheses', 'reasons'):
        assert views[0][key] == views[1][key] == views[2][key]
    assert views[0]['impact'] is None and views[0]['gaps'] and views[0]['hypotheses']
    assert views[0]['observations'] == []
    assert len(views[1]['observations']) == 2 and 'query' not in views[1]['observations'][0]
    assert any(e['falsifies'] == [h.hypothesis_id] for e in views[2]['observations'])
    assert 'test-secret' not in json.dumps(views) and 'raw-only-marker' not in json.dumps(views)
    assert snapshot(case) == before and get_case(case) == legacy

def test_empty_case_is_unmeasured_not_healthy(case):
    r = get_case(case, 'brief')['report']
    assert r['grade'] == 'candidate' and r['recorded_grade'] is None
    assert r['impact'] is None and r['evidence_ids'] == []

def test_invalid_view_is_rejected_without_mutation(case):
    before = snapshot(case)
    with pytest.raises(CaseError, match='report_view'):
        get_case(case, 'admin')
    assert snapshot(case) == before

def test_free_text_secret_and_controls(case):
    record_evidence(case, Evidence(source_skill='vmware-monitor', source_tool='get_events',
                    query={'pass\u202eword': 'key-secret'}, fetched_at='2026-11-20T00:01:00Z',
                    summary='pass\u202eword=synthetic-secret\x00'))
    text = json.dumps(get_case(case, 'operator')['report'])
    assert 'key-secret' not in text
    assert 'synthetic-secret' not in text and '\\u0000' not in text and '\\u202e' not in text

def test_mcp_optional_view_and_error_frame(case):
    from mcp.shared.memory import create_connected_server_and_client_session
    from vmware_debug.mcp_server.server import build_server
    from vmware_policy.serve import ServeOptions, configure
    server = build_server()
    configure(server, ServeOptions(skill='debug'))
    async def run():
        async with create_connected_server_and_client_session(server._mcp_server) as client:
            tool = next(t for t in (await client.list_tools()).tools if t.name == 'get_case')
            assert tool.annotations.readOnlyHint
            assert tool.inputSchema['properties']['report_view']['default'] is None
            for args in ({'case_id': case}, {'case_id': case, 'report_view': 'operator'}):
                result = await client.call_tool('get_case', args)
                assert not result.isError
                assert ('report' in json.loads(result.content[0].text)) == ('report_view' in args)
            assert (await client.call_tool('get_case', {'case_id': case, 'report_view': 'admin'})).isError
    asyncio.run(run())


def test_report_retains_each_hypothesis_verdict(case):
    first = add_hypothesis(case, 'First possibility remains open')
    second = add_hypothesis(case, 'Second possibility is refuted')
    for source in ('vmware-monitor', 'vmware-aria'):
        record_evidence(case, Evidence(source_skill=source, source_tool='test', query={},
                        fetched_at='2026-11-21T00:00:00Z', summary='Observed counter-evidence',
                        falsifies=(second.hypothesis_id,)))
    before = snapshot(case)
    for view in ('brief', 'operator', 'investigation'):
        r = get_case(case, view)['report']
        assert {h['id']: h['status'] for h in r['hypotheses']} == {
            first.hypothesis_id: 'open', second.hypothesis_id: 'excluded'}
        assert r['grade'] == 'candidate'
    assert snapshot(case) == before
