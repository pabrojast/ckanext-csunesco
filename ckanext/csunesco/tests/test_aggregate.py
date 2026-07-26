# encoding: utf-8
"""Unit tests for ``logic/aggregate.py`` -- pure, NO CKAN, NO database.

This module imports nothing but the stdlib and the module under test, so it
runs with a bare ``pytest`` outside the container as well as inside it.

Several assertions run against ``fixtures/ofform_form3.json``: a real, trimmed
capture of the CS Toolbox form 3 ("Physical-chemical water quality") from the
dev portal -- 200 observations spread over 2024-03..2026-06, 26 sites, the real
field schema. Aggregation bugs that only show up on real-world shapes (sparse
months, a site column with 26 values, pH outliers) are exactly the ones a
synthetic fixture would miss.
"""
import datetime
import json
import os

from ckanext.csunesco.logic import aggregate as ag

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures',
                       'ofform_form3.json')


def _fixture():
    with open(FIXTURE, 'r') as fh:
        return json.load(fh)


def _row(date, **answers):
    return {'id': 1, 'date': date, 'lat': None, 'lng': None,
            'source': 'native', 'answers': answers}


# --------------------------------------------------------------------------- #
# Value coercion                                                              #
# --------------------------------------------------------------------------- #

def test_to_number_accepts_numeric_strings_and_rejects_booleans():
    assert ag.to_number(7.2) == 7.2
    assert ag.to_number('7.2') == 7.2
    assert ag.to_number('  8 ') == 8.0
    assert ag.to_number('') is None
    assert ag.to_number('abc') is None
    assert ag.to_number(None) is None
    # A checkbox is not a measurement, even though Python would call it 1.0.
    assert ag.to_number(True) is None
    assert ag.to_number(False) is None


def test_category_value_normalizes_booleans_and_drops_blanks():
    assert ag.category_value('Site 1') == 'Site 1'
    assert ag.category_value(True) == 'true'
    assert ag.category_value(False) == 'false'
    assert ag.category_value('   ') is None
    assert ag.category_value(3) is None
    assert ag.category_value(None) is None


# --------------------------------------------------------------------------- #
# Dates                                                                       #
# --------------------------------------------------------------------------- #

def test_parse_iso_day_tolerates_time_zone_and_offsets():
    day = datetime.date(2026, 7, 20)
    assert ag.parse_iso_day('2026-07-20') == day
    assert ag.parse_iso_day('2026-07-20T14:33:00') == day
    assert ag.parse_iso_day('2026-07-20T14:33:00Z') == day
    assert ag.parse_iso_day('2026-07-20T14:33:00+02:00') == day
    assert ag.parse_iso_day('20/07/2026') is None
    assert ag.parse_iso_day('2026-13-40') is None
    assert ag.parse_iso_day(None) is None


def test_date_span_ignores_unparseable_rows():
    rows = [_row('2025-05-02'), _row('bad'), _row('2024-01-31')]
    assert ag.date_span(rows) == (datetime.date(2024, 1, 31),
                                  datetime.date(2025, 5, 2))
    assert ag.date_span([]) == (None, None)


def test_filter_rows_by_date_is_inclusive_and_drops_undated_rows():
    rows = [_row('2025-01-01'), _row('2025-06-30'), _row('2025-12-31'),
            _row('nonsense')]
    kept = ag.filter_rows_by_date(rows, datetime.date(2025, 1, 1),
                                  datetime.date(2025, 6, 30))
    assert [r['date'] for r in kept] == ['2025-01-01', '2025-06-30']
    # With no bounds nothing is filtered -- undated rows survive untouched.
    assert len(ag.filter_rows_by_date(rows)) == 4


def test_preset_start_uses_the_supplied_now():
    now = datetime.date(2026, 7, 24)
    assert ag.preset_start('30d', now) == datetime.date(2026, 6, 24)
    assert ag.preset_start('1y', now) == datetime.date(2025, 7, 24)
    assert ag.preset_start('all', now) is None
    assert ag.preset_start('', now) is None
    assert ag.preset_start('nope', now) is None


