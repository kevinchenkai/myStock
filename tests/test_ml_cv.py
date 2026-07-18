"""借鉴 ② — Purged/Embargo 滚动切分纯函数单测。

只验 index 集合（cv.py 零数据依赖）：隔离带存在、min_train 守住、扩张窗单调、
purge 宽度 = feat_lookback + label_horizon、第 0 折不被 off-by-purge 丢弃、
短序列优雅降折。
"""
from mystock.ml.cv import PurgedConfig, purged_walk_forward


def test_no_overlap_purge_gap():
    # 每折 train 末尾与 test 首行之间应有 >= purge_w 的隔离带
    cfg = PurgedConfig(n_folds=4, min_train=250)
    splits = purged_walk_forward(1255, cfg)
    assert splits
    for train, test in splits:
        assert max(train) < min(test) - cfg.purge_w + 1  # tr_end = te_start - purge_w
        # 更严格：train 最大 index 应 <= te_start - purge_w - 1
        assert max(train) <= min(test) - cfg.purge_w - 1


def test_min_train_respected():
    cfg = PurgedConfig(n_folds=5, min_train=250)
    for train, _ in purged_walk_forward(1255, cfg):
        assert len(train) >= cfg.min_train


def test_expanding_window_monotone():
    # 折 k+1 的 train 是折 k 的超集（扩张窗单调增）
    splits = purged_walk_forward(1255, PurgedConfig(n_folds=5, min_train=250))
    for a, b in zip(splits, splits[1:]):
        assert set(a[0]).issubset(set(b[0]))
        assert len(b[0]) > len(a[0])


def test_purge_width_matches_feature_window():
    cfg = PurgedConfig(feat_lookback=21, label_horizon=1)
    assert cfg.purge_w == 22
    # 隔离带确实是 22 行：te_start - tr_end == purge_w
    splits = purged_walk_forward(1255, cfg)
    for train, test in splits:
        tr_end = max(train) + 1
        assert min(test) - tr_end == cfg.purge_w


def test_first_fold_survives():
    # 回归 v0.1 off-by-purge bug：第 0 折应存在，且 train 恰为 [0, min_train)
    cfg = PurgedConfig(n_folds=4, min_train=250, feat_lookback=21, label_horizon=1)
    splits = purged_walk_forward(1255, cfg)
    assert splits, "第 0 折被错误丢弃"
    train0, test0 = splits[0]
    assert train0[0] == 0
    assert len(train0) == cfg.min_train           # 恰好 250，未被 off-by-purge 砍空
    assert test0[0] == cfg.min_train + cfg.purge_w  # test 从 272 起（250 + 22）


def test_degenerate_short_series():
    # n 很小 → 优雅降折/返回空，不抛异常
    assert purged_walk_forward(50, PurgedConfig(min_train=250)) == []
    assert purged_walk_forward(0, PurgedConfig()) == []
    # 刚好够一折：min_train + purge_w + 一点 test
    short = purged_walk_forward(300, PurgedConfig(n_folds=4, min_train=250))
    assert isinstance(short, list)
    for train, test in short:
        assert len(train) >= 250 and len(test) >= 1
