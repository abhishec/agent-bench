"""
ReportScheduler — background asyncio task that generates a report every 4 hours.
"""
from __future__ import annotations
import asyncio
import datetime


class ReportScheduler:
    INTERVAL_HOURS = 4

    async def start(self) -> None:
        asyncio.create_task(self._run_loop())

    async def _run_loop(self) -> None:
        while True:
            await asyncio.sleep(self.INTERVAL_HOURS * 3600)
            try:
                await self._generate_and_save_report()
            except Exception as e:
                print(f"[ReportScheduler] Report generation failed: {e}", flush=True)

    async def _generate_and_save_report(self) -> None:
        from src.run_store import get_recent_runs
        from src.reporter import BenchmarkReporter
        runs = get_recent_runs(hours=self.INTERVAL_HOURS)
        reporter = BenchmarkReporter()
        report = reporter.generate_report(runs)
        json_url = reporter.save_to_s3(report)
        try:
            md_url = reporter.save_markdown_report(report)
        except Exception as e:
            md_url = f"(markdown save failed: {e})"
        now = datetime.datetime.utcnow().isoformat()
        print(f"[{now}] Saved 4h report: {json_url}  |  {md_url}", flush=True)
