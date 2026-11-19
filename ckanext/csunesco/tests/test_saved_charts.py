"""Saved chart publication stays scoped to approved sources and current policy."""
from types import SimpleNamespace
import pytest
import ckan.plugins.toolkit as tk
from ckanext.csunesco.logic import blocks, chart_definition
from ckanext.csunesco.logic.action import data


@pytest.fixture
def sources(monkeypatch):
    rows = {key: SimpleNamespace(id=key, project_id='p', form_id=i, status='approved') for i,key in enumerate(['current','history'])}
    payloads = {
        'current': {'schema': {'fields': [{'name':'ph','type':'number'}]}, 'field_fingerprints':{'ph':'target'}, 'total':1,
                    'rows':[{'date':'2026-11-01','answers':{'ph':8}}]},
        'history': {'schema': {'fields': [{'name':'old_ph','type':'number'}]}, 'field_fingerprints':{'old_ph':'source'}, 'total':1,
                    'rows':[{'date':'2026-10-01','answers':{'old_ph':6}}]},
    }
    monkeypatch.setattr(tk,'check_access',lambda *a: None)
    monkeypatch.setattr(data.db,'get_data_source',lambda key:rows.get(key))
    monkeypatch.setattr(data.db,'get_project',lambda key:SimpleNamespace(id=key))
    monkeypatch.setattr(data.data_access,'require',lambda *a:None)
    monkeypatch.setattr(data.data_access,'dashboard',lambda source:payloads[source.id])
    return rows,payloads


def request():
    return {'id':'current','definition':{'field':'ph','include_history':True},'history_sources':[
        {'id':'history','mappings':{'old_ph':'ph'},'source_fingerprints':{'old_ph':'source'},'target_fingerprints':{'ph':'target'}}]}


def test_pinned_history_projects_compatible_values(sources):
    result=data.csunesco_data_source_series({},request())
    assert result['used_rows']==2
    assert [p['y'] for p in result['series'][0]['xy']]==[6,8]


@pytest.mark.parametrize('failure',['rejected','different_project','revoked','definition_changed','history_disabled','invalid_mapping'])
def test_history_never_drops_unavailable_or_changed_data_silently(sources,monkeypatch,failure):
    rows,payloads=sources; args=request()
    if failure=='rejected': rows['history'].status='rejected'
    if failure=='different_project': rows['history'].project_id='other'
    if failure=='revoked':
        def require(context,source):
            if source.id=='history': raise tk.ObjectNotFound('unavailable')
        monkeypatch.setattr(data.data_access,'require',require)
    if failure=='definition_changed': payloads['history']['field_fingerprints']['old_ph']='changed'
    if failure=='history_disabled': args['definition']['include_history']=False
    if failure=='invalid_mapping': args['history_sources'][0]['source_fingerprints']=[]
    with pytest.raises((tk.ValidationError,tk.ObjectNotFound)):
        data.csunesco_data_source_series({},args)


def test_nested_placement_round_trip_preserves_full_definition():
    definition=chart_definition.normalize({'field':'ph','bucket':'raw','sites':['A','B'],'axes':{'yMin':3},
        'filters':{'facets':{'rain':['true']},'search':'River'},'include_history':True})
    chart=dict(id='chart123',type='chart',saved_chart_id=17,saved_chart_revision=3,definition=definition,
               history_sources=request()['history_sources'],data_source_id='current',field='ph')
    result=blocks.normalize_blocks([dict(id='data1234',type='builtin_data',charts=[chart])])
    normalized=result[0]['charts'][0]
    assert normalized['definition']==definition
    assert normalized['saved_chart_id']==17
    assert normalized['saved_chart_revision']==3
    assert normalized['history_sources']==chart['history_sources']
    assert blocks.normalize_blocks(result)==result


@pytest.mark.parametrize('definition',[None,[],{'field':'x','sites':{}},{'field':'x','axes':{'xMin':float('nan')}}])
def test_invalid_saved_definition_does_not_crash_normalizer(definition):
    result=blocks.normalize_blocks([dict(type='chart',definition=definition)])
    assert 'definition' not in result[0]


@pytest.mark.parametrize('agg,expected',[('mean',3),('sum',6),('min',2),('max',4),('count',2)])
def test_numeric_calculations_keep_portal_aggregate_meaning(agg,expected):
    payload={'schema':{'fields':[{'name':'value','type':'number'}]},'rows':[
        {'date':'2026-11-01','answers':{'value':2}}, {'date':'2026-11-02','answers':{'value':4}}]}
    result=chart_definition.series({'field':'value','agg':agg,'bucket':'month'},payload)
    assert result['series'][0]['xy']==[{'x':'2026-11-01','y':expected}]


def test_invalid_v2_read_never_degrades_to_legacy_series():
    raw = dict(type='chart', field='ph', definition={'version': 2, 'field': 'ph', 'presentation': {'palette': 'unknown'}})
    result = blocks.normalize_blocks([raw])[0]
    assert result['definition'] == {'version': 2, 'invalid': True}
    with pytest.raises(ValueError):
        blocks.validate_chart_definitions([result])


@pytest.mark.parametrize('nested', [False, True])
def test_publication_rejects_invalid_new_chart_before_normalization(nested):
    from ckanext.csunesco.logic import portal
    chart = dict(type='chart', field='ph', definition={'version': 2, 'field': 'ph', 'presentation': {'legend': {'position': 'middle'}}})
    value = dict(type='builtin_data', charts=[chart]) if nested else chart
    with pytest.raises(tk.ValidationError):
        portal.validate_payload({'blocks': [value]})


def test_valid_v2_presentation_round_trips_in_nested_data():
    definition = chart_definition.normalize({'version': 2, 'field': 'ph', 'presentation': {
        'series': {'s:0123456789abcdef': {'color': '#0072B2', 'label': 'Upstream'}},
        'legend': {'display': 'show', 'position': 'right'},
        'referenceBands': [{'id': 'target', 'min': 4, 'max': 6, 'label': 'Reference supplied by project', 'color': '#0072B2'}]}})
    raw = [dict(type='builtin_data', charts=[dict(type='chart', definition=definition, field='ph')])]
    result = blocks.normalize_blocks(raw)
    assert result[0]['charts'][0]['definition'] == definition
    blocks.validate_chart_definitions(result)


def test_native_page_publication_rejects_invalid_stored_v2():
    from ckanext.csunesco.logic.action.page import _validate_policy
    raw = [dict(type='chart', definition={'version': 2, 'invalid': True})]
    with pytest.raises(tk.ValidationError):
        _validate_policy(blocks.normalize_blocks(raw))
