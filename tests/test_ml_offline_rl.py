"""P4 离线 RL 数据集收集逻辑单测（不依赖 d3rlpy/GPU）。"""
import numpy as np
import pytest

pytest.importorskip("sklearn")  # 需要预测器后端

from mystock.ml.offline_rl import collect_dataset, RLConfig
from mystock.ml.policy import N_ACTIONS


def _has_db():
    from mystock.ml import config as mlcfg
    return mlcfg.ML_DB_PATH.exists()


@pytest.mark.skipif(not _has_db(), reason="需要 data/ml/mystock_ml.db")
def test_collect_dataset_shapes_and_coverage():
    cfg = RLConfig(n_episodes=3)
    obs, acts, rews, terms = collect_dataset("US.NVDA", cfg)
    # 形状一致
    n = len(obs)
    assert acts.shape == (n,) and rews.shape == (n,) and terms.shape == (n,)
    # state 维度 = 1 常数 + 16 特征
    assert obs.shape[1] == 17
    # 动作 id 在合法范围
    assert acts.min() >= 0 and acts.max() < N_ACTIONS
    # 每个 episode 末尾有 terminal
    assert int(terms.sum()) == cfg.n_episodes
    # 行为策略（ε=0.5）应覆盖多个动作（不只是"不动"）
    assert len(np.unique(acts)) >= 5


@pytest.mark.skipif(not _has_db(), reason="需要 data/ml/mystock_ml.db")
def test_reward_is_finite():
    cfg = RLConfig(n_episodes=2)
    _, _, rews, _ = collect_dataset("US.PDD", cfg)
    assert np.all(np.isfinite(rews))
