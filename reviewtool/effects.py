"""分地貌分措施成效估计与敏感性分析。

分别量化封育、封育加补植相对可比未治理地面的变化；直接实施与明确已受
邻地影响的范围分开报告。沿同一坡沟归属同一降雨过程的地块不作为独立证据
重复计数。没有共同参照或受影响日期无法界定的区域给出局部结论或暂不判定。
"""
from __future__ import annotations

from dataclasses import dataclass

from .exposure import reference_eligible
from .model import MEASURES
from .series import chain_change


@dataclass(frozen=True)
class RefDetail:
    ground_id: str
    gully: str
    change: float
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class Estimate:
    measure: str
    terrain: str
    ground_id: str
    plot_id: str
    baseline: int
    outcome: int
    change: float
    uncertainty: float
    references: tuple[RefDetail, ...]
    reference_change: float
    effect: float
    same_gully_reference: bool  # 处理与参照同沟：同一降雨过程，不作独立证据重复
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class NeighborEntry:
    ground_id: str
    plot_id: str
    measure: str
    terrain: str
    baseline: int
    outcome: int
    first_affected: int
    affected_before_direct: bool
    change: float | None  # 自身趋势可连接时的变化，否则 None
    note: str


@dataclass(frozen=True)
class DeferredNote:
    ground_id: str
    measure: str
    terrain: str
    reason: str


@dataclass(frozen=True)
class EffectBook:
    direct: tuple[Estimate, ...]  # 直接实施范围的相对成效
    neighbor: tuple[NeighborEntry, ...]  # 明确已受邻地影响的范围（单列，不并入直接证据）
    deferred: tuple[DeferredNote, ...]  # 暂不判定
    terrain_notes: tuple[str, ...]


def find_references(dataset, exposures, obs_by_ground, pair_offsets, config,
                    terrain: str, exclude: str, baseline: int, outcome: int) -> list[RefDetail]:
    """同地貌、窗口内未受任何影响、趋势可连接的地面方可作参照。"""
    refs = []
    for gid in sorted(obs_by_ground):
        if gid == exclude or gid not in exposures:
            continue
        if dataset.ground_attr(gid).terrain != terrain:
            continue
        if not reference_eligible(exposures[gid], baseline, outcome):
            continue
        chain = chain_change(obs_by_ground[gid], baseline, outcome,
                             pair_offsets=pair_offsets,
                             min_uncertainty=config.min_bridge_uncertainty_pp)
        if not chain.ok:
            continue
        refs.append(RefDetail(gid, dataset.ground_attr(gid).gully, chain.change,
                              chain.evidence_ids))
    return refs


def build_effects(dataset, exposures, obs_by_ground, pair_offsets, config) -> EffectBook:
    direct: list[Estimate] = []
    neighbor: list[NeighborEntry] = []
    deferred: list[DeferredNote] = []
    asof_year = dataset.as_of.year
    for plot in sorted(dataset.plots.values(), key=lambda p: p.plot_id):
        if plot.measure not in MEASURES or plot.start_year is None:
            continue
        for gid in dataset.plot_grounds_at(plot.plot_id, asof_year):
            by_year = obs_by_ground.get(gid)
            if not by_year:
                continue
            attr = dataset.ground_attr(gid)
            baseline, outcome = plot.start_year - 1, max(by_year)
            exp = exposures[gid]
            chain = chain_change(by_year, baseline, outcome, pair_offsets=pair_offsets,
                                 min_uncertainty=config.min_bridge_uncertainty_pp)
            segs = [s for s in exp.segments if s.overlaps(baseline, outcome)]
            if any(s.kind in ("邻地影响", "影响未界定") for s in segs):
                notes = []
                if exp.affected_before_direct:
                    notes.append(f"受影响（{exp.first_affected}年）早于现归属项目开工"
                                 f"（{exp.current_direct_start}年）")
                if not chain.ok:
                    notes.append(f"自身趋势不可连接：{chain.reason}")
                neighbor.append(NeighborEntry(
                    gid, plot.plot_id, plot.measure, attr.terrain, baseline, outcome,
                    exp.first_affected if exp.first_affected is not None else -1,
                    exp.affected_before_direct,
                    chain.change if chain.ok else None,
                    "；".join(notes) or "窗口内受邻地影响"))
                continue
            if not chain.ok:
                deferred.append(DeferredNote(gid, plot.measure, attr.terrain, chain.reason))
                continue
            refs = find_references(dataset, exposures, obs_by_ground, pair_offsets, config,
                                   attr.terrain, gid, baseline, outcome)
            if not refs:
                deferred.append(DeferredNote(gid, plot.measure, attr.terrain,
                                             "无共同参照（同地貌无可比未治理地面）"))
                continue
            ref_change = sum(r.change for r in refs) / len(refs)
            evidence = list(chain.evidence_ids)
            for r in refs:
                evidence += r.evidence_ids
            direct.append(Estimate(
                plot.measure, attr.terrain, gid, plot.plot_id, baseline, outcome,
                chain.change, chain.uncertainty, tuple(refs), ref_change,
                chain.change - ref_change,
                any(r.gully == attr.gully for r in refs),
                tuple(dict.fromkeys(evidence))))
    terrain_notes = []
    terrains = sorted({dataset.ground_attr(g).terrain for g in obs_by_ground})
    for t in terrains:
        has_ref = any(dataset.ground_attr(g).terrain == t and not exposures[g].segments
                      for g in exposures)
        if not has_ref:
            terrain_notes.append(f"{t}：无未治理对照，{t}范围的相对成效暂不判定")
    return EffectBook(tuple(direct), tuple(neighbor), tuple(deferred), tuple(terrain_notes))