# --------------------------------------------------------------------------- #
# Bucketing                                                                   #
# --------------------------------------------------------------------------- #

def test_bucket_key_is_sortable_and_locale_free():
    day = datetime.date(2026, 7, 20)
    assert ag.bucket_key(day, 'day') == '2026-07-20'
    assert ag.bucket_key(day, 'month') == '2026-07'
    assert ag.bucket_key(day, 'year') == '2026'


def test_bucket_key_uses_iso_weeks_across_the_year_boundary():
    # 2026-01-01 is a Thursday, so ISO puts it in week 1 of 2026...
    assert ag.bucket_key(datetime.date(2026, 1, 1), 'week') == '2026-W01'
    # ...while 2027-01-01 (a Friday) still belongs to 2026's week 53. Naive
    # "year + week number" formatting gets this wrong and sorts it first.
    assert ag.bucket_key(datetime.date(2027, 1, 1), 'week') == '2026-W53'
    assert ag.bucket_key(datetime.date(2026, 12, 31), 'week') == '2026-W53'


def test_choose_bucket_picks_finest_readable_granularity():
    start = datetime.date(2025, 1, 1)
    assert ag.choose_bucket(start, start + datetime.timedelta(days=45)) == 'day'
    assert ag.choose_bucket(start, start + datetime.timedelta(days=90)) == 'day'
    assert ag.choose_bucket(start, start + datetime.timedelta(days=400)) == 'week'
    assert ag.choose_bucket(start, start + datetime.timedelta(days=730)) == 'week'
    assert ag.choose_bucket(start, start + datetime.timedelta(days=1825)) == 'month'
    assert ag.choose_bucket(None, None) == 'day'


def test_choose_bucket_coarsens_until_the_axis_fits():
    start = datetime.date(2000, 1, 1)
    # 60 years of months is 720 labels -- past the ceiling, so it must fall
    # through to the year overflow step rather than emit an unusable axis.
    assert ag.choose_bucket(start, datetime.date(2060, 1, 1),
                            max_labels=400) == 'year'
    # A tight ceiling coarsens even a short span, one step at a time: 60 days
    # is 61 daily labels, 10 weekly ones, 3 monthly ones.
    assert ag.choose_bucket(start, datetime.date(2000, 3, 1),
                            max_labels=10) == 'week'
    assert ag.choose_bucket(start, datetime.date(2000, 3, 1),
                            max_labels=5) == 'month'


def test_bucket_labels_are_dense_so_gaps_stay_visible():
    labels = ag.bucket_labels(datetime.date(2025, 1, 15),
                              datetime.date(2025, 4, 2), 'month')
    # March has no observation in the data below, but it MUST still get a
    # label: a missing month is information, and dropping it would draw a
    # straight line across the gap as if the value had held steady.
    assert labels == ['2025-01', '2025-02', '2025-03', '2025-04']


def test_bucket_labels_cover_day_and_week_without_gaps():
    days = ag.bucket_labels(datetime.date(2025, 1, 1),
                            datetime.date(2025, 1, 5), 'day')
    assert days == ['2025-01-01', '2025-01-02', '2025-01-03',
                    '2025-01-04', '2025-01-05']
    weeks = ag.bucket_labels(datetime.date(2025, 1, 1),
                             datetime.date(2025, 1, 20), 'week')
    assert weeks == ['2025-W01', '2025-W02', '2025-W03', '2025-W04']
    assert ag.bucket_labels(None, None, 'day') == []


# --------------------------------------------------------------------------- #
# Robust range                                                                #
# --------------------------------------------------------------------------- #

def test_robust_range_needs_enough_points():
    assert ag.robust_range([1, 2, 3, 4, 5, 6, 7]) == {}
    assert ag.robust_range([]) == {}


