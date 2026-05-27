#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from calendar import monthrange
from datetime import date


def _parse_iso_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid {field_name}: {value!r}. Expected YYYY-MM-DD."
        ) from exc


def _month_start(year: int, month: int) -> date:
    return date(year, month, 1)


def _month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def _previous_month(target: date) -> tuple[int, int]:
    if target.month == 1:
        return target.year - 1, 12
    return target.year, target.month - 1


def _iter_months(start_date: date, end_date: date):
    year = start_date.year
    month = start_date.month

    while (year, month) <= (end_date.year, end_date.month):
        yield year, month
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def _default_window(today: date) -> tuple[date, date]:
    previous_year, previous_month = _previous_month(today)
    return _month_start(previous_year, previous_month), today


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh stored FinOps/Jenkins source data and rebuild the FinOps build "
            "documents used by the chatbot."
        )
    )
    parser.add_argument(
        "--date",
        dest="target_date",
        type=lambda value: _parse_iso_date(value, "date"),
        help="Generate documents for one day only.",
    )
    parser.add_argument(
        "--start-date",
        type=lambda value: _parse_iso_date(value, "start-date"),
        help="Start of the generation window.",
    )
    parser.add_argument(
        "--end-date",
        type=lambda value: _parse_iso_date(value, "end-date"),
        help="End of the generation window.",
    )
    parser.add_argument(
        "--skip-source-refresh",
        action="store_true",
        help="Reuse stored data instead of refreshing Azure daily costs and Jenkins first.",
    )
    parser.add_argument(
        "--sync-chroma",
        action="store_true",
        help="Also upsert the generated document chunks into the FinOps Chroma collection.",
    )
    return parser


def _resolve_window(args: argparse.Namespace) -> tuple[date | None, date | None, date | None]:
    if args.target_date is not None:
        if args.start_date is not None or args.end_date is not None:
            raise ValueError("Use either --date or --start-date/--end-date, not both.")
        return args.target_date, args.target_date, args.target_date

    start_date = args.start_date
    end_date = args.end_date
    if start_date is None and end_date is None:
        start_date, end_date = _default_window(date.today())
    elif start_date is None:
        start_date = end_date
    elif end_date is None:
        end_date = start_date

    if start_date > end_date:
        raise ValueError("start-date must be before or equal to end-date.")

    return None, start_date, end_date


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        target_date, start_date, end_date = _resolve_window(args)
    except ValueError as exc:
        parser.error(str(exc))

    from app import app
    from services.finops_build_documents_service import sync_finops_build_documents
    from services.finops_chroma_service import sync_finops_documents_to_chroma
    from services.finops_storage_service import refresh_finops_month
    from services.jenkins_service import refresh_pipeline_storage_from_jenkins

    with app.app_context():
        summary = {
            "window": {
                "target_date": target_date.isoformat() if target_date is not None else None,
                "start_date": start_date.isoformat() if start_date is not None else None,
                "end_date": end_date.isoformat() if end_date is not None else None,
            },
            "source_refresh": None,
            "documents": None,
            "chroma": None,
        }

        if args.skip_source_refresh:
            summary["source_refresh"] = {"skipped": True}
        else:
            months = []
            if start_date is not None and end_date is not None:
                months = list(_iter_months(start_date, end_date))
            elif target_date is not None:
                months = [(target_date.year, target_date.month)]

            subscription_id = str(app.config.get("AZURE_SUBSCRIPTION_ID") or "").strip()
            if not subscription_id:
                raise RuntimeError(
                    "AZURE_SUBSCRIPTION_ID is missing, so Azure daily costs cannot be refreshed. "
                    "Use --skip-source-refresh only if the stored FinOps data is already up to date."
                )

            finops_refresh = []
            for year, month in months:
                result = refresh_finops_month(subscription_id, year, month, force=True)
                finops_refresh.append(
                    {
                        "year": year,
                        "month": month,
                        "result": result,
                    }
                )

            pipeline_refresh = refresh_pipeline_storage_from_jenkins(
                include_quality_metrics=False,
                include_quality_backfill=False,
            )
            summary["source_refresh"] = {
                "skipped": False,
                "finops_months": finops_refresh,
                "pipeline": {
                    "connected": bool(pipeline_refresh.get("connected")),
                    "selected_branch": ((pipeline_refresh.get("pipeline") or {}).get("selected_branch")),
                },
            }

        document_result = sync_finops_build_documents(
            target_date=target_date,
            start_date=start_date,
            end_date=end_date,
        )
        summary["documents"] = document_result

        if args.sync_chroma:
            summary["chroma"] = sync_finops_documents_to_chroma(
                target_date=target_date,
                start_date=start_date,
                end_date=end_date,
            )
        else:
            summary["chroma"] = {"skipped": True}

    json.dump(summary, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