@dataclass(frozen=True)
class AttributionScenario:
    ground_id: str
    terrain: str
    scenario: str  # 误作直接证据 | 误作未治理参照
    detail: str
    swings: tuple[str, ...]


@dataclass(frozen=True)
class RainCheck:
    label: str
    treated_zone: str
    reference_zones: tuple[str, ...]
    rain_delta_treated: float
    rain_delta_reference: float
    level: str  # 低 | 中 | 高


@dataclass(frozen=True)
class Sensitivity:
    attribution: tuple[AttributionScenario, ...]
    rain: tuple[RainCheck, ...]
    bridge_notes: tuple[str, ...]


def build_sensitivity(dataset, exposures, book, obs_by_ground, pair_offsets, config) -> Sensitivity:
    scenarios: list[AttributionScenario] = []
    for entry in book.neighbor:
        gid = entry.ground_id
        by_year = obs_by_ground[gid]
        ests = [e for e in book.direct
                if e.measure == entry.measure and e.terrain == entry.terrain]
        # 情景一：误作直接证据
        if ests:
            b, o = ests[0].baseline, ests[0].outcome
            chain = chain_change(by_year, b, o, pair_offsets=pair_offsets,
                                 min_uncertainty=config.min_bridge_uncertainty_pp)
            if not chain.ok:
                scenarios.append(AttributionScenario(
                    gid, entry.terrain, "误作直接证据",
                    f"趋势不可连接（{chain.reason}），即便误作直接证据也不形成结论", ()))
            else:
                refs = find_references(dataset, exposures, obs_by_ground, pair_offsets,
                                       config, entry.terrain, gid, b, o)
                if not refs:
                    scenarios.append(AttributionScenario(
                        gid, entry.terrain, "误作直接证据",
                        "同地貌无可比未治理参照，仍不形成结论", ()))
                else:
                    ref_change = sum(r.change for r in refs) / len(refs)
                    naive = chain.change - ref_change
                    pool = "、".join(f"{e.effect:+.1f}" for e in ests)
                    scenarios.append(AttributionScenario(
                        gid, entry.terrain, "误作直接证据",
                        f"按 {b}–{o} 窗口：自身变化 {chain.change:+.1f}pp，"
                        f"相对参照 {naive:+.1f}pp",
                        (f"{entry.measure}·{entry.terrain} 直接证据池 [{pool}] "
                         f"将并入 {naive:+.1f}pp（虚高）",)))
        else:
            scenarios.append(AttributionScenario(
                gid, entry.terrain, "误作直接证据",
                f"同地貌无{entry.measure}直接实施估计可并列", ()))
        # 情景二：误作未治理参照
        swings = []
        for e in book.direct:
            if e.terrain != entry.terrain:
                continue
            chain = chain_change(by_year, e.baseline, e.outcome,
                                 pair_offsets=pair_offsets,
                                 min_uncertainty=config.min_bridge_uncertainty_pp)
            if not chain.ok:
                swings.append(f"{e.measure}·{e.terrain}（{e.ground_id}）：{gid} 在 "
                              f"{e.baseline}–{e.outcome} 不可连接，无法充当参照")
                continue
            changes = [r.change for r in e.references] + [chain.change]
            new_ref = sum(changes) / len(changes)
            new_effect = e.change - new_ref
            before = e.effect >= config.renewal_min_effect_pp
            after = new_effect >= config.renewal_min_effect_pp
            flip = "跌破续建阈值" if before and not after else "未翻转阈值"
            swings.append(f"{e.measure}·{e.terrain}（{e.ground_id}）："
                          f"{e.effect:+.1f} → {new_effect:+.1f}pp（{flip}）")
        if swings:
            scenarios.append(AttributionScenario(
                gid, entry.terrain, "误作未治理参照",
                f"{gid} 进入参照池后重算：", tuple(swings)))
        else:
            scenarios.append(AttributionScenario(
                gid, entry.terrain, "误作未治理参照",
                f"当前无{entry.terrain}处理估计可配对；一旦建立配对，结论将受其主导", ()))
    rain: list[RainCheck] = []
    for e in book.direct:
        tz = dataset.ground_attr(e.ground_id).gully
        rz = sorted({r.gully for r in e.references})
        dt = (dataset.weather[(tz, e.outcome)].rain_mm
              - dataset.weather[(tz, e.baseline)].rain_mm)
        dr = sum(dataset.weather[(z, e.outcome)].rain_mm
                 - dataset.weather[(z, e.baseline)].rain_mm for z in rz) / len(rz)
        diff = abs(dt - dr)
        level = "低" if diff <= config.rain_tolerance_mm else (
            "中" if diff <= 3 * config.rain_tolerance_mm else "高")
        rain.append(RainCheck(f"{e.measure}·{e.terrain}（{e.ground_id}）", tz, tuple(rz),
                              dt, dr, level))
    bridge_notes = tuple(
        f"{e.ground_id}：桥接换算不确定度 ±{e.uncertainty:.1f}pp，相对成效 {e.effect:+.1f}pp"
        for e in book.direct if e.uncertainty > 0)
    return Sensitivity(tuple(scenarios), tuple(rain), bridge_notes)
