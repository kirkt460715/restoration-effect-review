"""季报渲染：Markdown 报告与机器可读 JSON 侧车。"""
from __future__ import annotations

from .core import ReviewRun


def _s(x) -> str:
    return "—" if x is None else f"{x:+.1f}"


def _n(x) -> str:
    return "—" if x is None else f"{x:.1f}"


def _table(headers, rows):
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def render_markdown(run: ReviewRun) -> str:
    ds = run.dataset
    L: list[str] = []
    L.append("# 生态修复成效复核与续建核定季报")
    L.append("")
    supp = run.supplementary
    supp_date = supp[0].received_at.isoformat() if supp else "—"
    L.append(f"- 复核截止：{ds.as_of.isoformat()}（数据版本按 received_at 截止）")
    L.append(f"- 观测记录：{len(ds.observations)} 条；本期补报 {len(supp)} 条"
             f"（{supp_date} 到件）："
             + ("、".join(o.observation_id for o in supp) if supp else "无"))
    L.append("")

    L.append("## 1. 共同地面与面积去重")
    L.append("")
    L.append(f"监测总面积（共同地面去重，只计一次）：**{run.common.total_area_once:.1f} ha**；"
             f"各项目报送合计 {run.common.total_area_reported:.1f} ha，"
             f"重合 {run.common.overlap_area:.1f} ha。")
    L.append("")
    rows = [(g.ground_id, g.terrain, g.gully, f"{g.area_ha:.1f}",
             "；".join(g.versions), "是" if g.shared else "",
             "；".join(g.notes)) for g in run.common.grounds]
    L += _table(["地面编号", "地貌", "坡沟", "面积(ha)", "归属项目@版本(有效期)", "跨项目共享", "备注"], rows)
    L.append("")

    L.append("## 2. 暴露时段与参照资格")
    L.append("")
    rows = []
    for gid in sorted(run.exposures):
        e = run.exposures[gid]
        direct = "、".join(f"{s.plot_id}·{s.measure} {s.start}-{s.end}"
                        for s in e.segments if s.kind == "直接实施") or "—"
        neighbor = "、".join(f"{s.start}-{s.end}" for s in e.segments
                          if s.kind in ("邻地影响", "影响未界定")) or "—"
        first = e.first_affected if e.first_affected is not None else "—"
        note = ""
        if e.affected_before_direct:
            note = "受影响早于现归属项目开工"
        elif e.unknown_affect_date:
            note = "受影响日期未界定"
        rows.append((gid, e.current_plot or "—", direct, neighbor, first, note))
    L += _table(["地面编号", "当前项目", "直接实施时段", "邻地影响时段", "首次受影响", "提示"], rows)
    L.append("")

    L.append("## 3. 分地貌分措施成效（相对可比未治理地面）")
    L.append("")
    groups: dict[tuple[str, str], list] = {}
    for e in run.book.direct:
        groups.setdefault((e.measure, e.terrain), []).append(e)
    for (measure, terrain) in sorted(groups):
        L.append(f"### {measure} · {terrain}")
        L.append("")
        rows = []
        for e in groups[(measure, terrain)]:
            refs = "、".join(f"{r.ground_id}({_s(r.change)})" for r in e.references)
            rows.append((e.ground_id, f"{e.baseline}–{e.outcome}", _s(e.change),
                         refs, _s(e.reference_change), _s(e.effect),
                         f"±{e.uncertainty:.1f}" if e.uncertainty else "0",
                         "同沟参照，非独立证据" if e.same_gully_reference else ""))
        L += _table(["地面", "窗口", "自身变化pp", "参照（变化pp）", "参照均值pp",
                     "相对成效pp", "换算不确定度pp", "证据独立性"], rows)
        L.append("")
    if run.book.neighbor:
        L.append("### 明确已受邻地影响的范围（单列，不并入直接证据）")
        L.append("")
        rows = [(n.ground_id, n.plot_id, n.measure, n.terrain,
                 f"{n.baseline}–{n.outcome}", n.first_affected,
                 _s(n.change), n.note) for n in run.book.neighbor]
        L += _table(["地面", "项目", "措施", "地貌", "窗口", "首次受影响",
                     "自身变化pp", "说明"], rows)
        L.append("")
    if run.book.deferred or run.book.terrain_notes:
        L.append("### 暂不判定")
        L.append("")
        for d in run.book.deferred:
            L.append(f"- {d.ground_id}（{d.measure}·{d.terrain}）：{d.reason}")
        for note in run.book.terrain_notes:
            L.append(f"- {note}")
        L.append("")

    L.append("## 4. 敏感性（范围归属 / 共变因素 / 换算不确定度）")
    L.append("")
    L.append("### 范围归属情景")
    L.append("")
    for sc in run.sensitivity.attribution:
        L.append(f"- **{sc.ground_id}（{sc.terrain}）{sc.scenario}**：{sc.detail}")
        for sw in sc.swings:
            L.append(f"  - {sw}")
    L.append("")
    L.append("### 降雨共变")
    L.append("")
    rows = [(r.label, r.treated_zone, "、".join(r.reference_zones),
             _s(r.rain_delta_treated), _s(r.rain_delta_reference), r.level)
            for r in run.sensitivity.rain]
    L += _table(["估计", "处理坡沟", "参照坡沟", "处理降雨变化mm",
                 "参照降雨变化mm", "敏感度"], rows)
    L.append("")
    if run.sensitivity.bridge_notes:
        L.append("### 桥接换算不确定度")
        L.append("")
        for note in run.sensitivity.bridge_notes:
            L.append(f"- {note}")
        L.append("")

    L.append("## 5. 合同验收复核")
    L.append("")
    for o in run.reviews:
        b, y = o.window
        L.append(f"### {o.review_id}（{o.plot_id}，合同窗 {b}–{y}，"
                 f"最低效果 {o.min_effect_pp:.1f}pp，状态：{o.status}）")
        L.append("")
        if o.snapshot is not None:
            L.append("（下表为当前口径复核，仅作批注；原签认依据见快照，不被覆盖。）")
            L.append("")
        rows = [(g.ground_id, g.terrain, g.attribution, _s(g.change), _s(g.effect),
                 g.verdict, "；".join(g.reasons) or "—") for g in o.grounds]
        L += _table(["合同地面", "地貌", "范围归属", "自身变化pp", "相对成效pp",
                     "判定", "说明"], rows)
        L.append("")
        if o.snapshot is not None:
            snap = o.snapshot
            L.append(f"- 原签认快照（数据版本 {snap.data_version}）：{snap.verdict}；"
                     f"当时采用证据：{'、'.join(snap.evidence_ids)}")
            L.append(f"- 当前复核分类：**{o.classification}**（原签认不覆盖）")
            for note in o.notes:
                L.append(f"  - {note}")
        else:
            L.append(f"- 当前复核结论：**{o.verdict}**")
        if o.supplements:
            L.append("- 补证事项：")
            for s in o.supplements:
                L.append(f"  - {s}")
        L.append("")

    L.append("## 6. 续建核定与资源排布")
    L.append("")
    L.append("### 候选适用性")
    L.append("")
    rows = [(a.candidate_id, a.measure, a.terrain, a.status, _s(a.best_effect),
             "、".join(a.evidence) or "—", "；".join(a.notes) or "—")
            for a in run.renewals.applicability]
    L += _table(["候选", "措施", "地貌", "适用性", "最佳成效pp", "依据地块", "备注"], rows)
    L.append("")
    L.append("### 窗口排布")
    L.append("")
    for w in run.renewals.windows:
        L.append(f"- **{w.window}**：容量 {w.capacity_days:.0f} 工日 / "
                 f"{w.capacity_yuan:.0f} 元；已生效占用 {'、'.join(w.occupied) or '无'}"
                 f"（余 {w.rem_days:.0f} 工日 / {w.rem_yuan:.0f} 元）")
        L.append(f"  - 本期签出：{'、'.join(w.signed) or '无'}")
        if w.feasible:
            alts = "、".join("{" + "+".join(combo) + "}" for combo in w.feasible)
            L.append(f"  - 可行组合：{alts}")
        for cid, reason in w.blocked:
            L.append(f"  - 暂缓 {cid}：{reason}")
    L.append("")
    if run.renewals.deadlock:
        L.append("**本周期没有任何组合能同时守住履约安排和适用条件，详见不能签出事项。**")
        L.append("")

    L.append("## 7. 补报影响清单")
    L.append("")
    L.append(f"本期补报 {len(supp)} 条（{supp_date} 到件）："
             + ("、".join(o.observation_id for o in supp) if supp else "无"))
    L.append("")
    L.append("- 未验收合同需补证：")
    if run.impact["需补证合同"]:
        for rid, items in run.impact["需补证合同"]:
            L.append(f"  - {rid}：{'；'.join(items)}")
    else:
        L.append("  - 无")
    L.append("- 现可排续建组合：")
    if run.impact["现可排续建组合"]:
        for window, ids in run.impact["现可排续建组合"]:
            L.append(f"  - {window}：{'、'.join(ids)}")
    else:
        L.append("  - 无")
    L.append("- 已验收维持原结论但需人工复议："
             + ("、".join(run.impact["已验收维持但需人工复议"]) or "无"))
    if run.impact["改判风险需人工复议"]:
        L.append("- 改判风险（原签认保留，需人工复议）："
                 + "、".join(run.impact["改判风险需人工复议"]))
    L.append("")

    L.append("## 8. 不能签出事项")
    L.append("")
    if run.unsignable:
        for item in run.unsignable:
            L.append(f"- {item}")
    else:
        L.append("- 无")
    L.append("")
    return "\n".join(L)


