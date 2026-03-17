"""
PDF 论文 → Notion 笔记 自动化系统
====================================
使用 Claude Files API 解析 PDF，提取结构化论文信息，
并自动创建 Notion 数据库页面。

使用方式:
    python pdf_to_notion.py paper.pdf
    python pdf_to_notion.py paper1.pdf paper2.pdf paper3.pdf
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
import anthropic
from notion_client import Client as NotionClient

# ── 加载环境变量 ───────────────────────────────────────────────
load_dotenv()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")  # 可选，Claude Code 环境自动注入
NOTION_API_KEY = os.environ.get("NOTION_API_KEY")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID")

# ── 论文笔记数据结构（Pydantic-free，直接用 dict） ─────────────
EXTRACTION_PROMPT = """你是一位专业的学术论文阅读助手。请仔细阅读以下 PDF 论文，并按照指定的 JSON 格式提取关键信息。

请严格按照以下 JSON 格式返回，不要包含任何其他文字：

{
  "title": "论文完整标题",
  "authors": ["作者1", "作者2"],
  "institutions": ["机构1", "机构2"],
  "publish_date": "2024-03",
  "venue": "发表期刊/会议名称（如 NeurIPS 2024, CVPR 2024, Nature, arXiv 等）",
  "keywords": ["关键词1", "关键词2", "关键词3"],
  "tldr": "一句话总结这篇论文做了什么，**核心方法名**加粗，!!最关键结论!!标红（30字以内）",
  "motivation": "研究动机，用**关键术语**标注核心概念，!!痛点/gap!!标红（100字以内）",
  "challenges": [
    "**挑战名称**：具体描述，!!核心难点!!标红",
    "**挑战名称**：具体描述，!!核心难点!!标红",
    "**挑战名称**：具体描述，!!核心难点!!标红"
  ],
  "core_contributions": [
    "**贡献名称**：具体描述，**方法/模块名**加粗",
    "**贡献名称**：具体描述，**方法/模块名**加粗",
    "**贡献名称**：具体描述，**方法/模块名**加粗"
  ],
  "data_innovations": [
    "**创新名称**：具体描述，**数据集/技术名**加粗",
    "**创新名称**：具体描述，**数据集/技术名**加粗",
    "**创新名称**：具体描述，**数据集/技术名**加粗"
  ],
  "method_innovations": [
    "**创新名称**：具体描述，**模块/算法名**加粗",
    "**创新名称**：具体描述，**模块/算法名**加粗",
    "**创新名称**：具体描述，**模块/算法名**加粗"
  ],
  "evaluation_method": "评估方法描述，**基准名称**加粗，**评估指标**加粗（100字以内）",
  "experiment_results": "实验结果描述，!!关键数值/提升幅度!!标红，**对比基线**加粗（100字以内）",
  "conclusion": "论文结论描述，**核心贡献**加粗，!!最重要意义!!标红（100字以内）",
  "strengths": [
    "**优点名称**：具体描述",
    "**优点名称**：具体描述",
    "**优点名称**：具体描述"
  ],
  "weaknesses": [
    "**局限名称**：具体描述，!!关键缺陷!!标红",
    "**局限名称**：具体描述，!!关键缺陷!!标红",
    "**局限名称**：具体描述，!!关键缺陷!!标红"
  ],
  "personal_notes": "研究方向与细节备注，**值得关注的点**加粗"
}

