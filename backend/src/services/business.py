"""管段漏损分诊：可剪枝的决策树与分层留出验证。"""
import numpy as np
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.model_selection import train_test_split
from sklearn.metrics import precision_score, recall_score, f1_score, confusion_matrix

from src.utils.serialization import checksum

FEATURES = ["night_flow", "pressure_drop", "acoustic_db", "pipe_age"]
LABELS = ["夜间流量(m³/h)", "压力降(kPa)", "声学读数(dB)", "管龄(年)"]
ACOUSTIC_INDEX = FEATURES.index("acoustic_db")
TRIAGE_LEAK = "优先听漏复核"
TRIAGE_ROUTINE = "常规巡查"
DRILL_DEFAULT_LO = 12.0
DRILL_DEFAULT_HI = 85.0
DRILL_MAX_DB = 200.0


def metadata():
    return {
        "name": "供水漏损分诊实验", "short": "water-triage",
        "tagline": "把历史巡检证据转成可解释分诊规则，先核验漏检代价。",
        "client": "澄川水务 · 城北巡检班12人 · 280个已复核管段样本",
        "workflow": "值班员把夜间流量、压力和听漏记录抄入表格，根据单一声学阈值安排巡查。",
        "pains": ["单阈值遗漏低噪声漏点", "缺少对巡检规则复杂度的控制", "规则好坏只凭训练样本判断"],
        "upstream": "城北分区流量计与人工复核台账 north_zone_segments.csv",
        "downstream": "巡检组长审阅分诊误报和漏检，决定下一轮人工现场检查顺序",
        "entities": ["管段复核样本", "分诊规则试验", "巡检复核简报"],
        "dataset_name": "城北管段夜间流量与已复核标签",
        "columns": [{"key": "segment_id", "label": "管段编号", "type": "string"}] + [{"key": k, "label": l, "type": "number"} for k, l in zip(FEATURES, LABELS)] + [{"key": "confirmed_leak", "label": "复核漏点(1/0)", "type": "number"}],
        "parameters": [{"key": "max_depth", "label": "决策树最大深度", "type": "number", "default": 3, "min": 1, "max": 8}, {"key": "min_leaf", "label": "叶节点最少样本", "type": "number", "default": 5, "min": 2, "max": 20}],
        "drill": {"label": "听漏仪停用演练", "description": "听漏仪停用时，用已完成试验的原规则复核验证样本；把声学读数替换为一个共用区间，区间内可能翻转的管段列为待复测。", "default_lo": DRILL_DEFAULT_LO, "default_hi": DRILL_DEFAULT_HI, "min": 0, "max": DRILL_MAX_DB, "unit": "dB", "acoustic_field": "acoustic_db"},
        "workbench_title": "漏损分诊规则试验", "action_label": "训练并核验分诊规则", "unit": "段管线",
        "accent": "oklch(48% 0.10 210)", "style": "blueprint",
        "seed_story": ["07:30 值班员导入 north_zone_segments.csv 的280段历史复核记录", "07:40 检查夜间流量、压力降、声学读数与管龄", "08:00 用深度3、最小叶节点5发起规则试验", "08:01 系统固定保留30%样本用于独立核验", "08:10 组长先看漏检数量，再看特征重要性", "08:20 将深度从3调为5比较验证F1", "08:30 下载管段分诊明细，选择现场复核清单", "09:00 管理员安排次日07:30规则复核简报"],
        "method": "分层70/30留出，random_state=42；Gini决策树按max_depth和min_samples_leaf剪枝；在留出集计算精确率、召回率和F1。",
        "limitations": ["模拟历史样本不能替代听漏与开挖确认", "未使用空间邻接关系", "特征重要性反映模型分裂贡献而非因果"],
    }


def seed_records():
    rng = np.random.default_rng(1009)
    rows = []
    for i in range(280):
        flow = float(np.clip(rng.normal(23, 10), 2, 60))
        pressure = float(np.clip(rng.gamma(2.2, 5), 0.5, 40))
        sound = float(np.clip(rng.normal(42, 12), 12, 85))
        age = int(rng.integers(1, 46))
        leak = (flow > 26 and pressure > 10) or (sound > 50 and age > 18) or pressure > 25
        if rng.random() < 0.07:
            leak = not leak
        rows.append({"segment_id": f"NC-{i+1:03}", "night_flow": round(flow, 2), "pressure_drop": round(pressure, 2), "acoustic_db": round(sound, 1), "pipe_age": age, "confirmed_leak": int(leak)})
    return rows


