import importlib.util
import sys
from pathlib import Path


SIMULATOR_PATH = Path(__file__).resolve().parents[1] / "argocd" / "traffic-simulator" / "simulator.py"


def load_simulator():
    spec = importlib.util.spec_from_file_location("traffic_simulator", SIMULATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_config_reads_worker_load_shape(monkeypatch):
    sim = load_simulator()
    monkeypatch.setenv("TOTAL_USERS", "42")
    monkeypatch.setenv("TARGET_RPM", "500")
    monkeypatch.setenv("CONCURRENCY", "25")
    monkeypatch.setenv("RUN_DURATION_S", "900")
    monkeypatch.setenv("RAMP_UP_S", "120")
    monkeypatch.setenv("MODEL_MIX", "gpt-4o-mini=1")

    config = sim.Config.from_env()

    assert config.total_users == 42
    assert config.target_rpm == 500
    assert config.concurrency == 25
    assert config.run_duration_s == 900
    assert config.ramp_up_s == 120
    assert config.model_mix == [("gpt-4o-mini", 1.0)]


def test_metrics_summary_includes_latency_percentiles_and_status_counts():
    sim = load_simulator()
    metrics = sim.Metrics()

    metrics.record(status=200, latency_s=0.10, model="gpt-4o-mini")
    metrics.record(status=200, latency_s=0.20, model="gpt-4o-mini")
    metrics.record(status=429, latency_s=0.30, model="gpt-4o-mini")
    metrics.record(status=0, latency_s=60.0, model="gpt-4o-mini", timeout=True)

    summary = metrics.summary(elapsed_s=60.0)

    assert summary["requests"] == 4
    assert summary["successes"] == 2
    assert summary["errors"] == 2
    assert summary["timeouts"] == 1
    assert summary["status_counts"] == {0: 1, 200: 2, 429: 1}
    assert summary["latency_p50_s"] == 0.25
    assert summary["latency_p95_s"] == 60.0
    assert summary["latency_p99_s"] == 60.0


def test_request_builder_uses_gpt_only_model_mix(monkeypatch):
    sim = load_simulator()
    monkeypatch.setenv("MODEL_MIX", "gpt-4o-mini=1")
    monkeypatch.setenv("PROVIDER_FAIL_RATE", "0")
    monkeypatch.setenv("LONG_CONTEXT_RATE", "0")
    monkeypatch.setenv("DUPLICATE_RATE", "0")
    monkeypatch.setenv("SENSITIVE_RATE", "0")
    monkeypatch.setenv("EXPENSIVE_RATE", "0")
    config = sim.Config.from_env()
    user = {"id": "engineering-user-001", "team": "engineering"}

    payload = sim.build_payload(user, config)

    assert payload["model"] == "gpt-4o-mini"
    assert payload["user"] == "engineering-user-001"
    assert payload["metadata"]["team"] == "engineering"
