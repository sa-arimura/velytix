"""Velytix の動作確認に使う架空の受注CSVを生成する。"""

import csv
from datetime import date, timedelta
from pathlib import Path

from validator import REQUIRED_COLUMNS


_PRODUCTS = (
    ("001", "油性ボールペン", 130, 18),
    ("002", "方眼ノート", 240, 9),
    ("003", "マスキングテープ", 290, 7),
    ("004", "クリアファイル", 100, 14),
    ("005", "キッチンクロス", 390, 5),
    ("006", "ステンレスボトル", 1980, 2),
    ("007", "デスクトレー", 820, 3),
    ("008", "コットン巾着", 450, 4),
)


def _normal_rows() -> list[tuple[str, ...]]:
    rows = []
    first_date = date(2024, 1, 3)
    for order_index in range(50):
        # 受注を5つの時期に分け、売上のない日・週・月を作る。
        block, offset = divmod(order_index, 10)
        order_date = first_date + timedelta(days=block * 70 + offset * 2)
        order_id = f"{order_index + 1:05d}"
        for line_no in (1, 2):
            product_index = (order_index * 3 + (line_no - 1) * 5) % len(_PRODUCTS)
            code, name, regular_price, base_quantity = _PRODUCTS[product_index]
            quantity = base_quantity + (order_index + line_no) % 4
            unit_price = regular_price - 10 * (order_index % 3)
            rows.append((
                order_id, str(line_no), order_date.isoformat(),
                code, name, str(quantity), str(unit_price),
            ))
    return rows


def _error_rows() -> tuple[tuple[str, ...], ...]:
    # 通常行と別の受注IDを使い、重複によって通常行がエラーになるのを防ぐ。
    return (
        ("E001", "1", "2024-08-20", "001", "", "2", "130"),
        ("E002", "0", "2024-08-20", "002", "方眼ノート", "3", "240"),
        ("E003", "1", "2024-08-20", "003", "マスキングテープ", "0", "290"),
        ("E004", "1", "2024-08-20", "004", "クリアファイル", "4", "-100"),
        ("E005", "1", "2024-02-30", "005", "キッチンクロス", "2", "390"),
        ("E006", "1", "2024-8-20", "006", "ステンレスボトル", "1.5", "1980"),
        ("E007", "1", "2024-08-20", "007", "デスクトレー", "10000", "820"),
        ("E007", "001", "2024-08-20", "008", "コットン巾着", "10000", "450"),
        ("E008", "1", "2024-08-20", "001", "油性ボールペン", "　", "130"),
        ("E009", "1", "2024-08-20", "002", "方眼ノート", "２", "240"),
    )


def generate_samples(output_dir: Path) -> None:
    """指定フォルダーに2種類のサンプルを同じ内容で書き出す。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    normal = _normal_rows()
    files = {
        "sample_orders_clean.csv": normal,
        "sample_orders.csv": [*normal[:90], *_error_rows()],
    }
    for filename, rows in files.items():
        with (output_dir / filename).open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file, lineterminator="\n")
            writer.writerow(REQUIRED_COLUMNS)
            writer.writerows(rows)


if __name__ == "__main__":
    generate_samples(Path(__file__).resolve().parent)
