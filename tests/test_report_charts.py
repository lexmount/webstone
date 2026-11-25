"""Shared home-page charts keep exact values as text and draw marks that match them."""
import pytest
from bs4 import BeautifulSoup

from harness.reporting import charts


def render():
    return charts.charts(
        charts.ranked_bars([charts.Bar.rate("B", 1, 4), charts.Bar.rate("A", 3, 4, "a.html")],
                           title="Pass rate", caption="n/N is passed / tasks."),
        charts.dumbbell([charts.Pair("A", 0.9, 0.75), charts.Pair("B", 0.5, None)], title="Request vs task",
                        caption="Gap.", first_label="Request", second_label="Task"))


def test_ranked_bars_order_by_rate_and_keep_counts_as_text():
    soup = charts.check_charts(render(), required=True)
    rows = soup.select('[data-chart="ranked-bar"] li')
    assert [row.select_one(".chart-label").get_text() for row in rows] == ["A", "B"]
    assert rows[0].select_one(".chart-value").get_text() == "75.0% · 3/4"
    assert rows[0].select_one(".chart-fill")["style"] == "width:75.0%"
    assert rows[0].a["href"] == "a.html"
    assert len(soup.select("style")) == 1


def test_dumbbell_marks_both_rates_and_leaves_unavailable_out():
    soup = charts.check_charts(render())
    first, second = soup.select('[data-chart="dumbbell"] li')
    assert [m["style"] for m in first.select("[data-chart-mark]")] == ["left:90.0%", "left:75.0%"]
    assert "Request 90.0% → Task 75.0%" in first.get_text(" ", strip=True).replace("  ", " ")
    assert len(second.select("[data-chart-mark]")) == 1 and "—" in second.select_one(".chart-value").get_text()


@pytest.mark.parametrize("mutate, message", [
    (lambda s: s.select_one(".chart-fill").__setitem__("style", "width:10.0%"), "differs from its value"),
    (lambda s: s.select_one('[data-chart="ranked-bar"] .chart-value').string.replace_with("0.0% · 3/4"), "text differs"),
    (lambda s: s.select_one('[data-chart="ranked-bar"] ol').append(s.select_one('[data-chart="ranked-bar"] li').extract()), "rank order"),
    (lambda s: s.select_one('[data-chart="dumbbell"] [data-chart-mark]').__setitem__("style", "left:1%"), "differs from its value"),
    (lambda s: s.select_one(".chart-value").clear(), "exact value as text"),
    (lambda s: [m.decompose() for m in s.select('[data-chart="dumbbell"] li')[0].select("[data-chart-mark]")],
     "lacks its mark"),
    (lambda s: _swap_series_text(s.select('[data-chart="dumbbell"] li')[0]), "text differs"),
])
def test_chart_check_rejects_marks_text_or_order_that_disagree(mutate, message):
    soup = BeautifulSoup(render(), "html.parser")
    mutate(soup)
    with pytest.raises(ValueError, match=message):
        charts.check_charts(soup)


def _swap_series_text(row):
    first, second = row.select("[data-chart-series]")
    first.contents[-1].replace_with(" 75.0%")
    second.contents[-1].replace_with(" 90.0%")


def test_required_chart_missing_is_rejected():
    with pytest.raises(ValueError, match="lacks its comparison chart"):
        charts.check_charts("<main></main>", required=True)
    assert charts.charts() == ""


def test_marks_keep_full_precision_so_rounding_boundaries_still_check():
    figure = charts.charts(charts.ranked_bars([charts.Bar("A", 0.0025001)], title="F1", caption="Mean."),
                           charts.dumbbell([charts.Pair("A", 0.0025001, 0.99949999)], title="T", caption="C.",
                                           first_label="x", second_label="y"))
    soup = charts.check_charts(figure)
    assert soup.select_one(".chart-fill")["style"] == "width:0.3%"
