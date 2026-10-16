# SPDX-License-Identifier: MIT
"""The neural forecaster modelled in BESSER: the B-UML models (model/forecast_nn.py), our
TimeSeriesForecasterGenerator (besser_sdmx/generators/forecast_nn), the generated code and the
backend's adapter (app/backend/forecasting/nn.py). Synthetic series only: no database."""
import importlib.util
import sys
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "app" / "backend"
for p in (ROOT, ROOT / "model", BACKEND):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import forecast_nn  # noqa: E402  (model/forecast_nn.py)
from besser.BUML.metamodel.nn import NN, Configuration, LinearLayer  # noqa: E402

from besser_sdmx.generators.forecast_nn import ForecastConfig, TimeSeriesForecasterGenerator  # noqa: E402


def synthetic(n: int = 132, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    return 1e9 * np.exp(0.004 * t + 0.15 * np.sin(2 * np.pi * t / 12) - 0.2 * (t % 12 == 7)
                        + 0.03 * rng.standard_normal(n))


def load(path: Path):
    spec = importlib.util.spec_from_file_location(f"generated_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- the B-UML models
def test_the_buml_models_validate():
    for name, result in forecast_nn.validate().items():
        assert result["success"], (name, result["errors"])


def test_generation_writes_the_forecasters_and_the_committed_copy_is_up_to_date(tmp_path):
    paths = forecast_nn.generate(tmp_path, stock=False)
    assert sorted(p.name for p in paths) == ["forecaster_gru.py", "forecaster_mlp.py"]
    mlp = (tmp_path / "forecaster_mlp.py").read_text(encoding="utf-8")
    assert "class NeuralNetwork(nn.Module):" in mlp
    assert "self.hidden = nn.Linear(in_features=26, out_features=32, bias=True)" in mlp
    assert "self.dropout = nn.Dropout(p=0.1)" in mlp
    assert 'LAYOUT = "flat"' in mlp and "def fit(" in mlp and "def backtest(" in mlp
    gru = (tmp_path / "forecaster_gru.py").read_text(encoding="utf-8")
    assert "nn.GRU(input_size=3, hidden_size=16" in gru and 'LAYOUT = "sequence"' in gru
    for p in paths:
        compile(p.read_text(encoding="utf-8"), str(p), "exec")
        installed = forecast_nn.INSTALLED / p.name
        assert installed.read_text(encoding="utf-8") == p.read_text(encoding="utf-8"), (
            f"{installed} is out of date: run python model/forecast_nn.py --install")


def test_generation_is_deterministic(tmp_path):
    a = forecast_nn.generate(tmp_path / "a", stock=False)
    b = forecast_nn.generate(tmp_path / "b", stock=False)
    assert [p.read_bytes() for p in a] == [p.read_bytes() for p in b]


def test_the_stock_besser_generators_write_the_networks(tmp_path):
    forecast_nn.generate(tmp_path, stock=True)
    assert "nn.GRU(" in (tmp_path / "stock" / "gru" / "pytorch_nn_subclassing.py").read_text(encoding="utf-8")
    assert "layers.GRU(" in (tmp_path / "stock" / "gru" / "tf_nn_subclassing.py").read_text(encoding="utf-8")


def _net(in_features: int, out_features: int, loss: str = "mse") -> NN:
    model = NN(name="bad")
    model.add_layer(LinearLayer(name="a", in_features=in_features, out_features=8, actv_func="relu"))
    model.add_layer(LinearLayer(name="b", in_features=8, out_features=out_features))
    model.add_configuration(Configuration(batch_size=8, epochs=1, learning_rate=0.01, optimizer="adam",
                                          loss_function=loss, metrics=["mae"]))
    return model


@pytest.mark.parametrize("in_features, out_features, loss, message", [
    (10, 12, "mse", "in_features must be 26"),
    (26, 6, "mse", "the horizon is 12"),
    (26, 12, "crossentropy", "'mse' loss"),
])
def test_the_generator_rejects_a_network_that_does_not_fit_the_pipeline(tmp_path, in_features, out_features,
                                                                        loss, message):
    with pytest.raises(ValueError, match=message):
        TimeSeriesForecasterGenerator(_net(in_features, out_features, loss), ForecastConfig(window=24, horizon=12),
                                      output_dir=str(tmp_path))


def test_forecast_config_is_checked():
    with pytest.raises(ValueError, match="window"):
        ForecastConfig(window=0, horizon=12).validate()


# --------------------------------------------------------------------------- the generated code
needs_torch = pytest.mark.skipif(importlib.util.find_spec("torch") is None, reason="torch is not installed")
INSTALLED_MLP = forecast_nn.INSTALLED / "forecaster_mlp.py"
INSTALLED_GRU = forecast_nn.INSTALLED / "forecaster_gru.py"


@needs_torch
def test_generated_mlp_trains_and_predicts_deterministically(tmp_path):
    fc = load(INSTALLED_MLP)
    values = synthetic()
    a = fc.fit(values).predict(values, 15)
    b = fc.fit(values).predict(values, 15)
    assert a.shape == (15,) and np.isfinite(a).all() and (a > 0).all()
    assert np.array_equal(a, b)
    assert 0.5 * values[-12:].min() < a.mean() < 2 * values[-12:].max()
    # the global random state is not touched
    import torch
    torch.manual_seed(123)
    before = torch.rand(1)
    torch.manual_seed(123)
    fc.fit(values, seeds=[0], epochs=1)
    assert torch.equal(torch.rand(1), before)
    # save and load give the same forecast
    f = fc.fit(values, seeds=[0], epochs=2)
    fc.save(f, str(tmp_path / "m.pt"))
    assert np.array_equal(fc.load(str(tmp_path / "m.pt")).predict(values), f.predict(values))


@needs_torch
def test_generated_gru_trains_on_several_series():
    fc = load(INSTALLED_GRU)
    series = [synthetic(80, seed) for seed in (1, 2, 3)]
    out = fc.fit(series, starts=[0, 3, 6], seeds=[0], epochs=2).predict(series[0], 12)
    assert out.shape == (12,) and np.isfinite(out).all()


@needs_torch
def test_generated_backtest_and_command_line(tmp_path, capsys):
    fc = load(INSTALLED_MLP)
    values = synthetic(96)
    forecasts = fc.backtest(values, [80, 83], seeds=[0])
    scores = fc.mase(values, forecasts)
    assert set(forecasts) == {80, 83} and 1 in scores and all(np.isfinite(list(scores.values())))
    csv = tmp_path / "series.csv"
    csv.write_text("period,value\n" + "\n".join(f"{2010 + i // 12}-{i % 12 + 1:02d},{v}"
                                                   for i, v in enumerate(values)), encoding="utf-8")
    fc.main([str(csv), "--steps", "3"])
    assert capsys.readouterr().out.count("t+") == 3


@needs_torch
def test_too_short_a_series_is_refused():
    fc = load(INSTALLED_MLP)
    with pytest.raises(ValueError, match="needs series of at least"):
        fc.fit(synthetic(30))


# --------------------------------------------------------------------------- the backend adapter
@pytest.fixture
def backend(tmp_path, monkeypatch):
    from forecasting import models, nn, pool, service
    monkeypatch.setattr(pool, "CACHE_DIR", tmp_path / "cache")
    saved = pool._loader
    pool.set_loader(None)                    # no pool: the network trains on the series alone
    nn._trained.clear()
    service._MEM.clear()
    yield models, nn, service
    nn._trained.clear()
    service._MEM.clear()
    pool.set_loader(saved)


@needs_torch
def test_adapter_is_a_method_of_the_service(backend):
    models, nn, service = backend
    assert "nn" in models.METHODS and "nn" in service.METHODS
    assert service.resolve("nn") == "nn"
    assert "BESSER" in service.label("nn") and "this series" in service.label("nn")
    assert "nn" not in models.ENSEMBLE          # off by default: it does not help (docs/forecasting_nn.md)


@needs_torch
def test_adapter_trains_and_predicts_deterministically(backend):
    models, nn, service = backend
    values = synthetic()
    z, log = models.transform(values)
    a = nn.forecast(z, 12, "2010-01")
    nn._trained.clear()
    for f in nn.pool.CACHE_DIR.glob("nn_*.pt"):
        f.unlink()                           # train again, from scratch
    b = nn.forecast(z, 12, "2010-01")
    assert np.array_equal(a, b) and np.isfinite(a).all()
    # on the log scale, near the same month a year earlier
    assert np.abs(a - z[-12:]).max() < 0.5
    # a second call reads the trained network from the cache (memory, then disk)
    nn._trained.clear()
    assert np.array_equal(nn.forecast(z, 12, "2010-01"), a)


@needs_torch
def test_service_forecast_with_nn_has_a_backtest(backend):
    models, nn, service = backend
    values = synthetic(120)
    periods = [models.period_of(models.month_index("2010-01") + i) for i in range(len(values))]
    out = service.forecast(values, periods, "nn", 12, 0.8, table=("snaive", "nn"))
    assert out["point"].shape == (12,) and np.isfinite(out["point"]).all()
    assert (out["lo"] <= out["point"]).all() and (out["point"] <= out["hi"]).all()
    rows = {m["method"]: m for m in out["backtest"]["methods"]}
    assert set(rows) == {"snaive", "nn"} and rows["nn"]["mase_h1"] is not None
    assert out["backtest"]["summary"].startswith("The neural network generated by BESSER")
