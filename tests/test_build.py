import json
import os
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from html.parser import HTMLParser
from urllib.parse import unquote, urlparse

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import manualsite
from manualsite import build, init


class LocalLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        for key, value in attrs:
            if key in ("href", "src") and value:
                self.hrefs.append(value)


def make_project():
    temp = tempfile.TemporaryDirectory()
    root = Path(temp.name)
    (root / "manuals").mkdir(parents=True, exist_ok=True)
    return temp, root


class StaticBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.root = make_project()
        init(self.root)
        self.manual = self.root / "manuals" / "厨房" / "测试品牌" / "A-123"
        self.manual.mkdir(parents=True)
        (self.manual / "书.txt").write_text("清洗滤网时先断电。\f第二页：重新安装滤网。", encoding="utf-8")
        (self.manual / "manual.json").write_text(json.dumps({"title": "<测试说明书>", "aliases": ["滤网"], "tags": ["维护"]}), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    # --- 基础：页面、索引、链接 ---
    def test_build_pages_index_and_valid_links(self):
        build(self.root)
        site = self.root / "site"
        catalog = json.loads((site / "catalog.json").read_text())
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["category"], "未分类")
        self.assertEqual(catalog[0]["brand"], "")
        self.assertTrue((site / catalog[0]["url"]).is_file())
        index = json.loads((site / "search.json").read_text())
        self.assertEqual(len(index), 2)
        self.assertIn("清洗滤网", index[0]["text"])
        self.assertEqual(index[0]["tags"], ["维护"])
        self.assertIn("维护", (site / catalog[0]["url"]).read_text())
        self.assertIn("清洗滤网", (site / "assets" / "search-index.js").read_text())
        # 搜索元数据 fallback 已发布
        self.assertIn("MANUAL_SHELF_META", (site / "assets" / "search-index.js").read_text())
        self.assertIn("MANUAL_SHELF_INDEX", (site / "assets" / "search-index.js").read_text())
        # 不再发布 import.js
        self.assertFalse((site / "assets" / "import.js").exists())
        self.assertNotIn("import.js", (site / "index.html").read_text())
        self.assertNotIn("<dialog", (site / "index.html").read_text())
        for file in site.rglob("*.html"):
            parser = LocalLinks()
            parser.feed(file.read_text())
            for link in parser.hrefs:
                path = urlparse(link).path
                if path and not urlparse(link).scheme:
                    self.assertTrue((file.parent / unquote(path)).is_file(), f"broken link {file}: {link}")

    # --- 空站：无 dialog / 无 import.js / 明确资料目录指引 ---
    def test_fresh_empty_site_has_no_import_dialog_and_links_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init(root)
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
            self.assertIn('<h1 id="empty-title">家中说明书</h1>', page)
            self.assertIn("当前没有说明书", page)
            self.assertIn("manuals", page)
            self.assertIn("manuals_dir", page)
            self.assertIn("python3 manualsite.py build", page)
            self.assertIn("report.html", page)
            self.assertNotIn("<dialog", page)
            self.assertNotIn("data-open-import", page)
            self.assertNotIn("import.js", page)
            self.assertNotIn('id="search"', page)
            self.assertFalse((root / "site" / "assets" / "import.js").exists())
            self.assertTrue((root / "site" / "assets" / "site.css").is_file())
            self.assertTrue((root / "site" / "assets" / "search.js").is_file())
            self.assertTrue((root / "site" / "report.html").is_file())
            self.assertTrue((root / "site" / "report.json").is_file())
            parsed = LocalLinks()
            parsed.feed(page)
            for link in parsed.hrefs:
                path = urlparse(link).path
                if path and not urlparse(link).scheme:
                    self.assertTrue((root / "site" / unquote(path)).is_file(), f"broken onboarding link: {link}")

    def test_empty_then_build_shows_search_and_report(self):
        source = self.root / "manuals" / "first.txt"
        source.write_text("第一份说明书", encoding="utf-8")
        build(self.root)
        updated = (self.root / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="search"', updated)
        self.assertIn("report.html", updated)
        self.assertNotIn("当前没有说明书", updated)
        self.assertNotIn("data-open-import", updated)

    # --- 缓存增量 ---
    def test_cache_refresh_after_edit(self):
        build(self.root)
        content = self.manual / "书.txt"
        content.write_text("更新后的检索词", encoding="utf-8")
        build(self.root)
        self.assertIn("更新后的检索词", (self.root / "site" / "search.json").read_text())
        self.assertEqual(len(json.loads((self.root / "site" / "search.json").read_text())), 1)

    # --- init 不覆盖配置 ---
    def test_init_does_not_overwrite_settings(self):
        (self.root / "site.json").write_text('{"title": "我自己的配置"}', encoding="utf-8")
        init(self.root)
        self.assertEqual(json.loads((self.root / "site.json").read_text())["title"], "我自己的配置")
        for file in self.manual.iterdir():
            file.unlink()
        build(self.root)
        self.assertIn('id="empty-title">我自己的配置', (self.root / "site" / "index.html").read_text())

    # --- 外部目录 + --manuals 兼容（覆盖配置打印警示但不改写）---
    def test_external_directory_and_root_level_files(self):
        external = self.root / "external"
        external.mkdir()
        (external / "直接放入.txt").write_text("独立说明书正文", encoding="utf-8")
        build(self.root, external)
        catalog = json.loads((self.root / "site" / "catalog.json").read_text())
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["title"], "直接放入")
        self.assertEqual(catalog[0]["category"], "未分类")
        # 配置未被 --manuals 改写
        self.assertEqual(json.loads((self.root / "site.json").read_text())["manuals_dir"], "manuals")

    # --- AI 风格元数据 ---
    def test_ai_style_metadata_then_build_creates_search_and_original(self):
        folder = self.root / "manuals" / "空气环境" / "某品牌" / "AB-123"
        folder.mkdir(parents=True)
        (folder / "原件.txt").write_text("首次使用前清洗滤网", encoding="utf-8")
        (folder / "manual.json").write_text(json.dumps({
            "title": "空气净化器", "category": "空气环境", "brand": "某品牌",
            "model": "AB-123", "aliases": ["净化器"], "tags": ["清洗滤网"],
        }, ensure_ascii=False), encoding="utf-8")
        build(self.root)
        catalog = json.loads((self.root / "site" / "catalog.json").read_text())
        self.assertEqual(len(catalog), 2)
        item = next(item for item in catalog if item["model"] == "AB-123")
        self.assertEqual(item["title"], "空气净化器")
        self.assertEqual(item["category"], "空气环境")
        self.assertEqual(item["aliases"], ["净化器"])
        index = json.loads((self.root / "site" / "search.json").read_text())
        self.assertTrue(any(page["model"] == "AB-123" and "首次使用" in page["text"] for page in index))
        self.assertTrue((self.root / "site" / "manual" / item["id"] / "media" / "原件.txt").is_file())

    # --- 未提供 meta：仅文件夹名 / 品牌型号空 / 未分类 ---
    def test_without_metadata_build_only_uses_folder_name(self):
        manifest = self.manual / "manual.json"
        manifest.unlink()
        build(self.root)
        item = json.loads((self.root / "site" / "catalog.json").read_text())[0]
        self.assertEqual((item["title"], item["brand"], item["model"], item["category"]), ("A-123", "", "", "未分类"))

    # --- 自然排序照片 ---
    def test_natural_sort_photos(self):
        photo_manual = self.root / "manuals" / "相册"
        photo_manual.mkdir()
        for name in ("10.jpg", "2.jpg", "1.jpg"):
            (photo_manual / name).write_text("fake", encoding="utf-8")
        build(self.root)
        catalog = json.loads((self.root / "site" / "catalog.json").read_text())
        item = next(m for m in catalog if m["title"] == "相册")
        order = [p["source"] for p in item["pages_detail"]]
        self.assertEqual(order, ["1.jpg", "2.jpg", "10.jpg"])

    # --- 多文件合并告警 ---
    def test_multiple_files_warn_about_merge(self):
        multi = self.root / "manuals" / "多文件"
        multi.mkdir()
        (multi / "a.txt").write_text("第一节。", encoding="utf-8")
        (multi / "b.txt").write_text("第二节。", encoding="utf-8")
        build(self.root)
        catalog = json.loads((self.root / "site" / "catalog.json").read_text())
        item = next(m for m in catalog if m["title"] == "多文件")
        self.assertTrue(any("合并" in issue for issue in item["issues"]))

    # --- 每页识别方式 + 缺工具不虚构页数 ---
    def test_per_page_method_and_unknown_pdf_page_count(self):
        pdf_manual = self.root / "manuals" / "无工具PDF"
        pdf_manual.mkdir()
        (pdf_manual / "说明书.pdf").write_text("%PDF-1.4 fake", encoding="utf-8")
        build(self.root)
        catalog = json.loads((self.root / "site" / "catalog.json").read_text())
        item = next(m for m in catalog if m["title"] == "无工具PDF")
        # 未知页数不得强制虚构
        self.assertFalse(item["page_count_known"])
        self.assertEqual(item["page_count"], 0)
        self.assertEqual(item["status"], "original_only")
        self.assertTrue(any("pdftotext" in issue or "pdfinfo" in issue for issue in item["issues"]))

    def test_text_page_method_is_direct(self):
        build(self.root)
        catalog = json.loads((self.root / "site" / "catalog.json").read_text())
        item = next(m for m in catalog if m["title"] == "<测试说明书>")
        self.assertEqual(item["status"], "all_text")
        self.assertTrue(all(p["method"] == "direct" for p in item["pages_detail"]))

    # --- 非法元数据：失败但不消失 ---
    def test_illegal_metadata_recorded_and_not_dropped(self):
        bad = self.root / "manuals" / "坏元数据"
        bad.mkdir()
        (bad / "手册.txt").write_text("内容", encoding="utf-8")
        (bad / "manual.json").write_text('{这不是合法json', encoding="utf-8")
        build(self.root)
        catalog = json.loads((self.root / "site" / "catalog.json").read_text())
        item = next((m for m in catalog if m["title"] == "坏元数据"), None)
        self.assertIsNotNone(item, "非法元数据的说明书不应从目录中消失")
        self.assertEqual(item["status"], "processing_failed")
        self.assertTrue(any("处理失败" in issue for issue in item["issues"]))
        self.assertTrue((self.root / "site" / item["url"]).is_file())

    # --- 搜索元数据 fallback：无正文也能按名称查到 ---
    def test_search_metadata_fallback_present(self):
        pdf_manual = self.root / "manuals" / "无名PDF"
        pdf_manual.mkdir()
        (pdf_manual / "x.pdf").write_text("%PDF-1.4 fake", encoding="utf-8")
        build(self.root)
        meta_blob = (self.root / "site" / "assets" / "search-index.js").read_text()
        meta = json.loads(meta_blob.split("MANUAL_SHELF_META = ")[1].rstrip(";\n"))
        self.assertTrue(any(m["title"] == "无名PDF" for m in meta))
        catalog = json.loads((self.root / "site" / "catalog.json").read_text())
        item = next(m for m in catalog if m["title"] == "无名PDF")
        # 该本无正文，search.json 不应含其页面正文，但 catalog 含其元数据
        self.assertEqual(item["page_count"], 0)
        self.assertTrue(any(m["title"] == "无名PDF" for m in catalog))

    # --- report：新增/更新/移除 基于上次指纹 ---
    def test_report_changes_added_updated_removed(self):
        shutil.rmtree(self.root / "manuals")
        (self.root / "manuals").mkdir(parents=True)
        a = self.root / "manuals" / "设备A"
        a.mkdir()
        (a / "a.txt").write_text("初版内容。", encoding="utf-8")
        build(self.root)
        report = json.loads((self.root / "site" / "report.json").read_text())
        self.assertEqual(len(report["changes"]["added"]), 1)

        b = self.root / "manuals" / "设备B"
        b.mkdir()
        (b / "b.txt").write_text("第二台设备。", encoding="utf-8")
        (a / "a.txt").write_text("更新后的内容。", encoding="utf-8")
        build(self.root)
        report = json.loads((self.root / "site" / "report.json").read_text())
        titles = [c["title"] for c in report["changes"]["added"]]
        self.assertIn("设备B", titles)
        self.assertTrue(any(c["title"] == "设备A" for c in report["changes"]["updated"]))

        shutil.rmtree(a)
        build(self.root)
        report = json.loads((self.root / "site" / "report.json").read_text())
        self.assertTrue(any(c["title"] == "设备A" for c in report["changes"]["removed"]))

    # --- 原子发布：旧站进入备份，staging 成为 site ---
    def test_publish_moves_old_site_to_backup(self):
        output = self.root / "site"
        (output / "index.html").write_text("OLD")
        staging = self.root / ".site-build-x"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "index.html").write_text("NEW")
        backups = self.root / ".backups"
        manualsite._publish(staging, output, backups)
        self.assertEqual((output / "index.html").read_text(), "NEW")
        backups_list = list(backups.iterdir())
        self.assertEqual(len(backups_list), 1)
        # 备份保留的是旧站内容
        self.assertNotEqual((backups_list[0] / "index.html").read_text(), "NEW")

    def test_publish_rollback_on_rename_failure_keeps_old_site(self):
        output = self.root / "site"
        output.mkdir(parents=True, exist_ok=True)
        (output / "index.html").write_text("OLD")
        staging = self.root / ".site-build-x"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "index.html").write_text("NEW")
        backups = self.root / ".backups"
        orig_rename = Path.rename

        def patched(self, target):
            if self.name.startswith(".site-build"):
                raise OSError("boom")
            return orig_rename(self, target)

        Path.rename = patched
        try:
            with self.assertRaises(OSError):
                manualsite._publish(staging, output, backups)
        finally:
            Path.rename = orig_rename
        self.assertEqual((output / "index.html").read_text(), "OLD")
        self.assertFalse(staging.exists())

    # --- 目录无效：旧站保留 ---
    def test_invalid_source_keeps_old_site(self):
        build(self.root)  # 先生成正常站点
        (self.root / "site.json").write_text(json.dumps({"manuals_dir": "不存在的目录"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            build(self.root)
        # 旧站未被覆盖
        self.assertTrue((self.root / "site" / "index.html").is_file())
        catalog = json.loads((self.root / "site" / "catalog.json").read_text())
        self.assertTrue(any(m["title"] == "<测试说明书>" for m in catalog))

    # --- 注入构建失败：旧站不变 ---
    def test_injected_build_failure_keeps_old_site(self):
        build(self.root)
        original = (self.root / "site" / "index.html").read_text()
        original_build = (self.root / "site" / "catalog.json").read_text()
        real_build_manual = manualsite.build_manual

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        manualsite.build_manual = boom
        try:
            with self.assertRaises(RuntimeError):
                build(self.root)
        finally:
            manualsite.build_manual = real_build_manual
        self.assertEqual((self.root / "site" / "index.html").read_text(), original)
        self.assertEqual((self.root / "site" / "catalog.json").read_text(), original_build)

    # --- 拒绝穿透符号链接 ---
    def test_symlinks_are_refused(self):
        real = self.root / "manuals" / "真实"
        real.mkdir(parents=True)
        (real / "书.txt").write_text("真实内容。", encoding="utf-8")
        try:
            link_dir = self.root / "manuals" / "链接目录"
            link_dir.symlink_to(real, target_is_directory=True)
            link_file = self.root / "manuals" / "linked.txt"
            (real / "书2.txt").write_text("另一份。", encoding="utf-8")
            link_file.symlink_to(real / "书2.txt")
        except (OSError, NotImplementedError):
            self.skipTest("本环境无法创建符号链接，跳过 symlink 拒绝测试")
        build(self.root)
        catalog = json.loads((self.root / "site" / "catalog.json").read_text())
        titles = [m["title"] for m in catalog]
        self.assertIn("真实", titles)
        self.assertNotIn("链接目录", titles)
        self.assertNotIn("linked", titles)

    # --- 目录重叠拒绝 ---
    def test_overlapping_source_rejected(self):
        (self.root / "site.json").write_text(json.dumps({"manuals_dir": "site"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            build(self.root)

    # --- 真实 PDF / 扫描 / 照片：依赖工具可用，否则明确 skip，不声称 OCR 成功 ---
    def test_real_pdf_direct_extraction_requires_tools(self):
        if not (shutil.which("pdftotext") and shutil.which("pdfinfo")):
            self.skipTest("本机未安装 pdftotext/pdfinfo，跳过真实 PDF 提取测试（未验证 OCR）")
        # 仅在具备工具时运行；无可用测试 PDF 也跳过，避免伪造成功
        self.skipTest("需要真实样本 PDF 才能验证；当前不内置生成样本，跳过（未声称已验证 OCR）")

    def test_real_ocr_requires_tesseract(self):
        if not shutil.which("tesseract"):
            self.skipTest("本机未安装 tesseract，跳过 OCR 测试（未声称已验证 OCR 成功）")
        self.skipTest("需要真实扫描样本才能验证 OCR；当前不内置，跳过（未声称已验证 OCR）")

    # --- Issue 1：空态有报告链接与生成时间；有内容补更新说明 ---
    def test_empty_homepage_shows_report_link_and_generated_time(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            init(root)
            page = (root / "site" / "index.html").read_text(encoding="utf-8")
            self.assertIn("本报告生成时间", page)
            self.assertIn("report.html", page)
            self.assertIn("构建报告", page)

    def test_homepage_with_content_shows_update_instruction(self):
        source = self.root / "manuals" / "first.txt"
        source.write_text("第一份说明书", encoding="utf-8")
        build(self.root)
        page = (self.root / "site" / "index.html").read_text(encoding="utf-8")
        self.assertIn("python3 manualsite.py build", page)
        self.assertIn("重新生成", page)
        self.assertIn("report.html", page)
        self.assertNotIn("当前没有说明书", page)

    # --- Issue 2：_publish 用 uuid 唯一备份，绝不删除已有备份；回滚失败明确路径 ---
    def test_publish_creates_unique_backups_and_keeps_old(self):
        output = self.root / "site"
        output.mkdir(parents=True, exist_ok=True)
        (output / "index.html").write_text("V1")
        backups = self.root / ".backups"
        staging1 = self.root / f".site-build-{uuid.uuid4().hex}"
        staging1.mkdir()
        (staging1 / "index.html").write_text("V2")
        manualsite._publish(staging1, output, backups)
        self.assertEqual((output / "index.html").read_text(), "V2")
        self.assertEqual(len(list(backups.iterdir())), 1)

        staging2 = self.root / f".site-build-{uuid.uuid4().hex}"
        staging2.mkdir()
        (staging2 / "index.html").write_text("V3")
        manualsite._publish(staging2, output, backups)
        self.assertEqual((output / "index.html").read_text(), "V3")
        # 两版备份都保留，旧备份从未被 rmtree
        backup_dirs = list(backups.iterdir())
        self.assertEqual(len(backup_dirs), 2)
        contents = {(b / "index.html").read_text() for b in backup_dirs}
        self.assertIn("V1", contents)

    def test_publish_rollback_failure_exposes_backup_path(self):
        output = self.root / "site"
        output.mkdir(parents=True, exist_ok=True)
        (output / "index.html").write_text("OLD")
        staging = self.root / f".site-build-{uuid.uuid4().hex}"
        staging.mkdir()
        (staging / "index.html").write_text("NEW")
        backups = self.root / ".backups"
        orig_rename = Path.rename

        def patched(self, target):
            if self.name.startswith(".site-build"):
                raise OSError("boom on staging rename")
            if str(target).endswith("site"):
                raise OSError("boom on rollback rename")
            return orig_rename(self, target)

        Path.rename = patched
        try:
            with self.assertRaises(OSError) as cm:
                manualsite._publish(staging, output, backups)
        finally:
            Path.rename = orig_rename
        # 回滚失败信息必须明确备份位置，不再静默吞掉
        self.assertIn("备份", str(cm.exception))
        self.assertIn(str(backups), str(cm.exception))
        # 回滚失败：site 未恢复，但旧站内容完整保留在备份中（未被删除）
        backup_dirs = list(backups.iterdir())
        self.assertEqual(len(backup_dirs), 1)
        self.assertEqual((backup_dirs[0] / "index.html").read_text(), "OLD")

    # --- Issue 3：build_manual 复制单个不可读文件不拖垮整本；源链接只给成功复制者 ---
    def test_build_manual_keeps_book_when_one_source_unreadable(self):
        folder = self.root / "manuals" / "可读"
        folder.mkdir()
        good = folder / "a.txt"
        good.write_text("可检索正文。", encoding="utf-8")
        bad = folder / "b.txt"
        bad.write_text("另一份。", encoding="utf-8")
        # build 内部会 resolve(root)，macOS 上 /var → /private/var；用解析后的路径
        # 构造注入的报错，与真实 OSError 消息使用的绝对前缀保持一致。
        bad_path = str((Path(self.root).resolve() / "manuals" / "可读" / "b.txt"))

        real_copy = shutil.copy2

        def boom(src, dst, *args, **kwargs):
            if Path(src).name == "b.txt":
                raise OSError(f"[Errno 13] Permission denied: {bad_path}")
            return real_copy(src, dst, *args, **kwargs)

        manualsite.shutil.copy2 = boom
        try:
            build(self.root)
        finally:
            manualsite.shutil.copy2 = real_copy

        catalog = json.loads((self.root / "site" / "catalog.json").read_text())
        item = next(m for m in catalog if m["title"] == "可读")
        self.assertNotEqual(item["status"], "processing_failed")
        self.assertTrue((self.root / "site" / item["url"]).is_file())
        page = (self.root / "site" / item["url"]).read_text()
        # 只成功复制者（a.txt）有可点击源链接；b.txt 不可读，不应有 media 链接
        self.assertIn('media/a.txt', page)
        self.assertNotIn('media/b.txt', page)
        self.assertTrue(any("无法复制源文件" in i for i in item["issues"]))
        # 复制失败提示脱敏，不泄露绝对路径
        self.assertNotIn(bad_path, "\n".join(item["issues"]))

    # --- Issue 4：pdf 末尾空白页保留；image 无文字 method=none；语言包精确匹配 ---
    def test_pdf_pages_keeps_trailing_blank_page(self):
        real_run = manualsite.run

        def fake_run(*args):
            if args and args[0] == "pdftotext":
                # 3 页，第 3 页空白，末尾仅一个 formfeed 哨兵
                return "第一页内容\f第二页内容\f\f"
            if args and args[0] == "pdfinfo":
                return "Pages: 3\n"
            return ""

        manualsite.run = fake_run
        tools = {"pdftotext": True, "pdfinfo": True, "pdftoppm": False,
                 "tesseract": False, "ocr_language_available": False}
        try:
            pages, count_known = manualsite.pdf_pages(Path("fake.pdf"), "chi_sim+eng", tools, [])
        finally:
            manualsite.run = real_run
        self.assertTrue(count_known)
        # 旧 while-pop 会丢尾部空白页（只得到 2 页）；修正后应保留 3 页
        self.assertEqual(len(pages), 3)
        self.assertEqual(pages[2]["text"], "")
        self.assertEqual(pages[2]["method"], "none")

    def test_image_pages_method_none_when_no_text(self):
        real_run = manualsite.run

        def fake_run(*args):
            if args and args[0] == "tesseract":
                return ""  # OCR 返回空
            return ""

        manualsite.run = fake_run
        tools = {"tesseract": True, "ocr_language_available": True}
        img = self.root / "photo.jpg"
        img.write_text("fake", encoding="utf-8")
        try:
            pages = manualsite.image_pages(img, "chi_sim+eng", tools, [])
        finally:
            manualsite.run = real_run
        self.assertEqual(pages[0]["method"], "none")
        self.assertEqual(pages[0]["text"], "")

    def test_lang_available_exact_match(self):
        # 不存在的语言包必须返回 False（精确匹配，不靠子串误判）
        self.assertFalse(manualsite._lang_available("definitely_not_a_real_langpack_zzz"))
        if shutil.which("tesseract"):
            avail = manualsite.list_tesseract_langs()
            if "chi_sim" in avail:
                self.assertTrue(manualsite._lang_available("chi_sim"))
            # 组合里含缺失包应判为不可用
            self.assertFalse(manualsite._lang_available("chi_sim+eng+definitely_missing"))

    # --- Issue 5：非法缓存结构重新提取而非崩溃；合法缓存命中跳过提取 ---
    def test_cache_invalid_schema_triggers_re_extraction(self):
        build(self.root)
        catalog = json.loads((self.root / "site" / "catalog.json").read_text())
        item = catalog[0]
        slug = item["id"]
        cache_file = self.root / ".cache" / f"{slug}.json"
        self.assertTrue(cache_file.exists())
        bad_cases = [
            "[1, 2, 3]",  # list 而非 dict
            json.dumps({"version": 0, "fingerprint": "x", "pages": [], "notes": [], "count_known": True}),
            json.dumps({"version": 1, "fingerprint": "wrong", "pages": [], "notes": [], "count_known": True}),
            json.dumps({"version": 1, "fingerprint": item["fingerprint"], "pages": "notalist", "notes": [], "count_known": True}),
            json.dumps({"version": 1, "fingerprint": item["fingerprint"], "pages": [], "notes": "no", "count_known": True}),
            json.dumps({"version": 1, "fingerprint": item["fingerprint"], "pages": [], "notes": [], "count_known": "yes"}),
        ]
        for bad in bad_cases:
            cache_file.write_text(bad, encoding="utf-8")
            build(self.root)
            catalog2 = json.loads((self.root / "site" / "catalog.json").read_text())
            item2 = next(m for m in catalog2 if m["id"] == slug)
            self.assertEqual(item2["page_count"], item["page_count"], f"非法缓存未触发重新提取：{bad[:30]}")
            self.assertEqual(len(item2["pages_detail"]), 2)

    def test_cache_valid_hit_skips_re_extraction(self):
        calls = {"n": 0}
        real_extract = manualsite.extract

        def counting(*a, **k):
            calls["n"] += 1
            return real_extract(*a, **k)

        manualsite.extract = counting
        try:
            build(self.root)  # 首次：写缓存，extract 1 次
            build(self.root)  # 二次：命中缓存，不应再提取
        finally:
            manualsite.extract = real_extract
        self.assertEqual(calls["n"], 1)

    # --- Issue 6：notes/error 中的绝对路径写出前脱敏 ---
    def test_redact_strips_absolute_prefixes(self):
        out = manualsite.redact(
            "/abs/root/manuals/厨房/X：boom /abs/root",
            ("/abs/root", "/abs/root/manuals"),
        )
        self.assertNotIn("/abs/root", out)
        self.assertIn("厨房/X", out)
        self.assertIn("boom", out)


if __name__ == "__main__":
    unittest.main()
