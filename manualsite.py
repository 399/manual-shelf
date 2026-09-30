#!/usr/bin/env python3
"""Build a readable, searchable static website from a directory of manuals.

设计边界（详见 agent.md / README.md）：
- 纯静态、无数据库/账号/MCP/API/常驻进程；正文写入 HTML，浏览器内搜索读预生成索引。
- 不提供网页内导入浮窗：资料放在 site.json 的 manuals_dir（默认 manuals），每本一个文件夹，运行 build 查看报告。
- 构建只复制所需静态资源（site.css、search.js），不再发布 import.js。
- 单本异常完整记录并继续，全局发布错误不吞；旧站通过 rename 备份保留，不使用 rmtree 删除旧站或个人目录。
"""

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parent
SUPPORTED = {".pdf", ".txt", ".md", ".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
ASSETS = ROOT / "assets"
CACHE_VERSION = 1
E = html.escape

STATUS_LABELS = {
    "all_text": "全部页面已提取文字",
    "partial_text": "部分页面未提取文字",
    "original_only": "仅提供原件",
    "processing_failed": "处理失败",
}
METHOD_LABELS = {"direct": "直接提取", "ocr": "OCR 识别", "none": "未识别文字"}


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def rel(path, base):
    """返回相对路径用于展示，绝不泄露本机绝对路径。"""
    try:
        return path.relative_to(base)
    except ValueError:
        return Path(path).name


def redact(text, secrets):
    """去掉可能泄露本机绝对路径的前缀（root / source / staging 等）。

    笔记与错误信息（尤其是 OSError 的消息）常内嵌绝对路径，写出
    report / catalog / 每本 HTML 前必须脱敏，只保留相对信息。
    """
    text = str(text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "")
    return text


def title(text):
    return E(text, quote=True)


def natural_key(name):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def run(*args):
    try:
        process = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        return process.stdout if process.returncode == 0 else ""
    except OSError:
        return ""


def list_tesseract_langs():
    try:
        process = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError:
        return set()
    text = (process.stdout or "") + (process.stderr or "")
    langs = set()
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"^[\w+]+$", line) and line.lower() != "tesseract" and "tesseract" not in line.lower():
            langs.add(line)
    return langs


def tool_state(language):
    return {
        "pdftotext": bool(shutil.which("pdftotext")),
        "pdfinfo": bool(shutil.which("pdfinfo")),
        "pdftoppm": bool(shutil.which("pdftoppm")),
        "tesseract": bool(shutil.which("tesseract")),
        "ocr_language": language,
        "ocr_language_available": _lang_available(language),
    }


def _lang_available(language):
    if not shutil.which("tesseract"):
        return False
    available = list_tesseract_langs()
    needed = [part.strip() for part in language.split("+") if part.strip()]
    if not needed:
        return False
    # 精确匹配：语言包代码必须完全一致，避免 eng 误命中 english 之类。
    for part in needed:
        if part not in available:
            return False
    return True


def load_config(root):
    """读取 site.json；缺失时使用默认配置，使 build 不依赖 init。"""
    config_file = root / "site.json"
    default = {
        "title": "家中说明书",
        "description": "每件设备，都有答案。",
        "ocr_language": "chi_sim+eng",
        "manuals_dir": "manuals",
    }
    if not config_file.exists():
        return default
    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(f"site.json 不是合法 JSON：{error}")
    if not isinstance(data, dict):
        raise ValueError("site.json 必须是 JSON 对象")
    merged = dict(default)
    merged.update(data)
    return merged


