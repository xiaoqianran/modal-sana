from __future__ import annotations

from modal_sana.core.ledger import CostEvent, filter_events, paginate, period_rows, summarize


def _events() -> list[CostEvent]:
    return [
    CostEvent(
        id="evt_1_gpu_load",
        ts="2026-08-16T07:10:00+00:00",
        kind="gpu_load",
        job_id="job_alpha",
        model="sana-sprint-1.6b",
        actual_gpu="H100",
        cost_usd=0.01,
        gpu_seconds=18,
    ),
        CostEvent(
            id="evt_1_gpu_generate",
            ts="2026-08-16T07:10:05+00:00",
            kind="gpu_generate",
            model="sana-sprint-1.6b",
            actual_gpu="H100",
            cost_usd=0.002,
            gpu_seconds=3.6,
        ),
        CostEvent(
            id="evt_2_gpu_generate",
            ts="2026-08-15T07:10:05+00:00",
            kind="gpu_generate",
            model="sana-1.5-4.8b",
            actual_gpu="L40S",
            cost_usd=0.001,
            gpu_seconds=1,
        ),
    ]


def test_summarize_splits_load_and_generate() -> None:
    totals = summarize(_events())
    assert totals["load_count"] == 1
    assert totals["generate_count"] == 2
    assert abs(totals["load_cost_usd"] - 0.01) < 1e-12
    assert abs(totals["generate_cost_usd"] - 0.003) < 1e-12
    assert abs(totals["total_cost_usd"] - 0.013) < 1e-12


def test_period_rows_and_pagination() -> None:
    rows = period_rows(_events(), "day")
    assert [row["period"] for row in rows] == ["2026-08-16", "2026-08-15"]
    assert rows[0]["load_count"] == 1
    page = paginate(_events(), 1, 2)
    assert page["total"] == 3
    assert page["pages"] == 2
    assert len(page["items"]) == 2
    assert "formula" in page["items"][0]
    assert "chain" in page["items"][0]


def test_filter_by_kind_and_gpu() -> None:
    only_load = filter_events(_events(), period="all", kind="gpu_load")
    assert len(only_load) == 1
    only_h100 = filter_events(_events(), period="all", gpu="H100")
    assert {item.id for item in only_h100} == {"evt_1_gpu_load", "evt_1_gpu_generate"}


def test_filter_by_job_and_enrich_formula() -> None:
    from modal_sana.core.ledger import call_chain, enrich_event

    matched = filter_events(_events(), period="all", job_id="job_alpha")
    assert [item.id for item in matched] == ["evt_1_gpu_load"]
    payload = enrich_event(matched[0])
    assert payload["usd_per_second"] == 0.001097
    assert payload["billed_gpu"] == "H100"
    assert "× $0.001097/s" in payload["formula"]
    names = [step["kind"] for step in call_chain(matched[0])]
    assert "gpu_load" in names
    assert "job" in names
