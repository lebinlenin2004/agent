import io

import pandas as pd
from fastapi.responses import Response

_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")
MEDIA = {"csv": "text/csv; charset=utf-8",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


def _safe(v):
    """Neutralise spreadsheet formula injection from scraped text (e.g. a job title starting with '=')."""
    return "'" + v if isinstance(v, str) and v.startswith(_DANGEROUS) else v


def to_response(rows: list[dict], columns: list[str], fmt: str, basename: str) -> Response:
    df = pd.DataFrame(rows, columns=columns)
    df = df.apply(lambda col: col.map(_safe)) if len(df) else df
    if fmt == "csv":
        body = df.to_csv(index=False).encode("utf-8-sig")  # BOM so Excel opens UTF-8 correctly
    else:
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as w:
            df.to_excel(w, index=False, sheet_name="Vacancies"[:31])
            ws = w.sheets["Vacancies"]
            for i, col in enumerate(df.columns, start=1):
                width = min(60, max([len(str(col))] + [len(str(x)) for x in df[col].head(200)]) + 2)
                ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = width
            ws.freeze_panes = "A2"
        body = buf.getvalue()
    return Response(body, media_type=MEDIA[fmt],
                    headers={"Content-Disposition": f'attachment; filename="{basename}.{fmt}"'})
