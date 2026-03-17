"""
Notion 数据库初始化脚本
========================
在指定的 Notion 页面下创建带有完整字段的论文笔记数据库。

使用方式:
    python setup_notion_db.py --parent-page-id YOUR_PAGE_ID
"""

import os
import sys
import argparse
from dotenv import load_dotenv
from notion_client import Client as NotionClient

load_dotenv()

NOTION_API_KEY = os.environ.get("NOTION_API_KEY")


def create_paper_database(notion: NotionClient, parent_page_id: str) -> str:
    """在指定页面下创建论文笔记数据库，返回数据库 ID"""

    print("🏗️  创建 Notion 论文笔记数据库...")

    response = notion.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": "📚 论文笔记库"}}],
        properties={
            # 标题（必须有且只能有一个 title 类型）
            "Name": {"title": {}},

            # 作者（富文本）
            "Authors": {"rich_text": {}},

            # 年份（数字）
            "Year": {"number": {"format": "number"}},

            # 发表场所（富文本）
            "Venue": {"rich_text": {}},

            # 关键词（多选标签）
            "Keywords": {"multi_select": {}},

            # TL;DR 一句话总结（富文本）
            "TLDR": {"rich_text": {}},

            # 阅读状态（选择）
            "Status": {
                "select": {
                    "options": [
                        {"name": "未读", "color": "gray"},
                        {"name": "阅读中", "color": "yellow"},
                        {"name": "已读", "color": "green"},
                        {"name": "重要", "color": "red"},
                    ]
                }
            },

            # 评分（选择）
            "Rating": {
                "select": {
                    "options": [
                        {"name": "⭐⭐⭐⭐⭐", "color": "yellow"},
                        {"name": "⭐⭐⭐⭐", "color": "orange"},
                        {"name": "⭐⭐⭐", "color": "blue"},
                        {"name": "⭐⭐", "color": "gray"},
                        {"name": "⭐", "color": "gray"},
                    ]
                }
            },

            # 添加日期
            "Added": {"date": {}},

            # 数据创新（富文本）
            "Data Innovations": {"rich_text": {}},

            # 方法创新（富文本）
            "Methods": {"rich_text": {}},
        },
    )

    db_id = response["id"]
    db_url = response.get("url", "")
    print(f"✅ 数据库创建成功!")
    print(f"   数据库 ID: {db_id}")
    print(f"   数据库 URL: {db_url}")
    print()
    print("📋 请将以下内容添加到你的 .env 文件:")
    print(f"   NOTION_DATABASE_ID={db_id.replace('-', '')}")

    return db_id


def main():
    parser = argparse.ArgumentParser(description="初始化 Notion 论文笔记数据库")
    parser.add_argument(
        "--parent-page-id",
        required=True,
        help="父页面的 ID（从 Notion 页面 URL 中获取）",
    )
    args = parser.parse_args()

    if not NOTION_API_KEY:
        print("❌ 缺少 NOTION_API_KEY 环境变量")
        sys.exit(1)

    notion = NotionClient(auth=NOTION_API_KEY)
    create_paper_database(notion, args.parent_page_id)


if __name__ == "__main__":
    main()
