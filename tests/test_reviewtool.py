import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path

from reviewtool import Config, load_dataset, run_review
from reviewtool import acceptance, effects, exposure, renewal
from reviewtool.__main__ import main as cli_main
from reviewtool.model import Capacity, Renewal
from reviewtool.series import chain_change, method_pair_offsets

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 11, 24)


def load():
    return load_dataset(ROOT / "data", as_of=AS_OF)


class SeriesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load()
        cls.obs = cls.ds.observations_by_ground()
        cls.offsets = method_pair_offsets(cls.obs)
        cls.cfg = Config()

    def chain(self, gid, b, o):
        return chain_change(self.obs[gid], b, o, pair_offsets=self.offsets,
                            min_uncertainty=self.cfg.min_bridge_uncertainty_pp)

    def test_bridged_switch_chains_with_uncertainty(self):
        # G-03 在 2024 年由样方甲切换样方乙，同年双方法桥接（42 甲 / 45 乙）
        c = self.chain("G-03", 2023, 2025)
        self.assertTrue(c.ok)
        self.assertEqual(len(c.bridges), 1)
        self.assertAlmostEqual(c.bridges[0].offset, 3.0)
        self.assertAlmostEqual(c.change, 2.0)  # 51 - 3 - 46
        self.assertAlmostEqual(c.uncertainty, 1.0)
        self.assertIn("O-3-bridge", c.evidence_ids)

    def test_unbridged_switch_cannot_chain(self):
        # G-02 在 2024 年切换样方乙（补报），无同年双方法重测
        c = self.chain("G-02", 2022, 2025)
        self.assertFalse(c.ok)
        self.assertIn("桥接", c.reason)

    def test_same_method_window_chains_directly(self):
        c = self.chain("G-01", 2022, 2025)
        self.assertTrue(c.ok)
        self.assertAlmostEqual(c.change, 16.0)
        self.assertAlmostEqual(c.uncertainty, 0.0)
        c2 = self.chain("G-10", 2024, 2025)  # 切换后同方法段
        self.assertTrue(c2.ok)
        self.assertAlmostEqual(c2.change, 7.0)
        self.assertEqual(c2.bridges, ())


class ExposureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ds = load()
        cls.exposures = exposure.build_exposures(cls.ds)

    def test_shared_ground_exposure_order(self):
        g02 = self.exposures["G-02"]
        self.assertEqual(g02.first_affected, 2023)  # 早于 P-02 开工 2024
        self.assertEqual(g02.current_plot, "P-02")
        self.assertTrue(g02.affected_before_direct)

    def test_reference_eligibility_respects_exposure_window(self):
        self.assertTrue(exposure.reference_eligible(self.exposures["G-06"], 2022, 2025))
        # G-08（2024 开工）在受影响以前可为较早开工组提供参照
        self.assertTrue(exposure.reference_eligible(self.exposures["G-08"], 2022, 2023))
        self.assertFalse(exposure.reference_eligible(self.exposures["G-08"], 2022, 2025))
        self.assertFalse(exposure.reference_eligible(self.exposures["G-02"], 2022, 2025))

    def test_common_ground_area_counted_once(self):
        common = exposure.common_ground(self.ds)
        shared = [g.ground_id for g in common.grounds if g.shared]
        self.assertEqual(shared, ["G-02"])
        self.assertAlmostEqual(common.total_area_once, 168.3, places=6)
        self.assertAlmostEqual(common.total_area_reported, 170.3, places=6)
        self.assertAlmostEqual(common.overlap_area, 2.0, places=6)


class EffectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = run_review(load())

    def estimate(self, measure, terrain, gid):
        for e in self.review.book.direct:
            if (e.measure, e.terrain, e.ground_id) == (measure, terrain, gid):
                return e
        self.fail(f"缺少估计 {measure}·{terrain}·{gid}")

    def test_direct_effects_against_untreated(self):
        self.assertAlmostEqual(self.estimate("封育", "坡腹", "G-01").effect, 12.0)
        self.assertAlmostEqual(self.estimate("封育", "坡腹", "G-11").effect, 10.0)
        self.assertAlmostEqual(self.estimate("封育加补植", "坡腹", "G-08").effect, 10.0)
        self.assertAlmostEqual(self.estimate("封育", "缓坡", "G-04").effect, 4.0)
        self.assertAlmostEqual(self.estimate("封育加补植", "缓坡", "G-10").effect, 4.0)

    def test_bridged_estimate_carries_uncertainty(self):
        e = self.estimate("封育加补植", "坡腹", "G-03")
        self.assertAlmostEqual(e.effect, 3.0)
        self.assertAlmostEqual(e.uncertainty, 1.0)

    def test_same_gully_reference_not_independent(self):
        e = self.estimate("封育加补植", "坡腹", "G-08")
        self.assertTrue(e.same_gully_reference)  # G-08 与参照 G-06 同属 D-3 降雨过程

    def test_neighbor_affected_kept_separate(self):
        neighbor = {n.ground_id: n for n in self.review.book.neighbor}
        self.assertIn("G-02", neighbor)
        self.assertIn("G-05", neighbor)
        self.assertTrue(neighbor["G-02"].affected_before_direct)
        self.assertIsNone(neighbor["G-02"].change)  # 无桥接，自身趋势不可连
        self.assertAlmostEqual(neighbor["G-05"].change, 22.0)
        # 邻地影响范围不得混入直接证据
        self.assertNotIn("G-02", {e.ground_id for e in self.review.book.direct})
        self.assertNotIn("G-05", {e.ground_id for e in self.review.book.direct})

    def test_terrain_without_reference_deferred(self):
        self.assertTrue(any("坡脚" in n for n in self.review.book.terrain_notes))

    def test_attribution_sensitivity_shows_swing(self):
        scenarios = [s for s in self.review.sensitivity.attribution
                     if s.ground_id == "G-05" and s.scenario == "误作未治理参照"]
        self.assertTrue(scenarios)
        text = " ".join(scenarios[0].swings)
        self.assertIn("跌破续建阈值", text)  # 缓坡参照池掺入邻地影响地后 +4.0 → +2.3

    def test_rain_covariate_low_sensitivity(self):
        for r in self.review.sensitivity.rain:
            self.assertEqual(r.level, "低")


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = run_review(load())
        cls.outcomes = {o.review_id: o for o in cls.review.reviews}

    def test_r01_partial_pass_g02_supplement(self):
        o = self.outcomes["R-01"]
        self.assertEqual(o.verdict, "暂不签出（待补证）")
        g01 = next(g for g in o.grounds if g.ground_id == "G-01")
        self.assertEqual(g01.verdict, "达标")
        self.assertAlmostEqual(g01.effect, 12.0)
        g02 = next(g for g in o.grounds if g.ground_id == "G-02")
        self.assertEqual(g02.verdict, "排除")
        self.assertEqual(g02.attribution, "邻地影响")
        self.assertIn("现行边界版本", g02.boundary_note)
        self.assertTrue(o.supplements)

    def test_r02_remains_pending(self):
        o = self.outcomes["R-02"]
        self.assertEqual(o.verdict, "暂不签出（待补证）")
        g03 = next(g for g in o.grounds if g.ground_id == "G-03")
        self.assertEqual(g03.verdict, "暂不判定")  # +3.0±1.0 触及阈值 4.0
        g02 = next(g for g in o.grounds if g.ground_id == "G-02")
        self.assertEqual(g02.verdict, "排除")

    def test_r03_snapshot_preserved_and_flagged(self):
        o = self.outcomes["R-03"]
        self.assertIsNotNone(o.snapshot)
        self.assertEqual(o.snapshot.data_version, "2025-01-10")
        self.assertEqual(o.snapshot.verdict, "可签认")
        self.assertEqual(dict(o.snapshot.ground_effects)["G-05"], 15.5)
        # G-10 在 2025 年开工前为较早开工组提供参照，纳入当时参照池
        self.assertIn("O-10-2021", o.snapshot.evidence_ids)
        # 快照只含当时可见证据，不含 2026 年补报
        self.assertIn("O-5-2021", o.snapshot.evidence_ids)
        self.assertNotIn("O-2-2024", o.snapshot.evidence_ids)
        # 结论维持，但 G-05 现列为邻地影响范围 → 人工复议
        self.assertEqual(o.classification, "维持但需人工复议")
        self.assertEqual(o.verdict, "可签认")  # 原签认不被当前改判覆盖
        self.assertTrue(any("邻地影响" in n for n in o.notes))

    def test_data_version_view(self):
        ds = load()
        past = ds.view(date(2025, 1, 10))
        ids = {o.observation_id for o in past.observations}
        self.assertNotIn("O-2-2024", ids)  # 补报 2026-02-15 才到件
        self.assertIn("O-5-2024", ids)


class RenewalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = run_review(load())
        cls.plan = cls.review.renewals

    def test_applicability(self):
        apps = {a.candidate_id: a for a in self.plan.applicability}
        self.assertEqual(apps["N-03"].status, "适用")
        self.assertEqual(apps["N-05"].status, "适用")
        self.assertEqual(apps["N-04"].status, "边界适用")
        self.assertEqual(apps["N-02"].status, "边界适用")
        # 占用现行对照的候选应提示
        self.assertTrue(any("对照" in n for n in apps["N-03"].notes))

    def test_schedule_respects_capacity_and_prerequisite(self):
        windows = {w.window: w for w in self.plan.windows}
        spring = windows["2026春"]
        self.assertEqual(spring.occupied, ("N-01",))  # 已生效占用资源
        self.assertEqual(spring.signed, ("N-03",))
        autumn = windows["2026秋"]
        self.assertEqual(autumn.signed, ("N-05",))  # 证据强于 N-04
        self.assertIn(("N-04", "2026秋"), [(c, w.window) for w in self.plan.windows
                                           for c, _ in w.blocked])
        unsignable = dict(self.plan.unsignable)
        self.assertIn("N-02", unsignable)
        self.assertIn("前置工程 N-04", unsignable["N-02"])
        self.assertIn("N-04", unsignable)
        self.assertFalse(self.plan.deadlock)

    def test_combined_only_evidence_not_signable_as_single(self):
        book = self.review.book
        candidate = Renewal("N-X", "G-02", "封育加补植", "坡脚",
                            "2026秋", 4, 30000, "提议", None)
        app = renewal.assess_candidates([candidate], book, Config())[0]
        self.assertEqual(app.status, "不可单项签认")

    def test_deadlock_reports_unsignable(self):
        ds = load()
        stuck = Renewal("N-Y", "G-11", "封育", "坡腹", "2026春", 99, 100, "提议", None)
        ds2 = replace(ds, renewals=[stuck],
                      capacity={"2026春": Capacity("2026春", 1, 1000)})
        plan = run_review(ds2).renewals
        self.assertTrue(plan.deadlock)
        self.assertEqual(plan.unsignable[0][0], "N-Y")


class CliTests(unittest.TestCase):
    def test_cli_run_outputs_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = cli_main(["run", "--data", str(ROOT / "data"),
                           "--out", tmp, "--as-of", "2026-11-24"])
            self.assertEqual(rc, 0)
            md = (Path(tmp) / "report.md").read_text(encoding="utf-8")
            self.assertIn("补报影响清单", md)
            self.assertIn("人工复议", md)
            self.assertIn("不能签出", md)
            import json
            payload = json.loads((Path(tmp) / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["common_ground"]["shared_grounds"], ["G-02"])
            r03 = next(r for r in payload["reviews"] if r["review_id"] == "R-03")
            self.assertEqual(r03["snapshot"]["data_version"], "2025-01-10")
            self.assertEqual(r03["classification"], "维持但需人工复议")


if __name__ == "__main__":
    unittest.main()