def test_robust_range_returns_nothing_for_clean_data():
    # Well-behaved pH readings never cross a fence -> let Chart.js auto-scale.
    assert ag.robust_range([7.0, 7.1, 7.2, 7.3, 6.9, 7.05, 7.15, 7.25]) == {}


def test_robust_range_clamps_an_absurd_outlier():
    # One bogus pH of 800 among sane readings would otherwise flatten the whole
    # series onto the bottom axis.
    values = [6.8, 7.0, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 800.0]
    out = ag.robust_range(values)
    assert 'max' in out and out['max'] < 100
    assert out['max'] >= 7.6


def test_robust_range_never_clamps_non_negative_data_below_zero():
    values = [0, 0, 0, 0, 1, 1, 1, 2, 900]
    out = ag.robust_range(values)
    assert out.get('min', 0) >= 0


def test_robust_range_ignores_a_zero_spread():
    assert ag.robust_range([5] * 20) == {}


# --------------------------------------------------------------------------- #
# Aggregations                                                                #
# --------------------------------------------------------------------------- #

def test_aggregate_numeric_means_and_aligns_to_labels():
    rows = [_row('2025-01-10', site='A', ph=7.0),
            _row('2025-01-20', site='A', ph=8.0),
            _row('2025-03-05', site='A', ph=6.0),
            _row('2025-01-11', site='B', ph=5.0)]
    labels = ag.bucket_labels(datetime.date(2025, 1, 10),
                              datetime.date(2025, 3, 5), 'month')
    out = ag.aggregate_numeric(rows, 'ph', 'month', labels, site_field='site')
    assert labels == ['2025-01', '2025-02', '2025-03']
    by_name = {s['name']: s for s in out['series']}
    # A is first: it has more samples, and slots are handed out largest-first.
    assert out['series'][0]['name'] == 'A'
    assert by_name['A']['points'] == [7.5, None, 6.0]
    assert by_name['A']['counts'] == [2, 0, 1]
    assert by_name['B']['points'] == [5.0, None, None]
    assert out['used_rows'] == 4
    for series in out['series']:
        assert len(series['points']) == len(labels)


def test_aggregate_numeric_honours_every_aggregation():
    rows = [_row('2025-01-01', ph=2.0), _row('2025-01-02', ph=4.0)]
    labels = ['2025-01']
    for agg, expected in (('mean', 3.0), ('min', 2.0), ('max', 4.0),
                          ('sum', 6.0), ('count', 2.0)):
        out = ag.aggregate_numeric(rows, 'ph', 'month', labels, agg=agg)
        assert out['series'][0]['points'] == [expected], agg
    # An unknown aggregation falls back to the mean rather than erroring.
    out = ag.aggregate_numeric(rows, 'ph', 'month', labels, agg='bogus')
    assert out['series'][0]['points'] == [3.0]


def test_aggregate_numeric_folds_rows_without_a_site_into_one_series():
    rows = [_row('2025-01-01', ph=7.0), _row('2025-01-02', ph=9.0)]
    out = ag.aggregate_numeric(rows, 'ph', 'month', ['2025-01'])
    assert len(out['series']) == 1
    assert out['series'][0]['name'] == ''
    assert out['series'][0]['points'] == [8.0]


def test_aggregate_numeric_caps_the_series_count():
    rows = [_row('2025-01-0%d' % (i + 1), site='S%d' % i, ph=float(i))
            for i in range(9)]
    out = ag.aggregate_numeric(rows, 'ph', 'month', ['2025-01'],
                               site_field='site', max_series=8)
    assert len(out['series']) == 8


def test_aggregate_counts_produces_one_dense_series():
    rows = [_row('2025-01-05'), _row('2025-01-06'), _row('2025-03-01')]
    labels = ['2025-01', '2025-02', '2025-03']
    out = ag.aggregate_counts(rows, 'month', labels)
    assert out['series'][0]['points'] == [2.0, 0.0, 1.0]
    assert out['used_rows'] == 3


