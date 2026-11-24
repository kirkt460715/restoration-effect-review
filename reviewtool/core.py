"""季度复核编排：共同地面、成效、验收、续建与补报影响清单。"""
from __future__ import annotations

from dataclasses import dataclass

from . import acceptance, effects, exposure, renewal
from .model import Config, Dataset
from .series import method_pair_offsets


@dataclass
class ReviewRun:
    dataset: Dataset
    config: Config
    common: exposure.CommonGround
    exposures: dict
    book: effects.EffectBook
    sensitivity: effects.Sensitivity
    reviews: tuple
    renewals: renewal.RenewalPlan
    supplementary: tuple  # 本期补报批次（最近到件日）
    impact: dict
    unsignable: tuple


def run_review(dataset: Dataset, config: Config | None = None) -> ReviewRun:
    config = config or Config()
    obs_by_ground = dataset.observations_by_ground()
    offsets = method_pair_offsets(obs_by_ground)
    exposures = exposure.build_exposures(dataset)
    common = exposure.common_ground(dataset)
    book = effects.build_effects(dataset, exposures, obs_by_ground, offsets, config)
    reviews = acceptance.evaluate_all(dataset, exposures, obs_by_ground, config)
    sens = effects.build_sensitivity(dataset, exposures, book, obs_by_ground,
                                     offsets, config)
    plan = renewal.assess_and_schedule(dataset, book, config)
    if dataset.observations:
        latest = max(o.received_at for o in dataset.observations)
        supplementary = tuple(o for o in dataset.observations if o.received_at == latest)
    else:
        supplementary = ()
    impact = {
        "需补证合同": [(o.review_id, list(o.supplements)) for o in reviews
                     if o.snapshot is None and o.supplements],
        "现可排续建组合": [(w.window, list(w.signed)) for w in plan.windows if w.signed],
        "已验收维持但需人工复议": [o.review_id for o in reviews
                             if o.classification == "维持但需人工复议"],
        "改判风险需人工复议": [o.review_id for o in reviews
                           if o.classification and o.classification.startswith("改判风险")],
    }
    unsignable = []
    for o in reviews:
        if o.snapshot is None and o.verdict != "可签认":
            unsignable.append(f"合同 {o.review_id}（{o.plot_id}）：{o.verdict}")
    for cid, reason in plan.unsignable:
        unsignable.append(f"续建 {cid}：{reason}")
    for note in book.terrain_notes:
        unsignable.append(f"成效判定：{note}")
    return ReviewRun(dataset, config, common, exposures, book, sens, reviews, plan,
                     supplementary, impact, tuple(unsignable))
