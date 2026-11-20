"""axe-core (vendored, test only) for the browser tests."""

from pathlib import Path

AXE = Path(__file__).resolve().parent.parent / "vendor" / "axe" / "axe.min.js"


def run_axe(page, label):
    """Run axe-core (vendored) on the page. Returns serious/critical violations as text."""
    page.add_script_tag(path=str(AXE))
    result = page.evaluate("""async () => {
        const r = await axe.run(document, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "best-practice"] },
                                            resultTypes: ["violations"] });
        return r.violations.map(v => ({ id: v.id, impact: v.impact, help: v.help,
                                        nodes: v.nodes.slice(0, 4).map(n => n.target.join(" ") + " :: " + (n.failureSummary || "").split("\\n").slice(0, 2).join(" ")) }));
    }""")
    bad = [v for v in result if v["impact"] in ("serious", "critical")]
    return [f"{label}: [{v['impact']}] {v['id']}: {v['help']}\n    " + "\n    ".join(v["nodes"]) for v in bad], result
