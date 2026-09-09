from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(unittest.TestCase):
    def test_operational_workspace_landmarks_are_present(self):
        html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")

        self.assertIn('class="app-shell"', html)
        self.assertIn('id="taskStats"', html)
        self.assertIn('id="tasksList"', html)

    def test_existing_form_controls_and_routes_remain_available(self):
        html = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
        script = (ROOT / "app/static/app.js").read_text(encoding="utf-8")

        for element_id in (
            "linksList",
            "addLinkBtn",
            "limitPerLink",
            "onlyRegion",
            "mergeFile",
            "startBtn",
        ):
            self.assertIn(f'id="{element_id}"', html)

        for route in (
            "/api/tasks",
            "/api/proxies",
            "/api/captcha-key",
            "/api/cookie-service-key",
        ):
            self.assertIn(route, script)


if __name__ == "__main__":
    unittest.main()
