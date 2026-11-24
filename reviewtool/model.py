"""资料模型与载入：按地块版本与资料可见日期组织复核数据。"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

MEASURES = ("封育", "封育加补植")
UNTREATED = "未治理"
INDICATOR = "地表覆盖"
SEASONS = ("春", "夏", "秋", "冬")


@dataclass(frozen=True)
class Config:
    """复核判定参数。"""

    renewal_min_effect_pp: float = 4.0  # 续建适用的最低相对成效（百分点）
    min_bridge_uncertainty_pp: float = 1.0  # 桥接换算不确定度下限（百分点）
    rain_tolerance_mm: float = 10.0  # 降雨共变不敏感阈值（毫米）


@dataclass(frozen=True)
class Plot:
    plot_id: str
    county: str
    area_ha: float
    measure: str
    start_year: int | None
    neighbor_exposure: bool
    boundary_version: int


@dataclass(frozen=True)
class GroundUnit:
    ground_id: str
    plot_id: str
    version: int
    valid_from: int
    valid_to: int
    terrain: str
    gully: str
    area_ha: float
    first_neighbor_year: int | None


@dataclass(frozen=True)
class Observation:
    observation_id: str
    ground_id: str
    year: int
    indicator: str
    value: float
    method: str
    sample_coverage: float
    received_at: date


@dataclass(frozen=True)
class Weather:
    zone: str
    year: int
    rain_mm: float
    dry_days: int


@dataclass(frozen=True)
class Review:
    review_id: str
    plot_id: str
    period: str
    contract_indicator: str
    status: str
    ground_ids: tuple[str, ...]
    baseline_year: int
    outcome_year: int
    min_effect_pp: float
    evidence_cutoff: date | None


@dataclass(frozen=True)
class Renewal:
    candidate_id: str
    ground_id: str
    measure: str
    terrain: str
    window: str
    crew_days: float
    cost_yuan: float
    status: str
    requires: str | None


@dataclass(frozen=True)
class Capacity:
    window: str
    crew_days: float
    budget_yuan: float


def window_key(window: str) -> tuple[int, int]:
    """作业窗口排序键，如 2026春 < 2026秋。"""
    for i, tag in enumerate(SEASONS):
        if window.endswith(tag) and window[:-1].isdigit():
            return (int(window[:-1]), i)
    return (9999, 9)


@dataclass
class Dataset:
    plots: dict[str, Plot]
    ground_units: list[GroundUnit]
    observations: list[Observation]
    weather: dict[tuple[str, int], Weather]
    reviews: list[Review]
    renewals: list[Renewal]
    capacity: dict[str, Capacity]
    as_of: date

    def view(self, cutoff: date | None) -> "Dataset":
        """取某一资料可见截止日的数据版本；历史签认按当时版本复查，不被当前改判覆盖。"""
        if cutoff is None:
            return self
        kept = [o for o in self.observations if o.received_at <= cutoff]
        return Dataset(self.plots, self.ground_units, kept, self.weather,
                       self.reviews, self.renewals, self.capacity, self.as_of)

    def units_by_ground(self) -> dict[str, list[GroundUnit]]:
        out: dict[str, list[GroundUnit]] = {}
        for u in self.ground_units:
            out.setdefault(u.ground_id, []).append(u)
        return out

    def observations_by_ground(self, indicator: str = INDICATOR) -> dict[str, dict[int, list[Observation]]]:
        out: dict[str, dict[int, list[Observation]]] = {}
        for o in self.observations:
            if o.indicator == indicator:
                out.setdefault(o.ground_id, {}).setdefault(o.year, []).append(o)
        return out

    def ground_attr(self, ground_id: str) -> GroundUnit:
        """地面编号的稳定属性（地貌 / 坡沟 / 面积，跨版本应一致）。"""
        rows = self.units_by_ground()[ground_id]
        return sorted(rows, key=lambda u: (u.valid_from, u.plot_id))[0]

    def plot_grounds_at(self, plot_id: str, year: int) -> list[str]:
        """某年度项目现行边界版本内的地面编号。"""
        return sorted(u.ground_id for u in self.ground_units
                      if u.plot_id == plot_id and u.valid_from <= year <= u.valid_to)


def _int_or_none(s: str) -> int | None:
    s = (s or "").strip()
    return int(s) if s else None


def _date_or_none(s: str) -> date | None:
    s = (s or "").strip()
    return date.fromisoformat(s) if s else None


def _read(data_dir: Path, name: str) -> list[dict]:
    with (data_dir / name).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_dataset(data_dir, as_of: date | None = None) -> Dataset:
    data_dir = Path(data_dir)
    as_of = as_of or date.today()
    plots = {}
    for r in _read(data_dir, "plots.csv"):
        p = Plot(r["plot_id"], r["county"], float(r["area_ha"]), r["measure"],
                 _int_or_none(r["start_year"]), r["neighbor_exposure"].strip() == "1",
                 int(r["boundary_version"]))
        plots[p.plot_id] = p
    units = [GroundUnit(r["ground_id"], r["plot_id"], int(r["version"]),
                        int(r["valid_from"]), int(r["valid_to"]), r["terrain"],
                        r["gully"], float(r["area_ha"]), _int_or_none(r["first_neighbor_year"]))
             for r in _read(data_dir, "ground_units.csv")]
    observations = [Observation(r["observation_id"], r["ground_id"], int(r["year"]),
                                r["indicator"], float(r["value"]), r["method"],
                                float(r["sample_coverage"]), date.fromisoformat(r["received_at"]))
                    for r in _read(data_dir, "observations.csv")]
    weather = {(r["zone"], int(r["year"])): Weather(r["zone"], int(r["year"]),
               float(r["rain_mm"]), int(r["dry_days"])) for r in _read(data_dir, "weather.csv")}
    reviews = [Review(r["review_id"], r["plot_id"], r["period"], r["contract_indicator"],
                      r["status"], tuple(x for x in r["ground_ids"].split("|") if x),
                      int(r["baseline_year"]), int(r["outcome_year"]),
                      float(r["min_effect_pp"]), _date_or_none(r["evidence_cutoff"]))
               for r in _read(data_dir, "reviews.csv")]
    renewals = [Renewal(r["candidate_id"], r["ground_id"], r["measure"], r["terrain"],
                        r["window"], float(r["crew_days"]), float(r["cost_yuan"]),
                        r["status"], (r["requires"].strip() or None))
                for r in _read(data_dir, "renewals.csv")]
    capacity = {r["window"]: Capacity(r["window"], float(r["crew_days"]), float(r["budget_yuan"]))
                for r in _read(data_dir, "capacity.csv")}
    return Dataset(plots=plots, ground_units=units, observations=observations,
                   weather=weather, reviews=reviews, renewals=renewals,
                   capacity=capacity, as_of=as_of)
