"""
批量 PDF → Notion 论文笔记处理脚本
====================================
扫描指定目录下的所有 PDF 文件，逐一调用 pdf_to_notion.py 处理，
并记录成功/失败/跳过状态，支持断点续处理（已处理的文件自动跳过）。

使用方式:
    python batch_pdf_to_notion.py /path/to/pdf/folder
    python batch_pdf_to_notion.py /path/to/pdf/folder --recursive
    python batch_pdf_to_notion.py /path/to/pdf/folder --retry-failed
    python batch_pdf_to_notion.py /path/to/pdf/folder --dry-run
"""

import os
import sys
import json
import argparse
import time
from pathlib import Path
from datetime import datetime

from dotenv import load_dotenv
import anthropic
from notion_client import Client as NotionClient

# 确保能 import 同目录下的 pdf_to_notion
sys.path.insert(0, str(Path(__file__).parent))
from pdf_to_notion import (
    extract_paper_info,
    create_notion_page,
    ensure_notion_database,
    check_env,
)

load_dotenv()

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

# 进度记录文件名（存放在扫描目录下）
PROGRESS_FILE = ".batch_progress.json"


# ── 进度管理 ───────────────────────────────────────────────────

def load_progress(pdf_dir: Path) -> dict:
    """加载已有的处理进度记录"""
    progress_path = pdf_dir / PROGRESS_FILE
    if progress_path.exists():
        try:
            return json.loads(progress_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"success": {}, "failed": {}}