def init(root):
    """兼容命令：创建设置、资料目录与空站点。build 已不要求先 init。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "manuals").mkdir(parents=True, exist_ok=True)
    config = root / "site.json"
    if not config.exists():
        write(config, json.dumps(
            {"title": "家中说明书", "description": "每件设备，都有答案。", "ocr_language": "chi_sim+eng", "manuals_dir": "manuals"},
            ensure_ascii=False, indent=2) + "\n")
    build(root)
    print(f"初始网页已就绪：{'site/index.html'}（更新内容时运行 build）")


def pdf_pages(path, language, tools, notes):
    """返回 (pages, count_known)。未知页数不虚构：无文字且无 pdfinfo 时返回空。"""
    text = run("pdftotext", "-enc", "UTF-8", "-layout", str(path), "-")
    raw = text.split("\f") if text else []
    # 只移除一个末尾 formfeed 哨兵；真实尾部空白页必须保留，不能整段丢。
    if raw and not raw[-1].strip():
        raw.pop()
    info = run("pdfinfo", str(path))
    match = re.search(r"^Pages:\s*(\d+)", info, re.M)
    info_count = int(match.group(1)) if match else None
    text_count = len(raw)
    count_known = (info_count is not None) or (text_count > 0)
    total = info_count if info_count is not None else text_count
    if total == 0:
        if not tools["pdftotext"] and not tools["pdfinfo"]:
            notes.append(f"{path.name}：未安装 pdftotext / pdfinfo，无法提取文字也不能确认页数，仅保留原件")
        elif not text_count:
            notes.append(f"{path.name}：未提取到文字且无法确认页数（需要 pdftotext 或 pdfinfo）")
        return [], count_known
    ocr_attempted = False
    pages = []
    for i in range(total):
        content = raw[i].strip() if i < len(raw) else ""
        method = "direct" if content else "none"
        if not content and tools["pdftoppm"] and tools["tesseract"]:
            ocr_attempted = True
            if tools["ocr_language_available"]:
                with tempfile.TemporaryDirectory() as temp:
                    prefix = Path(temp) / "page"
                    run("pdftoppm", "-f", str(i + 1), "-l", str(i + 1), "-singlefile", "-scale-to", "2200", "-png", str(path), str(prefix))
                    image = prefix.with_suffix(".png")
                    if image.exists():
                        ocr = run("tesseract", str(image), "-", "-l", language).strip()
                        if ocr:
                            content = ocr
                            method = "ocr"
            if not content:
                if tools["ocr_language_available"]:
                    notes.append(f"{path.name} 第 {i + 1} 页：扫描图片未识别到文字，可能是空白页或 tesseract 识别失败（语言包 {language}）")
                else:
                    notes.append(f"{path.name} 第 {i + 1} 页：扫描件需要 OCR，但 tesseract 语言包（{language}）缺失")
        elif not content and not tools["pdftoppm"] and not tools["tesseract"]:
            notes.append(f"{path.name} 第 {i + 1} 页：无可检索文字（需要 pdftotext 或 pdftoppm + tesseract）")
        elif not content:
            notes.append(f"{path.name} 第 {i + 1} 页：扫描件需要 tesseract{' + pdftoppm' if not tools['pdftoppm'] else ''} 才能 OCR")
        pages.append({"text": content, "source": path.name, "source_page": i + 1, "method": method})
    if ocr_attempted and not tools["ocr_language_available"]:
        # 逐本（每 PDF 文件即一本）警告：存在扫描页但语言包缺失。
        notes.append(f"{path.name}：存在需要 OCR 的扫描页，但 tesseract 语言包（{language}）缺失，相关页未完成文字识别")
    return pages, count_known


def image_pages(file, language, tools, notes):
    if not tools["tesseract"]:
        notes.append(f"{file.name}：未安装 tesseract，无法识别图片文字")
        return [{"text": "", "source": file.name, "source_page": 1, "method": "none"}]
    text = run("tesseract", str(file), "-", "-l", language).strip()
    method = "ocr" if text else "none"
    if not text:
        if tools["ocr_language_available"]:
            notes.append(f"{file.name}：图片未识别到文字，可能是空白图片或 tesseract 识别失败（语言包 {language}）")
        else:
            notes.append(f"{file.name}：tesseract 语言包（{language}）缺失，无法识别图片文字")
    return [{"text": text, "source": file.name, "source_page": 1, "method": method}]


def text_pages(file):
    data = file.read_text(encoding="utf-8-sig", errors="replace")
    pages = []
    for i, content in enumerate(data.split("\f"), 1):
        if content.strip():
            pages.append({"text": content.strip(), "source": file.name, "source_page": i, "method": "direct"})
    return pages


def extract(folder, files, language, tools, notes):
    """提取所有来源文件的页面，记录每页识别方式；多文件合并给出告警。
    返回 (pages, count_known)：任何来源文件页数未知则整本标记为未知。"""
    pages = []
    count_known = True
    pdfs = [f for f in files if f.suffix.lower() == ".pdf"]
    images = [f for f in files if f.suffix.lower() in IMAGES]
    other = [f for f in files if f.suffix.lower() in (".txt", ".md")]
    for file in files:
        suffix = file.suffix.lower()
        if suffix == ".pdf":
            extracted, known = pdf_pages(file, language, tools, notes)
            count_known = count_known and known
            pages.extend(extracted)
        elif suffix in IMAGES:
            pages.extend(image_pages(file, language, tools, notes))
        elif suffix in (".txt", ".md"):
            pages.extend(text_pages(file))
    if len(files) > 1:
        notes.append(
            f"本说明书由 {len(files)} 个文件合并（{len(pdfs)} 个 PDF + {len(images)} 张图片 + {len(other)} 个文字文件），"
            f"页序按文件名自然排序拼接，请核对页序与是否缺页。"
        )
    return pages, count_known


def fingerprint(folder, files, language, tools):
    digest = hashlib.sha256()
    digest.update(language.encode("utf-8"))
    digest.update(json.dumps(tools, sort_keys=True).encode("utf-8"))
    for file in files:
        digest.update(file.name.encode("utf-8"))
        digest.update(file.read_bytes())
    meta_file = folder / "manual.json"
    if meta_file.exists() and not meta_file.is_symlink():
        digest.update(b"meta:")
        digest.update(meta_file.read_bytes())
    return digest.hexdigest()


def manual_meta(folder, source):
    meta = {"category": "未分类", "brand": "", "model": "", "title": folder.name, "aliases": [], "tags": []}
    meta_file = folder / "manual.json"
    if meta_file.exists():
        if meta_file.is_symlink():
            raise ValueError(f"{rel(meta_file, source)} 是符号链接，已拒绝读取")
        try:
            supplied = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as error:
            raise ValueError(f"{rel(meta_file, source)} 不是合法 JSON：{error}")
        if not isinstance(supplied, dict):
            raise ValueError(f"{rel(meta_file, source)} 必须是 JSON 对象")
        for key in ("category", "brand", "model", "title"):
            if key in supplied and supplied[key] is not None:
                meta[key] = supplied[key]
        for field in ("aliases", "tags"):
            if field in supplied and supplied[field] is not None:
                meta[field] = supplied[field]
    for key in ("category", "brand", "model", "title"):
        if not isinstance(meta[key], str):
            raise ValueError(f"{rel(meta_file if meta_file.exists() else folder, source)}：{key} 必须是文字")
    for field in ("aliases", "tags"):
        if not isinstance(meta[field], list) or not all(isinstance(a, str) for a in meta[field]):
            raise ValueError(f"{rel(meta_file if meta_file.exists() else folder, source)}：{field} 必须是文字数组")
    return meta


def sources(folder, source, warnings):
    files = []
    try:
        for entry in sorted(folder.iterdir(), key=lambda p: natural_key(p.name)):
            if entry.is_symlink():
                warnings.append(f"已跳过符号链接文件：{rel(entry, source)}")
                continue
            if entry.is_file() and entry.suffix.lower() in SUPPORTED:
                files.append(entry)
    except OSError as error:
        warnings.append(f"无法读取目录，已跳过其内容：{rel(folder, source)}（{error}）")
        return None
    return files


def walk_dirs(source, warnings):
    """递归收集子目录（拒绝符号链接），遍历出错不静默变空。"""
    results = []
    stack = [source]
    while stack:
        current = stack.pop()
        try:
            for entry in sorted(current.iterdir(), key=lambda p: natural_key(p.name)):
                if entry.is_symlink():
                    warnings.append(f"已跳过符号链接：{rel(entry, source)}")
                    continue
                if entry.is_dir():
                    results.append(entry)
                    stack.append(entry)
        except OSError as error:
            warnings.append(f"遍历时无法访问目录，已跳过：{rel(current, source)}（{error}）")
    return results


def derive_status(pages, count_known, failed):
    if failed:
        return "processing_failed"
    if not pages:
        return "original_only"
    texts = [p for p in pages if p["text"].strip()]
    if not texts:
        return "original_only"
    if len(texts) == len(pages):
        return "all_text"
    return "partial_text"


def source_link(source, source_page, label):
    url = "media/" + quote(source)
    if source.lower().endswith(".pdf"):
        url += f"#page={source_page}"
    return f'<a class="text-link" href="{title(url)}">{E(label)}</a>'


def shell(page_title, site_title, body, asset_prefix, description=""):
    root_prefix = asset_prefix.removesuffix("assets/")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="{title(description or page_title)}">
  <title>{E(page_title)} · {E(site_title)}</title>
  <link rel="stylesheet" href="{asset_prefix}site.css">
  <link rel="alternate" type="application/json" title="说明书目录" href="{root_prefix}catalog.json">
  <link rel="alternate" type="application/json" title="构建报告" href="{root_prefix}report.json">
</head>
<body>
  <a class="skip-link" href="#content">跳到正文</a>
  <header class="site-header">
    <div class="wrap header-inner">
      <a class="brand" href="{root_prefix}index.html">{E(site_title)}</a>
      <a class="nav-report" href="{root_prefix}report.html">构建报告</a>
    </div>
  </header>
  <main id="content">{body}</main>
</body>
</html>
"""