def test_aggregate_categories_keeps_a_short_tail_intact():
    rows = ([_row('2025-01-01', weather='sunny')] * 3
            + [_row('2025-01-02', weather='cloudy')])
    out = ag.aggregate_categories(rows, 'weather', top_n=12)
    assert out['labels'] == ['sunny', 'cloudy']
    assert out['series'][0]['points'] == [3.0, 1.0]
    assert 'Other' not in out['labels']


def test_aggregate_categories_folds_the_long_tail_into_other():
    rows = []
    for i in range(40):
        # Value i appears (40 - i) times, so the ordering is unambiguous.
        rows += [_row('2025-01-01', kind='v%02d' % i)] * (40 - i)
    out = ag.aggregate_categories(rows, 'kind', top_n=12)
    assert len(out['labels']) == 12
    assert out['labels'][-1] == 'Other'
    # The fold must conserve the total: nothing may be silently dropped.
    assert sum(out['series'][0]['points']) == float(len(rows))
    assert out['used_rows'] == len(rows)


def test_round_series_shrinks_the_payload_without_losing_nulls():
    series = [{'name': 'A', 'points': [7.213333333333333, None], 'counts': [3, 0]}]
    out = ag.round_series(series, digits=4)
    assert out[0]['points'] == [7.2133, None]
    # The input is left alone -- callers may still need the raw values.
    assert series[0]['points'][0] == 7.213333333333333


# --------------------------------------------------------------------------- #
# Schema introspection                                                        #
# --------------------------------------------------------------------------- #

def test_value_counts_orders_by_frequency_then_value():
    rows = ([_row('2025-01-01', k='b')] * 2 + [_row('2025-01-01', k='a')] * 2
            + [_row('2025-01-01', k='c')])
    assert ag.value_counts(rows, 'k') == [('a', 2), ('b', 2), ('c', 1)]


def test_detect_site_field_prefers_the_canonical_names():
    schema = {'fields': [{'name': 'river', 'type': 'short_text'},
                         {'name': 'site', 'type': 'short_text'}]}
    rows = [_row('2025-01-01', river='R', site='S')]
    # 'site' outranks 'river' regardless of schema order.
    assert ag.detect_site_field(schema, rows) == 'site'


def test_detect_site_field_returns_none_without_a_candidate():
    schema = {'fields': [{'name': 'notes', 'type': 'long_text'}]}
    assert ag.detect_site_field(schema, [_row('2025-01-01', notes='x')]) is None


def test_categorical_field_options_respects_the_cardinality_band():
    schema = {'fields': [{'name': 'one', 'type': 'short_text'},
                         {'name': 'two', 'type': 'short_text'},
                         {'name': 'many', 'type': 'short_text'}]}
    rows = []
    for i in range(40):
        rows.append(_row('2025-01-01', one='same', two='a' if i % 2 else 'b',
                         many='v%d' % i))
    names = [f['name'] for f in ag.categorical_field_options(schema, rows)]
    assert names == ['two']  # 'one' has 1 value, 'many' has 40


# --------------------------------------------------------------------------- #
# Against the real form-3 capture                                             #
# --------------------------------------------------------------------------- #

def test_fixture_span_selects_month_buckets():
    data = _fixture()
    first, last = ag.date_span(data['rows'])
    assert (first, last) == (datetime.date(2024, 3, 13),
                             datetime.date(2026, 6, 15))
    # 824 days -- past the two-year week threshold.
    assert ag.choose_bucket(first, last) == 'month'
    assert len(ag.bucket_labels(first, last, 'month')) == 28