def run_to_json(run: ReviewRun) -> dict:
    """机器可读侧车：供季度间比对与留痕。"""
    ds = run.dataset
    return {
        "as_of": ds.as_of.isoformat(),
        "config": {
            "renewal_min_effect_pp": run.config.renewal_min_effect_pp,
            "min_bridge_uncertainty_pp": run.config.min_bridge_uncertainty_pp,
            "rain_tolerance_mm": run.config.rain_tolerance_mm,
        },
        "supplementary_batch": [
            {"observation_id": o.observation_id, "ground_id": o.ground_id,
             "year": o.year, "method": o.method, "value": o.value,
             "received_at": o.received_at.isoformat()} for o in run.supplementary],
        "common_ground": {
            "total_area_once": run.common.total_area_once,
            "total_area_reported": run.common.total_area_reported,
            "overlap_area": run.common.overlap_area,
            "shared_grounds": [g.ground_id for g in run.common.grounds if g.shared],
        },
        "effects": {
            "direct": [
                {"measure": e.measure, "terrain": e.terrain, "ground_id": e.ground_id,
                 "window": [e.baseline, e.outcome], "change": e.change,
                 "reference_change": e.reference_change, "effect": e.effect,
                 "uncertainty": e.uncertainty,
                 "references": [r.ground_id for r in e.references],
                 "same_gully_reference": e.same_gully_reference,
                 "evidence_ids": list(e.evidence_ids)} for e in run.book.direct],
            "neighbor": [
                {"ground_id": n.ground_id, "plot_id": n.plot_id, "measure": n.measure,
                 "terrain": n.terrain, "window": [n.baseline, n.outcome],
                 "first_affected": n.first_affected,
                 "affected_before_direct": n.affected_before_direct,
                 "change": n.change, "note": n.note} for n in run.book.neighbor],
            "deferred": [
                {"ground_id": d.ground_id, "measure": d.measure,
                 "terrain": d.terrain, "reason": d.reason} for d in run.book.deferred],
            "terrain_notes": list(run.book.terrain_notes),
        },
        "sensitivity": {
            "attribution": [
                {"ground_id": s.ground_id, "terrain": s.terrain, "scenario": s.scenario,
                 "detail": s.detail, "swings": list(s.swings)}
                for s in run.sensitivity.attribution],
            "rain": [
                {"label": r.label, "treated_zone": r.treated_zone,
                 "reference_zones": list(r.reference_zones),
                 "rain_delta_treated": r.rain_delta_treated,
                 "rain_delta_reference": r.rain_delta_reference,
                 "level": r.level} for r in run.sensitivity.rain],
            "bridge_notes": list(run.sensitivity.bridge_notes),
        },
        "reviews": [
            {"review_id": o.review_id, "plot_id": o.plot_id, "status": o.status,
             "window": list(o.window), "min_effect_pp": o.min_effect_pp,
             "verdict": o.verdict, "supplements": list(o.supplements),
             "classification": o.classification, "notes": list(o.notes),
             "grounds": [
                 {"ground_id": g.ground_id, "terrain": g.terrain,
                  "attribution": g.attribution, "boundary_note": g.boundary_note,
                  "change": g.change, "effect": g.effect, "verdict": g.verdict,
                  "reasons": list(g.reasons), "evidence_ids": list(g.evidence_ids)}
                 for g in o.grounds],
             "snapshot": None if o.snapshot is None else {
                 "data_version": o.snapshot.data_version,
                 "verdict": o.snapshot.verdict,
                 "ground_effects": [[g, v] for g, v in o.snapshot.ground_effects],
                 "evidence_ids": list(o.snapshot.evidence_ids)}}
            for o in run.reviews],
        "renewals": {
            "applicability": [
                {"candidate_id": a.candidate_id, "measure": a.measure,
                 "terrain": a.terrain, "status": a.status,
                 "best_effect": a.best_effect, "evidence": list(a.evidence),
                 "notes": list(a.notes)} for a in run.renewals.applicability],
            "windows": [
                {"window": w.window, "capacity_days": w.capacity_days,
                 "capacity_yuan": w.capacity_yuan, "occupied": list(w.occupied),
                 "remaining_days": w.rem_days, "remaining_yuan": w.rem_yuan,
                 "signed": list(w.signed),
                 "feasible": [list(c) for c in w.feasible],
                 "blocked": [[c, r] for c, r in w.blocked]}
                for w in run.renewals.windows],
            "unsignable": [[c, r] for c, r in run.renewals.unsignable],
            "deadlock": run.renewals.deadlock,
        },
        "impact": run.impact,
        "unsignable": list(run.unsignable),
    }