def build_manual(staging, item, pages, files, site_title, status, issues, count_known, sources_list, secrets=()):
    slug, meta = item["id"], item
    files = files or []
    base = staging / "manual" / slug
    # 逐文件复制：单个不可读文件不能让整本失败；只成功复制者才有源链接。
    copied = []
    for file in files:
        dest = base / "media" / file.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if file.is_symlink():
            continue
        try:
            shutil.copy2(file, dest)
            copied.append(file)
        except OSError as error:
            issues.append(redact(f"无法复制源文件 {file.name}：{error}", secrets))
    files_html = " · ".join(source_link(f.name, 1, f.name) for f in copied)
    copied_names = {f.name for f in copied}
    tag_html = '<div class="manual-tags">' + "".join(f"<span>{E(tag)}</span>" for tag in meta["tags"]) + "</div>" if meta["tags"] else ""
    warning_html = ""
    if issues:
        warning_html = '<div class="manual-warnings"><h2>处理提示</h2><ul>' + "".join(f"<li>{E(note)}</li>" for note in issues) + "</ul></div>"

    if status == "processing_failed":
        body = f"""<div class="wrap">
          <nav class="breadcrumbs" aria-label="当前位置"><a href="../../index.html">全部说明书</a><span aria-hidden="true">/</span><span>{E(meta["title"])}</span></nav>
          <div class="manual-hero"><span class="eyebrow">{E(meta["category"])} / {E(meta["brand"] or "其他品牌")}</span>
            <h1>{E(meta["title"])}</h1>
            <p class="model-line">{E(meta["model"])}</p>
            <p class="status-failed">本说明书处理失败，未生成逐页内容。详情见下方提示与构建报告。</p>
          </div>
          {warning_html}
        </div>"""
        write(base / "index.html", shell(meta["title"], site_title, body, "../../assets/", f'{meta["brand"]} {meta["model"]} {meta["title"]} 说明书'))
        write(base / "read.html", shell(f'{meta["title"]} · 连续阅读', site_title, f'<div class="wrap"><p>本说明书处理失败，无法生成连续阅读。</p></div>', "../../assets/"))
        write(base / "fulltext.txt", f'{meta["title"]}（处理失败）\n' + "\n".join(issues) + "\n")
        return

    page_rows = "\n".join(
        f'''<li><a href="page-{n}.html"><span>第 {n:02d} 页</span></small>{E(p["text"][:86].replace(chr(10), " ") or "尚无识别文字")}</small></a>
            <span class="src">{E(p["source"])} · 原件第 {p["source_page"]} 页</span>
            <span class="method method-{p["method"]}">{METHOD_LABELS.get(p["method"], p["method"])}</span></li>'''
        for n, p in enumerate(pages, 1)
    )
    missing = [p for p in pages if not p["text"].strip()]
    missing_html = ""
    if missing:
        missing_html = '<div class="page-missing"><p>以下页面未识别到文字，请查看原件：' + "、".join(
            f'第 {i + 1} 页（{E(p["source"])} 原件第 {p["source_page"]} 页）' for i, p in enumerate(pages) if not p["text"].strip()
        ) + "。</p></div>"
    count_note = "" if count_known else '<p class="count-unknown">页数未知：缺少 PDF 页数确认工具或正文，可能存在缺页。</p>'

    top = f"""<div class="wrap">
      <nav class="breadcrumbs" aria-label="当前位置"><a href="../../index.html">全部说明书</a><span aria-hidden="true">/</span><span>{E(meta["title"])}</span></nav>
      <div class="manual-hero"><span class="eyebrow">{E(meta["category"])} / {E(meta["brand"] or "其他品牌")}</span>
        <h1>{E(meta["title"])}</h1>
        <p class="model-line">{E(meta["model"])}</p>
        {tag_html}
        <div class="manual-meta">{len(pages) if count_known else "?"} 页 · {len(files)} 个原始文件 · {STATUS_LABELS.get(status, status)}</div>
      </div>
      {warning_html}
      <div class="detail-grid"><section class="card">
        <h2>原始文件</h2><p>文字是为阅读与检索提取的；参数和安全事项请以原件为准。</p>
        <div class="file-links">{files_html}</div>
      </section><section class="card">
        <h2>逐页阅读</h2><p>每页都能独立打开和引用；也可<a href="read.html">连续阅读</a>或<a href="fulltext.txt">下载全文</a>。</p>
        <ol class="page-list">{page_rows}</ol>
        {count_note}
        {missing_html}
      </section></div>
    </div>"""
    write(base / "index.html", shell(meta["title"], site_title, top, "../../assets/", f'{meta["brand"]} {meta["model"]} {meta["title"]} 说明书'))

    read_sections = "\n".join(
        f'''<section class="read-page">
          <header><span class="src">{E(p["source"])} · 原件第 {p["source_page"]} 页</span><span class="method method-{p["method"]}">{METHOD_LABELS.get(p["method"], p["method"])}</span></header>
          <div class="page-text">{E(p["text"] or "此页暂时没有可检索的文字。请查看原始文件。")}</div>
        </section>'''
        for p in pages
    )
    read_body = f"""<div class="wrap reading-wrap">
      <nav class="breadcrumbs" aria-label="当前位置"><a href="../../index.html">全部说明书</a><span aria-hidden="true">/</span><a href="index.html">{E(meta["title"])}</a><span aria-hidden="true">/</span><span>连续阅读</span></nav>
      <article class="reading"><h1>{E(meta["title"])}</h1>{read_sections}</article>
    </div>"""
    write(base / "read.html", shell(f'{meta["title"]} · 连续阅读', site_title, read_body, "../../assets/"))

    fulltext = f'{meta["title"]}（{meta["brand"]} {meta["model"]}）\n'
    for p in pages:
        fulltext += f'\n=== 来源：{p["source"]} · 原件第 {p["source_page"]} 页 · 识别方式：{METHOD_LABELS.get(p["method"], p["method"])} ===\n'
        fulltext += (p["text"] or "（无识别文字，请查看原件）") + "\n"
    if not pages:
        fulltext += "\n（未提取到任何页面）\n"
    write(base / "fulltext.txt", fulltext)

    for n, page in enumerate(pages, 1):
        copy = page["text"] or "此页暂时没有可检索的文字。请查看原始文件。"
        previous = f'<a href="page-{n-1}.html">← 上一页</a>' if n > 1 else "<span></span>"
        following = f'<a href="page-{n+1}.html">下一页 →</a>' if n < len(pages) else "<span></span>"
        image = ""
        if Path(page["source"]).suffix.lower() in IMAGES:
            image = f'<figure class="source-image"><img src="media/{title(quote(page["source"]))}" alt="第 {n} 页原始照片" loading="lazy"></figure>'
        source_label = f'{page["source"]} · 原件第 {page["source_page"]} 页' if page["source"].lower().endswith(".pdf") else page["source"]
        # 源链接只给成功复制者：未复制的源文件不渲染可点击链接（避免指向缺失的 media）。
        source_html = source_link(page["source"], page["source_page"], source_label) if page["source"] in copied_names else E(source_label)
        content = f"""<div class="wrap reading-wrap">
          <nav class="breadcrumbs" aria-label="当前位置"><a href="../../index.html">全部说明书</a><span aria-hidden="true">/</span><a href="index.html">{E(meta["title"])}</a><span aria-hidden="true">/</span><span>第 {n} 页</span></nav>
          <article class="reading"><span class="eyebrow">{E(meta["brand"])} · {E(meta["model"])}</span><h1>第 {n} 页</h1>
            <div class="page-source">来源：{source_html} · 识别方式：{METHOD_LABELS.get(page["method"], page["method"])}</div>
            <div class="page-text">{E(copy)}</div>{image}
          </article><nav class="pagination" aria-label="翻页">{previous}<a href="index.html">返回目录</a>{following}</nav>
        </div>"""
        write(base / f"page-{n}.html", shell(f'{meta["title"]} · 第 {n} 页', site_title, content, "../../assets/", copy[:145]))


