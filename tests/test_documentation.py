import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
EXTERNAL_SCHEMES = ("http://", "https://", "mailto:", "tel:", "data:")


class DocumentationTest(unittest.TestCase):
    def test_local_markdown_links_resolve(self) -> None:
        markdown_files = [ROOT / "README.md"]
        markdown_files.extend(ROOT.glob("*.md"))
        markdown_files.extend((ROOT / "docs").rglob("*.md"))
        markdown_files.extend((ROOT / "web").rglob("README.md"))

        failures: list[str] = []
        for document in sorted(set(markdown_files)):
            text = document.read_text(encoding="utf-8")
            for raw_target in MARKDOWN_LINK_RE.findall(text):
                target = raw_target.strip().strip("<>")
                if not target or target.startswith("#") or target.lower().startswith(EXTERNAL_SCHEMES):
                    continue
                path_part = unquote(target.split("#", 1)[0].split("?", 1)[0])
                if not path_part:
                    continue
                resolved = (document.parent / path_part).resolve()
                if not resolved.exists():
                    failures.append(f"{document.relative_to(ROOT)} -> {target}")

        self.assertEqual(failures, [], "Broken local links:\n" + "\n".join(failures))

    def test_removed_legacy_manuals_do_not_return(self) -> None:
        removed = {
            "BUILD_MANUAL.md",
            "CODEX_OPERATOR_HOOK.md",
            "HG113_CONNECTION_MANUAL.md",
            "HG113_PRODUCT_PLAN.md",
            "INTERACTION_TARGETS.md",
            "WIFI_PROVISIONING.md",
        }

        self.assertTrue(all(not (ROOT / "docs" / name).exists() for name in removed))

    def test_wiring_diagram_uses_current_esp32s3_pins(self) -> None:
        wiring = (ROOT / "docs" / "electronics" / "assets" / "hg113_reference_wiring.svg").read_text(encoding="utf-8")

        self.assertIn("ESP32-S3", wiring)
        self.assertIn("GPIO4：开关输入", wiring)
        self.assertIn("GPIO2：蜂鸣器", wiring)
        self.assertIn("GPIO1：LED", wiring)
        self.assertNotIn("GPIO0：开关输入", wiring)
        self.assertNotIn("GPIO21：蜂鸣器", wiring)
        self.assertNotIn("GPIO20：LED", wiring)

    def test_command_center_has_verified_offline_fallback_and_assets(self) -> None:
        command_center = (ROOT / "web" / "variant-earth-command-center" / "index.html").read_text(encoding="utf-8")

        self.assertIn("maplibre-gl@5.6.2", command_center)
        self.assertIn('integrity="sha384-', command_center)
        self.assertIn('typeof globalThis.maplibregl !== "undefined"', command_center)
        self.assertIn('stage.dataset.mapStyle = "offline-unavailable"', command_center)
        self.assertNotIn("earth-clouds.png", command_center)
        self.assertFalse((ROOT / "web" / "variant-earth-command-center" / "assets" / "earth-clouds.png").exists())
        self.assertFalse((ROOT / "docs" / "electronics" / "assets" / "photos" / "IMG_8616..JPG").exists())


if __name__ == "__main__":
    unittest.main()
