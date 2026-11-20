// QR codes as inline SVG, using the vendored qrcode-generator (MIT, ../vendor/qrcode/).
import qrcode from "../vendor/qrcode/qrcode.mjs";

const SVG = "http://www.w3.org/2000/svg";

export function qrSvg(text, { label = "QR code" } = {}) {
  const qr = qrcode(0, "M");
  qr.addData(text);
  qr.make();
  const n = qr.getModuleCount();
  const quiet = 2;
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", `${-quiet} ${-quiet} ${n + quiet * 2} ${n + quiet * 2}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", label);
  svg.setAttribute("shape-rendering", "crispEdges");
  let d = "";
  for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (qr.isDark(r, c)) d += `M${c} ${r}h1v1h-1z`;
  const path = document.createElementNS(SVG, "path");
  path.setAttribute("d", d);
  path.setAttribute("fill", "#111114");
  svg.appendChild(path);
  return svg;
}