请确保：
1. 所有字段都要填写，如果论文中没有相关信息，填写 "未提及"
2. publish_date 格式为 "YYYY-MM"，如无法确定月份则填 "YYYY-01"
3. core_contributions 严格只列3条最核心的创新方案
4. data_innovations 严格只列3条论文在数据层面的创新（如新数据集构建、数据增强方法、数据标注策略、数据处理流程等）；若论文无明显数据创新，则如实填写"未提及"
5. method_innovations 严格只列3条论文在方法/模型/算法层面的创新（如新架构设计、新训练策略、新损失函数、新推理机制等）；若论文无明显方法创新，则如实填写"未提及"
6. strengths 和 weaknesses 各列3条客观评价
7. challenges 列出论文明确提到的或隐含的技术挑战
8. 列表项格式严格为 **关键词**：详细描述，**关键词**须高度凝练（2-5字），概括该条核心主题，直接用 **加粗** 标注，不要使用【】方括号
9. 文本字段中用 **词语** 标注需要加粗的关键概念/方法名，用 !!词语!! 标注需要标红的重要数值/核心结论/关键痛点
10. 中文输出
11. 只返回 JSON，不要有任何额外说明
"""


def extract_paper_info(client: anthropic.Anthropic, pdf_path: str) -> dict:
    """用 pypdf 提取文字，通过 Claude Code CLI 分析"""
    import subprocess
    from pypdf import PdfReader

    print(f"  📖 提取 PDF 文字: {Path(pdf_path).name} ...")
    reader = PdfReader(pdf_path)
    text_parts = []
    for page in reader.pages:
        t = page.extract_text()
        if t:
            text_parts.append(t)
    pdf_text = "\n".join(text_parts)
    # 移除 null 字节（某些 PDF 提取后含有 \x00，会导致 subprocess 报错）
    pdf_text = pdf_text.replace("\x00", "").replace("\ufeff", "")[:80000]
    print(f"  📝 提取文字 {len(pdf_text)} 字符，正在分析...")

    prompt = f"以下是论文的全文内容：\n\n{pdf_text}\n\n---\n\n{EXTRACTION_PROMPT}"

    print("  🤖 Claude 正在分析论文...")
    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "text", "--max-turns", "1"],
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode != 0:
        raise RuntimeError(f"Claude CLI 调用失败: {result.stderr}")

    raw_text = result.stdout.strip()

    # 提取 JSON 块（支持有无 markdown 代码块标记）
    import re
    # 优先匹配 ```json ... ``` 块
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
    if json_match:
        raw_text = json_match.group(1)
    else:
        # 找第一个 { 到最后一个 }
        start = raw_text.find("{")
        end = raw_text.rfind("}")
        if start != -1 and end != -1:
            raw_text = raw_text[start:end+1]

    # 修复 JSON 字符串中的未转义双引号（如 "Good" 嵌入在值里）
    # 策略：将 JSON 字符串值内部的 " 替换为 「」中文引号，避免破坏结构
    def fix_unescaped_quotes(s: str) -> str:
        result = []
        in_string = False
        escape_next = False
        i = 0
        while i < len(s):
            c = s[i]
            if escape_next:
                result.append(c)
                escape_next = False
            elif c == '\\':
                result.append(c)
                escape_next = True
            elif c == '"':
                if not in_string:
                    in_string = True
                    result.append(c)
                else:
                    # 判断是否是合法的结束引号（后跟 : , } ] 或空白）
                    j = i + 1
                    while j < len(s) and s[j] in ' \t\n\r':
                        j += 1
                    if j >= len(s) or s[j] in ':,}]':
                        in_string = False
                        result.append(c)
                    else:
                        # 非法的内嵌引号，替换为中文引号
                        result.append('\u201c')
            else:
                result.append(c)
            i += 1
        return ''.join(result)

    raw_text = fix_unescaped_quotes(raw_text.strip())

    # 修复常见 JSON 问题：末尾截断时补齐括号
    open_braces = raw_text.count("{") - raw_text.count("}")
    open_brackets = raw_text.count("[") - raw_text.count("]")
    if open_brackets > 0:
        raw_text = raw_text.rstrip().rstrip(",") + "]" * open_brackets
    if open_braces > 0:
        raw_text = raw_text.rstrip().rstrip(",") + "}" * open_braces

    paper_info = json.loads(raw_text.strip())
    print(f"  ✅ 分析完成: 《{paper_info.get('title', '未知标题')}》")
    return paper_info


def cleanup_pdf_from_claude(client: anthropic.Anthropic, file_id: str):
    """分析完成后删除 Claude 上的 PDF 文件（节省存储）"""
    try:
        client.beta.files.delete(file_id)
        print(f"  🗑️  已清理 Claude Files: {file_id}")
    except Exception as e:
        print(f"  ⚠️  清理文件时出错（可忽略）: {e}")


# ── Notion 富文本工具函数 ──────────────────────────────────────

def parse_inline(text: str) -> list:
    """将含有 **粗体** 和 !!红色!! 标记的文本解析为 Notion rich_text 列表。

    支持的标记：
      **词语**  → bold
      !!词语!!  → red color
    两种标记可嵌套（先匹配 !! 再匹配 **，不支持交叉嵌套）。
    """
    import re
    # 将文本按标记切分为 token 列表：(text, bold, color)
    segments = []
    # 合并两种标记的正则，优先匹配
    pattern = re.compile(r'\*\*(.+?)\*\*|!!(.+?)!!')
    last = 0
    for m in pattern.finditer(text):
        if m.start() > last:
            segments.append((text[last:m.start()], False, None))
        if m.group(1) is not None:       # **bold**
            segments.append((m.group(1), True, None))
        else:                             # !!red!!
            segments.append((m.group(2), False, "red"))
        last = m.end()
    if last < len(text):
        segments.append((text[last:], False, None))

    result = []
    for content, bold, color in segments:
        if not content:
            continue
        block = {"type": "text", "text": {"content": content[:2000]}}
        annotations = {}
        if bold:
            annotations["bold"] = True
        if color:
            annotations["color"] = color
        if annotations:
            block["annotations"] = annotations
        result.append(block)
    return result or [{"type": "text", "text": {"content": ""}}]


def rich_text(text: str, bold: bool = False) -> dict:
    """保留原始接口供 heading 等不需要内联标记的场景使用。"""
    block = {"type": "text", "text": {"content": text[:2000]}}
    if bold:
        block["annotations"] = {"bold": True}
    return block


def heading2(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_2",
        "heading_2": {"rich_text": [rich_text(text)]},
    }


def heading3(text: str) -> dict:
    return {
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": [rich_text(text)]},
    }


def paragraph(text: str, bold: bool = False) -> dict:
    if bold:
        # 全段加粗时不走内联解析
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [rich_text(text, bold=True)]},
        }
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": parse_inline(text)},
    }


def bulleted_item(text: str) -> dict:
    """支持 **粗体** / !!红色!! 内联标记的列表项。"""
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": parse_inline(text[:2000])},
    }


def divider() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def callout(text: str, emoji: str = "💡") -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": parse_inline(text[:2000]),
            "icon": {"type": "emoji", "emoji": emoji},
        },
    }


# ── 构建 Notion 页面内容 ───────────────────────────────────────

def build_notion_page_content(info: dict) -> list:
    """将论文信息转换为 Notion block 列表"""
    blocks = []

    # TL;DR Callout
    tldr = info.get("tldr", "")
    if tldr and tldr != "未提及":
        blocks.append(callout(f"TL;DR  {tldr}", "🎯"))
        blocks.append(divider())

    # ── 基本信息 ──────────────────────────────────
    institutions = info.get("institutions", [])
    venue = info.get("venue", "")
    publish_date = info.get("publish_date", "")
    if institutions or venue or publish_date:
        blocks.append(heading2("🏛️ 基本信息"))
        if institutions:
            blocks.append(paragraph(f"**机构：** {'、'.join(institutions)}"))
        if venue:
            blocks.append(paragraph(f"**发表：** {venue}"))
        if publish_date:
            blocks.append(paragraph(f"**日期：** {publish_date}"))
        blocks.append(divider())

    # ── 研究动机 ──────────────────────────────────
    motivation = info.get("motivation", "")
    if motivation and motivation != "未提及":
        blocks.append(heading2("💡 研究动机"))
        blocks.append(paragraph(motivation))
        blocks.append(divider())

    # ── 挑战 ──────────────────────────────────────
    challenges = info.get("challenges", [])
    if challenges:
        blocks.append(heading2("🧩 面临挑战"))
        for c in challenges:
            blocks.append(bulleted_item(c))
        blocks.append(divider())

    # ── 核心创新方案 ──────────────────────────────
    contributions = info.get("core_contributions", [])
    if contributions:
        blocks.append(heading2("🚀 核心创新方案"))
        for c in contributions:
            blocks.append(bulleted_item(c))
        blocks.append(divider())

    # ── 数据创新 ──────────────────────────────────
    data_innovations = info.get("data_innovations", [])
    if data_innovations and data_innovations != ["未提及"]:
        blocks.append(heading2("🗄️ 数据创新"))
        for d in data_innovations:
            blocks.append(bulleted_item(d))
        blocks.append(divider())

    # ── 方法创新 ──────────────────────────────────
    method_innovations = info.get("method_innovations", [])
    if method_innovations and method_innovations != ["未提及"]:
        blocks.append(heading2("⚙️ 方法创新"))
        for m in method_innovations:
            blocks.append(bulleted_item(m))
        blocks.append(divider())

    # ── 评估方法 ──────────────────────────────────
    eval_method = info.get("evaluation_method", "")
    if eval_method and eval_method != "未提及":
        blocks.append(heading2("📐 评估方法"))
        blocks.append(paragraph(eval_method))
        blocks.append(divider())

    # ── 实验结果 ──────────────────────────────────
    exp_results = info.get("experiment_results", "")
    if exp_results and exp_results != "未提及":
        blocks.append(heading2("📊 实验结果"))
        blocks.append(paragraph(exp_results))
        blocks.append(divider())

    # ── 结论 ──────────────────────────────────────
    conclusion = info.get("conclusion", "")
    if conclusion and conclusion != "未提及":
        blocks.append(heading2("🏁 结论"))
        blocks.append(paragraph(conclusion))
        blocks.append(divider())

    # ── 优缺点评价 ────────────────────────────────
    strengths = info.get("strengths", [])
    weaknesses = info.get("weaknesses", [])
    if strengths or weaknesses:
        blocks.append(heading2("⚖️ 优缺点评价"))
        if strengths:
            blocks.append(heading3("✅ 三大优点"))
            for s in strengths:
                blocks.append(bulleted_item(s))
        if weaknesses:
            blocks.append(heading3("❌ 三大缺点"))
            for w in weaknesses:
                blocks.append(bulleted_item(w))
        blocks.append(divider())

    # ── 备注 ──────────────────────────────────────
    notes = info.get("personal_notes", "")
    if notes and notes != "未提及":
        blocks.append(callout(notes, "📝"))

    return blocks


def strip_inline_markers(text: str) -> str:
    """移除 **粗体** 和 !!红色!! 标记，只保留纯文本内容。用于写入 Notion 数据库属性字段。"""
    import re
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'!!(.+?)!!', r'\1', text)
    return text


def build_notion_properties(info: dict, existing_props: set = None) -> dict:
    """构建 Notion 数据库页面的属性字段（按指定列顺序填充）

    existing_props: 数据库中已存在的属性名集合，不在其中的字段自动跳过。
    """
    properties = {}

    def has_prop(name: str) -> bool:
        return existing_props is None or name in existing_props

    def rt(text: str) -> dict:
        return {"rich_text": [{"text": {"content": strip_inline_markers(str(text))[:2000]}}]}

    # Name（必填）
    properties["Name"] = {"title": [{"text": {"content": info.get("title", "未知标题")[:2000]}}]}

    # Keywords
    keywords = info.get("keywords", [])
    if keywords and has_prop("Keywords"):
        properties["Keywords"] = {"multi_select": [{"name": kw[:100]} for kw in keywords[:10]]}

    # Date
    publish_date = info.get("publish_date", "")
    if publish_date and publish_date != "未提及" and has_prop("Date"):
        if len(publish_date) == 7:
            publish_date += "-01"
        properties["Date"] = {"date": {"start": publish_date}}

    # Institutions
    institutions = info.get("institutions", [])
    if institutions and has_prop("Institutions"):
        properties["Institutions"] = rt("、".join(institutions))

    # TLDR
    tldr = info.get("tldr", "")
    if tldr and tldr != "未提及" and has_prop("TLDR"):
        properties["TLDR"] = rt(tldr)

    # Motivation
    motivation = info.get("motivation", "")
    if motivation and motivation != "未提及" and has_prop("Motivation"):
        properties["Motivation"] = rt(motivation)

    # Challenges
    challenges = info.get("challenges", [])
    if challenges and has_prop("Challenges"):
        properties["Challenges"] = rt("\n".join(challenges))

    # Contributions
    contributions = info.get("core_contributions", [])
    if contributions and has_prop("Contributions"):
        properties["Contributions"] = rt("\n".join(contributions))

    # Data Innovations
    data_innovations = info.get("data_innovations", [])
    if data_innovations and data_innovations != ["未提及"] and has_prop("Data Innovations"):
        properties["Data Innovations"] = rt("\n".join(data_innovations))

    # Methods
    method_innovations = info.get("method_innovations", [])
    if method_innovations and method_innovations != ["未提及"] and has_prop("Methods"):
        properties["Methods"] = rt("\n".join(method_innovations))

    # Evaluation
    eval_method = info.get("evaluation_method", "")
    if eval_method and eval_method != "未提及" and has_prop("Evaluation"):
        properties["Evaluation"] = rt(eval_method)

    # Results
    exp_results = info.get("experiment_results", "")
    if exp_results and exp_results != "未提及" and has_prop("Results"):
        properties["Results"] = rt(exp_results)

    # Conclusion
    conclusion = info.get("conclusion", "")
    if conclusion and conclusion != "未提及" and has_prop("Conclusion"):
        properties["Conclusion"] = rt(conclusion)

    # Strengths
    strengths = info.get("strengths", [])
    if strengths and has_prop("Strengths"):
        properties["Strengths"] = rt("\n".join(strengths))

    # Weaknesses
    weaknesses = info.get("weaknesses", [])
    if weaknesses and has_prop("Weaknesses"):
        properties["Weaknesses"] = rt("\n".join(weaknesses))

    # Notes 列由人工填写，跳过自动填充

    return properties



def create_notion_page(notion: NotionClient, database_id: str, info: dict,
                       existing_props: set = None) -> str:
    """在 Notion 数据库中创建论文笔记页面"""
    print("  📝 创建 Notion 页面...")

    properties = build_notion_properties(info, existing_props=existing_props)
    children = build_notion_page_content(info)

    # Notion 单次最多添加 100 个 block
    first_batch = children[:100]

    response = notion.pages.create(
        parent={"database_id": database_id},
        properties=properties,
        children=first_batch,
    )

    page_id = response["id"]

    # 如果有超过 100 个 block，分批追加
    if len(children) > 100:
        for i in range(100, len(children), 100):
            notion.blocks.children.append(
                block_id=page_id,
                children=children[i : i + 100],
            )

    page_url = response.get("url", "")
    print(f"  ✅ Notion 页面创建成功!")
    print(f"  🔗 {page_url}")
    return page_url


# ── 主流程 ────────────────────────────────────────────────────

def check_env():
    """检查必要的环境变量"""
    missing = []
    # ANTHROPIC_API_KEY 由 Claude Code 环境自动注入，不强制检查
    if not NOTION_API_KEY:
        missing.append("NOTION_API_KEY")
    if not NOTION_DATABASE_ID:
        missing.append("NOTION_DATABASE_ID")
    if missing:
        print(f"❌ 缺少必要的环境变量: {', '.join(missing)}")
        print("请复制 .env.example 为 .env 并填写相应的值。")
        sys.exit(1)


def ensure_notion_database(notion: NotionClient, database_id: str):
    """检查 Notion 数据库是否可访问，并提示缺少的字段"""
    try:
        db = notion.databases.retrieve(database_id)
        existing_props = set(db["properties"].keys())
        required_props = {"Name", "Authors", "Date", "Venue", "Keywords", "TLDR"}
        missing_props = required_props - existing_props
        if missing_props:
            print(f"  ⚠️  Notion 数据库缺少以下属性，将自动跳过相应字段: {missing_props}")
            print("  💡 建议在 Notion 中手动添加这些属性以获得完整体验。")
        else:
            print("  ✅ Notion 数据库结构检查通过")
        return existing_props
    except Exception as e:
        print(f"❌ 无法访问 Notion 数据库: {e}")
        print("请确认：")
        print("  1. NOTION_DATABASE_ID 正确")
        print("  2. 已将 Notion Integration 添加到该数据库（Share → 搜索你的 Integration）")
        sys.exit(1)


def process_pdf(pdf_path: str, anthropic_client: anthropic.Anthropic,
                notion_client: NotionClient, database_id: str,
                keep_file: bool = False,
                existing_props: set = None) -> Optional[str]:
    """处理单个 PDF 文件的完整流程"""
    pdf_path = str(Path(pdf_path).resolve())

    if not Path(pdf_path).exists():
        print(f"  ❌ 文件不存在: {pdf_path}")
        return None

    if not pdf_path.lower().endswith(".pdf"):
        print(f"  ❌ 不是 PDF 文件: {pdf_path}")
        return None

    print(f"\n{'='*60}")
    print(f"📄 处理: {Path(pdf_path).name}")
    print(f"{'='*60}")

    try:
        # 1. 使用 Claude CLI 提取论文信息
        paper_info = extract_paper_info(None, pdf_path)

        # 2. 在 Notion 中创建页面
        page_url = create_notion_page(notion_client, database_id, paper_info,
                                      existing_props=existing_props)

        return page_url

    except json.JSONDecodeError as e:
        print(f"  ❌ JSON 解析失败: {e}")
        print("  💡 Claude 返回了非标准 JSON，请重试或检查 PDF 是否可读")
        return None
    except Exception as e:
        print(f"  ❌ 处理失败: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(
        description="将 PDF 论文自动转化为 Notion 笔记",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python pdf_to_notion.py paper.pdf
  python pdf_to_notion.py *.pdf
  python pdf_to_notion.py paper.pdf --keep-file
        """,
    )
    parser.add_argument("pdfs", nargs="+", help="PDF 文件路径（支持多个）")
    parser.add_argument(
        "--keep-file",
        action="store_true",
        help="处理完成后保留 Claude Files 中的文件（默认自动删除）",
    )
    args = parser.parse_args()

    # 检查环境变量
    check_env()

    # 初始化客户端
    # 初始化 Anthropic 客户端（Claude Code 环境已自动注入凭证，无需显式传 api_key）
    anthropic_client = anthropic.Anthropic()
    notion_client = NotionClient(auth=NOTION_API_KEY)

    print("\n🚀 PDF → Notion 论文笔记系统启动")
    print(f"📚 待处理文件数: {len(args.pdfs)}")

    # 检查 Notion 数据库
    print("\n🔍 检查 Notion 数据库...")
    existing_props = ensure_notion_database(notion_client, NOTION_DATABASE_ID)

    # 批量处理
    results = []
    for pdf_path in args.pdfs:
        url = process_pdf(
            pdf_path,
            anthropic_client,
            notion_client,
            NOTION_DATABASE_ID,
            keep_file=args.keep_file,
            existing_props=existing_props,
        )
        results.append((pdf_path, url))

    # 汇总结果
    print(f"\n{'='*60}")
    print("📊 处理完成汇总")
    print(f"{'='*60}")
    success = [(p, u) for p, u in results if u]
    failed = [(p, u) for p, u in results if not u]

    print(f"✅ 成功: {len(success)} 篇")
    for path, url in success:
        print(f"   • {Path(path).name}")
        print(f"     {url}")

    if failed:
        print(f"❌ 失败: {len(failed)} 篇")
        for path, _ in failed:
            print(f"   • {Path(path).name}")


if __name__ == "__main__":
    main()
