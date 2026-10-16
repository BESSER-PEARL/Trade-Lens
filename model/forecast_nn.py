# SPDX-License-Identifier: MIT
"""B-UML neural network models of the trade forecaster (BESSER's NN metamodel), and their generation.

Two small networks forecast the next 12 months of a monthly trade series from its last 24 months.
The series is taken in logs and differenced over 12 months (the year-on-year growth, which removes
the season and the scale), standardised, and the network returns the next 12 year-on-year growths:
the forecast is the same month a year earlier times the forecast growth.

- ``mlp``: Linear(24 lags + 2 calendar features -> 32, ReLU) -> Dropout(0.1) -> Linear(32 -> 12)
- ``gru``: GRU(3 channels per month -> 16, last step) -> Linear(16 -> 12)

    python model/forecast_nn.py              # -> build/nn/ (stock BESSER code + the forecasters)
    python model/forecast_nn.py --install    # also refresh app/backend/forecasting/generated/

The stock ``PytorchGenerator`` and ``TFGenerator`` write the networks alone (build/nn/stock/); our
``TimeSeriesForecasterGenerator`` (besser_sdmx/generators/forecast_nn) wraps the same network code
in the forecasting pipeline: windowing, transforms, seeded training, prediction, backtest, CLI.
docs/forecasting_nn.md says what each part does and how it scored.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from besser.BUML.metamodel.nn import NN, Configuration, DropoutLayer, LinearLayer
from besser.BUML.metamodel.nn.neural_network import GRULayer

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from besser_sdmx.generators.forecast_nn import ForecastConfig, TimeSeriesForecasterGenerator  # noqa: E402

BUILD = ROOT / "build" / "nn"
INSTALLED = ROOT / "app" / "backend" / "forecasting" / "generated"
REGENERATE = "python model/forecast_nn.py --install"

WINDOW, HORIZON = 24, 12

# How a monthly series becomes training examples (shared by both networks).
PIPELINE = ForecastConfig(
    window=WINDOW, horizon=HORIZON, season=12, log=True, difference=12, calendar=True, standardize=True,
    seeds=(0, 1, 2),
    description="Monthly trade series: logs, 12-month difference (year-on-year growth), standardised.",
)


def mlp_model() -> NN:
    """A multilayer perceptron on the flat window: 24 lags plus the sine and cosine of the month."""
    model = NN(name="mlp")
    model.add_layer(LinearLayer(name="hidden", in_features=WINDOW + 2, out_features=32, actv_func="relu"))
    model.add_layer(DropoutLayer(name="dropout", rate=0.1))
    model.add_layer(LinearLayer(name="output", in_features=32, out_features=HORIZON))
    model.add_configuration(Configuration(batch_size=32, epochs=60, learning_rate=0.003, optimizer="adamW",
                                          loss_function="mse", metrics=["mae"], weight_decay=0.01))
    return model


def gru_model() -> NN:
    """A recurrent network: a GRU reads the 24 months (value, sine, cosine) and a linear layer
    turns its last state into the 12 forecasts."""
    model = NN(name="gru")
    model.add_layer(GRULayer(name="recurrent", input_size=3, hidden_size=16, return_type="last",
                             batch_first=True, output_var="state"))
    model.add_layer(LinearLayer(name="output", in_features=16, out_features=HORIZON, input_var="state"))
    model.add_configuration(Configuration(batch_size=32, epochs=60, learning_rate=0.003, optimizer="adamW",
                                          loss_function="mse", metrics=["mae"], weight_decay=0.01))
    return model


MODELS = {"mlp": mlp_model, "gru": gru_model}


def validate() -> dict[str, dict]:
    """BESSER's own validation of each model ({name: {success, errors, warnings}})."""
    return {name: build().validate(raise_exception=False) for name, build in MODELS.items()}


def generate(out: Path = BUILD, stock: bool = True) -> list[Path]:
    """Write the forecasters (and, with ``stock``, what the stock BESSER generators make of the same
    models) into ``out``. Returns the forecaster files."""
    written = []
    for name, build in MODELS.items():
        if stock:
            from besser.generators.nn.pytorch.pytorch_code_generator import PytorchGenerator
            from besser.generators.nn.tf.tf_code_generator import TFGenerator
            PytorchGenerator(build(), output_dir=str(out / "stock" / name)).generate()
            TFGenerator(build(), output_dir=str(out / "stock" / name)).generate()
        path = TimeSeriesForecasterGenerator(build(), PIPELINE, output_dir=str(out), regenerate=REGENERATE).generate()
        written.append(Path(path))
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the trade forecasters from their B-UML NN models")
    parser.add_argument("--out", default=str(BUILD), help="output folder (default: build/nn/)")
    parser.add_argument("--install", action="store_true",
                        help="also copy the forecasters into app/backend/forecasting/generated/")
    args = parser.parse_args()
    for name, result in validate().items():
        state = "valid" if result["success"] else "INVALID: " + "; ".join(result["errors"])
        print(f"B-UML model {name}: {state}" + (f" ({len(result['warnings'])} warnings)" if result["warnings"] else ""))
    for path in generate(Path(args.out)):
        print(f"generated {path}")
        if args.install:
            INSTALLED.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, INSTALLED / path.name)
            print(f"installed {INSTALLED / path.name}")


if __name__ == "__main__":
    main()