def shell_report(site_title, body, asset_prefix):
    root_prefix = asset_prefix.removesuffix("assets/")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>构建报告 · {E(site_title)}</title>
  <link rel="stylesheet" href="{asset_prefix}site.css">
</head>
<body>
  <a class="skip-link" href="#content">跳到正文</a>
  <header class="site-header"><div class="wrap header-inner"><a class="brand" href="{root_prefix}index.html">{E(site_title)}</a></div></header>
  <main id="content">{body}</main>
</body>
</html>
"""


def build_report(site_title, generated_at, source_rel, tools, manuals, changes, warnings):
    counts = {"manuals": len(manuals)}
    by_status = {}
    pages_total = 0
    for m in manuals:
        by_status[m["status"]] = by_status.get(m["status"], 0) + 1
        pages_total += m.get("page_count") or 0
    counts["pages"] = pages_total
    counts["by_status"] = by_status

    change_rows = ""
    for kind, label in (("added", "新增"), ("updated", "更新"), ("removed", "移除")):
        items = changes.get(kind, [])
        if items:
            lis = "".join(f"<li>{E(i['title'])}</li>" for i in items)
            change_rows += f"<li><strong>{label}（{len(items)}）</strong><ul>{lis}</ul></li>"

    tool_rows = "".join(
        f"<li>{E(name)}：{'可用' if state else '未安装'}</li>"
        for name, state in [("pdftotext", tools["pdftotext"]), ("pdfinfo", tools["pdfinfo"]),
                            ("pdftoppm", tools["pdftoppm"]), ("tesseract", tools["tesseract"])]
    )
    lang_state = "可用" if tools["ocr_language_available"] else "语言包缺失"
    tool_rows += f"<li>tesseract 语言包（{E(tools['ocr_language'])}）：{lang_state}</li>"

    warn_rows = "".join(f"<li>{E(w)}</li>" for w in warnings) or "<li>无</li>"

    manual_blocks = ""
    for m in manuals:
        page_detail = ""
        for p in m.get("pages_detail", []):
            page_detail += (f"<li>第 {p['order']} 页 · {E(p['source'])} 原件第 {p['source_page']} 页 · "
                            f"{METHOD_LABELS.get(p['method'], p['method'])} · {'有文字' if p['has_text'] else '无文字'}</li>")
        issue_rows = "".join(f"<li>{E(i)}</li>" for i in m.get("issues", [])) or "<li>无</li>"
        count_txt = f"{m['page_count']} 页" if m.get("page_count_known") else "页数未知"
        manual_blocks += f"""<section class="report-manual">
          <h3><a href="{E(m['url'])}">{E(m['title'])}</a> <span class="status">{STATUS_LABELS.get(m['status'], m['status'])}</span></h3>
          <p class="meta-line">{E(m['category'])} / {E(m['brand'] or '其他品牌')} / {E(m['model'] or '—')} · {count_txt}</p>
          <details><summary>逐页状态（{len(m.get('pages_detail', []))}）</summary><ul>{page_detail}</ul></details>
          <details><summary>处理提示（{len(m.get('issues', []))}）</summary><ul>{issue_rows}</ul></details>
        </section>"""

    body = f"""<div class="wrap">
      <h1>构建报告</h1>
      <p class="report-time">生成时间：{E(generated_at)}</p>
      <p>资料目录：<code>{E(str(source_rel))}</code></p>
      <section class="report-section"><h2>概览</h2>
        <ul>
          <li>说明书：{counts['manuals']} 本</li>
          <li>页面：{counts['pages']} 页</li>
          <li>状态分布：{ "、".join(f"{STATUS_LABELS.get(k, k)} {v}" for k, v in by_status.items()) or "—" }</li>
        </ul>
      </section>
      <section class="report-section"><h2>本次变更（基于上次目录指纹）</h2>
        <ul>{change_rows or "<li>首次构建或与上次无可比目录。</li>"}</ul>
      </section>
      <section class="report-section"><h2>工具状态</h2><ul>{tool_rows}</ul></section>
      <section class="report-section"><h2>全局提示</h2><ul>{warn_rows}</ul></section>
      <section class="report-section"><h2>逐本明细</h2>{manual_blocks}</section>
    </div>"""
    return (shell_report(site_title, body, "assets/"),
            {"generated_at": generated_at, "source": str(source_rel), "tools": tools,
             "counts": counts, "changes": changes, "warnings": warnings, "manuals": manuals})


def homepage(site_title, description, manuals, manuals_dir, generated_at, overridden=False):
    if not manuals:
        config_note = "（可在 site.json 的 manuals_dir 指定其他目录）" if not overridden else "（本次通过 --manuals 指定）"
        body = f"""<div class="wrap"><section class="empty-view" aria-labelledby="empty-title">
          <h1 id="empty-title">{E(site_title)}</h1>
          <p class="empty-intro">把家电的 PDF、照片或文字资料整理成可浏览、可搜索的说明书目录。</p>
          <div class="empty-state"><h2>当前没有说明书</h2>
            <p>在 <code>{E(str(manuals_dir))}/</code> 下放资料，每本一个文件夹{config_note}；运行构建命令后刷新本页查看报告。</p>
            <pre><code>python3 manualsite.py build</code></pre>
          </div>
          <div class="startup-note"><h2>打开与更新</h2><p>现在打开的 <code>site/index.html</code> 就是网站，不需要启动服务。添加文件后，在项目根目录运行 <code>python3 manualsite.py build</code>，再刷新网页；Python 负责生成目录、逐页内容、搜索索引与构建报告。</p>
            <p>可随时查看 <a href="report.html">构建报告</a>，了解每本说明书的提取状态与处理提示。</p>
          </div>
          <p class="build-time">本报告生成时间：{E(generated_at)}</p>
        </section></div>"""
        return shell(site_title, site_title, body, "assets/", "说明书资料库")
    categories = sorted({m["category"] for m in manuals})
    filters = "\n".join(f'<button type="button" class="filter" data-category="{title(c)}" aria-pressed="false">{E(c)}</button>' for c in categories)
    cards = "\n".join(
        f"""<a class="manual-card" href="{E(m['url'])}" data-category="{title(m['category'])}" data-status="{title(m['status'])}">
          <span class="card-top"><span class="category">{E(m['category'])}</span><span class="arrow" aria-hidden="true">↗</span></span>
          <span class="card-title">{E(m['title'])}</span><span class="card-sub">{E(m['brand'] or '其他品牌')} · {E(m['model'])}</span>
          <span class="card-bottom">{m['page_count'] if m.get('page_count_known') else '?'} 页 · {STATUS_LABELS.get(m['status'], m['status'])} <span>查看说明书 →</span></span>
        </a>""" for m in manuals
    )
    body = f"""<div class="wrap">
      <section class="library" aria-labelledby="library-heading"><div class="section-heading"><h1 id="library-heading">说明书 <span class="count">{len(manuals)}</span></h1></div>
        <p class="build-time">上次构建：{E(generated_at)} · <a href="report.html">查看构建报告</a> · 新增或更新资料后运行 <code>python3 manualsite.py build</code> 重新生成</p>
        <div class="search-shell"><label for="search">搜索</label><input id="search" type="search" autocomplete="off" placeholder="名称、型号或正文"></div>
        <div class="filters" aria-label="筛选类别"><button type="button" class="filter active" data-category="all" aria-pressed="true">全部</button>{filters}</div>
        <p class="status" id="status" role="status" aria-live="polite"></p>
        <div class="card-grid" id="cards">{cards}</div>
        <div class="results" id="results" hidden></div>
      </section>
    </div><script src="assets/search-index.js" defer></script><script src="assets/search.js" defer></script>"""
    return shell(site_title, site_title, body, "assets/", description)


def _overlaps(a, b):
    try:
        a.relative_to(b)
        return True
    except ValueError:
        pass
    try:
        b.relative_to(a)
        return True
    except ValueError:
        pass
    return False


def build(root, manuals_dir=None):
    root = root.resolve()
    config = load_config(root)
    site_title = config["title"]
    configured = config.get("manuals_dir", "manuals")
    language = config.get("ocr_language", "chi_sim+eng")

    if manuals_dir is not None:
        source = Path(manuals_dir).resolve()
        if str(source) != str((root / configured).resolve()):
            print(f"注意：本次使用 --manuals 指定的目录，覆盖 site.json 中的 manuals_dir（'{configured}'）。")
    else:
        source = (root / configured).resolve()

    if not source.exists():
        raise ValueError(f"说明书目录不存在：{rel(source, root)}（请在 {configured}/ 放入资料，或修改 site.json 的 manuals_dir）")
    if not (os.access(str(source), os.R_OK) and source.is_dir()):
        raise ValueError(f"说明书目录不可读取：{rel(source, root)}")

    # 脱敏用：凡可能出现在 notes/warnings 中的绝对前缀，写出前一律抹掉。
    secrets = (str(root), str(source))
    output = root / "site"
    cache_dir = root / ".cache"
    backups = root / ".backups"
    if _overlaps(source, output) or _overlaps(source, cache_dir) or _overlaps(source, backups) or _overlaps(output, source):
        raise ValueError("说明书目录与输出/缓存/备份目录重叠，已拒绝构建以免误删资料")

    tools = tool_state(language)

    # 预检旧站目录指纹，用于变更统计
    previous = []
    if output.exists():
        prev_cat = output / "catalog.json"
        if prev_cat.exists():
            try:
                previous = json.loads(prev_cat.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                previous = []

    staging = root / f".site-build-{uuid.uuid4().hex}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    assets_out = staging / "assets"
    assets_out.mkdir(parents=True, exist_ok=True)
    for name in ("site.css", "search.js"):
        src = ASSETS / name
        if src.exists():
            shutil.copy2(src, assets_out / name)

    warnings = []
    manuals = []
    search = []
    meta_index = []

    # 顶层散文件各自成一本；子目录递归作为单本
    loose = []
    try:
        for entry in sorted(source.iterdir(), key=lambda p: natural_key(p.name)):
            if entry.is_symlink():
                warnings.append(f"已跳过符号链接（不安全）：{rel(entry, source)}")
                continue
            if entry.is_file() and entry.suffix.lower() in SUPPORTED:
                loose.append((entry, [entry]))
            elif entry.is_dir():
                pass
    except OSError as error:
        raise ValueError(f"无法读取说明书目录：{rel(source, root)}（{error}）")
    dirs = walk_dirs(source, warnings)
    jobs = loose + [(d, sources(d, source, warnings)) for d in sorted(dirs, key=lambda p: natural_key(p.name))]

    for location, files in jobs:
        folder = location.parent if location.is_file() else location
        relative = location.relative_to(source).as_posix() if not location.is_file() else location.name
        slug = hashlib.sha256(relative.encode("utf-8")).hexdigest()[:12]
        notes = []
        pages = []
        count_known = True
        sig = None
        failed = False
        status = "original_only"
        sources_list = []
        meta = None
        try:
            if files is None:
                raise ValueError(f"无法读取目录：{relative}")
            if location.is_file():
                meta = {"category": "未分类", "brand": "", "model": "", "title": location.stem, "aliases": [], "tags": []}
            else:
                meta = manual_meta(folder, source)

            if not files:
                notes.append(f"{relative}：目录中没有受支持的说明书文件，已跳过")
                continue

            sig = fingerprint(folder, files, language, tools)
            cache_path = cache_dir / f"{slug}.json"
            cached = _load_cache(cache_path, sig)
            if cached is not None:
                pages = cached["pages"]
                notes = cached["notes"]
                count_known = cached["count_known"]
            else:
                pages, count_known = _extract_with_cache(folder, files, language, tools, notes, cache_path, sig)

            status = derive_status(pages, count_known, failed)
            sources_list = [{"name": f.name, "type": f.suffix.lower().lstrip(".")} for f in files]
        except Exception as error:
            failed = True
            status = "processing_failed"
            notes.append(f"{relative}：处理失败 — {error}")
            if meta is None:
                meta = {"category": "未分类", "brand": "", "model": "", "title": folder.name, "aliases": [], "tags": []}

        # 写出前脱敏：notes 内可能含绝对路径（尤其 OSError 消息）。
        notes[:] = [redact(n, secrets) for n in notes]

        meta["id"] = slug
        meta["url"] = f"manual/{slug}/index.html"
        meta["read_url"] = f"manual/{slug}/read.html"
        meta["fulltext_url"] = f"manual/{slug}/fulltext.txt"
        meta["status"] = status
        meta["page_count_known"] = bool(count_known) if status != "processing_failed" else False
        meta["page_count"] = len(pages) if count_known else 0
        meta["sources"] = sources_list
        # 复用 try 内已算出的 sig；except 后绝不重算 fingerprint，避免单本失败拖垮全局。
        meta["fingerprint"] = sig if sig is not None else hashlib.sha256(relative.encode()).hexdigest()
        meta["pages_detail"] = [
            {"order": i + 1, "source": p["source"], "source_page": p["source_page"], "method": p["method"], "has_text": bool(p["text"].strip())}
            for i, p in enumerate(pages)
        ]
        manuals.append(meta)

        build_manual(staging, meta, pages, files, site_title, status, notes, count_known, sources_list, secrets)
        # build_manual 可能追加复制失败提示，须在事后写入 issues，保证进了 catalog/report。
        meta["issues"] = notes
        if status != "processing_failed":
            for n, page in enumerate(pages, 1):
                search.append({**{k: meta[k] for k in ("id", "title", "brand", "model", "category", "aliases", "tags")},
                               "page": n, "text": page["text"], "url": f"manual/{slug}/page-{n}.html",
                               "method": page["method"]})
        meta_index.append({k: meta[k] for k in ("id", "title", "brand", "model", "category", "aliases", "tags", "url")})

    manuals.sort(key=lambda m: (m["category"], m["brand"], m["title"]))
    search.sort(key=lambda s: (s["category"], s["brand"], s["title"], s["page"]))
    meta_index.sort(key=lambda m: (m["category"], m["brand"], m["title"]))

    generated_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    warnings = [redact(w, secrets) for w in warnings]
    report_html, report_data = build_report(site_title, generated_at, rel(source, root), tools, manuals,
                                            _changes(previous, manuals), warnings)

    write(staging / "search.json", json.dumps(search, ensure_ascii=False, separators=(",", ":")))
    write(staging / "assets" / "search-index.js",
          "window.MANUAL_SHELF_INDEX = " + json.dumps(search, ensure_ascii=False, separators=(",", ":")) + ";\n" +
          "window.MANUAL_SHELF_META = " + json.dumps(meta_index, ensure_ascii=False, separators=(",", ":")) + ";\n")
    write(staging / "catalog.json", json.dumps(manuals, ensure_ascii=False, indent=2))
    write(staging / "report.json", json.dumps(report_data, ensure_ascii=False, indent=2))
    write(staging / "report.html", report_html)
    # 只用安全相对标签（绝不泄露绝对路径），--manuals 与默认配置保持一致。
    write(staging / "index.html", homepage(site_title, config.get("description", ""), manuals, rel(source, root), generated_at, manuals_dir is not None))

    base_url = config.get("base_url", "").rstrip("/")
    if base_url:
        links = ["/index.html", "/report.html"] + [f'/{m["url"]}' for m in manuals] + [s["url"] for s in search]
        write(staging / "sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' +
              "".join(f'<url><loc>{E(base_url + "/" + x.lstrip("/"))}</loc></url>' for x in links) + "</urlset>\n")

    _publish(staging, output, backups)
    total_issues = sum(len(m.get("issues", [])) for m in manuals)
    print(f"构建完成：{len(manuals)} 本说明书、{sum(m.get('page_count', 0) for m in manuals)} 页；"
          f"提示 {total_issues} 条；报告见 report.html")


def _load_cache(cache_path, sig):
    """读取并校验缓存；结构不合法或版本/指纹不符都返回 None（触发重新提取）。

    旧实现直接 .get(...)，当缓存文件是 list 或字段缺失时会抛 AttributeError / TypeError，
    把单本缓存问题变成整次构建失败。这里改为完整 schema 校验，任何不合法都当作缓存未命中。
    """
    if not cache_path.exists():
        return None
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(cached, dict):
        return None
    if cached.get("version") != CACHE_VERSION:
        return None
    if cached.get("fingerprint") != sig:
        return None
    pages = cached.get("pages")
    if not isinstance(pages, list) or not all(isinstance(p, dict) for p in pages):
        return None
    notes = cached.get("notes")
    if not isinstance(notes, list):
        return None
    count_known = cached.get("count_known", True)
    if not isinstance(count_known, bool):
        return None
    return {"pages": pages, "notes": notes, "count_known": count_known}


def _extract_with_cache(folder, files, language, tools, notes, cache_path, sig):
    pages, count_known = extract(folder, files, language, tools, notes)
    cache_dir = cache_path.parent
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        write(cache_path, json.dumps({"version": CACHE_VERSION, "fingerprint": sig, "pages": pages,
                                      "notes": notes, "count_known": count_known}, ensure_ascii=False))
    except OSError:
        pass
    return pages, count_known


def _changes(previous, current):
    prev_by_id = {m.get("id"): m for m in previous if isinstance(m, dict)}
    cur_by_id = {m["id"]: m for m in current}
    added, updated, removed = [], [], []
    for cid, m in cur_by_id.items():
        if cid not in prev_by_id:
            added.append({"id": cid, "title": m["title"]})
        elif prev_by_id[cid].get("fingerprint") != m.get("fingerprint"):
            updated.append({"id": cid, "title": m["title"]})
    for pid, m in prev_by_id.items():
        if pid not in cur_by_id:
            removed.append({"id": pid, "title": m.get("title", pid)})
    return {"added": added, "updated": updated, "removed": removed}


def _publish(staging, output, backups):
    """原子发布：旧站 rename 到备份（uuid 唯一，绝不删除已有备份），
    staging rename 到 site；任何失败回滚并保留旧站，回滚失败必须明确备份路径。"""
    backup_dir = None
    if output.exists():
        backups.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        # uuid 后缀保证同秒、同 pid 也不会撞名，从而绝不 rmtree 已有备份。
        backup_dir = backups / f"site-{stamp}-{uuid.uuid4().hex[:12]}"
        output.rename(backup_dir)
    try:
        staging.rename(output)
    except OSError as error:
        if backup_dir is not None and backup_dir.exists() and not output.exists():
            try:
                backup_dir.rename(output)
            except OSError as rollback_error:
                raise OSError(
                    f"发布失败且回滚失败：旧站备份位于 {backup_dir}，但无法恢复为 site（{rollback_error}）。"
                    f"请手动将该备份目录重命名为 site 以恢复旧站。"
                ) from rollback_error
        if staging.exists():
            shutil.rmtree(staging)
        raise


def main():
    parser = argparse.ArgumentParser(description="把本地说明书目录构建为静态网站")
    parser.add_argument("command", choices=("init", "build"))
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="站点数据目录，默认为当前目录")
    parser.add_argument("--manuals", type=Path, help="已有的说明书目录；不指定则使用 site.json 的 manuals_dir")
    args = parser.parse_args()
    try:
        if args.command == "init":
            init(args.root.resolve())
        else:
            build(args.root.resolve(), args.manuals)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as error:
        parser.exit(1, f"错误：{error}\n")


if __name__ == "__main__":
    main()