def _validate_params(params):
    try:
        depth_raw = float(params.get("max_depth", 3))
        leaf_raw = float(params.get("min_leaf", 5))
        depth, leaf = int(depth_raw), int(leaf_raw)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("树深度与最小叶节点须为整数。") from exc
    if depth != depth_raw or leaf != leaf_raw or not 1 <= depth <= 8 or not 2 <= leaf <= 20:
        raise ValueError("树深度须为1~8，最小叶节点须为2~20，且都为整数。")
    return depth, leaf


def _validate_records(records):
    if len(records) < 40:
        raise ValueError("至少需要40条管段复核样本。")
    try:
        x = np.asarray([[float(row[k]) for k in FEATURES] for row in records])
        y_raw = np.asarray([float(row["confirmed_leak"]) for row in records])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("管段字段必须完整且为数值。") from exc
    if not np.isfinite(x).all() or (x < 0).any() or not np.isin(y_raw, [0, 1]).all():
        raise ValueError("测量值须非负有限，复核标签须为0/1。")
    y = y_raw.astype(int)
    if np.bincount(y, minlength=2).min() < 8:
        raise ValueError("漏点与非漏点样本各至少8条。")
    return x, y


def fit_model(records, params):
    """按固定口径复现原试验：同一数据、同一参数得到同一棵树。"""
    depth, leaf = _validate_params(params)
    x, y = _validate_records(records)
    train, valid = train_test_split(np.arange(len(y)), test_size=0.3, stratify=y, random_state=42)
    model = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=leaf, random_state=42)
    model.fit(x[train], y[train])
    return model, x, y, train, valid


def tree_spec(model):
    if model is None:
        raise ValueError("试验未包含可复用的规则树。")
    t = model.tree_
    return {
        "feature": [int(v) for v in t.feature],
        "threshold": [float(v) for v in t.threshold],
        "left": [int(v) for v in t.children_left],
        "right": [int(v) for v in t.children_right],
        "value": [[float(c) for c in row] for row in t.value.reshape(len(t.value), -1)],
        "samples": [int(v) for v in t.n_node_samples],
    }


def _triage_for_counts(value):
    c0, c1 = float(value[0]), float(value[1])
    total = c0 + c1
    pct = round(100.0 * c1 / total, 1) if total else 0.0
    return (TRIAGE_LEAK if c1 > c0 else TRIAGE_ROUTINE), pct


def leaf_outcome(tree, node):
    return _triage_for_counts(tree["value"][int(node)])


def _walk(tree, features, acoustic, node=0):
    while True:
        left, right = tree["left"][node], tree["right"][node]
        if left == right == -1:
            return leaf_outcome(tree, node)
        feature = tree["feature"][node]
        value = acoustic if feature == ACOUSTIC_INDEX else features[FEATURES[feature]]
        node = left if value <= tree["threshold"][node] else right


def acoustic_candidates(tree, lo, hi):
    """决策树对声学是阶跃函数：候选取值 = 两端点 + 区间内全部分裂阈值。

    阈值本身走左支，右支由更大的候选值覆盖；两端相同也要把中间阈值全部走一遍。
    """
    thresholds = sorted({float(t) for f, t in zip(tree["feature"], tree["threshold"]) if f == ACOUSTIC_INDEX and lo <= float(t) <= hi})
    return sorted(set([float(lo), float(hi)] + thresholds)), thresholds


def _flip_triggers(candidates, outcomes):
    """翻转只可能发生在候选序列的相邻两项之间，且边界必为声学分叉阈值。

    候选值已包含区间内全部声学阈值；相邻候选结论不同，说明恰好跨过 candidates[i-1] 这个阈值。
    """
    triggers, seen = [], set()
    for i in range(1, len(candidates)):
        before, after = outcomes[i - 1][0], outcomes[i][0]
        boundary = candidates[i - 1]
        if before == after or boundary in seen:
            continue
        seen.add(boundary)
        triggers.append({"threshold": round(boundary, 4), "left": [before], "right": [after]})
    return triggers


def _trigger_text(triggers):
    if not triggers:
        return ""
    ordered = [f">{t['threshold']:g} dB 时翻为{'/'.join(t['right'])}（≤{t['threshold']:g} dB 为{'/'.join(t['left'])}）" for t in triggers]
    text = "声学" + "；".join(ordered[:3])
    return text + ("；等" if len(ordered) > 3 else "")


