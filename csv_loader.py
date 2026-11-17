"""受注CSVを読み込み、構造を検証する。"""

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

from validator import REQUIRED_COLUMNS as REQUIRED_COLUMNS
from validator import normalize_row

MAX_CSV_BYTES = 10_000_000

# 二重引用符は引用フィールド内でのみエスケープとして扱う。
_FIELD = re.compile(r'"(?P<quoted>(?:[^"]++|"")*+)"|(?P<unquoted>[^,"\r\n]*)')
_NEWLINE = re.compile(r"\r\n|\r|\n")


class CSVLoadError(ValueError):
    """受注CSV全体を読み込めないときのエラー。"""


@dataclass(frozen=True, slots=True)
class OrderRow:
    row_number: int
    raw_values: tuple[str, ...]
    normalized_values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LoadedCSV:
    columns: tuple[str, ...]
    rows: tuple[OrderRow, ...]


def load_csv(content: bytes) -> LoadedCSV:
    """CSVのバイト列から、列順と元の行番号を保った受注明細を返す。"""
    if len(content) > MAX_CSV_BYTES:
        raise CSVLoadError("CSVファイルのサイズが10MBを超えています。")

    records = _read_records(_decode_csv(content))
    header_record = next(records, None)
    if header_record is None:
        raise CSVLoadError("CSVにデータレコードがありません。")

    _, columns = header_record
    duplicates = [column for column, count in Counter(columns).items() if count > 1]
    if duplicates:
        names = "、".join(repr(column) for column in duplicates)
        raise CSVLoadError(f"CSVのヘッダー名が重複しています（{names}）。")

    missing = [column for column in REQUIRED_COLUMNS if column not in columns]
    if missing:
        names = "、".join(missing)
        raise CSVLoadError(f"CSVに必須列がありません（{names}）。")

    rows = []
    for row_number, values in records:
        if len(values) != len(columns):
            raise CSVLoadError(
                f"CSVの{row_number}行目の列数がヘッダーと一致しません"
                f"（{len(columns)}列必要、実際は{len(values)}列）。"
            )
        rows.append(OrderRow(row_number, values, normalize_row(values)))

    if not rows:
        raise CSVLoadError("CSVにデータレコードがありません。")
    return LoadedCSV(columns, tuple(rows))


def _decode_csv(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass

    try:
        return content.decode("cp932")
    except UnicodeDecodeError as error:
        raise CSVLoadError("CSVをUTF-8またはCP932で読み込めません。") from error


def _read_records(text: str) -> Iterator[tuple[int, tuple[str, ...]]]:
    position = 0
    line_number = 1
    while position < len(text):
        blank_line = _NEWLINE.match(text, position)
        if blank_line is not None:
            position = blank_line.end()
            line_number += 1
            continue

        record_start = line_number
        values = []
        while True:
            # 空欄もフィールドとして読み取る。裸の引用符は直後の区切り検査で弾く。
            field = _FIELD.match(text, position)
            quoted = field["quoted"]
            if quoted is None:
                values.append(field["unquoted"])
            else:
                values.append(quoted.replace('""', '"'))
                line_number += (
                    quoted.count("\r") + quoted.count("\n") - quoted.count("\r\n")
                )

            position = field.end()
            if position == len(text):
                break
            if text[position] == ",":
                position += 1
                continue

            newline = _NEWLINE.match(text, position)
            if newline is None:
                raise CSVLoadError(f"CSVの{record_start}行目から始まるレコードの引用符が不正です。")
            position = newline.end()
            line_number += 1
            break

        yield record_start, tuple(values)
