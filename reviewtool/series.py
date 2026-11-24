"""观测序列：测量切换的同期重测桥接与趋势连接。

同一地面同一指标若中途更换测量方法，只有存在同年双方法重测（桥接调查）
时才可把两端连成同一趋势；换算不确定度触及判断阈值时不得硬连。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bridge:
    from_method: str
    to_method: str
    years: tuple[int, ...]  # 桥接调查年份
    offset: float  # to_method − from_method（桥接年均值）
    uncertainty: float


@dataclass(frozen=True)
class Chain:
    ok: bool
    change: float | None  # 端点变化（换算到基线方法口径）
    uncertainty: float
    reason: str
    bridges: tuple[Bridge, ...]
    method_path: tuple[str, ...]
    baseline_value: float | None
    outcome_value: float | None
    evidence_ids: tuple[str, ...]  # 本段趋势实际采用的观测编号


def _fail(reason: str, path: tuple[str, ...] = ()) -> Chain:
    return Chain(False, None, 0.0, reason, (), path, None, None, ())


def method_pair_offsets(obs_by_ground: dict) -> dict:
    """汇总全部地面同年双方法记录，得到方法对换算差（用于不确定度估计）。"""
    offsets: dict[tuple[str, str], list[float]] = {}
    for by_year in obs_by_ground.values():
        for obs in by_year.values():
            values: dict[str, list[float]] = {}
            for o in obs:
                values.setdefault(o.method, []).append(o.value)
            methods = sorted(values)
            for i, a in enumerate(methods):
                for b in methods[i + 1:]:
                    va = sum(values[a]) / len(values[a])
                    vb = sum(values[b]) / len(values[b])
                    offsets.setdefault((a, b), []).append(vb - va)
    return offsets


def chain_change(by_year: dict, baseline: int, outcome: int, *,
                 pair_offsets: dict, min_uncertainty: float = 1.0) -> Chain:
    """连接 [baseline, outcome] 窗口两端值（换算到基线方法口径）。

    by_year: {年份: [Observation]}（同一地面同一指标）。
    """
    missing = [y for y in (baseline, outcome) if y not in by_year]
    if missing:
        return _fail("、".join(f"{y}年缺测" for y in missing))
    years = sorted(y for y in by_year if baseline <= y <= outcome)

    # 每年选定主用方法：与更晚年份保持一致优先，使切换点定位准确
    chosen: dict[int, str] = {}
    for y in reversed(years):
        methods = {o.method for o in by_year[y]}
        nxt = next((z for z in years if z > y), None)
        if nxt is not None and chosen[nxt] in methods:
            chosen[y] = chosen[nxt]
        elif len(methods) == 1:
            chosen[y] = next(iter(methods))
        else:
            chosen[y] = max(by_year[y], key=lambda o: o.received_at).method

    def value(y: int) -> float:
        vals = [o.value for o in by_year[y] if o.method == chosen[y]]
        return sum(vals) / len(vals)

    evidence = [o.observation_id for o in by_year[baseline] if o.method == chosen[baseline]]
    evidence += [o.observation_id for o in by_year[outcome] if o.method == chosen[outcome]]
    bridges: list[Bridge] = []
    total_offset = 0.0
    uncertainty = 0.0
    path = [chosen[years[0]]]
    for prev, cur in zip(years, years[1:]):
        if chosen[cur] != path[-1]:
            path.append(chosen[cur])
        m_old, m_new = chosen[prev], chosen[cur]
        if m_old == m_new:
            continue
        bridge_years = tuple(y for y in years
                             if {o.method for o in by_year[y]} >= {m_old, m_new})
        if not bridge_years:
            return _fail(f"{m_old}→{m_new}测量切换无同期重测桥接", tuple(path))
        local = []
        for y in bridge_years:
            vals: dict[str, list[float]] = {}
            for o in by_year[y]:
                vals.setdefault(o.method, []).append(o.value)
            local.append(sum(vals[m_new]) / len(vals[m_new])
                         - sum(vals[m_old]) / len(vals[m_old]))
            evidence += [o.observation_id for o in by_year[y]]
        offset = sum(local) / len(local)
        key = tuple(sorted((m_old, m_new)))
        known = pair_offsets.get(key, local)
        spread = (max(known) - min(known)) / 2 if len(known) > 1 else min_uncertainty
        unc = max(min_uncertainty, spread)
        bridges.append(Bridge(m_old, m_new, bridge_years, offset, unc))
        total_offset += offset
        uncertainty += unc
    change = (value(outcome) - total_offset) - value(baseline)
    return Chain(True, change, uncertainty, "", tuple(bridges), tuple(path),
                 value(baseline), value(outcome), tuple(dict.fromkeys(evidence)))
