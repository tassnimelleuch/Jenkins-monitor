from extensions import cache


def get_cached_daily_cost_chart(service, year, month, mode="actual", only="all"):
    year = int(year)
    month = int(month)
    mode = str(mode).strip().lower()
    only = str(only).strip().lower()

    raw_key = f"daily_cost_chart_raw:{year}:{month}:{mode}"
    raw = cache.get(raw_key)
    print(f"[CACHE] key={raw_key} hit={raw is not None}")

    if raw is None:
        raw = service.get_daily_cost_chart(year, month, mode, only="all")
        cache.set(raw_key, raw, timeout=1800)

    if only == "all":
        return raw

    filtered = dict(raw)
    filtered["only"] = only

    if only == "aks":
        filtered["series"] = {
            "aks": raw["series"]["aks"],
            "vm": [0.0] * len(raw["series"]["vm"]),
        }
    elif only == "vm":
        filtered["series"] = {
            "aks": [0.0] * len(raw["series"]["aks"]),
            "vm": raw["series"]["vm"],
        }

    return filtered
