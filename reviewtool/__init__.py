"""生态修复成效复核与续建核定工具（季度复用）。

按地块版本与实际暴露时段使用监测资料：
- 共同地面还原与面积去重（重合监测范围只计一次）
- 封育 / 封育加补植相对可比未治理地面的成效估计，
  区分直接实施与明确已受邻地影响的范围
- 测量切换的同期重测桥接与换算不确定度判断
- 合同验收复核（含已验收签认的当时数据版本快照，当前改判不覆盖原依据）
- 续建候选的适用性核定与作业窗口 / 资金容量排布
"""

from .core import ReviewRun, run_review
from .model import Config, Dataset, load_dataset

__all__ = ["Config", "Dataset", "ReviewRun", "load_dataset", "run_review"]