def drill_verdict(snapshot, ordinal, sample, lo, hi):
    """单条验证样本的区间裁决；可逐条续算，同 ordinal/segment_id 只记一次。"""
    tree = snapshot["tree"]
    lo, hi = float(lo), float(hi)
    candidates, thresholds = acoustic_candidates(tree, lo, hi)
    features = sample["features"]
    outcomes = [_walk(tree, features, value) for value in candidates]
    triages = sorted({triage for triage, _ in outcomes})
    risks = [pct for _, pct in outcomes]
    retest = len(triages) > 1
    lo_triage = outcomes[0][0]
    hi_triage = outcomes[-1][0]
    triggers = _flip_triggers(candidates, outcomes) if retest else []
    recorded = float(features[FEATURES[ACOUSTIC_INDEX]])
    return {
        "ordinal": ordinal,
        "segment_id": str(sample["segment_id"]),
        "acoustic_recorded": round(recorded, 2),
        "night_flow": round(float(features["night_flow"]), 2),
        "pressure_drop": round(float(features["pressure_drop"]), 2),
        "pipe_age": int(round(float(features["pipe_age"]))),
        "baseline_triage": sample["baseline_triage"],
        "baseline_leak_pct": round(float(sample["baseline_leak_pct"]), 1),
        "lo_triage": lo_triage,
        "hi_triage": hi_triage,
        "possible_triages": triages,
        "possible_text": " / ".join(triages),
        "risk_min": round(min(risks), 1),
        "risk_max": round(max(risks), 1),
        "risk_text": f"{round(min(risks), 1)}–{round(max(risks), 1)}",
        "retest_required": retest,
        "trigger": _trigger_text(triggers) if retest else "",
        "triggers": triggers,
        "checked_values": len(candidates),
        "acoustic_thresholds": [round(t, 4) for t in thresholds],
        "interior_only_flip": bool(retest and lo_triage == hi_triage),
    }


def drill_verdicts(snapshot, lo, hi):
    return [drill_verdict(snapshot, ordinal, sample, lo, hi) for ordinal, sample in enumerate(snapshot["validation"])]


def drill_summary(rows):
    expected = len(rows)
    retest = [r for r in rows if r["retest_required"]]
    interior = [r for r in retest if r["interior_only_flip"]]
    candidates = rows[0]["checked_values"] if rows else 0
    thresholds = sorted({t for r in rows for t in r["acoustic_thresholds"]})
    return {
        "summary": f"{expected}段原验证样本套用共用声学区间；穷举区间内{candidates}个候选值（含全部声学分叉阈值）后，{len(retest)}段结论可能翻转，列为待复测。",
        "metrics": [
            {"label": "验证样本", "value": expected, "unit": "段"},
            {"label": "待复测", "value": len(retest), "unit": "段"},
            {"label": "内部翻转", "value": len(interior), "unit": "段"},
            {"label": "候选声学值", "value": candidates, "unit": "个"},
        ],
        "findings": [
            f"区间内可达的声学分叉阈值：{'、'.join(f'{t:g}' for t in thresholds)} dB。" if thresholds else "区间内没有声学分叉阈值，所有样本结论稳定。",
            "两端结论相同的样本也逐阈值检查了中间取值，内部翻转单独标出。" if interior else "没有出现两端相同、中间翻转的样本。",
            "原规则树、原验证样本与训练参数均未重新拟合，演练只替换声学读数。",
        ],
        "warnings": ["演练仅用于听漏仪停用期间安排人工复测顺序，不替代现场听漏与开挖确认。"],
        "method": "读取已完成试验冻结的规则树与验证样本；把声学读数替换为共用区间，按区间内全部声学分叉阈值穷举左右结论，两端与中间都覆盖；其他字段按原记录值沿原规则判定。",
    }


def drill_table(rows):
    columns = [
        {"key": "segment_id", "label": "管段"},
        {"key": "baseline_triage", "label": "原分诊"},
        {"key": "possible_text", "label": "区间内可能结论"},
        {"key": "risk_text", "label": "叶节点漏点率区间(%)"},
        {"key": "acoustic_recorded", "label": "原声学读数(dB)"},
        {"key": "trigger", "label": "翻转触发条件"},
    ]
    return {"columns": columns, "rows": rows}


def drill_brief(rows):
    columns = [
        {"key": "segment_id", "label": "待复测管段"},
        {"key": "possible_text", "label": "区间内可能结论"},
        {"key": "risk_text", "label": "叶节点漏点率区间(%)"},
        {"key": "acoustic_recorded", "label": "原声学读数(dB)"},
        {"key": "trigger", "label": "翻转触发条件"},
    ]
    return {"columns": columns, "rows": [r for r in rows if r["retest_required"]]}


