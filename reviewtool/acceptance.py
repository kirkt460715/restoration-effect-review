"""合同验收复核：合同口径签认、当前口径复核与已验收快照追查。

验收按合同签订时写明的直接治理地面、观察窗和最低效果执行。
已验收合同按 evidence_cutoff 还原当时数据版本与采用证据留痕；
当前复核只作批注，不覆盖原签认依据。
"""
from __future__ import annotations

from dataclasses import dataclass

from .effects import find_references
from .series import chain_change, method_pair_offsets


@dataclass(frozen=True)
class GroundVerdict:
    ground_id: str
    terrain: str
    attribution: str  # 直接实施 | 邻地影响 | 影响未界定
    boundary_note: str
    change: float | None
    uncertainty: float
    references: tuple
    effect: float | None
    verdict: str  # 达标 | 未达标 | 暂不判定 | 排除
    reasons: tuple[str, ...]
    supplements: tuple[str, ...]  # 补证事项
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class Snapshot:
    data_version: str  # 当时资料可见截止日
    verdict: str
    ground_effects: tuple[tuple[str, float | None], ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class ReviewOutcome:
    review_id: str
    plot_id: str
    status: str
    window: tuple[int, int]
    min_effect_pp: float
    grounds: tuple[GroundVerdict, ...]
    verdict: str
    supplements: tuple[str, ...]
    snapshot: Snapshot | None
    classification: str | None  # 已验收：维持 | 维持但需人工复议 | 改判风险（…）
    notes: tuple[str, ...]


def _evaluate_ground(review, gid, dataset, exposures, obs_by_ground,
                     pair_offsets, config, contract_attribution) -> GroundVerdict:
    attr = dataset.ground_attr(gid)
    exp = exposures[gid]
    b, o = review.baseline_year, review.outcome_year
    reasons: list[str] = []
    supplements: list[str] = []
    plot_rows = [u for u in dataset.units_by_ground()[gid] if u.plot_id == review.plot_id]
    boundary_note = ""
    if not plot_rows:
        boundary_note = f"{gid} 不在 {review.plot_id} 任何边界版本中"
    elif not any(u.valid_from <= o <= u.valid_to for u in plot_rows):
        boundary_note = f"{gid} 在 {o} 年已不属 {review.plot_id} 现行边界版本"
    attribution = "直接实施"
    if not contract_attribution:
        if any(s.kind == "邻地影响" and s.overlaps(b, o) for s in exp.segments):
            attribution = "邻地影响"
        elif any(s.kind == "影响未界定" and s.overlaps(b, o) for s in exp.segments):
            attribution = "影响未界定"
    by_year = obs_by_ground.get(gid, {})
    chain = chain_change(by_year, b, o, pair_offsets=pair_offsets,
                         min_uncertainty=config.min_bridge_uncertainty_pp)
    evidence = list(chain.evidence_ids)
    if attribution == "邻地影响":
        reasons.append(f"窗口内已受邻地影响（{exp.first_affected}年起），"
                       "不能作为直接实施证据")
        supplements.append(f"{gid}：按邻地影响范围单列，补同地貌对照后再议")
        if not chain.ok:
            reasons.append(f"自身趋势亦不可连接：{chain.reason}")
            supplements.append(f"{gid}：补 {b}–{o} 同期重测桥接")
        if boundary_note:
            reasons.append(boundary_note)
        return GroundVerdict(gid, attr.terrain, attribution, boundary_note,
                             chain.change if chain.ok else None, chain.uncertainty,
                             (), None, "排除", tuple(reasons), tuple(supplements),
                             tuple(evidence))
    if attribution == "影响未界定":
        reasons.append("邻地影响起始日期未记载，受影响时段无法界定")
        supplements.append(f"{gid}：补邻地影响起始界定")
        return GroundVerdict(gid, attr.terrain, attribution, boundary_note,
                             None, 0.0, (), None, "暂不判定",
                             tuple(reasons), tuple(supplements), tuple(evidence))
    if boundary_note:
        reasons.append(boundary_note)
        supplements.append(f"{gid}：按现行边界版本重核合同地面")
    if not chain.ok:
        reasons.append(chain.reason)
        supplements.append(f"{gid}：补 {b}–{o} 同期重测桥接（测量切换）")
        return GroundVerdict(gid, attr.terrain, attribution, boundary_note,
                             None, 0.0, (), None, "暂不判定",
                             tuple(reasons), tuple(supplements), tuple(evidence))
    refs = find_references(dataset, exposures, obs_by_ground, pair_offsets, config,
                           attr.terrain, gid, b, o)
    if not refs:
        reasons.append("无共同参照（同地貌无可比未治理地面）")
        supplements.append(f"{gid}：补同地貌未治理对照，或出具局部结论")
        return GroundVerdict(gid, attr.terrain, attribution, boundary_note,
                             chain.change, chain.uncertainty, (), None, "暂不判定",
                             tuple(reasons), tuple(supplements), tuple(evidence))
    for r in refs:
        evidence += r.evidence_ids
    ref_change = sum(r.change for r in refs) / len(refs)
    effect = chain.change - ref_change
    lo, hi = effect - chain.uncertainty, effect + chain.uncertainty
    if lo >= review.min_effect_pp:
        verdict = "达标"
    elif hi < review.min_effect_pp:
        verdict = "未达标"
        reasons.append(f"相对成效 {effect:+.1f}pp 低于合同最低 {review.min_effect_pp:.1f}pp")
    else:
        verdict = "暂不判定"
        reasons.append(f"换算不确定度 ±{chain.uncertainty:.1f}pp 使相对成效 "
                       f"{effect:+.1f}pp 触及合同阈值 {review.min_effect_pp:.1f}pp")
        supplements.append(f"{gid}：补测加密桥接，降低换算不确定度")
    return GroundVerdict(gid, attr.terrain, attribution, boundary_note,
                         chain.change, chain.uncertainty, tuple(refs), effect,
                         verdict, tuple(reasons), tuple(supplements),
                         tuple(dict.fromkeys(evidence)))


def evaluate_review(review, dataset, exposures, obs_by_ground, pair_offsets, config,
                    *, contract_attribution: bool = False):
    grounds = tuple(_evaluate_ground(review, gid, dataset, exposures, obs_by_ground,
                                     pair_offsets, config, contract_attribution)
                    for gid in review.ground_ids)
    if all(g.verdict == "达标" for g in grounds):
        verdict = "可签认"
    elif any(g.verdict == "未达标" for g in grounds):
        verdict = "未达标（暂不签出）"
    else:
        verdict = "暂不签出（待补证）"
    supplements = tuple(dict.fromkeys(s for g in grounds for s in g.supplements))
    return grounds, verdict, supplements


def evaluate_all(dataset, exposures, obs_by_ground, config) -> tuple[ReviewOutcome, ...]:
    outcomes = []
    for review in sorted(dataset.reviews, key=lambda r: r.review_id):
        if review.evidence_cutoff is not None:
            # 已验收：按当时数据版本与合同口径还原签认依据，留快照
            past = dataset.view(review.evidence_cutoff)
            past_obs = past.observations_by_ground(review.contract_indicator)
            past_offsets = method_pair_offsets(past_obs)
            grounds_then, verdict_then, _ = evaluate_review(
                review, past, exposures, past_obs, past_offsets, config,
                contract_attribution=True)
            snapshot = Snapshot(
                review.evidence_cutoff.isoformat(), verdict_then,
                tuple((g.ground_id, g.effect) for g in grounds_then),
                tuple(dict.fromkeys(i for g in grounds_then for i in g.evidence_ids)))
            # 当前口径复核（只作批注，不覆盖原签认）
            grounds_now, _, _ = evaluate_review(
                review, dataset, exposures, obs_by_ground,
                method_pair_offsets(obs_by_ground), config)
            # 当前数据、同合同口径重算：检验结论是否仍成立
            grounds_same, verdict_same, _ = evaluate_review(
                review, dataset, exposures, obs_by_ground,
                method_pair_offsets(obs_by_ground), config, contract_attribution=True)
            maintained = verdict_same == verdict_then
            basis_changes = []
            for g in grounds_now:
                if g.attribution != "直接实施":
                    basis_changes.append(f"{g.ground_id} 现列为{g.attribution}范围")
            ids_then = set(snapshot.evidence_ids)
            ids_same = set(i for g in grounds_same for i in g.evidence_ids)
            if ids_then != ids_same:
                basis_changes.append("窗口内采用证据的资料版本发生变化")
            if maintained and not basis_changes:
                classification = "维持"
            elif maintained:
                classification = "维持但需人工复议"
            else:
                classification = "改判风险（原签认保留，需人工复议）"
            outcomes.append(ReviewOutcome(
                review.review_id, review.plot_id, review.status,
                (review.baseline_year, review.outcome_year), review.min_effect_pp,
                grounds_now, verdict_then, (), snapshot, classification,
                tuple(basis_changes)))
        else:
            grounds, verdict, supplements = evaluate_review(
                review, dataset, exposures, obs_by_ground,
                method_pair_offsets(obs_by_ground), config)
            outcomes.append(ReviewOutcome(
                review.review_id, review.plot_id, review.status,
                (review.baseline_year, review.outcome_year), review.min_effect_pp,
                grounds, verdict, supplements, None, None, ()))
    return tuple(outcomes)
