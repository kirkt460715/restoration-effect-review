"""续建核定：适用性、作业窗口容量与前置关系。

续建采用候选地块当前的地貌与施工组合核定适用性；依赖成效只在合并治理
之后有证据的候选，不能先作为单项工程签认。各候选共用作业队窗口与资金，
已生效安排先行占用这部分资源。
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from .model import window_key

SIGNABLE = ("适用", "边界适用")


@dataclass(frozen=True)
class Applicability:
    candidate_id: str
    measure: str
    terrain: str
    status: str  # 适用 | 边界适用 | 不适用 | 不可单项签认 | 暂不判定
    best_effect: float | None
    evidence: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class WindowPlan:
    window: str
    capacity_days: float
    capacity_yuan: float
    occupied: tuple[str, ...]  # 已生效占用
    rem_days: float
    rem_yuan: float
    signed: tuple[str, ...]
    feasible: tuple[tuple[str, ...], ...]  # 窗口内全部可行组合
    blocked: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class RenewalPlan:
    applicability: tuple[Applicability, ...]
    windows: tuple[WindowPlan, ...]
    unsignable: tuple[tuple[str, str], ...]
    deadlock: bool  # 没有任何组合能同时守住履约安排和适用条件


def assess_candidates(candidates, book, config) -> tuple[Applicability, ...]:
    by_mt: dict[tuple[str, str], list] = {}
    for e in book.direct:
        by_mt.setdefault((e.measure, e.terrain), []).append(e)
    neighbor_mt = {(n.measure, n.terrain) for n in book.neighbor}
    deferred_mt = {(d.measure, d.terrain) for d in book.deferred}
    ref_use: dict[str, set] = {}
    for e in book.direct:
        for r in e.references:
            ref_use.setdefault(r.ground_id, set()).add(e.ground_id)
    apps = []
    for c in candidates:
        notes = []
        if c.ground_id in ref_use:
            used_by = "、".join(sorted(ref_use[c.ground_id]))
            notes.append(f"{c.ground_id} 现为未治理对照（供 {used_by} 估计），实施后需补对照")
        ests = by_mt.get((c.measure, c.terrain), [])
        if ests:
            best = max(ests, key=lambda e: e.effect - e.uncertainty)
            conservative = best.effect - best.uncertainty
            evidence = tuple(sorted({e.ground_id for e in ests}))
            if conservative >= config.renewal_min_effect_pp:
                limited = (conservative < config.renewal_min_effect_pp + 1.0
                           or (len(ests) == 1 and best.outcome - best.baseline <= 1))
                if limited:
                    notes.append("证据有限（接近阈值或单一坡沟 / 单一窗口），按边界适用处理")
                apps.append(Applicability(c.candidate_id, c.measure, c.terrain,
                                          "边界适用" if limited else "适用",
                                          best.effect, evidence, tuple(notes)))
            else:
                notes.append(f"保守成效 {conservative:+.1f}pp 低于续建阈值 "
                             f"{config.renewal_min_effect_pp:.1f}pp")
                apps.append(Applicability(c.candidate_id, c.measure, c.terrain, "不适用",
                                          best.effect, evidence, tuple(notes)))
        elif (c.measure, c.terrain) in neighbor_mt:
            notes.append("成效证据仅见于合并治理范围，不能先作单项工程签认")
            apps.append(Applicability(c.candidate_id, c.measure, c.terrain,
                                      "不可单项签认", None, (), tuple(notes)))
        elif (c.measure, c.terrain) in deferred_mt:
            notes.append("无共同参照或趋势不可连接，暂不判定")
            apps.append(Applicability(c.candidate_id, c.measure, c.terrain,
                                      "暂不判定", None, (), tuple(notes)))
        else:
            notes.append("无成效证据")
            apps.append(Applicability(c.candidate_id, c.measure, c.terrain,
                                      "不适用", None, (), tuple(notes)))
    return tuple(apps)


def schedule(renewals, capacity, apps) -> RenewalPlan:
    app_by_id = {a.candidate_id: a for a in apps}
    signed_window: dict[str, str] = {}
    plans = []
    blocked_all: dict[str, str] = {}
    for w in sorted(capacity, key=window_key):
        cap = capacity[w]
        occupied = [c for c in renewals if c.status == "已生效" and c.window == w]
        rem_days = cap.crew_days - sum(c.crew_days for c in occupied)
        rem_yuan = cap.budget_yuan - sum(c.cost_yuan for c in occupied)
        eligible, blocked = [], []
        for c in renewals:
            if c.status != "提议" or c.window != w:
                continue
            app = app_by_id[c.candidate_id]
            if app.status not in SIGNABLE:
                blocked.append((c.candidate_id, f"适用性：{app.status}"))
                continue
            reasons = []
            if c.requires:
                req = next((x for x in renewals if x.candidate_id == c.requires), None)
                if req is None:
                    reasons.append(f"前置工程 {c.requires} 不存在")
                else:
                    done = req.status == "已生效" or (
                        req.candidate_id in signed_window
                        and window_key(signed_window[req.candidate_id]) < window_key(w))
                    if not done:
                        reasons.append(f"前置工程 {req.candidate_id} 未先完成"
                                       f"（其窗口 {req.window}）")
            if c.crew_days > rem_days or c.cost_yuan > rem_yuan:
                reasons.append("超出窗口剩余容量")
            if reasons:
                blocked.append((c.candidate_id, "；".join(reasons)))
                continue
            eligible.append(c)
        feasible = []
        for k in range(1, len(eligible) + 1):
            for combo in combinations(eligible, k):
                if (sum(c.crew_days for c in combo) <= rem_days
                        and sum(c.cost_yuan for c in combo) <= rem_yuan):
                    feasible.append(combo)

        def score(combo):
            return (sum(app_by_id[c.candidate_id].best_effect or 0.0 for c in combo),
                    -sum(c.cost_yuan for c in combo),
                    tuple(c.candidate_id for c in combo))

        chosen = max(feasible, key=score) if feasible else ()
        chosen_ids = tuple(sorted(c.candidate_id for c in chosen))
        for c in chosen:
            signed_window[c.candidate_id] = w
        for c in eligible:
            if c.candidate_id not in chosen_ids:
                own = app_by_id[c.candidate_id].best_effect
                blocked.append((c.candidate_id,
                                f"与 {'、'.join(chosen_ids)} 同窗竞争容量，证据排序靠后"
                                f"（自身最佳 {own:+.1f}pp）"))
        plans.append(WindowPlan(
            w, cap.crew_days, cap.budget_yuan,
            tuple(sorted(c.candidate_id for c in occupied)), rem_days, rem_yuan,
            chosen_ids,
            tuple(sorted(tuple(sorted(x.candidate_id for x in combo)) for combo in feasible)),
            tuple(blocked)))
        for cid, reason in blocked:
            blocked_all[cid] = reason
    proposals = [c for c in renewals if c.status == "提议"]
    unsignable = tuple(sorted(
        (c.candidate_id, blocked_all.get(c.candidate_id, "本周期无可行窗口"))
        for c in proposals if c.candidate_id not in signed_window))
    deadlock = bool(proposals) and not signed_window
    return RenewalPlan(tuple(apps), tuple(plans), unsignable, deadlock)


def assess_and_schedule(dataset, book, config) -> RenewalPlan:
    candidates = sorted(dataset.renewals, key=lambda c: c.candidate_id)
    apps = assess_candidates(candidates, book, config)
    return schedule(dataset.renewals, dataset.capacity, apps)