def drill_snapshot(job, dataset):
    """冻结原试验的验证样本、原规则树与输入指纹；后续迟到记录不进入本快照。"""
    context = (job.result or {}).get("drill_context")
    source = "job_result"
    if not context:
        rebuilt = analyze(dataset.records, job.params)
        context = rebuilt.get("drill_context")
        source = "recomputed"
    if not context or not context.get("validation") or not context.get("tree"):
        raise ValueError("原试验缺少可复用的规则树，无法演练。")
    snapshot = {
        "job_id": int(job.id),
        "job_name": job.name,
        "params": job.params,
        "dataset_id": int(dataset.id),
        "dataset_checksum": dataset.checksum,
        "context_source": source,
        "model_fingerprint": context["fingerprint"],
        "tree": context["tree"],
        "validation": context["validation"],
        "acoustic_index": int(context["acoustic_index"]),
        "source_result_checksum": checksum(job.result or {}),
    }
    snapshot["snapshot_digest"] = checksum(snapshot)
    return snapshot


def _drill_context(model, valid, x, y, records):
    spec = tree_spec(model)
    probability = model.predict_proba(x[valid])[:, 1]
    prediction = model.predict(x[valid])
    samples = []
    for j in range(len(valid)):
        index = int(valid[j])
        row = records[index]
        samples.append({
            "segment_id": str(row.get("segment_id", f"row-{index+1}")),
            "features": {k: float(row[k]) for k in FEATURES},
            "baseline_triage": TRIAGE_LEAK if int(prediction[j]) else TRIAGE_ROUTINE,
            "baseline_leak_pct": round(float(probability[j]) * 100, 1),
        })
    return {
        "acoustic_index": ACOUSTIC_INDEX,
        "feature_names": FEATURES,
        "tree": spec,
        "validation": samples,
        "validation_index": [int(v) for v in valid],
        "fingerprint": checksum({"params": {"depth": int(model.get_depth()), "leaves": int(model.get_n_leaves())}, "tree": spec, "validation": [s["segment_id"] for s in samples]}),
    }


def analyze(records, params):
    depth, _ = _validate_params(params)
    model, x, y, train, valid = fit_model(records, params)
    probability = model.predict_proba(x[valid])[:, 1]
    prediction = model.predict(x[valid])
    precision = float(precision_score(y[valid], prediction, zero_division=0))
    recall = float(recall_score(y[valid], prediction, zero_division=0))
    f1 = float(f1_score(y[valid], prediction, zero_division=0))
    tn, fp, fn, tp = [int(v) for v in confusion_matrix(y[valid], prediction, labels=[0, 1]).ravel()]
    order = np.argsort(-probability)
    rows = [{"segment_id": str(records[int(valid[j])].get("segment_id", f"row-{int(valid[j])+1}")), "risk_pct": round(float(probability[j]) * 100, 1), "triage": "优先听漏复核" if prediction[j] else "常规巡查", "actual": "已确认漏点" if y[valid[j]] else "已确认正常"} for j in order]
    important = int(np.argmax(model.feature_importances_))
    first_rule = export_text(model, feature_names=FEATURES).splitlines()[0].strip()
    return {
        "summary": f"{len(train)}段训练、{len(valid)}段独立验证；实际树深{model.get_depth()}，生成{model.get_n_leaves()}个可复核叶节点。",
        "metrics": [{"label": "验证F1", "value": round(f1, 4)}, {"label": "漏点召回率", "value": round(recall * 100, 2), "unit": "%"}, {"label": "分诊精确率", "value": round(precision * 100, 2), "unit": "%"}, {"label": "验证漏检", "value": fn, "unit": "段"}],
        "table": {"columns": [{"key": "segment_id", "label": "管段"}, {"key": "risk_pct", "label": "叶节点漏点率(%)"}, {"key": "triage", "label": "分诊建议"}, {"key": "actual", "label": "留出集复核结果"}], "rows": rows},
        "chart": {"type": "bar", "labels": LABELS, "series": [{"name": "Gini分裂重要性", "values": [round(float(v), 4) for v in model.feature_importances_]}]},
        "findings": [f"验证混淆矩阵：命中漏点{tp}、误报{fp}、漏检{fn}、正确排除{tn}。", f"模型最重视{LABELS[important]}；根节点规则：{first_rule}。", "调高树深可能拟合局部噪声；只依据留出F1和漏检率选择规则复杂度。"],
        "method": metadata()["method"], "warnings": ["分诊规则仅辅助现场巡检，不作为自动停水或施工决策。"],
        "drill_context": _drill_context(model, valid, x, y, records),
    }