def test_fixture_detects_the_site_and_numeric_fields():
    data = _fixture()
    rows, schema = data['rows'], data['schema']
    assert ag.detect_site_field(schema, rows) == 'site'
    numeric = {f['name'] for f in ag.numeric_fields_with_data(schema, rows)}
    assert {'ph', 'ec', 'temperature'} <= numeric
    facets = {f['name'] for f in ag.categorical_field_options(schema, rows,
                                                              'site')}
    # 'river' has 17 values (inside the band); the site field is excluded.
    assert {'biosphere', 'weather', 'river'} == facets


def test_fixture_ph_by_site_is_capped_and_aligned():
    data = _fixture()
    rows, schema = data['rows'], data['schema']
    first, last = ag.date_span(rows)
    labels = ag.bucket_labels(first, last, 'month')
    out = ag.aggregate_numeric(rows, 'ph', 'month', labels, site_field='site')
    # The capture has 26 sites; the palette has 8 slots and never cycles.
    assert len(out['series']) == 8
    assert out['used_rows'] == 139
    for series in out['series']:
        assert len(series['points']) == len(labels) == 28
        assert len(series['counts']) == len(labels)
    counts = [sum(s['counts']) for s in out['series']]
    assert counts == sorted(counts, reverse=True)


def test_fixture_ph_outliers_trigger_a_clamped_axis():
    data = _fixture()
    rows = data['rows']
    values = [ag.to_number(r['answers'].get('ph')) for r in rows]
    out = ag.robust_range([v for v in values if v is not None])
    # The real capture contains readings well outside 0-14, so both fences fire.
    assert out['min'] >= 0
    assert 7 < out['max'] < 20


def test_fixture_weather_counts_need_no_other_bucket():
    data = _fixture()
    out = ag.aggregate_categories(data['rows'], 'weather', top_n=12)
    assert out['labels'][0] == 'sunny'
    assert len(out['labels']) == 6
    assert 'Other' not in out['labels']


def test_fixture_payload_stays_small_after_aggregation():
    """The whole point: 1.63 MB upstream must become a few KB downstream."""
    data = _fixture()
    rows = data['rows']
    first, last = ag.date_span(rows)
    labels = ag.bucket_labels(first, last, 'month')
    out = ag.aggregate_numeric(rows, 'ph', 'month', labels, site_field='site')
    payload = json.dumps({'labels': labels,
                          'series': ag.round_series(out['series'])})
    assert len(payload) < 8000


def test_detect_site_field_allows_many_sites():
    """A form with more monitoring sites than the FACET ceiling must still
    chart per site -- the series cap already bounds the lines drawn."""
    schema = {'fields': [{'name': 'site', 'type': 'short_text'}]}
    rows = [_row('2025-01-01', site='S%d' % i) for i in range(45)]
    assert ag.detect_site_field(schema, rows) == 'site'
    # It is still bounded: a free-text column is not a site column.
    huge = [_row('2025-01-01', site='S%d' % i)
            for i in range(ag.MAX_SITE_VALUES + 50)]
    assert ag.detect_site_field(schema, huge) is None


def test_categorical_options_keep_the_narrower_facet_ceiling():
    """Faceting stays capped at 30: a chip row must remain readable."""
    schema = {'fields': [{'name': 'kind', 'type': 'short_text'}]}
    rows = [_row('2025-01-01', kind='v%d' % i) for i in range(45)]
    assert ag.categorical_field_options(schema, rows) == []


# --------------------------------------------------------------------------- #
# Scalar aggregation (the shape most plain-language questions have)           #
# --------------------------------------------------------------------------- #

def test_aggregate_scalar_computes_an_overall_figure():
    rows = [_row('2025-01-01', ph=7.0), _row('2025-01-02', ph=8.0),
            _row('2025-01-03', ph=None), _row('2025-01-04', ph='9')]
    out = ag.aggregate_scalar(rows, 'ph', agg='mean')
    assert out['overall'] == 8.0
    assert out['used_rows'] == 3
    assert out['groups'] == []
    assert out['omitted_groups'] == 0
    assert sorted(out['values']) == [7.0, 8.0, 9.0]


