import itertools

FIELD_ORDER = ["minute", "hour", "dom", "month", "dow"]
FIELD_LABELS = {"minute": "分", "hour": "时", "dom": "日", "month": "月", "dow": "周"}
FIELD_RANGES = {"minute": (0, 59), "hour": (0, 23), "dom": (1, 31), "month": (1, 12), "dow": (0, 7)}
LAUNCHD_KEY = {"minute": "Minute", "hour": "Hour", "dom": "Day", "month": "Month", "dow": "Weekday"}
MAX_EXPANDED_ENTRIES = 100


class CrontabError(ValueError):
    pass


def _parse_range_part(part, lo, hi, field_name):
    step = 1
    if "/" in part:
        base, step_str = part.split("/", 1)
        if not step_str.isdigit() or int(step_str) <= 0:
            raise CrontabError(f"{field_name} 字段的步长 '{step_str}' 不合法")
        step = int(step_str)
    else:
        base = part

    if base == "*":
        start, end = lo, hi
    elif "-" in base:
        a, b = base.split("-", 1)
        if not (a.isdigit() and b.isdigit()):
            raise CrontabError(f"{field_name} 字段的范围 '{base}' 不合法")
        start, end = int(a), int(b)
        if start > end:
            raise CrontabError(f"{field_name} 字段的范围 '{base}' 起点大于终点")
    else:
        if not base.isdigit():
            raise CrontabError(f"{field_name} 字段的值 '{base}' 不是数字")
        start = end = int(base)

    if start < lo or end > hi:
        raise CrontabError(f"{field_name} 字段的值必须在 {lo}-{hi} 之间，收到 '{part}'")

    return set(range(start, end + 1, step))


def parse_field(expr, field_name):
    """Returns a set of concrete values, or None to mean "any value" (a bare `*`)."""
    if expr == "*":
        return None
    lo, hi = FIELD_RANGES[field_name]
    values = set()
    for part in expr.split(","):
        part = part.strip()
        if not part:
            raise CrontabError(f"{field_name} 字段里有空的取值")
        values |= _parse_range_part(part, lo, hi, field_name)
    if field_name == "dow":
        values = {0 if v == 7 else v for v in values}  # 7 and 0 both mean Sunday
    return values


def parse_crontab(expression):
    """Validate a standard 5-field cron expression (minute hour dom month dow).
    Returns {field_name: set(values) | None}; None means that field is a wildcard."""
    if not expression or not expression.strip():
        raise CrontabError("crontab 表达式不能为空")
    fields = expression.split()
    if len(fields) != 5:
        raise CrontabError(f"crontab 表达式需要 5 个字段（分 时 日 月 周），收到 {len(fields)} 个：'{expression}'")
    return {name: parse_field(raw, name) for name, raw in zip(FIELD_ORDER, fields)}


def to_launchd_intervals(parsed):
    """Expand a parsed crontab dict into one or more StartCalendarInterval dicts."""
    non_wildcard = [(LAUNCHD_KEY[f], sorted(parsed[f])) for f in FIELD_ORDER if parsed[f] is not None]
    if not non_wildcard:
        raise CrontabError("字段不能全部是 *（等于每分钟触发一次，launchd 的日历触发器不适合这种频率）")

    total = 1
    for _, values in non_wildcard:
        total *= len(values)
    if total > MAX_EXPANDED_ENTRIES:
        raise CrontabError(f"这个表达式会展开成 {total} 条触发规则，超过上限 {MAX_EXPANDED_ENTRIES}，请缩小范围")

    keys = [k for k, _ in non_wildcard]
    value_lists = [v for _, v in non_wildcard]
    return [dict(zip(keys, combo)) for combo in itertools.product(*value_lists)]


def cron_to_launchd(expression):
    """Validate + expand in one call. Raises CrontabError on anything invalid."""
    return to_launchd_intervals(parse_crontab(expression))


def launchd_to_cron(interval):
    """Best-effort reverse of a single StartCalendarInterval dict back to a cron string,
    for display. Returns None for a compound (list) schedule — those aren't representable
    as one cron line, so the caller should fall back to showing the raw plist."""
    if not isinstance(interval, dict):
        return None
    reverse_key = {v: k for k, v in LAUNCHD_KEY.items()}
    fields = {name: "*" for name in FIELD_ORDER}
    for plist_key, value in interval.items():
        field_name = reverse_key.get(plist_key)
        if field_name:
            fields[field_name] = str(value)
    return " ".join(fields[f] for f in FIELD_ORDER)
