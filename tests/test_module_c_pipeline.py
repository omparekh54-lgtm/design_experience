from packages.pms.module_c_pipeline import _percentile_map


def test_percentile_map_higher_is_better_by_default():
    scores = _percentile_map({1: 10.0, 2: 20.0, 3: 30.0})
    assert scores[3] == 100.0
    assert scores[2] == 50.0
    assert scores[1] == 0.0


def test_percentile_map_reverse_makes_smaller_better():
    scores = _percentile_map({1: 2.0, 2: 3.5, 3: 5.0}, reverse=True)
    assert scores[1] == 100.0
    assert scores[2] == 50.0
    assert scores[3] == 0.0


def test_percentile_map_averages_ties():
    scores = _percentile_map({1: 20.0, 2: 20.0, 3: 10.0})
    assert scores[1] == scores[2] == 75.0
    assert scores[3] == 0.0


def test_percentile_map_single_item_is_neutral():
    assert _percentile_map({7: 42.0}) == {7: 50.0}