def test_aggregate_scalar_honours_every_aggregation():
    rows = [_row('2025-01-01', ph=2.0), _row('2025-01-02', ph=6.0)]
    assert ag.aggregate_scalar(rows, 'ph', agg='min')['overall'] == 2.0
    assert ag.aggregate_scalar(rows, 'ph', agg='max')['overall'] == 6.0
    assert ag.aggregate_scalar(rows, 'ph', agg='sum')['overall'] == 8.0
    assert ag.aggregate_scalar(rows, 'ph', agg='count')['overall'] == 2.0
    # An unknown aggregation falls back rather than raising.
    assert ag.aggregate_scalar(rows, 'ph', agg='median')['overall'] == 4.0


def test_aggregate_scalar_groups_are_ordered_and_capped():
    rows = []
    for index in range(12):
        # Site 0 gets 12 readings, site 11 gets 1 -- so the order is knowable.
        rows += [_row('2025-01-01', ph=float(index), site='S%02d' % index)
                 for _ in range(12 - index)]
    out = ag.aggregate_scalar(rows, 'ph', agg='mean', group_by='site')
    assert [g['name'] for g in out['groups']][:2] == ['S00', 'S01']
    assert len(out['groups']) == ag.MAX_SERIES
    # The tail is REPORTED, not silently dropped: the caller has to be able to
    # say "and 4 more sites".
    assert out['omitted_groups'] == 4
    assert out['groups'][0]['count'] == 12


def test_aggregate_scalar_counts_ungrouped_rows_in_the_overall_only():
    """A reading with no site belongs to no site -- calling it "" would invent
    a monitoring site that does not exist."""
    rows = [_row('2025-01-01', ph=4.0, site='A'),
            _row('2025-01-02', ph=8.0)]
    out = ag.aggregate_scalar(rows, 'ph', agg='mean', group_by='site')
    assert out['overall'] == 6.0
    assert out['used_rows'] == 2
    assert out['groups'] == [{'name': 'A', 'value': 4.0, 'count': 1}]


def test_aggregate_scalar_on_the_real_fixture_matches_a_hand_count():
    data = _fixture()
    values = [ag.to_number((r.get('answers') or {}).get('ph'))
              for r in data['rows']]
    values = [v for v in values if v is not None]
    out = ag.aggregate_scalar(data['rows'], 'ph', agg='mean')
    assert out['used_rows'] == len(values)
    assert abs(out['overall'] - sum(values) / len(values)) < 1e-9


def test_aggregate_scalar_returns_none_when_nothing_matches():
    out = ag.aggregate_scalar([_row('2025-01-01', ph='n/a')], 'ph')
    assert out['overall'] is None
    assert out['used_rows'] == 0
    assert ag.aggregate_scalar(None, 'ph')['overall'] is None


def test_option_labels_maps_stored_values_to_form_labels():
    """A legend must read "Partly cloudy", not "partly_cloudy" -- but the
    aggregation stays keyed by the stored value, which is what is stable."""
    schema = {'fields': [{'name': 'weather', 'type': 'single_select',
                          'options': [{'value': 'partly_cloudy',
                                       'label': 'Partly cloudy'},
                                      {'value': 'sunny', 'label': 'Sunny'}]}]}
    assert ag.option_labels(schema, 'weather') == {
        'partly_cloudy': 'Partly cloudy', 'sunny': 'Sunny'}
    # A free-text field has no options, and an unknown field is not an error.
    assert ag.option_labels(schema, 'site') == {}
    assert ag.option_labels({}, 'weather') == {}


def test_option_labels_falls_back_to_the_value():
    schema = {'fields': [{'name': 'k', 'type': 'single_select',
                          'options': [{'value': 'a'}, {'label': 'no value'}]}]}
    assert ag.option_labels(schema, 'k') == {'a': 'a'}
