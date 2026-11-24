"""共同地面还原、面积去重与暴露时段。

不同项目重合的监测范围按稳定地面编号还原成共同地面，统计总体面积只计一次。
暴露时段区分直接实施与邻地影响；地块在真正受影响以前可为较早开工组提供参照。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    kind: str  # 直接实施 | 邻地影响 | 影响未界定
    plot_id: str
    measure: str | None
    start: int
    end: int

    def overlaps(self, baseline: int, outcome: int) -> bool:
        return self.start <= outcome and self.end >= baseline


@dataclass(frozen=True)
class Exposure:
    ground_id: str
    segments: tuple[Segment, ...]
    first_affected: int | None
    current_plot: str | None
    current_direct_start: int | None
    affected_before_direct: bool  # 受影响早于现归属项目开工
    unknown_affect_date: bool  # 有邻地影响记载但起始年份未界定


def build_exposures(dataset) -> dict[str, Exposure]:
    plots = dataset.plots
    # 项目记载邻地影响但全部地面均未记起始年份 → 该项目地面受影响日期无法界定
    plots_with_years = {u.plot_id for u in dataset.ground_units
                        if u.first_neighbor_year is not None}
    out = {}
    for gid, rows in dataset.units_by_ground().items():
        segments: list[Segment] = []
        unknown = False
        for u in rows:
            plot = plots[u.plot_id]
            if plot.start_year is not None:
                segments.append(Segment("直接实施", u.plot_id, plot.measure,
                                        max(plot.start_year, u.valid_from), u.valid_to))
            if u.first_neighbor_year is not None:
                segments.append(Segment("邻地影响", u.plot_id, None,
                                        u.first_neighbor_year, u.valid_to))
            elif plot.neighbor_exposure and u.plot_id not in plots_with_years:
                unknown = True
                segments.append(Segment("影响未界定", u.plot_id, None,
                                        u.valid_from, u.valid_to))
        current = [u for u in rows if u.valid_from <= dataset.as_of.year <= u.valid_to]
        current_plot = current[0].plot_id if current else None
        direct_start = None
        if current_plot is not None and plots[current_plot].start_year is not None:
            direct_start = plots[current_plot].start_year
        starts = [s.start for s in segments]
        first = min(starts) if starts else None
        affected_before = (first is not None and direct_start is not None
                           and first < direct_start)
        out[gid] = Exposure(gid, _merge(segments), first, current_plot,
                            direct_start, affected_before, unknown)
    return out


def _merge(segments: list[Segment]) -> tuple[Segment, ...]:
    """合并同一项目同一措施在相邻边界版本中的连续时段。"""
    merged: list[Segment] = []
    for seg in sorted(segments, key=lambda s: (s.kind, s.plot_id, s.start)):
        if (merged and merged[-1].kind == seg.kind
                and merged[-1].plot_id == seg.plot_id
                and merged[-1].measure == seg.measure
                and seg.start <= merged[-1].end + 1):
            last = merged[-1]
            merged[-1] = Segment(last.kind, last.plot_id, last.measure,
                                 last.start, max(last.end, seg.end))
        else:
            merged.append(seg)
    return tuple(sorted(merged, key=lambda s: (s.start, s.kind)))


def reference_eligible(exposure: Exposure, baseline: int, outcome: int) -> bool:
    """窗口内无任何直接 / 邻地 / 未界定影响 → 可作可比未治理参照。"""
    return not any(seg.overlaps(baseline, outcome) for seg in exposure.segments)


@dataclass(frozen=True)
class GroundSummary:
    ground_id: str
    terrain: str
    gully: str
    area_ha: float
    plots: tuple[str, ...]
    versions: tuple[str, ...]
    shared: bool
    notes: tuple[str, ...]


@dataclass(frozen=True)
class CommonGround:
    grounds: tuple[GroundSummary, ...]
    total_area_once: float  # 共同地面去重后的监测总面积
    total_area_reported: float  # 各项目报送面积合计
    overlap_area: float


def common_ground(dataset) -> CommonGround:
    summaries = []
    total_once = 0.0
    for gid in sorted(dataset.units_by_ground()):
        rows = sorted(dataset.units_by_ground()[gid], key=lambda u: (u.valid_from, u.plot_id))
        terrains = {u.terrain for u in rows}
        gullies = {u.gully for u in rows}
        areas = {u.area_ha for u in rows}
        notes = []
        if len(terrains) > 1:
            notes.append("地貌记载不一致")
        if len(gullies) > 1:
            notes.append("坡沟记载不一致")
        if len(areas) > 1:
            notes.append("面积记载不一致")
        area = rows[0].area_ha
        total_once += area
        plots = tuple(sorted({u.plot_id for u in rows}))
        versions = tuple(f"{u.plot_id}@v{u.version}({u.valid_from}-{u.valid_to})" for u in rows)
        summaries.append(GroundSummary(gid, rows[0].terrain, rows[0].gully, area,
                                       plots, versions, len(plots) > 1, tuple(notes)))
    reported = sum(p.area_ha for p in dataset.plots.values())
    return CommonGround(tuple(summaries), round(total_once, 4), round(reported, 4),
                        round(reported - total_once, 4))