def save_progress(pdf_dir: Path, progress: dict):
    """保存处理进度到文件"""
    progress_path = pdf_dir / PROGRESS_FILE
    progress_path.write_text(
        json.dumps(progress, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def mark_success(progress: dict, pdf_path: Path, notion_url: str):
    progress["success"][str(pdf_path)] = {
        "url": notion_url,
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    # 如果之前失败过，清除失败记录
    progress["failed"].pop(str(pdf_path), None)


def mark_failed(progress: dict, pdf_path: Path, reason: str):
    progress["failed"][str(pdf_path)] = {
        "reason": reason,
        "time": datetime.now().isoformat(timespec="seconds"),
    }


# ── 核心批量逻辑 ───────────────────────────────────────────────

def collect_pdfs(pdf_dir: Path, recursive: bool) -> list[Path]:
    """收集目录下所有 PDF 文件"""
    if recursive:
        pdfs = sorted(pdf_dir.rglob("*.pdf"))
    else:
        pdfs = sorted(pdf_dir.glob("*.pdf"))
    return pdfs


def batch_process(
    pdf_dir: Path,
    recursive: bool = False,
    retry_failed: bool = False,
    force: bool = False,
    dry_run: bool = False,
    delay: float = 2.0,
):
    """批量处理目录下所有 PDF"""

    # ── 初始化 ─────────────────────────────────────
    check_env()
    anthropic_client = anthropic.Anthropic()
    notion_client = NotionClient(auth=NOTION_API_KEY)

    print("\n🔍 检查 Notion 数据库...")
    existing_props = ensure_notion_database(notion_client, NOTION_DATABASE_ID)

    # ── 收集 PDF ───────────────────────────────────
    pdfs = collect_pdfs(pdf_dir, recursive)
    if not pdfs:
        print(f"\n⚠️  目录 {pdf_dir} 下未找到任何 PDF 文件。")
        return

    # ── 加载进度 ───────────────────────────────────
    progress = load_progress(pdf_dir)
    already_done = set(progress["success"].keys())
    previously_failed = set(progress["failed"].keys())

    # ── 过滤待处理列表 ─────────────────────────────
    to_process = []
    skipped = []
    for p in pdfs:
        key = str(p)
        if force:
            to_process.append(p)
        elif key in already_done:
            skipped.append(p)
        elif key in previously_failed and not retry_failed:
            skipped.append(p)
        else:
            to_process.append(p)

    # ── 概览 ───────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"📂 扫描目录: {pdf_dir}")
    print(f"{'='*60}")
    print(f"  📄 共找到 PDF : {len(pdfs)} 个")
    print(f"  ✅ 已处理跳过 : {len(skipped)} 个")
    print(f"  🔄 本次待处理 : {len(to_process)} 个")
    if force:
        print(f"  ⚡ 强制重新处理模式已启用")
    if dry_run:
        print("\n🧪 [Dry Run 模式] 仅列出待处理文件，不实际执行：")
        for p in to_process:
            print(f"   • {p.name}")
        return

    if not to_process:
        print("\n🎉 所有 PDF 已处理完毕，无需重复处理。")
        print(f"   （如需重新处理失败项，加 --retry-failed 参数）")
        print(f"   （如需强制重新处理所有文件，加 --force 参数）")
        return

    # ── 逐一处理 ───────────────────────────────────
    results_success = []
    results_failed = []

    for idx, pdf_path in enumerate(to_process, 1):
        print(f"\n{'='*60}")
        print(f"[{idx}/{len(to_process)}] 📄 {pdf_path.name}")
        print(f"{'='*60}")

        try:
            paper_info = extract_paper_info(None, str(pdf_path))
            url = create_notion_page(
                notion_client, NOTION_DATABASE_ID, paper_info,
                existing_props=existing_props,
            )
            mark_success(progress, pdf_path, url)
            save_progress(pdf_dir, progress)
            results_success.append((pdf_path, url))
            print(f"  🔗 {url}")

        except json.JSONDecodeError as e:
            reason = f"JSON 解析失败: {e}"
            print(f"  ❌ {reason}")
            mark_failed(progress, pdf_path, reason)
            save_progress(pdf_dir, progress)
            results_failed.append((pdf_path, reason))

        except Exception as e:
            reason = str(e)
            print(f"  ❌ 处理失败: {reason}")
            mark_failed(progress, pdf_path, reason)
            save_progress(pdf_dir, progress)
            results_failed.append((pdf_path, reason))

        # 避免频繁调用 API 触发限速
        if idx < len(to_process):
            time.sleep(delay)

    # ── 汇总 ───────────────────────────────────────
    print(f"\n{'='*60}")
    print("📊 批量处理完成汇总")
    print(f"{'='*60}")
    print(f"  ✅ 成功: {len(results_success)} 篇")
    for p, url in results_success:
        print(f"     • {p.name}")
        print(f"       {url}")

    if results_failed:
        print(f"  ❌ 失败: {len(results_failed)} 篇")
        for p, reason in results_failed:
            print(f"     • {p.name}")
            print(f"       原因: {reason}")
        print(f"\n  💡 可使用 --retry-failed 重新处理失败项")

    print(f"\n  📁 进度记录已保存至: {pdf_dir / PROGRESS_FILE}")


# ── 入口 ───────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="批量将目录下所有 PDF 论文转化为 Notion 笔记",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python batch_pdf_to_notion.py ~/Downloads/papers
  python batch_pdf_to_notion.py ~/Downloads/papers --recursive
  python batch_pdf_to_notion.py ~/Downloads/papers --retry-failed
  python batch_pdf_to_notion.py ~/Downloads/papers --dry-run
        """,
    )
    parser.add_argument("pdf_dir", help="包含 PDF 文件的目录路径")
    parser.add_argument(
        "--recursive", "-r",
        action="store_true",
        help="递归扫描子目录中的 PDF 文件",
    )
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="重新处理上次失败的文件（默认跳过）",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="强制重新处理所有文件，忽略已有的成功/失败记录",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅列出待处理文件，不实际执行",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="每篇论文处理间隔秒数，避免 API 限速（默认 2 秒）",
    )
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir).expanduser().resolve()
    if not pdf_dir.is_dir():
        print(f"❌ 路径不存在或不是目录: {pdf_dir}")
        sys.exit(1)

    print("\n🚀 批量 PDF → Notion 论文笔记系统启动")

    batch_process(
        pdf_dir=pdf_dir,
        recursive=args.recursive,
        retry_failed=args.retry_failed,
        force=args.force,
        dry_run=args.dry_run,
        delay=args.delay,
    )


if __name__ == "__main__":
    main()
