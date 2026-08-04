from __future__ import annotations


def partition_bounds(candle_count: int) -> dict[str, tuple[int, int]]:
    if candle_count <= 0:
        return {"training": (0, -1), "validation": (0, -1), "final_test": (0, -1), "entire_dataset": (0, -1)}
    first = candle_count // 3
    second = (candle_count * 2) // 3
    return {
        "training": (0, first - 1),
        "validation": (first, second - 1),
        "final_test": (second, candle_count - 1),
        "entire_dataset": (0, candle_count - 1),
    }


def partition_for_index(index: int, candle_count: int) -> str:
    for name, (start, end) in partition_bounds(candle_count).items():
        if name != "entire_dataset" and start <= index <= end:
            return name
    return "entire_dataset"