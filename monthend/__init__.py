"""联产加工月结：同一份业务口径下的首过、责任与复盘对账。

子模块：
- loader：读入交换资料并做结构与引用校验
- trace：领料路由、拆并批交接与可追溯检验的路径还原
- responsibility：返修责任的四类口径（正式确认/共担/未认领/无法分摊）
- pivots：区域、产线、责任方、客户视角的透视与重算
- review：复盘单生命周期（新开、覆盖、再开、保留）
- periods：期间关账口径、已签发视角与迟到更正影响
- reports：对内复盘包与对外月报两份输出
"""

from __future__ import annotations

from .loader import Dataset, load_dataset
from .trace import build_traces
from .responsibility import ResponsibilityLedger
from .pivots import PivotEngine
from .review import ReviewBoard
from .periods import PeriodBook
from .reports import build_working_package, build_issued_report

__all__ = [
    "Dataset",
    "load_dataset",
    "build_traces",
    "ResponsibilityLedger",
    "PivotEngine",
    "ReviewBoard",
    "PeriodBook",
    "build_working_package",
    "build_issued_report",
]
