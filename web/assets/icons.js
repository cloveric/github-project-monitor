(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const iconMap = {
    check: [["path", { d: "M20 6 9 17l-5-5" }]],
    copy: [
      ["rect", { x: "9", y: "9", width: "13", height: "13", rx: "2" }],
      ["path", { d: "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" }],
    ],
    download: [
      ["path", { d: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" }],
      ["path", { d: "M7 10l5 5 5-5" }],
      ["path", { d: "M12 15V3" }],
    ],
    "file-text": [
      ["path", { d: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" }],
      ["path", { d: "M14 2v6h6" }],
      ["path", { d: "M16 13H8" }],
      ["path", { d: "M16 17H8" }],
      ["path", { d: "M10 9H8" }],
    ],
    filter: [
      ["path", { d: "M22 3H2l8 9v7l4 2v-9z" }],
    ],
    "git-branch": [
      ["line", { x1: "6", y1: "3", x2: "6", y2: "15" }],
      ["circle", { cx: "18", cy: "6", r: "3" }],
      ["circle", { cx: "6", cy: "18", r: "3" }],
      ["path", { d: "M18 9a9 9 0 0 1-9 9" }],
    ],
    "git-pull-request-arrow": [
      ["circle", { cx: "5", cy: "6", r: "3" }],
      ["circle", { cx: "19", cy: "18", r: "3" }],
      ["path", { d: "M5 9v12" }],
      ["path", { d: "M19 15V6" }],
      ["path", { d: "m16 9 3-3 3 3" }],
    ],
    "hard-drive": [
      ["rect", { x: "2", y: "6", width: "20", height: "12", rx: "2" }],
      ["path", { d: "M2 14h20" }],
      ["path", { d: "M6 10h.01" }],
      ["path", { d: "M10 10h.01" }],
    ],
    play: [
      ["path", { d: "M5 5a2 2 0 0 1 3-1.7l10 7a2 2 0 0 1 0 3.4l-10 7A2 2 0 0 1 5 19z" }],
    ],
    radar: [
      ["circle", { cx: "12", cy: "12", r: "10" }],
      ["circle", { cx: "12", cy: "12", r: "6" }],
      ["circle", { cx: "12", cy: "12", r: "2" }],
      ["path", { d: "m12 12 7-7" }],
    ],
    "refresh-cw": [
      ["path", { d: "M21 12a9 9 0 0 1-15.5 6.2" }],
      ["path", { d: "M3 12A9 9 0 0 1 18.5 5.8" }],
      ["path", { d: "M21 3v6h-6" }],
      ["path", { d: "M3 21v-6h6" }],
    ],
    search: [
      ["circle", { cx: "11", cy: "11", r: "8" }],
      ["path", { d: "m21 21-4.3-4.3" }],
    ],
    tag: [
      ["path", { d: "M12.6 2.6H4a2 2 0 0 0-2 2v8.6a2 2 0 0 0 .6 1.4l6.8 6.8a2 2 0 0 0 2.8 0l9.2-9.2a2 2 0 0 0 0-2.8l-6.8-6.8a2 2 0 0 0-1.4-.6Z" }],
      ["circle", { cx: "7.5", cy: "7.5", r: ".5" }],
    ],
    "trash-2": [
      ["path", { d: "M3 6h18" }],
      ["path", { d: "M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" }],
      ["path", { d: "M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" }],
      ["path", { d: "M10 11v6" }],
      ["path", { d: "M14 11v6" }],
    ],
    wrench: [
      ["path", { d: "M14.7 6.3a4 4 0 0 0-5 5L3 18l3 3 6.7-6.7a4 4 0 0 0 5-5l-2.4 2.4-3-3z" }],
    ],
  };

  function createNode(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    return node;
  }

  function createIcon(name) {
    const svg = createNode("svg", {
      xmlns: SVG_NS,
      width: "24",
      height: "24",
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": "2",
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": "true",
      focusable: "false",
    });
    (iconMap[name] || iconMap.search).forEach(([tag, attrs]) => {
      svg.appendChild(createNode(tag, attrs));
    });
    return svg;
  }

  function createIcons() {
    document.querySelectorAll("[data-lucide]").forEach((placeholder) => {
      const name = placeholder.getAttribute("data-lucide") || "search";
      const icon = createIcon(name);
      placeholder.replaceWith(icon);
    });
  }

  window.lucide = { createIcons };
})();
