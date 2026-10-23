import React, { useEffect, useId, useState } from "react";

// Mermaid is large, so it is loaded the first time a diagram is shown, not with the app.
let mermaidReady = null;

function loadMermaid() {
  mermaidReady ??= import("mermaid").then(({ default: mermaid }) => {
    mermaid.initialize({
      startOnLoad: false,
      // Labels come from the user's words and the AI: "strict" escapes any HTML in them.
      securityLevel: "strict",
      theme: "dark",
      fontFamily: '"DM Sans", sans-serif',
      flowchart: { htmlLabels: false, curve: "basis" },
    });
    return mermaid;
  });
  return mermaidReady;
}

// The panel is narrow and tall: a left-to-right chart would be shrunk until its text is unreadable.
function topToBottom(source) {
  return source.replace(/^(\s*flowchart)\s+LR\b/, "$1 TD");
}

// Draws the backend's Mermaid flowchart. If it cannot be drawn, the source is shown instead.
export default function MermaidDiagram({ source }) {
  const renderId = `diagram-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  const [svg, setSvg] = useState("");
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setSvg("");
    setFailed(false);

    async function draw() {
      try {
        const mermaid = await loadMermaid();
        const chart = topToBottom(source);
        // Checked first: rendering invalid text would leave Mermaid's error graphic in the page.
        if (!(await mermaid.parse(chart, { suppressErrors: true }))) throw new Error("invalid diagram");
        const { svg: drawn } = await mermaid.render(renderId, chart);
        if (!cancelled) setSvg(drawn);
      } catch {
        if (!cancelled) setFailed(true);
      }
    }

    draw();
    return () => {
      cancelled = true;
    };
  }, [source, renderId]);

  if (failed) return <pre className="diagram-code">{source}</pre>;
  if (!svg) return <div className="diagram-loading">Drawing the diagram…</div>;
  // The SVG comes from Mermaid in strict mode, which sanitizes it.
  return <div className="diagram-render" role="img" aria-label="Workflow diagram" dangerouslySetInnerHTML={{ __html: svg }} />;
}
